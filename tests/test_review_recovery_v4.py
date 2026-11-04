"""Diagnosis R01-R24: behavioral recovery/revision/queue tests, no paid models."""
import copy
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from review_agent.agent_state import AgentBudget, AgentState
from review_agent.action_policy import ActionPolicy
from review_agent.repository import ConflictError, ValidationError
from review_agent.revision_patch import merge_triple_patch, expression_diagnostics
from review_agent.planner_view import summarize_vote
from review_agent.validators import judge_validator, vote_binding
from review_agent.request_cache import validation_request_key
from review_agent.request_execution import service_key
from review_agent.revision_service import RevisionService
from review_agent.extension_eligibility import extension_eligibility, validate_extension
from review_agent.llm_clients import MockLLMClient, LLMResponse
from tests.test_review_v2 import proposal, response
from tests import test_review_deduplication as dedup_helpers
from tests import test_review_planner as planner_helpers
from tests.test_review_planner import ontology, action, finish, valid_response
from tests import test_ontology_extension_agent as ontology_helpers


def request_caller(sender,database,url):
    from review_agent.repository import ReviewRepository
    from review_agent.llm_clients import OpenAICompatibleClient
    voter={'id':'a','model_id':'model-a','model_family':'family-a','request_timeout_seconds':.15,
           'http_timeout_seconds':5,'client':OpenAICompatibleClient(api_key='local-fixture',base_url=url,
            model_id='model-a',model_family='family-a',timeout_seconds=5)}
    sender.send(judge_validator(voter,proposal(),repository=ReviewRepository(database),per_call_timeout_seconds=.15))
    sender.close()


