"""Follow-up behavior: bounded ontology investigation, transport and durable grants."""
import copy
import json
import threading
import time
import unittest
from types import SimpleNamespace
from tests import test_review_recovery_v4 as helpers
from review_agent.llm_clients import OpenAICompatibleClient
from review_agent.repository import ConflictError,ValidationError
from review_agent.request_cache import validation_request_key
from review_agent.request_identity import semantic_request_key
from review_agent.jobs import JobRunner
from review_agent.agent_state import AgentBudget
from ontology_review.runtime import OntologyReviewRuntime


class RequestAndBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RequestRecoveryTests(methodName='runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.repo=self.fixture.repo

    def test_generation_change_cannot_escape_an_unknown_request(self):
        voter=self.fixture.voter;p=helpers.proposal()
        key=validation_request_key(voter,p)
        self.repo.claim_validation_request(key,'lost',semantic_key=semantic_request_key('triple_review',voter,p))
        voter['client'].thinking_mode='disabled'
        self.assertNotEqual(key,validation_request_key(voter,p))
        vote=self.fixture.judge(per_call_timeout_seconds=.05)
        self.assertEqual(vote['error_code'],'RELATED_REQUEST_OUTCOME_UNKNOWN')
        self.assertEqual(len(voter['client'].calls),0)

    def test_gateway_504_is_unknown_and_never_automatically_retried(self):
        self.fixture.client._callable=lambda payload:(_ for _ in ()).throw(RuntimeError('HTTP 504 Gateway Timeout'))
        vote=self.fixture.judge(max_retries=3,per_call_timeout_seconds=1)
        self.assertTrue(vote['outcome_unknown']);self.assertFalse(vote['retryable'])
        self.assertEqual(vote['error_code'],'GATEWAY_REQUEST_OUTCOME_UNKNOWN')
        self.fixture.judge(max_retries=3,per_call_timeout_seconds=.02)
        self.assertEqual(len(self.fixture.client.calls),1)

    def test_legacy_gateway_failure_is_quarantined_with_history(self):
        p=helpers.proposal();voter=self.fixture.voter
        key=validation_request_key(voter,p);binding=helpers.vote_binding(voter,p)
        self.repo.claim_validation_request(key,'legacy')
        aid=self.repo.begin_request_attempt(key,'legacy',1,{'binding':binding,'proposal':p,'task_type':'triple_review'})
        self.repo.finish_request_attempt(aid,'legacy',{**binding,'response_status':'api_error','http_status':504,
            'error_code':'TRANSIENT_API_ERROR','retryable':True})
        self.assertEqual(self.repo.quarantine_gateway_timeouts('operator diagnosis'),[aid])
        self.assertEqual(self.repo.validation_request(key)['status'],'unknown')
        self.assertEqual(self.repo.request_attempt(aid)['result']['previous_classification']['error_code'],'TRANSIENT_API_ERROR')
        self.fixture.judge(per_call_timeout_seconds=.02)
        self.assertEqual(len(self.fixture.client.calls),0)

    def test_additional_budget_is_idempotent_and_does_not_reset_usage(self):
        self.repo.create_run({'run_id':'run'})
        self.repo.add_candidate({**helpers.proposal()['candidate'],'run_id':'run','document_id':'doc',
            'document_version':'doc-v1','ontology_version':'ontology-v1'})
        case=self.repo.ensure_case('c')
        self.repo.charge_cases([case],{'tokens':1234},'old-cost','triple_review','old-audit')
        before=self.repo.case_status(case)
        grant=self.repo.authorize_case_budget('c',{'tokens':5000,'llm_calls':3},reason='explicit follow-up',actor='operator',authorization_key='fixture-one')
        self.assertEqual(grant,self.repo.authorize_case_budget('c',{'tokens':5000,'llm_calls':3},reason='explicit follow-up',actor='operator',authorization_key='fixture-one'))
        after=self.repo.case_status(case)
        self.assertEqual(after['usage'],before['usage'])
        self.assertEqual(after['limits']['tokens'],before['limits']['tokens']+5000)
        with self.assertRaises(ConflictError):
            self.repo.authorize_case_budget('c',{'tokens':1},reason='different',actor='operator',authorization_key='fixture-one')
        with self.assertRaises(ValidationError):
            self.repo.authorize_case_budget('c',{'tokens':-5},reason='reset',actor='operator',authorization_key='fixture-two')

    def test_serve_honors_one_worker_instead_of_chaining_a_second_worker(self):
        for i in range(3): self.repo.create_job('ontology_extension',{'extension_id':'fixture-'+str(i)})
        stop=threading.Event();lock=threading.Lock();active=0;peak=0;finished=0
        def handle(payload):
            nonlocal active,peak,finished
            with lock: active+=1;peak=max(peak,active)
            time.sleep(.04)
            with lock:
                active-=1;finished+=1
                if finished==3: stop.set()
            return {'status':'fixture-completed'}
        runner=JobRunner(self.repo,handle,max_workers=1,job_types=['ontology_extension'])
        thread=threading.Thread(target=runner.serve,kwargs={'stop_event':stop,'poll_seconds':.01})
        try:
            thread.start();thread.join(5)
            self.assertFalse(thread.is_alive());self.assertEqual(finished,3);self.assertEqual(peak,1)
        finally: stop.set();thread.join(2);runner.shutdown()

    def test_run_once_does_not_silently_start_the_remaining_queue(self):
        for i in range(3): self.repo.create_job('ontology_extension',{'extension_id':'once-'+str(i)})
        handled=[]
        runner=JobRunner(self.repo,lambda payload:(handled.append(payload['_job_id']) or {'status':'ok'}),max_workers=1,job_types=['ontology_extension'])
        try:
            runner.run_available_once();time.sleep(.1)
            self.assertEqual(len(handled),1)
            with self.repo.connect() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0],2)
        finally: runner.shutdown()


