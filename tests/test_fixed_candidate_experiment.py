import copy
import json
import unittest
from evaluation.adapters.baselines import simple_majority
from evaluation.metrics import fixed_candidate_metrics
from evaluation.split_integrity import verify_split
from evaluation.experiment import run_experiment, configure
from review_agent.agent_state import AgentState
from review_agent.fixed_candidate_workflow import next_action
from review_agent.action_policy import ActionPolicy
from review_agent.planner_contracts import ActionError
from tests.test_phase_f_completion import fixture, mock_services


class FixedCandidateTests(unittest.TestCase):
    def test_missing_and_abstaining_seats_do_not_turn_one_vote_into_a_majority(self):
        ok=lambda verdict:{'response_status':'ok','verdict':verdict}
        failed={'response_status':'timeout'}
        for others in ([failed,failed],[ok('uncertain'),failed],[ok('invalid'),failed]):
            self.assertEqual(simple_majority([ok('valid'),*others])['verdict'],'unresolved')
        self.assertEqual(simple_majority([ok('invalid'),ok('invalid'),failed])['verdict'],'invalid')
        self.assertEqual(simple_majority([])['verdict'],'unresolved')

    def test_metrics_keep_unknown_truth_and_failed_predictions_in_the_population(self):
        rows=[{'predicted_decision':'accept','label':1},
              {'predicted_decision':'accept','label':0},
              {'predicted_decision':'accept','label':None},
              {'predicted_decision':'reject','label':0},
              {'predicted_decision':'defer','label':1},
              {'predicted_decision':'accept','label':1,'status':'interrupted'},
              {'predicted_decision':'defer','stop_reason':'token_budget_exhausted'},
              {'prediction_missing':True}]
        r=fixed_candidate_metrics(rows)
        self.assertEqual(r['outcomes'],{'accept':3,'reject':1,'manual':4,'pending':0})
        self.assertEqual(r['technical_failure_handoff_count'],3)
        self.assertEqual(r['manual_rate']['value'],.5)
        self.assertEqual(r['admission_precision']['value'],.5)
        self.assertEqual(r['correct_candidate_retention']['value'],1/3)
        self.assertEqual(r['false_acceptance_rate']['value'],.5)
        self.assertEqual(r['automatic_decision_coverage']['value'],.5)
        self.assertEqual(r['unknown_truth_accept_count'],1)
        empty=fixed_candidate_metrics([])
        self.assertIsNone(empty['admission_precision']['value'])

    def test_disjoint_excerpts_of_one_book_cannot_cross_development_and_test(self):
        rows=[{'candidate_id':'a','revision':1,'document_id':'excerpt_a','document_version':'va','source_group_id':'book'},
              {'candidate_id':'b','revision':1,'document_id':'excerpt_b','document_version':'vb','source_group_id':'book'}]
        with self.assertRaisesRegex(ValueError,'cross evaluation splits'):
            verify_split(rows,{('a',1):'development',('b',1):'independent_test'})

    def test_workflow_uses_ordered_supplements_then_one_fixed_dispute(self):
        s=AgentState(candidate={'triple':{'head':'A','surface_relation':'rel','tail':'B'}},context={},audit_id='x')
        self.assertEqual(next_action(s)['action'],'inspect_ontology')
        s.ontology_checked=True;s.bundle={'items':[{'evidence_id':'e'}]}
        self.assertEqual(next_action(s)['action'],'run_validation')
        s.context.update(dispute_baseline={'proposal_digest':'p'},dispute_report={'disputes':[
            {'dispute_id':'b','status':'open'},{'dispute_id':'a','status':'open'}]})
        s.gaps={'need_more_evidence':True}
        self.assertEqual(next_action(s)['arguments']['scope'],'current_chunk')
        s.observations.append({'tool':'search_evidence'})
        self.assertEqual(next_action(s)['arguments']['scope'],'same_section')
        s.observations.append({'tool':'inspect_context'})
        self.assertEqual(next_action(s)['arguments'],{'dispute_id':'a'})
        s.observations.append({'tool':'review_dispute'})
        self.assertEqual(next_action(s)['action'],'finish_review')

    def test_agent_and_fixed_workflow_share_server_enforced_action_limits(self):
        s=AgentState(candidate={},context={},audit_id='x',observations=[{'tool':'search_evidence'},{'tool':'inspect_context'}])
        p=ActionPolicy({'allowed_tools':['search_evidence'],'fixed_candidate_limits':True})
        a={'action':'search_evidence','arguments':{'scope':'same_section','query':'A','purpose':'check_support','limit':1},
           'target_issue':'source','rationale_summary':'Find supporting context'}
        with self.assertRaisesRegex(ActionError,'two evidence supplements'):p.validate_request(a,s)

    def test_all_keep_executes_with_zero_requests_even_when_ontology_has_a_gap(self):
        h=fixture(self)
        spec={'split':{'train_document_ids':[],'test_document_ids':[h.index['document_id']]},
              'groups':[{'id':'b0','method':'all_keep','ontology_mode':'fixed'}]}
        out=h.folder/'all_keep'
        result=run_experiment(spec,[h.candidate],[h.index],h.base.path,out,h.config,services_factory=mock_services(h))
        self.assertEqual(result['groups'][0]['usage']['total']['calls'],0)
        prediction=json.loads((out/'b0/predictions.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(prediction['predicted_decision'],'accept')

    def test_fixed_workflow_is_explicit_and_not_a_planner_failure_fallback(self):
        group={'id':'b3','method':'fixed_workflow','workflow_policy':'fixed_candidate_workflow_v1'}
        limits={'llm_calls':12,'tokens':120000,'tool_calls':12,'extension_requests':1,'rebinds':1,'seconds':540}
        c=configure({},group,{'mode':'disabled'},limits)
        self.assertTrue(c['planner']['enabled'])
        self.assertEqual(c['planner']['fixed_workflow_policy'],'fixed_candidate_workflow_v1')

    def test_frozen_single_voter_is_not_silently_replaced_by_first_slot(self):
        h=fixture(self)
        calls=[];factory=mock_services(h)
        def services(c):
            result=factory(c)
            for v in result['validators']:
                original=v['client'].complete_json;seat=v['id']
                def wrapped(payload,_original=original,_seat=seat,**kw):
                    calls.append(_seat);return _original(payload,**kw)
                v['client'].complete_json=wrapped
            return result
        spec={'split':{'train_document_ids':[],'test_document_ids':[h.index['document_id']]},
              'groups':[{'id':'b1','method':'single','single_voter_id':'c','ontology_mode':'fixed'}]}
        run_experiment(spec,[h.candidate],[h.index],h.base.path,h.folder/'single_c',h.config,services_factory=services)
        self.assertEqual(calls,['c'])

    def test_fixed_workflow_runs_real_shared_runtime_without_planner_requests(self):
        from tests.test_review_four_axes import FourAxisIntegrationTests
        from evaluation.formal_preparation import configuration_overrides,merge_overrides
        from review_agent.orchestrator import ReviewOrchestrator
        f=FourAxisIntegrationTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        c,_,owner=f.run_case(version='validator_response_v8',model_mapping='matched',
                             assigned_mapping=True,dispute_review=True,weighting={'mode':'dynamic'})
        config=merge_overrides(owner.config,configuration_overrides())
        config['planner']['fixed_workflow_policy']='fixed_candidate_workflow_v1'
        before=sum(len(v['client'].calls) for v in owner.validators)
        workflow=ReviewOrchestrator(owner.repository,owner.ontology_guard,owner.validators,config=config)
        result=workflow.audit_candidate(c['candidate_id'],1,f.fixture.index)
        audit=owner.repository.get_audit(c['candidate_id'],result['audit_id'])
        self.assertEqual(audit['context']['executed_strategy'],'fixed_workflow')
        self.assertNotIn('fallback_reason',audit['context'])
        self.assertTrue(audit['context']['resolution_snapshots_enabled'])
        self.assertEqual(sum(len(v['client'].calls) for v in owner.validators)-before,3)
        self.assertEqual(result['research_operation'],'accept',result)

    def test_study_request_cap_is_shared_by_different_method_meters(self):
        import tempfile
        from pathlib import Path
        from evaluation.request_meter import RequestMeter,StudyBudget
        from review_agent.llm_clients import MockLLMClient,LLMResponse
        study=StudyBudget({'llm_calls':2,'known_tokens':100,'seconds':60})
        limits={'llm_calls':10,'tokens':100,'seconds':60}
        client=MockLLMClient(lambda _:LLMResponse({},input_tokens=2,output_tokens=3))
        with tempfile.TemporaryDirectory() as directory:
            a=RequestMeter(Path(directory)/'a.jsonl',limits,study)
            b=RequestMeter(Path(directory)/'b.jsonl',limits,study)
            a.wrap(client,'review',['a']).complete_json({})
            b.wrap(client,'review',['b']).complete_json({})
            with self.assertRaisesRegex(RuntimeError,'study_call_budget_exhausted'):
                b.wrap(client,'review',['c']).complete_json({})
        self.assertEqual(len(client.calls),2)
        self.assertEqual(study.snapshot()['known_tokens'],10)

    def test_unknown_request_routes_to_humans_without_stopping_another_method(self):
        import tempfile
        from pathlib import Path
        from evaluation.request_meter import RequestMeter,StudyBudget
        from review_agent.llm_clients import MockLLMClient
        study=StudyBudget({'llm_calls':10,'known_tokens':100,'seconds':60})
        limits={'llm_calls':10,'tokens':100,'seconds':60};client=MockLLMClient([])
        with tempfile.TemporaryDirectory() as directory:
            a=RequestMeter(Path(directory)/'a.jsonl',limits,study)
            b=RequestMeter(Path(directory)/'b.jsonl',limits,study)
            a.begin(client,{},'review',['a'],{})
            a.snapshot()
            b.begin(client,{},'review',['b'],{})
        self.assertEqual(study.snapshot()['physical_calls'],2)
        self.assertIsNone(study.snapshot()['blocked_reason'])
        self.assertEqual(study.snapshot()['outcome_unknown_requests'],1)
        self.assertEqual(study.snapshot()['human_review_case_ids'],['a'])

    def test_preparation_lexical_scope_preserves_only_general_descriptions(self):
        from evaluation.formal_preparation import prescreen
        general='刚体系统的广义坐标用于描述系统构型，约束条件限制各物体之间的相对运动。'*3
        specific='火炮后坐系统的运动分析与部件设计相关。'*5
        accepted,rejected=prescreen(general+'\n\n'+specific)
        self.assertEqual([r['text'] for r in accepted],[general])
        self.assertEqual(rejected[0]['reason'],'outside_general_concept_scope')

    def test_experiment_study_fence_preserves_both_groups_and_marks_failed_ballot(self):
        h=fixture(self);config=copy.deepcopy(h.config)
        config['budget'].update(max_retries_per_request=0,max_parallel_validators=1)
        spec={'split':{'train_document_ids':[],'test_document_ids':[h.index['document_id']]},
              'study_limits':{'llm_calls':1,'known_tokens':1000,'seconds':60},
              'groups':[{'id':'b2','method':'majority','ontology_mode':'fixed'},
                        {'id':'b0','method':'all_keep','ontology_mode':'fixed'}]}
        output=h.folder/'study_fence'
        report=run_experiment(spec,[h.candidate],[h.index],h.base.path,output,config,services_factory=mock_services(h))
        self.assertEqual(report['study_budget']['physical_calls'],1)
        self.assertEqual(len(report['groups']),2)
        failed=json.loads((output/'b2/predictions.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(failed['stop_reason'],'baseline_technical_incomplete')
        self.assertEqual(fixed_candidate_metrics([failed])['outcomes']['manual'],1)
        self.assertEqual(fixed_candidate_metrics([failed])['technical_failure_handoff_count'],1)
        self.assertEqual(report['groups'][1]['usage']['total']['calls'],0)

    def test_extraction_batches_cross_small_sections_without_changing_paragraphs(self):
        from evaluation.formal_preparation import pack_paragraphs
        rows=[{'text':'a'*70,'section':'one'},{'text':'b'*70,'section':'two'},
              {'text':'c'*70,'section':'three'}]
        result=pack_paragraphs(rows,limit=150)
        self.assertEqual(result,[rows[:2],rows[2:]])
        self.assertEqual([r for group in result for r in group],rows)
