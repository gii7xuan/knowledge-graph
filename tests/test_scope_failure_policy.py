"""Task-wide scope decisions and bounded technical failure recovery."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from review_agent.scope_policy import task_policy,validate_policy,classification_decision,scope_route
from review_agent.batch import versions_for
from review_agent.contracts import sha256_json
from review_agent.llm_clients import LLMResponse,MockLLMClient
from review_agent.extension_eligibility import extension_eligibility
from review_agent.validators import judge_validator
from review_agent.semantic_clarification import pending,VERSION
from tests import test_review_planner as planner_fixtures
from tests import test_targeted_review_corrections as correction_fixtures


class TaskScopeTests(unittest.TestCase):
    def test_explicit_boundaries_and_no_candidate_specific_policy(self):
        policy=validate_policy(task_policy())
        for scope in ('local_definition','example','derivation'):
            result=classification_decision(scope,True,policy)
            self.assertEqual(result['status'],'exclude');self.assertTrue(result['boundary'])
        for scope in ('general','conditional'):
            self.assertEqual(classification_decision(scope,True,policy)['status'],'include')
        for scope,confirmed in [('local_definition',False),('unresolved',True),('made_up',True)]:
            self.assertEqual(classification_decision(scope,confirmed,policy)['status'],'unclear')
        for change in ({'candidate_overrides':{'some-id':'include'}},{'excluded_scopes':['unresolved']},{'categories':{}}):
            with self.assertRaises(ValueError):validate_policy({**policy,**change})

    def test_scope_configuration_changes_hash_and_preserves_source_truth(self):
        policy=task_policy();included=copy.deepcopy(policy);included['excluded_scopes'].remove('local_definition')
        self.assertNotEqual(versions_for({'validator_contract_version':'validator_response_v8','scope_policy':policy})['scope_policy_hash'],
            versions_for({'validator_contract_version':'validator_response_v8','scope_policy':included})['scope_policy_hash'])
        excluded=scope_route('supported','faithful','matched','local_definition',scope_confirmed=True,policy=policy)
        accepted=scope_route('supported','faithful','matched','local_definition',scope_confirmed=True,policy=included)
        self.assertEqual(excluded['source_support'],accepted['source_support'])
        self.assertEqual(excluded['workflow_state'],'excluded_by_scope')
        self.assertEqual(accepted['workflow_state'],'eligible_for_existing_admission')

    def test_extension_uses_the_task_policy_instead_of_default_exclusions(self):
        policy=task_policy();policy['excluded_scopes']=[]
        state=SimpleNamespace(context={'admission_policy':{'scope_policy':policy}},
            ontology_lookup={},candidate={'revision':1,'triple':{}},issues=[],pending_extension_id=None,guard={})
        state.current_validation=lambda:{'consensus':{'scope_confirmed':True,'knowledge_scope':'local_definition','expression_status':'faithful'}}
        result=extension_eligibility(state)
        self.assertTrue(result['blocked_change_kinds'])
        self.assertFalse(any('knowledge scope is excluded' in reason for reason in result['blocked_change_kinds'].values()))


class MinimumReviewTests(unittest.TestCase):
    def setUp(self):
        self.f=planner_fixtures.PlannerRuntimeTests(methodName='runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.config['planner']['failure_policy']='bounded_minimum_v1'

    def test_two_malformed_outputs_use_one_retry_then_one_fixed_committee(self):
        f=self.f;result=f.run_script(['{broken','{still broken'])
        self.assertEqual(len(f.planner.calls),2)
        self.assertEqual(result['executed_strategy'],'deterministic_fallback')
        self.assertEqual([len(c.calls) for c in f.clients],[1]*4)
        self.assertEqual([t['tool'] for t in f.audit['tool_calls']],['inspect_ontology','run_validation','finish_review'])
        self.assertEqual(result['budget']['validation_rounds'],1)
        self.assertEqual(result['failure_handling']['minimum_steps'],3)
        self.assertIn('correction_feedback',f.planner.calls[1])

    def test_truncated_even_parseable_action_is_not_executed(self):
        f=self.f;original=f.runner
        runner=original([planner_fixtures.ontology(),planner_fixtures.finish()])
        client=runner.planner_client;called=[0];complete=client.complete_json
        def truncated(payload,**kw):
            called[0]+=1;reply=complete(payload,**kw)
            return LLMResponse(reply.content,finish_reason='length')
        client.complete_json=truncated
        result=runner.audit_candidate('c',1,f.index)
        audit=f.repo.get_audit('c',result['audit_id'])
        self.assertEqual(called[0],2)
        self.assertTrue(all(a['response_status']=='invalid_action' for a in audit['planner_steps'][0]['attempts']))
        self.assertEqual(len(audit['votes']),4)

    def test_retry_can_succeed_without_switching_strategy(self):
        f=self.f;result=f.run_script(['{broken',planner_fixtures.ontology(),planner_fixtures.action('run_validation'),planner_fixtures.finish()])
        self.assertEqual(result['executed_strategy'],'llm_react')
        self.assertEqual(len(f.audit['planner_steps'][0]['attempts']),2)

    def test_full_committee_budget_is_preserved_before_format_retry(self):
        f=self.f;f.config['budget']['max_llm_calls_per_candidate']=5
        result=f.run_script(['{broken'])
        self.assertEqual(len(f.planner.calls),1)
        self.assertEqual(result['budget']['llm_calls'],5)
        self.assertEqual([len(c.calls) for c in f.clients],[1]*4)

    def test_missing_seat_is_explicitly_incomplete_after_minimum_review(self):
        f=self.f;f.clients[-1]=MockLLMClient([TimeoutError('seat outcome unknown')],model_id='model_d',model_family='d')
        f.validators[-1]['client']=f.clients[-1]
        result=f.run_script(['{broken','{broken'])
        self.assertFalse(result['independent_review_completed'])
        self.assertEqual(result['review_completion_status'],'independent_review_incomplete')
        self.assertEqual(result['decision'],'manual_review')
        self.assertEqual(len(f.clients[-1].calls),1)

    def test_planner_step_limit_finishes_minimum_obligations(self):
        f=self.f;f.config['budget']['max_planner_steps']=1
        result=f.run_script([planner_fixtures.ontology()])
        self.assertEqual(len(f.planner.calls),1)
        self.assertEqual(result['budget']['validation_rounds'],1)
        self.assertEqual(result['executed_strategy'],'deterministic_fallback')

    def test_missing_source_uses_one_search_then_incomplete_handoff(self):
        from evidence.verification import make_evidence_bundle
        f=self.f;empty=make_evidence_bundle([],[])
        with patch('review_agent.runtime.retrieve_initial',return_value=empty),patch('review_agent.tools.retrieve_more',return_value=empty):
            result=f.run_script(['{broken','{broken'])
        self.assertEqual([t['tool'] for t in f.audit['tool_calls']],['inspect_ontology','search_evidence','finish_review'])
        self.assertFalse(result['independent_review_completed'])
        self.assertEqual(result['review_completion_status'],'manual_handoff_no_evidence')
        self.assertEqual(sum(len(c.calls) for c in f.clients),0)

    def test_failure_still_respects_existing_tool_budget(self):
        f=self.f;f.config['budget']['max_tool_calls']=1
        result=f.run_script(['{broken','{broken'])
        self.assertFalse(result['independent_review_completed'])
        self.assertEqual(result['stop_reason'],'tool_budget_exhausted')
        self.assertEqual(result['budget']['tool_calls'],1)

    def test_all_ballots_with_missing_mapping_context_still_mark_minimum_review_incomplete(self):
        from tests.test_review_four_axes import FourAxisIntegrationTests
        from review_agent.runtime import AgentRuntime
        fixture=FourAxisIntegrationTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        initialize=AgentRuntime.__init__
        def bounded(runtime,orchestrator):
            initialize(runtime,orchestrator)
            runtime.planner_config['failure_policy']='bounded_minimum_v1'
        with patch.object(AgentRuntime,'__init__',bounded):
            _,result,_=fixture.run_case(invalid_planner=True,mapping_preflight=True,version='validator_response_v8')
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual(result['failure_handling']['minimum_review_status'],'incomplete')
        self.assertIn('mapping_information_incomplete',result['failure_handling']['missing_information'])
        self.assertEqual(result['workflow_state'],'review_incomplete')
        self.assertEqual(result['decision'],'manual_review')


class SemanticClarificationTests(unittest.TestCase):
    def setUp(self):
        self.f=correction_fixtures.TargetedCorrectionTests(methodName='runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def review(self,answer=None,repository=None):
        f=self.f;raw=f.f.response()
        raw['evidence_assessments'].append({'evidence_id':f.f.f.counter['evidence_id'],'role':'counter','note':'material contradiction'})
        client,voter,p=f.setup_review(raw)
        from review_agent.response_corrections import policy_for
        p['correction_policy']=policy_for({'semantic_clarification':{'enabled':True}})
        p['correction_policy_hash']=sha256_json(p['correction_policy'])
        def reply(payload):
            if payload.get('task_type')!=VERSION:return copy.deepcopy(raw)
            if isinstance(answer,Exception):raise answer
            return answer or {'explanation':'Original pass conflicts with material counterevidence; human review required.',
                'positive_support':{'status':'found','evidence_ids':[f.f.f.good['evidence_id']]},
                'counterevidence':{'status':'found','evidence_ids':[f.f.f.counter['evidence_id']]},
                'unresolved_questions':['Reconcile the protected source materials.']}
        client.callback=reply
        return judge_validator(voter,p,max_retries=99,repository=repository),raw,client,voter,p

    def test_clarification_is_separate_and_never_an_approval_vote(self):
        vote,raw,client,_,_=self.review()
        self.assertEqual(len(client.calls),2)
        self.assertEqual(vote['response_status'],'invalid_contract')
        self.assertEqual(vote['raw_response'],raw)
        self.assertNotIn('corrected_response',vote)
        self.assertEqual(vote['semantic_clarification']['status'],'explanation_recorded')
        self.assertFalse(pending(vote)['counts_as_vote'])

    def test_invalid_clarification_does_not_retry_or_rewrite_vote(self):
        vote,raw,client,_,_=self.review({'verdict':'valid'})
        self.assertEqual(len(client.calls),2)
        self.assertEqual(vote['raw_response'],raw)
        self.assertEqual(vote['semantic_clarification']['status'],'failed')
        self.assertTrue(vote['correction_terminal'])

    def test_unknown_clarification_retains_original_judgment_without_resending(self):
        vote,raw,client,_,_=self.review(TimeoutError('clarification result unknown'))
        self.assertEqual(vote['raw_response'],raw)
        self.assertNotEqual(vote['response_status'],'ok')
        self.assertEqual(len(client.calls),2)
        self.assertTrue(vote['correction_terminal'])
        self.assertFalse(vote['semantic_clarification']['counts_as_vote'])

    def test_durable_clarification_cannot_be_repeated_during_recovery(self):
        vote,raw,client,voter,p=self.review(repository=self.f.repo)
        again=judge_validator(voter,p,max_retries=99,repository=self.f.repo)
        self.assertEqual(len(client.calls),2)
        self.assertEqual(again['raw_response'],raw)
        self.assertEqual(again['response_status'],'invalid_contract')
        with self.f.repo.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM review_response_corrections').fetchone()[0],1)


if __name__=='__main__':unittest.main()