class ProviderWireTests(unittest.TestCase):
    def test_streaming_assembles_json_and_usage_without_returning_reasoning(self):
        chunks=[SimpleNamespace(id='r',model='fixture',usage=None,choices=[SimpleNamespace(delta=SimpleNamespace(content='{"ok":',reasoning_content='private'),finish_reason=None)]),
            SimpleNamespace(id='r',model='fixture',usage=None,choices=[SimpleNamespace(delta=SimpleNamespace(content='true}'),finish_reason='stop')]),
            SimpleNamespace(id='r',model='fixture',choices=[],usage=SimpleNamespace(prompt_tokens=12,completion_tokens=3))]
        calls=[]
        def create(**kwargs): calls.append(kwargs);return iter(chunks)
        client=OpenAICompatibleClient(api_key='fixture',base_url='https://example.test',model_id='fixture',model_family='fixture',stream_responses=True)
        client._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result=client.complete_json({'task':'fixture'})
        self.assertEqual(result.content,'{"ok":true}');self.assertEqual(result.output_tokens,3)
        self.assertEqual(result.finish_reason,'stop');self.assertTrue(calls[0]['stream'])
        self.assertNotIn('private',result.content)

    def test_incomplete_stream_is_an_unknown_transport_outcome(self):
        chunk=SimpleNamespace(id='r',model='fixture',usage=None,choices=[SimpleNamespace(delta=SimpleNamespace(content='{}'),finish_reason=None)])
        client=OpenAICompatibleClient(api_key='fixture',base_url='https://example.test',model_id='fixture',model_family='fixture',stream_responses=True)
        client._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:iter([chunk]))))
        with self.assertRaises(ConnectionError): client.complete_json({'task':'fixture'})

    def test_thinking_control_and_usage_diagnostics_are_explicit(self):
        calls=[]
        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'),finish_reason='length')],
                usage=SimpleNamespace(prompt_tokens=10,completion_tokens=8192,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=7000)),model='fixture',id='fixture-response')
        client=OpenAICompatibleClient(api_key='fixture',base_url='https://example.test',model_id='fixture',model_family='fixture',thinking_mode='disabled')
        client._client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        response=client.complete_json({'task':'fixture'},max_tokens=8192)
        self.assertEqual(calls[-1]['extra_body'],{'thinking':{'type':'disabled'}})
        self.assertEqual(response.finish_reason,'length');self.assertEqual(response.reasoning_tokens,7000)
        client.thinking_mode=None;client.complete_json({'task':'fixture'})
        self.assertNotIn('extra_body',calls[-1])


