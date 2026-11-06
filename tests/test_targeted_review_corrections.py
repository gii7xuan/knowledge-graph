"""Field-only correction cannot reroll judgments or multiply paid requests."""
import copy
import json
from pathlib import Path
import shutil
import unittest
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from review_agent.llm_clients import LLMResponse
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.repository import ReviewRepository
from review_agent.validators import judge_validator
from review_agent.validator_contracts import versions_for,validate_vote,prompt_for
from review_agent.contracts import sha256_json
from review_agent.output_structure import resolve_profile
from review_agent.request_identity import semantic_request_key
from review_agent.request_cache import validation_request_key
from review_agent import response_contract_v6_2 as contract,response_corrections as repairs
from review_agent.correction_repository import reserve
from tests import test_validator_v6_dimensions as fixtures


class RecordingClient:
    model_id='m';model_family='family';base_url='https://fixture.example/v1';stream_responses=False
    def __init__(self,callback):self.callback=callback;self.calls=[];self.options=[]
    def complete_json(self,payload,**options):
        self.calls.append(copy.deepcopy(payload));self.options.append(copy.deepcopy(options))
        value=self.callback(payload)
        if isinstance(value,Exception):raise value
        return LLMResponse(value,input_tokens=10,output_tokens=10,returned_model_id='m')


class TargetedCorrectionTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.ValidatorV6DimensionTests(methodName='runTest');f.setUp();self.f=f
        self.folder=Path(__file__).resolve().parents[1]/'runs/_test_targeted_corrections'/uuid4().hex
        self.folder.mkdir(parents=True);self.addCleanup(shutil.rmtree,self.folder)
        self.repo=ReviewRepository(self.folder/'review.sqlite3');self.repo.initialize()

    def setup_review(self,raw,patch=None,limits=None):
        mapping=self.f.response(mapping='unassigned')['dimensions']['mapping']
        def respond(p):
            if p.get('task_type')!='triple_review_field_correction':return copy.deepcopy(raw)
            if patch is not None:return patch(p) if callable(patch) else copy.deepcopy(patch)
            return {'repairs':{e['key']:copy.deepcopy(mapping) for e in p['errors']}}
        client=RecordingClient(respond);voter={'id':'a','model_id':'m','model_family':'family','client':client}
        config={'validator_contract_version':contract.CONTRACT_VERSION,'response_correction':limits or {},
            'output_structure':{'capabilities_path':str(self.folder/'missing.json')}}
        proposal={**copy.deepcopy(self.f.proposal),**versions_for(config)}
        proposal['validator_generation']=[{'voter_id':'a','max_output_tokens':8192,'output_transport':resolve_profile(voter,config,contract.RESPONSE_SCHEMA)}]
        return client,voter,proposal

    def missing_mapping(self,source='supported',negative=False):
        raw=self.f.response(source=source)
        if negative:raw['dimensions']['qualifiers']['status']='fail';raw['primary_evidence_ids']=[]
        raw['dimensions'].pop('mapping');return raw

    def test_inline_patch_preserves_negative_judgment_and_limits_output(self):
        raw=self.missing_mapping(negative=True);before=copy.deepcopy(raw)
        client,voter,p=self.setup_review(raw)
        vote=judge_validator(voter,p,max_retries=99)
        self.assertEqual(vote['response_status'],'ok',vote)
        self.assertEqual(len(client.calls),2)
        self.assertEqual(vote['expression_status'],'needs_revision');self.assertEqual(vote['verdict'],'uncertain')
        self.assertEqual(vote['raw_response'],before);self.assertEqual(raw,before)
        self.assertEqual(vote['corrected_response']['dimensions']['qualifiers']['status'],'fail')
        second=client.calls[1];self.assertEqual(second['task_type'],'triple_review_field_correction')
        self.assertEqual(second['errors'][0]['path'],'/dimensions/mapping')
        self.assertEqual(second['errors'][0]['kind'],'missing')
        self.assertNotIn('ontology_catalog',second)
        self.assertEqual(second['ontology_context']['scope'],'selected_frozen_properties_only')
        self.assertLessEqual(client.options[1]['max_tokens'],1536)
        self.assertEqual(validate_vote(vote,contract.CONTRACT_VERSION),[])

    def test_true_source_rejection_and_counterevidence_survive_patch(self):
        raw=self.missing_mapping(source='contradicted');client,voter,p=self.setup_review(raw)
        vote=judge_validator(voter,p,max_retries=1)
        self.assertEqual(vote['response_status'],'ok');self.assertEqual(vote['verdict'],'invalid')
        self.assertEqual(vote['source_support'],'contradicted')
        self.assertEqual(vote['counter_evidence_ids'],[self.f.f.counter['evidence_id']])
        self.assertEqual(vote['corrected_response']['evidence_assessments'],raw['evidence_assessments'])

    def test_valid_negative_vote_is_never_reasked(self):
        raw=self.f.response(source='contradicted');client,voter,p=self.setup_review(raw)
        vote=judge_validator(voter,p,max_retries=99)
        self.assertEqual(vote['response_status'],'ok');self.assertEqual(vote['verdict'],'invalid')
        self.assertEqual(len(client.calls),1)

    def test_exact_type_error_repairs_only_note(self):
        raw=self.f.response();raw['dimensions']['qualifiers'].update(status='fail',note=False);raw['primary_evidence_ids']=[]
        client,voter,p=self.setup_review(raw,lambda q:{'repairs':{e['key']:'MOCK missing source qualifier' for e in q['errors']}})
        vote=judge_validator(voter,p,max_retries=1)
        self.assertEqual(vote['response_status'],'ok');error=client.calls[1]['errors'][0]
        self.assertEqual((error['path'],error['actual_type']),('/dimensions/qualifiers/note','boolean'))
        self.assertEqual(vote['corrected_response']['dimensions']['qualifiers']['status'],'fail')

    def test_missing_counter_review_citation_is_completed_without_status_change(self):
        raw=self.f.response();raw['dimensions']['counterevidence']['evidence_ids']=[]
        eid=self.f.f.good['evidence_id']
        client,voter,p=self.setup_review(raw,lambda q:{'repairs':{e['key']:[eid] for e in q['errors']}})
        vote=judge_validator(voter,p,max_retries=1)
        self.assertEqual(vote['response_status'],'ok')
        self.assertEqual(vote['corrected_response']['dimensions']['counterevidence']['status'],'pass')
        self.assertEqual(client.calls[1]['errors'][0]['kind'],'missing_citations')

    def test_patch_cannot_add_an_unrequested_status_change(self):
        raw=self.missing_mapping(negative=True)
        client,voter,p=self.setup_review(raw,lambda q:{'repairs':{q['errors'][0]['key']:{'status':'unassigned','relation_id':None,
            'competing_relation_ids':[],'evidence_ids':[],'note':'not mapped'},'outside_field':{'status':'pass'}}})
        vote=judge_validator(voter,p,max_retries=99)
        self.assertNotEqual(vote['response_status'],'ok');self.assertTrue(vote['correction_terminal'])
        self.assertEqual(vote['raw_response']['dimensions']['qualifiers']['status'],'fail')
        self.assertEqual(len(client.calls),2)

    def test_internal_semantic_conflict_and_unparseable_json_do_not_trigger_a_revote(self):
        raw=self.f.response();raw['evidence_assessments'].append({'evidence_id':self.f.f.counter['evidence_id'],'role':'counter','note':'still material'})
        for body in (raw,'{not-json'):
            with self.subTest(body_type=type(body).__name__):
                client,voter,p=self.setup_review(body)
                vote=judge_validator(voter,p,max_retries=99)
                self.assertEqual(len(client.calls),1);self.assertTrue(vote['correction_terminal'])
                self.assertEqual(vote['raw_response'],body)

    def test_wrong_existing_status_type_is_not_reinterpreted_as_support(self):
        raw=self.f.response();raw['dimensions']['qualifiers']['status']=['fail']
        client,voter,p=self.setup_review(raw)
        vote=judge_validator(voter,p,max_retries=1)
        self.assertEqual(len(client.calls),1);self.assertTrue(vote['correction_terminal'])
        self.assertEqual(vote['raw_response']['dimensions']['qualifiers']['status'],['fail'])

    def test_input_and_caller_budget_stop_correction_before_extra_call(self):
        for limits,caller in (({'max_input_bytes':128},None),({},0),({'max_corrections_per_seat':0},None)):
            client,voter,p=self.setup_review(self.missing_mapping(),limits=limits)
            if caller is not None:voter['correction_token_budget']=caller
            vote=judge_validator(voter,p,max_retries=1)
            self.assertEqual(len(client.calls),1);self.assertTrue(vote['correction_terminal'])

    def test_later_recovery_uses_one_patch_not_a_second_full_review(self):
        client,voter,p=self.setup_review(self.missing_mapping(negative=True))
        first=judge_validator(voter,p,repository=self.repo,max_retries=0)
        self.assertEqual(first['response_status'],'invalid_contract');self.assertEqual(len(client.calls),1)
        second=judge_validator(voter,p,repository=self.repo,max_retries=0)
        self.assertEqual(second['response_status'],'ok',second)
        self.assertEqual(len(client.calls),2);self.assertEqual(client.calls[1]['task_type'],'triple_review_field_correction')
        self.assertEqual(second['expression_status'],'needs_revision')
        third=judge_validator(voter,p,repository=self.repo,max_retries=99)
        self.assertEqual(third['actual_calls'],0);self.assertEqual(len(client.calls),2)
        self.assertEqual(third['response_status'],'ok',third)
        self.assertEqual(len(self.repo.request_history(second['request_key'])),2)

    def test_failed_patch_cannot_be_repeated_by_resuming_the_same_seat(self):
        client,voter,p=self.setup_review(self.missing_mapping(),patch={'wrong':'shape'})
        judge_validator(voter,p,repository=self.repo,max_retries=0)
        second=judge_validator(voter,p,repository=self.repo,max_retries=0)
        self.assertTrue(second['correction_terminal']);self.assertEqual(len(client.calls),2)
        for _ in range(3):
            again=judge_validator(voter,p,repository=self.repo,max_retries=99)
            self.assertEqual(again['actual_calls'],0)
        self.assertEqual(len(client.calls),2)
        with self.repo.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM review_response_corrections').fetchone()[0],1)

    def test_unknown_patch_outcome_is_not_sent_again(self):
        client,voter,p=self.setup_review(self.missing_mapping(),patch=lambda _:ConnectionError('synthetic provider outcome unknown'))
        first=judge_validator(voter,p,repository=self.repo,max_retries=1)
        self.assertTrue(first['outcome_unknown']);self.assertEqual(len(client.calls),2)
        again=judge_validator(voter,p,repository=self.repo,max_retries=99)
        self.assertTrue(again['outcome_unknown']);self.assertEqual(again['actual_calls'],0)
        self.assertEqual(len(client.calls),2)

    def test_frozen_correction_prompt_cannot_silently_change(self):
        from review_agent.targeted_validation import judge_repair_once
        raw=self.missing_mapping();client,voter,p=self.setup_review(raw)
        p['correction_policy']['prompt_hash']='changed'
        p['correction_policy_hash']=sha256_json(p['correction_policy'])
        with self.assertRaises(ValueError):prompt_for(p)
        plan,errors=repairs.make_plan(raw,p,p['correction_policy']);self.assertEqual(errors,[])
        result=judge_repair_once(voter,p,{'kind':'field_patch','repair_plan':plan,'original_response':raw,'original_raw_response':raw})
        self.assertNotEqual(result['response_status'],'ok');self.assertEqual(client.calls,[])

    def test_atomic_reservation_allows_only_one_correction_for_a_frozen_seat(self):
        raw=self.missing_mapping();client,voter,p=self.setup_review(raw)
        plan,_=repairs.make_plan(raw,p,p['correction_policy'])
        key=validation_request_key(voter,p);scope=semantic_request_key('triple_review',voter,p)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:reserve(self.repo,scope,key,p,plan,1000),range(2)))
        self.assertEqual(sum(ident is not None for ident,_ in results),1)

    def test_candidate_correction_budget_spans_different_seats(self):
        raw=self.missing_mapping();_,voter,p=self.setup_review(raw)
        plan,_=repairs.make_plan(raw,p,p['correction_policy'])
        for i in range(3):self.assertIsNotNone(reserve(self.repo,'scope'+str(i),'key'+str(i),p,plan,1000)[0])
        self.assertIsNone(reserve(self.repo,'scope4','key4',p,plan,1000)[0])

    def test_partial_ontology_context_cannot_invent_a_confirmed_gap(self):
        raw=self.missing_mapping()
        mapping=self.f.response(mapping='not_covered')['dimensions']['mapping']
        client,voter,p=self.setup_review(raw,lambda q:{'repairs':{q['errors'][0]['key']:mapping}})
        vote=judge_validator(voter,p,max_retries=1)
        self.assertNotEqual(vote['response_status'],'ok');self.assertEqual(len(client.calls),2)

    def test_stored_correction_provenance_is_checked_again(self):
        client,voter,p=self.setup_review(self.missing_mapping(negative=True))
        vote=judge_validator(voter,p,max_retries=1)
        self.assertEqual(validate_vote(vote,contract.CONTRACT_VERSION),[])
        tampered=copy.deepcopy(vote);tampered['corrected_response']['dimensions']['qualifiers']['status']='pass'
        self.assertTrue(validate_vote(tampered,contract.CONTRACT_VERSION))

    def test_request_subprocess_uses_field_patch_and_frozen_small_output_limit(self):
        raw=self.missing_mapping(negative=True);mapping=self.f.response(mapping='unassigned')['dimensions']['mapping'];captured=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));captured.append(request)
                payload=json.loads(request['messages'][1]['content'])
                answer=raw if payload.get('task_type')!='triple_review_field_correction' else {'repairs':{payload['errors'][0]['key']:mapping}}
                body=json.dumps({'id':'local_patch','model':'m','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(answer)}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':10}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        _,voter,p=self.setup_review(raw)
        voter['client']=OpenAICompatibleClient(api_key='local-fixture',base_url='http://127.0.0.1:'+str(server.server_port)+'/v1',model_id='m',model_family='family',timeout_seconds=8)
        p['validator_generation'][0]['output_transport']=resolve_profile(voter,{'output_structure':{'capabilities_path':str(self.folder/'missing.json')}},contract.RESPONSE_SCHEMA)
        vote=judge_validator(voter,p,repository=self.repo,max_retries=1,per_call_timeout_seconds=8)
        self.assertEqual(vote['response_status'],'ok',vote)
        self.assertEqual(len(captured),2)
        self.assertEqual(json.loads(captured[1]['messages'][1]['content'])['task_type'],'triple_review_field_correction')
        self.assertEqual(captured[1]['max_tokens'],1536)
        history=self.repo.request_history(vote['request_key'])
        self.assertEqual(history[-1]['metadata']['generation']['max_output_tokens'],1536)
        self.assertEqual(history[-1]['metadata']['generation']['request_phase'],'field_patch')
        self.assertEqual(vote['corrected_response']['dimensions']['qualifiers']['status'],'fail')
        tampered=copy.deepcopy(vote);tampered['raw_correction_response']={'repairs':{}}
        self.assertTrue(validate_vote(tampered,contract.CONTRACT_VERSION))


if __name__=='__main__':unittest.main()