class RequestRecoveryTests(unittest.TestCase):
    setUp=dedup_helpers.ValidationDeduplicationTests.setUp
    judge=dedup_helpers.ValidationDeduplicationTests.judge

    def test_R02_late_result_commits_to_original_attempt_without_resend(self):
        release=threading.Event()
        self.client._callable=lambda payload:(release.wait(2),response())[1]
        vote=self.judge(per_call_timeout_seconds=.2,max_retries=2)
        self.assertEqual(vote['response_status'],'timeout')
        key=vote['request_key'];attempt=vote['attempt_id']
        self.assertEqual(self.repo.request_attempt(attempt)['state'],'unknown')
        release.set()
        for _ in range(100):
            if self.repo.validation_request(key)['status']=='completed': break
            time.sleep(.01)
        reused=self.judge(per_call_timeout_seconds=.1)
        self.assertTrue(reused['cached']);self.assertEqual(reused['response_status'],'ok')
        self.assertEqual(len(self.client.calls),1)
        self.assertEqual(len(self.repo.request_history(key)),1)
        self.assertTrue(self.repo.request_attempt(attempt)['result']['late_response'])

    def test_R03_unreconciled_unknown_remains_fenced(self):
        key=validation_request_key(self.voter,proposal())
        self.repo.claim_validation_request(key,'lost-owner')
        vote=self.judge(per_call_timeout_seconds=.02)
        self.assertTrue(vote['outcome_unknown']);self.assertEqual(len(self.client.calls),0)

    def test_R04_historical_result_cannot_become_new_revision_vote(self):
        p=proposal()
        self.repo.create_run({'run_id':'run'})
        self.repo.add_candidate({**p['candidate'],'run_id':'run','document_id':'doc',
                                 'document_version':'doc-v1','ontology_version':'ontology-v1'})
        self.repo.start_audit('c',1,{'audit_id':p['audit_id']})
        failed={**vote_binding(self.voter,p),'response_status':'timeout','verdict':None}
        self.repo.append_round(p['audit_id'],p,[failed],{},{})
        candidate=self.repo.get_candidate('c')
        self.repo.revise_candidate('c',candidate['row_version'],{'triple':{'tail':'another quantity'}})
        with self.assertRaises(ConflictError):
            self.repo.record_recovered_vote(p['audit_id'],p,{**response(),**vote_binding(self.voter,p),'response_status':'ok'})
        self.assertEqual(self.repo.get_candidate('c')['revision'],2)

    def test_real_request_process_survives_candidate_process_exit(self):
        import multiprocessing
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        release=threading.Event();calls=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']));calls.append(1)
                release.wait(5)
                data=json.dumps({'id':'late-local-response','object':'chat.completion','created':0,'model':'model-a',
                    'choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(response())},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
                self.end_headers();self.wfile.write(data)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown);self.addCleanup(release.set)
        ctx=multiprocessing.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False)
        worker=ctx.Process(target=request_caller,args=(sender,str(self.repo.database_path),f'http://127.0.0.1:{server.server_port}/v1'))
        worker.start();sender.close()
        try:
            self.assertTrue(receiver.poll(10));vote=receiver.recv()
            self.assertEqual(vote['response_status'],'timeout')
            worker.join(3)
            if worker.is_alive(): worker.terminate();worker.join(3)
            self.assertFalse(worker.is_alive())
            release.set()
            for _ in range(200):
                if self.repo.validation_request(vote['request_key'])['status']=='completed': break
                time.sleep(.05)
            self.assertEqual(self.repo.validation_request(vote['request_key'])['status'],'completed')
            self.assertEqual(len(calls),1)
        finally:
            release.set()
            if worker.is_alive(): worker.terminate();worker.join(3)
            receiver.close();worker.close()

    def test_R23_billing_pause_blocks_services_and_related_queue(self):
        self.client._callable=lambda _: (_ for _ in ()).throw(RuntimeError('HTTP 402 insufficient balance'))
        job=self.repo.create_job('ontology_extension',{'extension_id':'x'})
        self.judge(max_retries=3)
        self.assertIsNotNone(self.repo.service_pause(service_key(self.voter)))
        self.assertEqual(self.repo.get_job(job['job_id'])['status'],'paused')
        self.judge();self.assertEqual(len(self.client.calls),1)

    def test_attempt_history_preserves_contract_correction(self):
        self.client._callable=None;self.client._queue=[{},response()]
        result=self.judge(max_retries=1,per_call_timeout_seconds=1)
        self.assertEqual(result['response_status'],'ok')
        history=self.repo.request_history(result['request_key'])
        self.assertEqual([r['response_status'] for r in history],['invalid_contract','ok'])
        self.assertEqual(history[1]['recovery_of'],history[0]['attempt_id'])

    def test_unknown_request_cannot_escape_by_credentials_namespace_or_output_limit(self):
        self.client._callable=lambda _: (_ for _ in ()).throw(TimeoutError('provider outcome unknown'))
        original=self.judge(per_call_timeout_seconds=1)
        self.assertTrue(original['outcome_unknown'])
        self.client.api_key='different-fixture-credential'
        changed={**proposal(),'request_cache_namespace':'changed-namespace'}
        blocked=self.judge(changed,voter={**self.voter,'max_output_tokens':8192})
        self.assertEqual(blocked['error_code'],'RELATED_REQUEST_OUTCOME_UNKNOWN')
        self.assertEqual(len(self.client.calls),1)
        self.assertEqual(blocked['actual_calls'],0)

    def test_shared_ontology_request_identity_blocks_equivalent_unknown_retry(self):
        from review_agent.request_execution import judge_typed
        calls=[]
        binding={'task_type':'ontology_extension','task_id':'ext','model_id':'model-a','model_family':'family-a','model_identity':'model-a'}
        def invoke(_):
            calls.append(1)
            return {**binding,'response_status':'timeout','outcome_unknown':True,'retryable':False}
        kwargs={'task_type':'ontology_review','binding':binding,'invoke':invoke,'validate_cached':lambda vote:[],
                'prompt_hash':'frozen-test-prompt','timeout':1}
        first=judge_typed(self.voter,{'task_id':'ext','object_revision':1},self.repo,**kwargs)
        self.assertTrue(first['outcome_unknown'])
        self.client.api_key='different-fixture-credential'
        blocked=judge_typed({**self.voter,'max_output_tokens':8192},
            {'task_id':'ext','object_revision':1,'request_cache_namespace':'new'},self.repo,**kwargs)
        self.assertEqual(blocked['error_code'],'RELATED_REQUEST_OUTCOME_UNKNOWN')
        self.assertEqual(len(calls),1)

    def test_deadline_before_dispatch_is_known_not_sent_and_costs_no_call(self):
        original=self.repo.begin_request_attempt
        def delayed(*args,**kwargs):
            time.sleep(.06)
            return original(*args,**kwargs)
        with patch.object(self.repo,'begin_request_attempt',side_effect=delayed):
            vote=self.judge(per_call_timeout_seconds=.03)
        self.assertTrue(vote['not_sent']);self.assertEqual(vote['actual_calls'],0)
        self.assertEqual(len(self.client.calls),0)

    def test_R18_old_lease_cannot_heartbeat_or_complete_after_reclaim(self):
        job=self.repo.create_job('ontology_extension',{'extension_id':'x'})
        old=self.repo.claim_job('owner-a',job_types=['ontology_extension'])
        with self.repo.transaction(immediate=True) as c:
            c.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE job_id=?",(job['job_id'],))
        new=self.repo.claim_job('owner-b',job_types=['ontology_extension'])
        self.assertNotEqual(old['lease_token'],new['lease_token'])
        with self.assertRaises(ConflictError):
            self.repo.heartbeat_job(job['job_id'],{},owner='owner-a',lease_token=old['lease_token'])
        with self.assertRaises(ConflictError):
            self.repo.complete_job(job['job_id'],{},owner='owner-a',lease_token=old['lease_token'])
        from review_agent.repository import ReviewRepository
        fenced=ReviewRepository(self.repo.database_path)
        fenced.job_lease_binding={'job_id':job['job_id'],'owner':'owner-a','lease_token':old['lease_token']}
        with self.assertRaises(ConflictError):
            with fenced.transaction(immediate=True): pass
        self.repo.complete_job(job['job_id'],{},owner='owner-b',lease_token=new['lease_token'])