class LosslessDeliveryTests(unittest.TestCase):
    def proposal(self):
        from ontologyBuild.rdf_graph import parse_turtle
        from ontologyBuild.rdf_artifact import graph_hash,canonical_turtle
        source='''@prefix ex: <https://example.test/a/long/ontology/namespace#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
ex:Device a owl:Class; ex:name "设备"@zh; ex:count "001"^^xsd:integer;
 owl:equivalentClass [owl:unionOf (ex:Device ex:Peer)] .
ex:Model a owl:Ontology; owl:imports <https://must-not-fetch.invalid/ontology> .'''
        graph=parse_turtle(source);rdf=canonical_turtle(graph);digest=graph_hash(graph)
        schema={'classes':{'C':{'id':'C','label':'设备','source_iri':'https://example.test/a/long/ontology/namespace#Device','definition':'device'}}}
        return {'versions':{'payload_version':'ontology_validator_payload_v2'},'base_schema':schema,'merged_schema':copy.deepcopy(schema),
            'source_patch':{'base_rdf':rdf,'merged_rdf':rdf,'base_graph_hash':digest,'graph_hash':digest},'evidence':[]}

    def test_complete_graphs_round_trip_with_lexical_literals_and_anonymous_axioms(self):
        from ontology_review.review_payload import validator_payload
        from ontologyBuild.rdf_graph import parse_turtle
        from ontologyBuild.rdf_artifact import graph_hash
        original=self.proposal();frozen=copy.deepcopy(original);payload=validator_payload(original)
        self.assertEqual(original,frozen)
        for key,hash_key in (('base_rdf','base_graph_hash'),('merged_rdf','graph_hash')):
            self.assertEqual(graph_hash(parse_turtle(payload['source_patch'][key])),original['source_patch'][hash_key])
            self.assertLess(len(payload['source_patch'][key]),len(original['source_patch'][key]))
        self.assertTrue(payload['base_schema']['complete_schema_identity_index'])
        from ontology_review.review_payload import schema_index
        reconstructed={v['id']:v for v in payload['base_schema']['items']}
        reconstructed.update({v['id']:v for v in payload['merged_schema']['changed_items']})
        for identity in payload['merged_schema']['removed_ids']: reconstructed.pop(identity)
        self.assertEqual(reconstructed,{v['id']:v for v in schema_index(original['merged_schema'])['items']})
        self.assertIn('must-not-fetch.invalid',payload['source_patch']['base_rdf'])

    def test_frozen_v1_delivery_is_not_silently_projected_or_rebound(self):
        from ontology_review.review_payload import validator_payload
        from ontology_review.validators import prompt_for
        p=self.proposal();p['versions']={'prompt_version':'ontology_extension_validator_v1'}
        self.assertEqual(validator_payload(p),p)
        self.assertNotIn('ontology_validator_payload_v2',prompt_for(p))

    def test_corrupted_frozen_graph_hash_is_rejected(self):
        from ontology_review.review_payload import validator_payload
        p=self.proposal();p['source_patch']['base_graph_hash']='corrupted'
        with self.assertRaises(ValueError): validator_payload(p)

    def test_repeated_schema_lookup_keeps_current_independent_result(self):
        from ontology_review.tools import OntologyTools
        runtime=SimpleNamespace(state={'delta':None,'evidence':[],'checks':{},'validation':None})
        tools=OntologyTools(runtime)
        tools.check_record('existing_schema',{'status':'pass','scope':'complete','items_scanned':10,'query':'term one','matches':[]})
        runtime.state['validation']={'frozen_vote':'retain'}
        tools.check_record('existing_schema',{'status':'pass','scope':'complete','items_scanned':10,'query':'term two','matches':[{'id':'C'}]})
        self.assertEqual(runtime.state['validation'],{'frozen_vote':'retain'})
        tools.check_record('existing_schema',{'status':'pass','scope':'complete','items_scanned':11,'query':'term two','matches':[]})
        self.assertIsNone(runtime.state['validation'])


class RevisionAndViewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests(methodName='runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def test_stale_draft_is_recreated_and_independently_reviewed_again(self):
        f=self.fixture;old=f.draft();f.approve(old)
        with f.repo.transaction(immediate=True) as c:
            c.execute('UPDATE candidates SET row_version=row_version+1 WHERE candidate_id=?',(old['candidate_id'],))
        self.assertEqual(f.service.draft(old['draft_id'])['status'],'stale')
        with self.assertRaises(ConflictError): f.service.apply(old['draft_id'],old['draft_hash'])
        newer=f.draft();self.assertNotEqual(newer['draft_id'],old['draft_id'])
        f.approve(newer)
        result=f.service.apply(newer['draft_id'],newer['draft_hash'])
        self.assertEqual(result['new_revision'],2)
        self.assertEqual([len(v['client'].calls) for v in f.fixture.voters],[2,2,2])

    def test_planner_view_omits_repeated_full_context_and_exposes_exact_action_shape(self):
        f=self.fixture.fixture
        runtime=OntologyReviewRuntime(f.repo,f.root,f.voters,None,f.config)
        runtime.base=f.base;runtime.candidates={f.candidate['candidate_id']:f.candidate}
        task=f.repo.extension_status(f.extension_id)
        runtime.state={'audit_id':'view','task':task,'delta':None,'evidence':[],
            'checks':{},'history':[{'step_no':i,'tool':'search_existing_schema','status':'pass','raw_context':'X'*100000} for i in range(30)],
            'validation':None,'critical_issues':[],'full_schema_searched':False}
        runtime.state['evidence']=[{'evidence_id':'e'+str(i),'quote':'source '*1000,'anchor_valid':True} for i in range(40)]
        view=runtime.planner_input(runtime.state,AgentBudget({'total_timeout_seconds':30}))
        self.assertLess(len(json.dumps(view,ensure_ascii=False)),30000)
        self.assertEqual(len(runtime.state['evidence']),40)
        self.assertGreater(view['evidence_omitted_count'],0)
        self.assertNotIn('propose_schema_delta',view['available_tools'])
        self.assertNotIn('run_ontology_validation',view['available_tools'])
        self.assertEqual(set(view['output_schema']['required']),{'action','arguments','target_issue','rationale_summary'})
        self.assertIn('trigger_endpoints',view);self.assertIn('operation_contract',view)
        runtime.state['full_schema_searched']=True
        runtime.state['checks']['existing_schema']={'status':'pass'}
        after=runtime.planner_input(runtime.state,AgentBudget({'total_timeout_seconds':30}))
        self.assertNotIn('search_existing_schema',after['available_tools'])
        self.assertTrue(after['schema_identity_index_complete'])

    def test_frozen_ontology_recovery_preserves_two_valid_seats_and_cost_history(self):
        from ontology_review.recovery import recover_frozen_ontology
        f=self.fixture.fixture
        f.voters[1]['client']._callable=lambda payload:(_ for _ in ()).throw(RuntimeError('HTTP 400 fixture known failure'))
        f.voters[1]['ontology_delivery_version']='ontology_validator_payload_v3'
        old=f.review()
        original=f.repo.ontology_audit(old['audit_id'])
        valid={v['voter_id']:copy.deepcopy(v) for v in original['votes'] if v['response_status']=='ok'}
        f.voters[1]['client']._callable=helpers.ontology_helpers.ontology_vote
        result=recover_frozen_ontology(f.repo,f.root,f.voters,f.config,old['audit_id'],reason='explicit offline frozen recovery',
            extra_calls=2,extra_tokens=10000,extra_seconds=30)
        self.assertEqual(result['recommendation'],'approve',result)
        self.assertFalse(result['release_published'])
        self.assertEqual([len(v['client'].calls) for v in f.voters],[1,2,1])
        current=f.repo.ontology_audit(old['audit_id'])
        self.assertEqual(current['context']['recovery_history'][-1]['delivery_versions'][f.voters[1]['id']],'ontology_validator_payload_v3')
        for vote in current['votes']:
            if vote['voter_id'] in valid: self.assertEqual(vote,valid[vote['voter_id']])
        self.assertTrue(current['context']['recovery_history'])

    def test_revision_obligation_keeps_origin_through_a_same_revision_no_vote_audit(self):
        from review_agent.issue_ledger import IssueLedger
        from review_agent.agent_state import AgentState
        f=self.fixture.fixture;candidate=f.repo.get_candidate(f.candidate['candidate_id'])
        old_audit=f.source_result['audit_id'];first=AgentState(f.candidate,{},old_audit)
        issue=IssueLedger(first).open('DIRECTION_MISMATCH','fixture direction correction',True)
        f.repo.update_audit_context(old_audit,{'issue_history':[issue]})
        revised=f.repo.revise_candidate(candidate['candidate_id'],candidate['row_version'],{'triple':{'tail':'C'}})
        middle=f.repo.start_audit(candidate['candidate_id'],2,{})
        second=AgentState(f.repo.get_revision(candidate['candidate_id']),{},middle)
        IssueLedger(second).inherit(f.repo)
        f.repo.finish_incomplete_audit(middle,'failed',{'fixture':'stopped before votes'})
        f.repo.update_audit_context(middle,{'issue_history':second.issues})
        current=AgentState(f.repo.get_revision(candidate['candidate_id']),{},'fresh-audit')
        ledger=IssueLedger(current);ledger.inherit(f.repo)
        carried=next(i for i in current.issues if i['code']=='DIRECTION_MISMATCH')
        self.assertEqual(carried['opened_revision'],1);self.assertEqual(carried['inherited_from_revision'],2)
        p={'proposal_digest':'new','evidence_bundle':{'evidence_hash':'new','items':[{'evidence_id':'e'}]}}
        votes=[{'voter_id':v,'source_support':'supported','supporting_evidence_ids':['e'],'counter_evidence_ids':[],
            'checks':[{'check':name,'status':'pass','evidence_ids':['e']} for name in ('triple_support','qualifiers','relation_direction')]} for v in 'abc']
        consensus={'eligible_voter_ids':list('abc'),'source_support_consensus':{'result':'supported','support_voter_ids':list('abc')}}
        ledger.observe(p,votes,consensus,3)
        self.assertEqual(carried['status'],'resolved')


if __name__=='__main__': unittest.main()
