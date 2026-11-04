from copy import deepcopy
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch
from ontology_review import clarification
from ontology_review.publisher import OntologyPublisher
from review_agent.llm_clients import MockLLMClient
from review_agent.contracts import sha256_json
from tests import test_ontology_extension_agent as helpers


class OriginalReviewerClarificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.OntologyExtensionTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;f.review();self.parent=f.repo.ontology_audit(f.runtime.state['audit_id'])['proposals'][0]
        with f.repo.transaction(immediate=True) as c:
            row=c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=? AND voter_id=?',(self.parent['audit_id'],'a')).fetchone()
            self.original=json.loads(row[0]);self.original['decision']='revise'
            c.execute('UPDATE ontology_votes SET vote_json=? WHERE audit_id=? AND voter_id=?',(json.dumps(self.original),self.parent['audit_id'],'a'))
        self.proof={'status':'pass','request_hash':'verified-semantic-fixture','parent_proposal_digest':self.parent['proposal_digest'],'question_ids':[]}
        def answer(payload):return {'original_vote_hash':payload['original_vote_hash'],'intended_decision':'approve',
            'correction_kind':'completed_verified_check','reaffirm_original_checks':True,'outstanding_changes':[],
            'evidence_ids':[self.parent['evidence'][0]['evidence_id']],'explanation':'MOCK original reviewer explicitly amends own inconsistent decision after the verified check.'}
        self.voter={**f.voters[0],'client':MockLLMClient(answer,model_id=f.voters[0]['model_id'],model_family=f.voters[0]['model_family'])}

    def test_original_vote_is_immutable_and_only_original_reviewer_intent_is_derived(self):
        f=self.fixture
        with patch.object(clarification,'semantic_result',return_value=self.proof):
            result=clarification.clarify(f.repo,f.root,self.parent,self.voter)
            audit=f.repo.ontology_audit(self.parent['audit_id']);effective,receipts=clarification.effective_votes(f.repo,f.root,self.parent,audit['votes'])
        self.assertEqual(next(v for v in audit['votes'] if v['voter_id']=='a'),self.original)
        self.assertEqual(result['intended_decision'],'approve')
        self.assertEqual(next(v for v in effective if v['voter_id']=='a')['decision'],'approve')
        self.assertEqual(receipts[0]['original_vote_hash'],sha256_json(self.original))

    def test_replaying_valid_clarification_does_not_call_the_model_again(self):
        f=self.fixture
        with patch.object(clarification,'semantic_result',return_value=self.proof):
            first=clarification.clarify(f.repo,f.root,self.parent,self.voter)
            second=clarification.clarify(f.repo,f.root,self.parent,self.voter)
        self.assertEqual(first,second);self.assertEqual(len(self.voter['client'].calls),1)

    def test_real_failed_check_cannot_be_overridden_by_a_clarification(self):
        f=self.fixture;value=deepcopy(self.original);value['checks'][0]['status']='fail'
        with f.repo.transaction(immediate=True) as c:c.execute('UPDATE ontology_votes SET vote_json=? WHERE audit_id=? AND voter_id=?',(json.dumps(value),self.parent['audit_id'],'a'))
        with patch.object(clarification,'semantic_result',return_value=self.proof):
            with self.assertRaisesRegex(ValueError,'otherwise fully passing'):clarification.clarify(f.repo,f.root,self.parent,self.voter)
        self.assertEqual(len(self.voter['client'].calls),0)

    def test_unknown_parent_and_different_model_are_blocked(self):
        f=self.fixture
        with patch.object(clarification,'semantic_result',return_value=self.proof):
            with self.assertRaisesRegex(ValueError,'original reviewer'):clarification.clarify(f.repo,f.root,self.parent,{**self.voter,'model_id':'different'})
        bad=deepcopy(self.original);bad.update(response_status='timeout',outcome_unknown=True)
        with f.repo.transaction(immediate=True) as c:c.execute('UPDATE ontology_votes SET vote_json=? WHERE audit_id=? AND voter_id=?',(json.dumps(bad),self.parent['audit_id'],'a'))
        with self.assertRaisesRegex(ValueError,'complete original committee'):clarification.request_for(f.repo,f.root,self.parent,'a')

    def test_saved_amendment_cannot_be_tampered_into_approval(self):
        f=self.fixture
        with patch.object(clarification,'semantic_result',return_value=self.proof):
            clarification.clarify(f.repo,f.root,self.parent,self.voter)
            with f.repo.transaction(immediate=True) as c:
                row=c.execute('SELECT result_json FROM ontology_vote_clarifications').fetchone();reply=json.loads(row[0]);reply['explanation']='tampered'
                c.execute('UPDATE ontology_vote_clarifications SET result_json=?',(json.dumps(reply),))
            effective,receipts=clarification.effective_votes(f.repo,f.root,self.parent,f.repo.ontology_audit(self.parent['audit_id'])['votes'])
        self.assertEqual(next(v for v in effective if v['voter_id']=='a')['decision'],'revise');self.assertEqual(receipts,[])

    def test_publication_records_amendment_and_preserves_original_vote(self):
        f=self.fixture
        with patch.object(clarification,'semantic_result',return_value=self.proof):
            clarification.clarify(f.repo,f.root,self.parent,self.voter)
            publisher=OntologyPublisher(f.repo,f.root,f.config);preview=publisher.preview(f.extension_id)
            request={'approved_by':'fixture','reason':'fixture own-reviewer addendum publication','proposal_digest':preview['proposal_digest'],
                'expected_base_release_id':preview['base_release_id'],'expected_row_version':preview['expected_row_version'],
                'confirm_high_impact':True,'confirm_profile_differences':True}
            result=publisher.publish(f.extension_id,request)
            self.assertEqual(publisher.publish(f.extension_id,request),result)
        original=next(v for v in f.repo.ontology_audit(self.parent['audit_id'])['votes'] if v['voter_id']=='a')
        self.assertEqual(original,self.original)

    def test_malformed_received_reply_keeps_usage_and_is_not_an_unknown_api_call(self):
        f=self.fixture
        with patch.object(clarification,'semantic_result',return_value=self.proof):
            request=clarification.request_for(f.repo,f.root,self.parent,'a')
        response=SimpleNamespace(content='{bad json',returned_model_id=self.voter['model_id'],response_id='received-id',
            input_tokens=123,output_tokens=45,latency_ms=10,finish_reason='stop',reasoning_tokens=0)
        with patch.object(self.voter['client'],'complete_json',return_value=response):
            result=clarification.judge_once(self.voter,request)
        self.assertEqual(result['response_status'],'invalid_json')
        self.assertEqual(result['raw_response'],'{bad json')
        self.assertEqual((result['input_tokens'],result['output_tokens']),(123,45))
        self.assertFalse(result.get('outcome_unknown'))