class RuntimeRecoveryTests(unittest.TestCase):
    setUp=planner_helpers.PlannerRuntimeTests.setUp
    runner=planner_helpers.PlannerRuntimeTests.runner
    run_script=planner_helpers.PlannerRuntimeTests.run_script

    def test_R01_only_technical_failure_seat_is_recovered(self):
        calls=0
        def flaky(payload):
            nonlocal calls
            calls+=1
            if calls==1: raise RuntimeError('HTTP 500 temporary error')
            return valid_response(payload)
        self.clients[-1]._callable=flaky
        def plan(payload):
            if not payload['ontology_checked']: return ontology()
            if not payload['current_validation']: return action('run_validation')
            self.assertNotIn('run_validation',payload['available_tools'])
            if payload['validation_recovery']['recoverable_voter_ids']: return action('recover_validation')
            return finish()
        self.run_script(plan)
        self.assertEqual([len(c.calls) for c in self.clients],[1,1,1,2])
        self.assertTrue(self.audit['context']['independent_review_completed'])
        self.assertEqual(len(self.audit['votes']),4)

    def test_R06_uncertain_and_opposing_valid_responses_are_immutable(self):
        self.clients[-1]._callable=lambda p:valid_response(p,invalid=True)
        def plan(payload):
            if not payload['ontology_checked']: return ontology()
            if not payload['current_validation']: return action('run_validation')
            self.assertNotIn('recover_validation',payload['available_tools'])
            return finish()
        self.run_script(plan)
        existing=self.audit['votes'][-1]
        with self.assertRaises(ConflictError): self.repo.record_recovered_vote(self.audit['audit_id'],self.audit['rounds'][0],existing)
        self.assertEqual([len(c.calls) for c in self.clients],[1]*4)

    def test_R05_same_frozen_result_hides_ordinary_validation(self):
        observed=[]
        def plan(payload):
            if not payload['ontology_checked']: return ontology()
            if not payload['current_validation']: return action('run_validation')
            observed.append(payload['available_tools']);return finish()
        self.run_script(plan)
        self.assertNotIn('run_validation',observed[0])

    def test_R24_shadow_keeps_manual_admission_after_complete_review(self):
        result=self.run_script([ontology(),action('run_validation'),finish()])
        self.assertTrue(self.audit['context']['independent_review_completed'])
        self.assertEqual(result['decision'],'manual_review')

    def test_R22_unconfigured_seat_does_not_make_partial_review_healthy(self):
        self.validators=self.validators[:3]
        runner=self.runner([ontology(),action('run_validation'),finish()])
        result=runner.audit_candidate('c',1,self.index)
        self.assertFalse(result['independent_review_completed'])
        from review_agent.batch import versions_for
        self.assertFalse(self.repo.has_healthy_audit('c',1,versions_for(self.config),runner.committee_members()))

    def test_frozen_recovery_extends_authorized_deadline_without_resetting_usage(self):
        def flaky(payload):
            if len(self.clients[-1].calls)==1: raise RuntimeError('HTTP 500 temporary error')
            return valid_response(payload)
        self.clients[-1]._callable=flaky
        orchestrator=self.runner([ontology(),action('run_validation'),finish()])
        result=orchestrator.audit_candidate('c',1,self.index)
        before=self.repo.get_audit('c',result['audit_id'])
        original_usage=before['context']['agent_budget']['usage']['llm_calls']
        self.repo.update_audit_context(result['audit_id'],{'execution_deadline_epoch':time.time()-10})
        from review_agent.recovery_service import recover_frozen_audit
        recovered=recover_frozen_audit(self.repo,orchestrator,'c',result['audit_id'],reason='offline explicit recovery budget',
            extra_calls=1,extra_tokens=5000,extra_seconds=20)
        self.assertTrue(recovered['committed'],recovered)
        self.assertTrue(recovered['independent_review_completed'])
        self.assertEqual([len(c.calls) for c in self.clients],[1,1,1,2])
        after=self.repo.get_audit('c',result['audit_id'])
        self.assertEqual(after['context']['agent_budget']['usage']['llm_calls'],original_usage+1)

    def test_v4_wire_contract_uses_one_schema_status_and_preserves_consensus(self):
        self.config['validator_contract_version']='validator_response_v4'
        def respond(payload):
            value=valid_response(payload)
            value['schema_assessment']={'status':value.pop('schema_status'),'issue':value.pop('schema_issue'),
                'evidence_ids':value['supporting_evidence_ids'],'note':'fixture schema check'}
            value['checks']=[c for c in value['checks'] if c['check']!='ontology']
            return value
        for client in self.clients: client._callable=respond
        self.run_script([ontology(),action('run_validation'),finish()])
        self.assertTrue(self.audit['context']['independent_review_completed'])
        self.assertTrue(all(v['response_contract_version']=='validator_response_v4' for v in self.audit['votes']))


