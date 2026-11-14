"""Typed revision recovery uses known error feedback without rerolling valid votes."""
import unittest
import json
from review_agent.request_execution import judge_typed
from tests import test_review_recovery_v4 as helpers


class TypedRecoveryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RequestRecoveryTests(methodName='runTest')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.repo=self.fixture.repo;self.voter=self.fixture.voter
        self.proposal={'draft_id':'draft-fixture','base_revision':1,'changed_fields':['qualifiers.condition'],
            'protected_evidence_ids':['original-support','original-counter'],
            'candidate':helpers.proposal()['candidate'],'proposal_digest':'fixed-draft-proposal'}
        self.binding={'task_type':'candidate_revision_review','task_id':'draft-fixture','voter_id':'a',
                      'model_identity':'model-a','model_family':'family-a','proposal_digest':'fixed-draft-proposal'}
        self.feedback=[]

    def judge(self,invalid=False,raw=None):
        def invoke(feedback):
            self.feedback.append(feedback)
            return {**self.binding,'response_status':'invalid_contract' if invalid else 'ok',
                    'error':'every changed field requires exactly one review' if invalid else None,
                    **({'raw_response':json.dumps(raw)} if raw is not None else {})}
        return judge_typed(self.voter,self.proposal,self.repo,task_type='candidate_revision_review',
            binding=self.binding,invoke=invoke,validate_cached=lambda vote:[],prompt_hash='frozen-prompt',timeout=2)

    def test_known_contract_failure_carries_original_error_and_changed_field_catalog(self):
        first=self.judge(invalid=True);second=self.judge()
        self.assertEqual(first['request_key'],second['request_key'])
        self.assertIsNone(self.feedback[0])
        self.assertEqual(self.feedback[1]['required_changed_fields'],['qualifiers.condition'])
        self.assertEqual(self.feedback[1]['required_reviewed_evidence_ids'],['original-support','original-counter'])
        self.assertIn('every changed field',self.feedback[1]['error'])
        self.assertEqual(len(self.repo.request_history(first['request_key'])),2)

    def test_approval_field_rules_distinguish_absence_from_removing_a_mapping(self):
        self.proposal.update(changed_fields=['head_type','qualifiers.condition','qualifier_status.condition'],
            after={'triple':{'head_type':None,'qualifier_status':{'condition':'not_stated'}}})
        self.judge(invalid=True);self.judge()
        self.assertEqual(self.feedback[1]['field_status_rules_for_approval'],{'head_type':'supported',
            'qualifiers.condition':'not_stated_in_scope','qualifier_status.condition':'not_stated_in_scope'})

    def test_recovery_identifies_exact_unassessed_protected_ids_without_relabelling_them(self):
        self.judge(invalid=True,raw={'counterevidence_reviewed_ids':['original-counter']})
        self.judge()
        self.assertEqual(self.feedback[1]['missing_reviewed_evidence_ids'],['original-support'])
        self.assertNotIn('counter_evidence_ids',self.feedback[1])

    def test_valid_vote_is_reused_without_another_invocation(self):
        self.judge();again=self.judge()
        self.assertTrue(again['cached']);self.assertEqual(len(self.feedback),1)

    def test_inline_retry_uses_the_same_task_specific_contract_feedback(self):
        recorded=[]
        def invoke(feedback):
            recorded.append(feedback)
            return {**self.binding,'response_status':'invalid_contract' if len(recorded)==1 else 'ok',
                'error':'protected evidence not reviewed',
                'raw_response':{'counterevidence_reviewed_ids':['original-counter']}}
        result=judge_typed(self.voter,self.proposal,self.repo,task_type='candidate_revision_review',
            binding=self.binding,invoke=invoke,validate_cached=lambda vote:[],
            prompt_hash='inline-repair',retries=1,timeout=2)
        self.assertEqual(result['response_status'],'ok')
        self.assertEqual(recorded[1]['required_changed_fields'],['qualifiers.condition'])
        self.assertEqual(recorded[1]['missing_reviewed_evidence_ids'],['original-support'])

    def test_transient_error_does_not_erase_the_previous_contract_repair(self):
        self.judge(invalid=True,raw={'counterevidence_reviewed_ids':['original-counter']})
        recorded=[]
        def invoke(feedback):
            recorded.append(feedback)
            return {**self.binding,'response_status':'api_error','retryable':True,'error':'HTTP 500'}
        judge_typed(self.voter,self.proposal,self.repo,task_type='candidate_revision_review',
            binding=self.binding,invoke=invoke,validate_cached=lambda vote:[],prompt_hash='frozen-prompt',timeout=2)
        self.judge()
        self.assertEqual(self.feedback[-1]['missing_reviewed_evidence_ids'],['original-support'])

    def test_inline_semantic_repair_handles_malformed_references_and_transient_error(self):
        recorded=[]
        proposal={**self.proposal,'questions':[{'id':'coverage'}],
            'source_documents':[{'document_id':'manual','text':'An exact source statement.'}]}
        def invoke(feedback):
            recorded.append(feedback)
            status=('invalid_contract','api_error','ok')[len(recorded)-1]
            return {**self.binding,'response_status':status,'retryable':True,'error':'malformed quotation reference',
                'raw_response':{'assessments':[{'question_id':'coverage',
                    'quotes':[{'document_id':['invalid-id-type'],'quote':'wrong'}]}]}}
        result=judge_typed(self.voter,proposal,self.repo,task_type='ontology_competency_review',
            binding=self.binding,invoke=invoke,validate_cached=lambda vote:[],
            prompt_hash='inline-semantic-repair',retries=2,timeout=2)
        self.assertEqual(result['response_status'],'ok')
        self.assertEqual(result['actual_calls'],3)
        self.assertEqual(recorded[1],recorded[2])
        self.assertEqual(recorded[1]['question_ids'],['coverage'])
        self.assertEqual(recorded[1]['invalid_quotation_references'][0]['exact_occurrences'],0)
        self.assertIn('Preserve the actual pass/fail/unknown',recorded[1]['quotation_rule'])

    def test_unknown_request_is_not_reopened_to_supply_feedback(self):
        first=self.judge(invalid=True)
        with self.repo.transaction(immediate=True) as connection:
            connection.execute("UPDATE validation_requests SET status='unknown' WHERE request_key=?",(first['request_key'],))
        again=self.judge()
        self.assertTrue(again['outcome_unknown']);self.assertEqual(len(self.feedback),1)

    def test_ontology_context_recovery_explains_exact_shape_without_requesting_approval(self):
        recorded=[]
        def run(invalid):
            def invoke(feedback):
                recorded.append(feedback)
                return {**self.binding,'response_status':'invalid_contract' if invalid else 'ok','error':'invalid requested_context' if invalid else None}
            return judge_typed(self.voter,self.proposal,self.repo,task_type='ontology_review',binding=self.binding,
                invoke=invoke,validate_cached=lambda vote:[],prompt_hash='ontology-policy',timeout=2)
        first=run(True);second=run(False)
        self.assertEqual(first['request_key'],second['request_key'])
        self.assertEqual(recorded[1]['requested_context_contract']['if_needed_exact_keys'],['query','scope','limit'])
        self.assertIn('Preserve revise/reject/unknown',recorded[1]['requested_context_instruction'])
        self.assertIn('missing annotation',recorded[1]['approval_necessity_rules']['confirmed_gap'])
        self.assertIn('without the proposed delta',recorded[1]['approval_necessity_rules']['reuse_existing'])

    def test_semantic_quote_recovery_identifies_only_the_invalid_original_reference(self):
        recorded=[];request={**self.proposal,'questions':[{'id':'q'}],'source_documents':[{'document_id':'doc','text':'Exact source wording.'}]}
        def run(invalid):
            def invoke(feedback):
                recorded.append(feedback)
                return {**self.binding,'response_status':'invalid_contract' if invalid else 'ok','error':'quote mismatch' if invalid else None,
                    'raw_response':{'assessments':[{'question_id':'q','quotes':[{'document_id':'doc','quote':'wrong wording'}]}]}}
            return judge_typed(self.voter,request,self.repo,task_type='ontology_competency_review',binding=self.binding,
                invoke=invoke,validate_cached=lambda vote:[],prompt_hash='semantic-policy',timeout=2)
        run(True);run(False)
        self.assertEqual(recorded[1]['invalid_quotation_references'][0]['exact_occurrences'],0)
        self.assertIn('Preserve the actual pass/fail/unknown',recorded[1]['quotation_rule'])


if __name__=='__main__':unittest.main()
