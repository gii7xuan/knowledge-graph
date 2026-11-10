"""Real aggregation fault boundaries: partial axes never bypass whole-vote admission."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
from review_agent.review_observability import assess_vote,outcome,redact
from review_agent.consensus import Committee
from review_agent.mapping_trace import process,statistics
from review_agent.shadow_revision import clone,apply_reviewed
from review_agent.repository import ConflictError
from tests.test_review_four_axes import FourAxisIntegrationTests
from tests.test_review_quality import QualityTests
from evaluation.review_quality import quality_report


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.f=FourAxisIntegrationTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.c,self.result,self.orchestrator=self.f.run_case(version='validator_response_v8')
        self.repo=self.f.fixture.repo
        self.audit=self.repo.get_audit(self.c['candidate_id'],self.result['audit_id'])
        self.p=self.audit['rounds'][0];self.votes=self.audit['votes'];self.committee=self.orchestrator.committee

    def test_unrelated_mapping_schema_failure_preserves_source_and_expression(self):
        v=deepcopy(self.votes[0]);v['raw_response']['dimensions']['mapping']['status']='invalid_enum'
        v.update(response_status='invalid_contract',error='mapping.status invalid')
        report=assess_vote(v,self.p)
        self.assertTrue(report['axis_observations']['source']['usable'],report)
        self.assertTrue(report['axis_observations']['expression']['usable'],report)
        self.assertFalse(report['axis_observations']['mapping']['usable'])
        self.assertFalse(report['whole_vote_eligible'])

    def test_mapping_failure_does_not_hide_source_role_conflict(self):
        v=deepcopy(self.votes[0]);v['raw_response']['dimensions']['mapping']['status']='invalid_enum'
        v['raw_response']['evidence_assessments'][0]['role']='counter'
        v['response_status']='invalid_contract'
        report=assess_vote(v,self.p)
        self.assertFalse(report['axis_observations']['source']['usable'])
        self.assertTrue(report['axis_observations']['expression']['usable'])

    def test_source_only_counter_field_failure_keeps_expression(self):
        v=deepcopy(self.votes[0]);del v['raw_response']['dimensions']['counterevidence']
        v['response_status']='invalid_contract'
        report=assess_vote(v,self.p)
        self.assertFalse(report['axis_observations']['source']['usable'])
        self.assertTrue(report['axis_observations']['expression']['usable'],report)

    def test_untrusted_provenance_unknown_version_or_truncation_cannot_be_salvaged(self):
        for changes in ({'evidence_hash':'foreign'},{'object_revision':999},{'response_contract_version':'unknown'},
            {'response_contract_version':None},{'finish_reason':'length'},{'response_status':'timeout','outcome_unknown':True}):
            with self.subTest(changes=changes):
                report=assess_vote({**self.votes[0],**changes},self.p)
                self.assertFalse(report['global_trustworthy'])
                self.assertFalse(any(o['usable'] for o in report['axis_observations'].values()))

    def test_provider_truncation_is_rejected_even_when_json_parses(self):
        from review_agent.llm_clients import LLMResponse
        from review_agent.validators import judge_validator
        voter=self.orchestrator.validators[0]
        with patch.object(voter['client'],'complete_json',return_value=LLMResponse(
                content=deepcopy(self.votes[0]['raw_response']),finish_reason='length')):
            vote=judge_validator(voter,self.p,max_retries=0)
        self.assertEqual(vote['response_status'],'output_incomplete')
        self.assertEqual(vote['finish_reason'],'length');self.assertIsNotNone(vote['raw_response'])

    def test_unhandled_runtime_exception_saves_failed_terminal_and_stage(self):
        f=FourAxisIntegrationTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        with patch('review_agent.orchestrator.ReviewOrchestrator._finalize',side_effect=RuntimeError('forced finalization failure')):
            with self.assertRaisesRegex(RuntimeError,'forced finalization'):f.run_case(version='validator_response_v8')
        repo=f.fixture.repo
        candidate=next(c for c in repo.list_candidates(f.fixture.candidate['run_id']) if c['candidate_id'].endswith('_four'))
        audit=repo.get_audit(candidate['candidate_id'])
        self.assertEqual(audit['status'],'failed')
        self.assertEqual(audit['context']['execution_failure']['reason_code'],'RUNTIME_EXCEPTION')
        self.assertTrue(audit['votes']);self.assertIsNone(repo.latest_decision(candidate['candidate_id']))

    def test_transport_failure_keeps_two_observations_without_inventing_insufficient(self):
        votes=deepcopy(self.votes);votes[0].update(response_status='timeout',outcome_unknown=True)
        report=outcome(self.p,votes,self.committee)
        source=report['dimension_results']['source']
        self.assertEqual(source['status'],'unresolved');self.assertEqual(source['valid_seats'],2)
        self.assertFalse(source['judgment_complete']);self.assertNotEqual(report['recommendation'],'admit')
        errors=report['vote_validation'][votes[0]['voter_id']]['errors']
        self.assertNotIn('parse',{e['category'] for e in errors})

    def test_tracking_metadata_is_not_an_admission_obligation(self):
        report=outcome(self.p,self.votes,self.committee,context={'final_gaps':{'validation_key':'trace-id','stop_reason':'finished','need_more_evidence':True}})
        self.assertNotIn('gap:validation_key',report['blocking_reasons'])
        self.assertIn('gap:need_more_evidence',report['blocking_reasons'])

    def test_frozen_version_inference_requires_verified_unique_metadata(self):
        v={**self.votes[0],'response_contract_version':None}
        request={'proposal_digest':self.p['proposal_digest'],'response_contract_version':self.p['response_contract_version']}
        self.assertFalse(assess_vote(v,self.p,frozen_request=request)['global_trustworthy'])
        request['unique_binding_verified']=True
        self.assertEqual(assess_vote(v,self.p,frozen_request=request)['version_inference'],'unique_frozen_request_binding')

    def test_malformed_dimensions_cannot_crash_the_status_projection(self):
        v=deepcopy(self.votes[0]);v['raw_response']['dimensions']=['bad'];v['response_status']='invalid_contract'
        report=assess_vote(v,self.p)
        self.assertFalse(any(o['usable'] for o in report['axis_observations'].values()))

    def test_complete_valid_mock_packet_can_recommend_admission_without_writing(self):
        from schema.fact_identity import relation_semantic_hash
        from review_agent.contracts import fact_key_for
        from review_agent.validators import judge_independently
        c=deepcopy(self.c);c['triple'].update(relation_id='R_old',head_type='C',tail_type='C',
            relation_semantic_hash=relation_semantic_hash(self.f.fixture.base.ontology,'R_old'))
        c['triple']['qualifiers']['polarity']='positive';c['triple']['qualifier_status']['polarity']='extracted'
        c['fact_key_state']='canonical';c['fact_key']=fact_key_for(c)
        guard=self.orchestrator.ontology_guard.check(c)
        self.assertEqual(guard['status'],'pass',guard)
        p=self.orchestrator._proposal(self.p['audit_id'],c,guard,self.p['evidence_bundle'],2,self.audit['context'])
        for voter in self.orchestrator.validators:
            raw=deepcopy(self.votes[0]['raw_response']);raw['dimensions']['mapping'].update(status='matched',relation_id='R_old')
            voter['client']._callable=lambda payload,raw=raw:deepcopy(raw)
        votes=judge_independently(self.orchestrator.validators,p,max_retries=0)
        report=outcome(p,votes,self.committee,context={'final_gaps':{'validation_key':'metadata'}})
        self.assertEqual(report['recommendation'],'admit',report['blocking_reasons'])
        self.assertEqual(report['action_taken'],'none');self.assertFalse(report['writes_authorized'])

    def test_duplicate_retry_does_not_create_an_independent_seat(self):
        report=outcome(self.p,[self.votes[0]]*3,self.committee)
        self.assertEqual(report['dimension_results']['scope']['valid_seats'],1)
        self.assertEqual(report['duplicate_records_ignored'],2)
        self.assertNotEqual(report['recommendation'],'admit')

    def test_terminal_reason_and_guard_survive_when_no_decision_exists(self):
        report=outcome(self.p,self.votes,self.committee,audit_status='interrupted',stop_reason='case_token_budget_exhausted')
        self.assertEqual(report['execution_status'],'interrupted')
        self.assertEqual(report['execution_reason'],'case_token_budget_exhausted')
        self.assertEqual(report['action_taken'],'none');self.assertNotEqual(report['recommendation'],'admit')
        self.repo.finish_incomplete_audit(self.result['audit_id'],'failed',{'reason_code':'SHOULD_NOT_OVERWRITE_COMPLETED'})
        self.assertEqual(self.repo.get_audit(self.c['candidate_id'],self.result['audit_id'])['status'],'completed')

    def test_mapping_stages_distinguish_missing_definition_failed_retrieval_and_not_run(self):
        p=deepcopy(self.p);p['mapping_preflight']={'search_trace':[{'stage':'review_subset','property_count':1},
            {'stage':'supplementary_budget','complete':False,'reason':'budget'}],
            'relation_candidates':[{'id':'r','retrieved':True,'missing_information':['definition_missing_in_bound_release']}],
            'entities':{'head':{'missing_information':['ambiguous']}}}
        stages={s['stage']:s for s in process(p)['stages']}
        self.assertEqual(stages['global_retrieval']['status'],'failed')
        self.assertEqual(stages['relation_definitions']['status'],'not_found')
        self.assertEqual(stages['mapping_attempt']['status'],'not_executed')
        self.assertFalse(process(p)['global_gap_confirmed'])
        self.assertEqual(statistics([process(p)])['stages']['entity_identity']['denominator'],1)

    def test_model_mapping_claim_cannot_override_an_unknown_ontology_guard(self):
        from review_agent.validator_contracts import contract_for
        votes=deepcopy(self.votes)
        for v in votes:
            raw=v['raw_response'];raw['dimensions']['mapping'].update(status='matched',relation_id='R_old')
            v.update(contract_for(v['response_contract_version']).normalize(raw))
        report=outcome(self.p,votes,self.committee)
        self.assertEqual(report['dimension_results']['mapping']['status'],'context_incomplete')
        self.assertNotEqual(report['recommendation'],'admit')

    def test_shadow_clone_cannot_be_source_database_and_requires_reviewed_draft(self):
        source=Path(self.repo.database_path);directory=self.f.fixture.folder/'isolated_shadow'
        with self.assertRaises(ValueError):clone(source,source.parent/'nested',allowed_root=self.f.fixture.folder)
        # Use a sibling output root so the shadow is outside the source experiment.
        directory=self.f.fixture.folder.parent/(self.f.fixture.folder.name+'_shadow')
        self.addCleanup(lambda:__import__('shutil').rmtree(directory,ignore_errors=True))
        shadow=clone(source,directory,allowed_root=self.f.fixture.folder.parent)
        with self.assertRaises(ConflictError):apply_reviewed(self.repo,draft_id='missing')
        with self.assertRaises(Exception):apply_reviewed(shadow,draft_id='missing')

    def test_redaction_removes_keys_and_bearer_from_nested_diagnostics(self):
        result=redact({'Authorization':'Bearer secret','nested':[{'api_key':'abc','error':'Bearer abc123'}]})
        self.assertNotIn('secret',str(result));self.assertNotIn('abc',str(result))


class RecommendationQualityTests(unittest.TestCase):
    packet=QualityTests.packet
    gold=QualityTests.gold
    def test_labeled_subset_is_separate_from_actual_admission_and_shows_coverage(self):
        p=self.packet('a',recommendation='admit');missing=self.packet('b',recommendation='admit')
        report=quality_report([p,missing],[self.gold(p)])
        metrics={m['id']:m for m in report['recommendation_metrics']['metrics']}
        self.assertEqual(metrics['recommendation_precision']['value'],1)
        self.assertEqual(metrics['recommendation_precision']['coverage']['unknown_or_missing'],1)
        self.assertIsNone(report['metrics'][0]['value'])

    def test_zero_denominator_and_missing_revision_truth_are_not_success(self):
        p=self.packet(recommendation='manual_review')
        report=quality_report([p])['recommendation_metrics']
        self.assertTrue(all(m['value'] is None for m in report['metrics']))
        self.assertEqual(report['unlabeled'],1)


if __name__=='__main__':unittest.main()