class ObservationAndPatchTests(unittest.TestCase):
    def triple(self):
        return {'head':'A','surface_relation':'measures','tail':'temperature','tail_kind':'entity',
            'qualifiers':{'polarity':'unknown','condition':'after calibration','time_scope':None,
                          'applicability':None,'modality':'can','quantifier':None},
            'qualifier_status':{'polarity':'unknown','condition':'extracted','time_scope':'not_stated',
                                'applicability':'not_stated','modality':'extracted','quantifier':'not_stated'}}

    def test_R07_actionable_judge_information_is_bounded_and_retained(self):
        vote={'checks':[{'check':'qualifiers','status':'unknown','evidence_ids':['ev'],'note':'x'*1000}],
            'suggested_revision':{'changes':{'qualifiers':{'condition':'after calibration'}}},
            'requested_context':{'scope':'same_section','query':'calibration condition','limit':2},
            'schema_change_hint':{'term_id':'R'},'outcome_unknown':True,'error_code':'HARD_REQUEST_TIMEOUT'}
        summary=summarize_vote(vote)
        self.assertEqual(len(summary['checks'][0]['note']),240)
        self.assertEqual(summary['requested_context']['query'],'calibration condition')
        self.assertIn('changes',summary['suggested_revision']);self.assertTrue(summary['failure']['outcome_unknown'])

    def test_R08_unknown_is_not_defaulted_to_positive(self):
        candidate={'contract_version':'extraction_candidate_v4','triple':self.triple()}
        self.assertFalse(expression_diagnostics(candidate)['expression_complete'])
        self.assertEqual(candidate['triple']['qualifiers']['polarity'],'unknown')

    def test_R09_local_polarity_patch_preserves_condition_and_modality(self):
        original=self.triple()
        patched=merge_triple_patch(original,{'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}},strict=True)
        self.assertEqual(patched['qualifiers']['condition'],'after calibration')
        self.assertEqual(patched['qualifiers']['modality'],'can')
        self.assertEqual(len(patched['qualifier_status']),6)

    def test_R10_invalid_status_and_inconsistent_value_are_rejected(self):
        with self.assertRaises(ValidationError): merge_triple_patch(self.triple(),{'qualifier_status':{'condition':'guessed'}},strict=True)
        with self.assertRaises(ValidationError): merge_triple_patch(self.triple(),{'qualifier_status':{'condition':'not_stated'}},strict=True)
        self.assertIs(merge_triple_patch(self.triple(),{'value':True})['value'],True)
        self.assertEqual(merge_triple_patch(self.triple(),{'value':2.5})['value'],2.5)

    def state(self):
        state=AgentState({'candidate_id':'c','revision':1,'triple':{'tail_kind':'entity'}},{},'a')
        state.ontology_checked=True;state.guard={'relation_known':True}
        state.ontology_lookup={'scope':'full_frozen_ontology','candidate_revision':1,'matched_ids':['R_existing'],
                              'endpoint_class_queries':{'head':'NewType'},'endpoint_class_matches':{}}
        state.validations[state.validation_key()]={'consensus':{'eligible_voter_ids':['a'],'source_support_consensus':{'result':'supported'}},
            'votes':[{'voter_id':'a','schema_issue':'not_covered','schema_change_hint':{'alias':'measures'}}]}
        return state

    def test_R13_existing_mapping_disallows_new_relation(self):
        self.assertNotIn('add_object_property',extension_eligibility(self.state())['allowed_change_kinds'])

    def test_R14_alias_uses_existing_target_rule(self):
        state=self.state()
        validate_extension(state,'add_alias',{'term_id':'R_existing','alias':'measures'})
        with self.assertRaises(ValueError): validate_extension(state,'add_alias',{'term_id':'invented','alias':'measures'})

    def test_R15_named_missing_endpoint_class_is_exposed(self):
        result=extension_eligibility(self.state())
        self.assertEqual(result['missing_endpoint_classes'],{'head':'NewType'})
        self.assertIn('add_class',result['allowed_change_kinds'])

    def test_R21_single_status_v4_and_strict_historical_v3(self):
        from review_agent import response_contract_v4 as v4
        from review_agent.response_contract import validate_response
        raw=response();raw['schema_assessment']={'status':raw.pop('schema_status'),'issue':raw.pop('schema_issue'),'evidence_ids':['ev'],'note':'checked'}
        raw['checks']=[c for c in raw['checks'] if c['check']!='ontology']
        self.assertEqual(v4.validate_response(raw),[])
        normalized=v4.normalize(raw)
        self.assertEqual(normalized['schema_status'],normalized['checks'][-1]['status'])
        old=response();old['checks'][3]['status']='unknown'
        self.assertTrue(validate_response(old))


class RevisionAndConsumerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=ontology_helpers.OntologyExtensionTests(methodName='runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.repo=self.fixture.repo;self.service=RevisionService(self.repo)

    def draft(self):
        current=self.repo.get_candidate(self.fixture.candidate['candidate_id'])
        candidate=self.repo.get_revision(current['candidate_id'])
        bundle=self.repo.get_audit(current['candidate_id'],self.fixture.source_result['audit_id'])['rounds'][0]['evidence_bundle']
        return self.service.propose(candidate,current['row_version'],{'qualifiers':{'modality':'can'}},
            [bundle['items'][0]['evidence_id']],'fixture correction',bundle,self.fixture.index)

    def approve(self,draft):
        def vote(payload):
            eid=payload['evidence_ids'][0]
            return {'decision':'approve','field_reviews':[{'field':f,'status':'supported','evidence_ids':[eid],'note':'fixture'} for f in payload['changed_fields']],
                'supporting_evidence_ids':[eid],'counter_evidence_ids':[],'counterevidence_reviewed_ids':payload['protected_evidence_ids'],'rationale_summary':'fixture'}
        voters=self.fixture.voters
        for voter in voters: voter['client']._callable=vote
        from review_agent.consensus import Committee
        return self.service.review(draft['draft_id'],voters,Committee(['a','b','c']),AgentBudget({'total_timeout_seconds':30}))

    def test_reviewed_revision_applies_atomically_and_queues_fresh_review(self):
        draft=self.draft();self.assertEqual(self.approve(draft)['status'],'approved')
        result=self.service.apply(draft['draft_id'],draft['draft_hash'])
        current=self.repo.get_candidate(draft['candidate_id'])
        self.assertEqual(current['revision'],2)
        self.assertEqual(current['triple']['qualifiers']['polarity'],'positive')
        self.assertIsNone(self.repo.latest_decision(draft['candidate_id']))
        self.assertEqual(self.repo.get_job(result['reaudit_job_id'])['status'],'queued')
        self.assertFalse(result['old_votes_reusable'])

    def test_R11_stale_approved_draft_fails_CAS(self):
        draft=self.draft();self.approve(draft)
        current=self.repo.get_candidate(draft['candidate_id'])
        self.repo.revise_candidate(draft['candidate_id'],current['row_version'],{'triple':{'tail':'C'}})
        with self.assertRaises(ConflictError): self.service.apply(draft['draft_id'],draft['draft_hash'])

    def test_R12_counterevidence_is_inherited_by_new_revision(self):
        candidate=self.repo.get_candidate(self.fixture.candidate['candidate_id'])
        audit=self.repo.get_audit(candidate['candidate_id'],self.fixture.source_result['audit_id'])
        eid=audit['rounds'][0]['evidence_bundle']['items'][0]['evidence_id']
        self.repo.update_audit_context(audit['audit_id'],{'issue_history':[{'issue_id':'historical_conflict','code':'source_conflict',
            'status':'open','critical':True,'evidence_ids':[eid],'requires_revision':False}]})
        updated=self.repo.revise_candidate(candidate['candidate_id'],candidate['row_version'],{'triple':{'tail':'C'}})
        from review_agent.issue_ledger import IssueLedger
        state=AgentState(self.repo.get_revision(updated['candidate_id']),{},'new-audit')
        IssueLedger(state).inherit(self.repo)
        self.assertIn(eid,state.protected_ids)
        self.assertTrue(any(i['code']=='source_conflict' and i['status']=='open' for i in state.issues))

    def test_R08_unknown_polarity_can_only_change_through_reviewed_revision(self):
        current=self.repo.get_candidate(self.fixture.candidate['candidate_id'])
        changed=self.repo.revise_candidate(current['candidate_id'],current['row_version'],{'triple':{
            'qualifiers':{'polarity':'unknown'},'qualifier_status':{'polarity':'unknown'}}})
        base=self.repo.get_revision(changed['candidate_id'])
        bundle=self.repo.get_audit(current['candidate_id'],self.fixture.source_result['audit_id'])['rounds'][0]['evidence_bundle']
        draft=self.service.propose(base,changed['row_version'],{'qualifiers':{'polarity':'positive'},
            'qualifier_status':{'polarity':'extracted'}},[bundle['items'][0]['evidence_id']],'affirmative source definition',bundle,self.fixture.index)
        self.assertEqual(self.repo.get_candidate(current['candidate_id'])['triple']['qualifiers']['polarity'],'unknown')
        self.approve(draft);self.service.apply(draft['draft_id'],draft['draft_hash'])
        final=self.repo.get_candidate(current['candidate_id'])
        self.assertTrue(expression_diagnostics(final)['expression_complete'])
        self.assertIn('qualifier_status.polarity',final['revision_provenance']['field_evidence'])

    def test_R16_subscription_and_job_are_committed_together_and_repair_is_idempotent(self):
        task=self.repo.extension_status(self.fixture.extension_id)
        self.assertEqual(len(task['queue_jobs']),1)
        with self.repo.transaction(immediate=True) as c:
            c.execute('DELETE FROM jobs WHERE job_id=?',(task['queue_jobs'][0]['job_id'],))
        self.assertEqual(len(self.repo.repair_extension_jobs()),1)
        self.assertEqual(self.repo.repair_extension_jobs(),[])
        self.assertEqual(len(self.repo.extension_status(self.fixture.extension_id)['subscriptions']),1)

    def test_R16_enqueue_failure_rolls_back_subscription_and_case_charge(self):
        candidate=copy.deepcopy(self.fixture.candidate)
        candidate['candidate_id']='atomic_subscription_fixture'
        self.repo.add_candidate(candidate)
        current=self.repo.get_candidate(candidate['candidate_id'])
        proposal=self.repo.get_audit(self.fixture.candidate['candidate_id'],self.fixture.source_result['audit_id'])['rounds'][0]
        lookup=self.repo.extension_status(self.fixture.extension_id)['existing_schema_search']
        with patch.object(self.repo,'enqueue_job_in_transaction',side_effect=RuntimeError('simulated enqueue crash')):
            with self.assertRaises(RuntimeError):
                self.repo.create_extension_request(candidate,expected_row_version=current['row_version'],proposal=proposal,
                    change_kind='add_object_property',description='atomic subscription test',proposed_changes={'label':'regulates'},
                    evidence_ids=[proposal['evidence_bundle']['items'][0]['evidence_id']],full_lookup=lookup)
        current=self.repo.get_candidate(candidate['candidate_id'])
        self.assertIsNone(current['pending_extension_id'])
        with self.repo.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM ontology_extension_subscriptions WHERE candidate_id=?',(candidate['candidate_id'],)).fetchone()[0],0)

    def test_planner_revision_flow_submits_revision_without_old_candidate_revote(self):
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.ontology_guard import OntologyGuard,load_snapshot
        def review(payload):
            self.assertEqual(payload['task_type'],'candidate_revision_review')
            eid=payload['evidence_ids'][0]
            return {'decision':'approve','field_reviews':[{'field':f,'status':'supported','evidence_ids':[eid],'note':'fixture correction'} for f in payload['changed_fields']],
                'supporting_evidence_ids':[eid],'counter_evidence_ids':[],'counterevidence_reviewed_ids':payload['protected_evidence_ids'],'rationale_summary':'fixture'}
        voters=self.fixture.voters
        for voter in voters: voter['client']._callable=review
        def plan(payload):
            if not payload['ontology_checked']: return action('inspect_ontology',relation_id=None,issue='frozen lookup')
            if not payload['revision_drafts']: return action('propose_revision',changes={'qualifiers':{'modality':'can'}},
                evidence_ids=payload['selected_evidence_ids'][:1],reason='fixture source correction')
            draft=payload['revision_drafts'][0]
            if draft['status']=='proposed': return action('review_revision',draft_id=draft['draft_id'])
            return action('apply_reviewed_revision',draft_id=draft['draft_id'],draft_hash=draft['draft_hash'])
        config={**self.fixture.config,'validator_contract_version':'validator_response_v4'}
        agent=ReviewOrchestrator(self.repo,OntologyGuard(load_snapshot(self.fixture.base.snapshot_path)),voters,
            config=config,planner_client=MockLLMClient(plan))
        result=agent.audit_candidate(self.fixture.candidate['candidate_id'],1,self.fixture.index,context={'revision_only':True})
        self.assertEqual(result.get('status'),'revision_applied',result)
        self.assertEqual(self.repo.get_audit(self.fixture.candidate['candidate_id'],result['audit_id'])['status'],'superseded')
        self.assertEqual([len(v['client'].calls) for v in voters],[1,1,1])
        self.assertEqual(self.repo.get_candidate(self.fixture.candidate['candidate_id'])['revision'],2)

    def test_R17_persistent_consumer_executes_ontology_agent(self):
        from review_agent.jobs import JobRunner
        stop=threading.Event()
        def handle(payload):
            self.assertEqual(payload['_job_type'],'ontology_extension')
            result=self.fixture.review(audit_id=payload['audit_id'])
            stop.set();return result
        runner=JobRunner(self.repo,handle,max_workers=1,job_types=['ontology_extension'])
        try: runner.serve(stop_event=stop,poll_seconds=.01)
        finally: runner.shutdown()
        self.assertTrue(self.repo.extension_status(self.fixture.extension_id)['ontology_review_executed'])

    def test_R19_review_does_not_publish_when_disabled(self):
        result=self.fixture.review()
        self.assertEqual(result['recommendation'],'approve')
        self.assertFalse(result['release_published'])
        self.assertFalse(result['auto_publish_enabled'])

    def test_R20_publication_does_not_rebind_a_revoked_subscription(self):
        from ontology_review.publisher import OntologyPublisher,current_release_id
        self.fixture.review()
        candidate_id=self.fixture.candidate['candidate_id']
        current=self.repo.get_candidate(candidate_id)
        historical=self.repo.get_audit(candidate_id,self.fixture.source_result['audit_id'])
        original_publish=ontology_helpers.ReleaseCatalog.publish
        revised=[]
        def release_then_revise(catalog,*args,**kwargs):
            release=original_publish(catalog,*args,**kwargs)
            revised.append(self.repo.revise_candidate(candidate_id,current['row_version'],{'triple':{'tail':'C'}}))
            return release
        request=self.fixture.publication_request()
        with patch.object(ontology_helpers.ReleaseCatalog,'publish',new=release_then_revise):
            published=OntologyPublisher(self.repo,self.fixture.root,self.fixture.config).publish(
                self.fixture.extension_id,request)
        self.assertEqual(self.repo.extension_status(self.fixture.extension_id)['subscriptions'][0]['status'],'revoked')
        self.assertNotEqual(current_release_id(self.fixture.root),self.fixture.base.manifest['release_id'])
        self.assertEqual(published['rebound'],[])
        self.assertEqual(published['reaudit_jobs'],[])
        self.assertFalse(published['old_votes_reused'])
        self.assertEqual(self.repo.get_candidate(candidate_id),revised[0])
        self.assertIsNone(self.repo.latest_decision(candidate_id))
        self.assertEqual(self.repo.get_audit(candidate_id,historical['audit_id']),historical)


if __name__=='__main__': unittest.main()
