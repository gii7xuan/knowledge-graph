from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from ontology_review import semantic_competency as semantic
from ontology_review.validators import competency_pass
from review_agent.contracts import sha256_json
from review_agent.llm_clients import MockLLMClient
from tests import test_ontology_extension_agent as helpers


class SemanticCompetencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.OntologyExtensionTests(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.review();f=self.fixture
        self.parent=f.repo.ontology_audit(f.runtime.state['audit_id'])['proposals'][0]
        self.request={'task_type':'ontology_competency_review','task_id':f.extension_id,'object_revision':1,
            'parent_proposal_digest':self.parent['proposal_digest'],'base_release_id':f.base.manifest['release_id'],
            'delta_hash':self.parent['delta_hash'],'response_contract_version':semantic.VERSION,
            'questions':[{'id':'source-meaning','text':'Are meanings grounded?'}],
            'source_documents':[{'document_id':'doc','text':'A regulates B.','document_version':'frozen'}],
            'committee_members':self.parent['committee_members'],'committee_policy':self.parent['committee_policy']}
        self.request['request_hash']=sha256_json(self.request)
        self.answer={'coverage':'complete','unresolved_terms':[],'assessments':[{'question_id':'source-meaning','status':'pass',
            'quotes':[{'document_id':'doc','quote':'A regulates B.'}],'note':'MOCK independently checked semantics'}]}

    def review(self,answer=None):
        f=self.fixture;voters=[]
        for member in self.parent['committee_members']:
            voters.append({**member,'client':MockLLMClient(lambda payload:deepcopy(answer or self.answer),model_id=member['model_id'],model_family=member['model_family'])})
        case=f.repo.ensure_case(f.candidate['candidate_id'])
        with patch.object(semantic,'request_for',return_value=(self.request,[case])):
            result=semantic.review(f.repo,f.root,self.parent,voters,reason='controlled fixture independent semantic question assessment')
            verified=semantic.verified_result(f.repo,f.root,self.parent)
        return result,verified,voters

    def test_complete_committee_and_exact_quotes_produce_a_bound_separate_receipt(self):
        before=deepcopy(self.fixture.repo.ontology_audit(self.parent['audit_id'])['votes'])
        result,proof,voters=self.review()
        self.assertEqual(proof['status'],'pass');self.assertEqual(len(proof['passed_voter_ids']),3)
        self.assertEqual(before,self.fixture.repo.ontology_audit(self.parent['audit_id'])['votes'])
        self.assertFalse(proof['original_ontology_votes_reused'])

    def test_unanchored_quote_cannot_pass(self):
        value=deepcopy(self.answer);value['assessments'][0]['quotes'][0]['quote']='not in the verified chapter'
        self.assertTrue(semantic.errors(value,self.request))

    def test_partial_coverage_or_unresolved_terms_cannot_pass(self):
        value=deepcopy(self.answer);value['coverage']='partial'
        self.assertTrue(semantic.errors(value,self.request))
        value=deepcopy(self.answer);value['unresolved_terms']=['unsupported class']
        self.assertTrue(semantic.errors(value,self.request))

    def test_receipt_cannot_be_transferred_to_changed_documents_or_questions(self):
        self.review();changed=deepcopy(self.request);changed['source_documents'][0]['text']+=' changed'
        changed['request_hash']=sha256_json({k:v for k,v in changed.items() if k!='request_hash'})
        with patch.object(semantic,'request_for',return_value=(changed,[])):
            self.assertIsNone(semantic.verified_result(self.fixture.repo,self.fixture.root,self.parent))

    def test_semantic_pass_does_not_override_unknown_endpoint_or_failed_check(self):
        proposal={'proposal_digest':'parent','check_records':{'competency':{'status':'unknown','checks':[
            {'id':'trigger-candidate','status':'unknown'},{'id':'source-meaning','status':'unknown'}]}}}
        proof={'status':'pass','request_hash':'bound','parent_proposal_digest':'parent','question_ids':['source-meaning']}
        self.assertFalse(competency_pass(proposal,proof))
        proposal['check_records']['competency']['checks'][0]['status']='pass'
        self.assertTrue(competency_pass(proposal,proof))
        proposal['check_records']['competency']['checks'][0]['status']='fail'
        self.assertFalse(competency_pass(proposal,proof))

    def test_missing_original_model_does_not_form_semantic_quorum(self):
        result,proof,_=self.review()
        self.assertEqual(semantic.aggregate(result['votes'][:2],self.request)['status'],'unknown')

    def test_saved_receipt_cannot_spoof_a_provider_model_identity(self):
        self.review();f=self.fixture
        with f.repo.transaction(immediate=True) as c:
            row=c.execute('SELECT result_json FROM ontology_semantic_competency').fetchone();result=json.loads(row[0])
            result['votes'][0]['returned_model_id']='forged-provider-model'
            c.execute('UPDATE ontology_semantic_competency SET result_json=?',(json.dumps(result),))
        with patch.object(semantic,'request_for',return_value=(self.request,[])):
            self.assertIsNone(semantic.verified_result(f.repo,f.root,self.parent))

    def test_semantic_receipt_does_not_override_a_valid_ontology_revise_vote(self):
        from ontology_review.validators import consensus
        _,proof,_=self.review()
        votes=deepcopy(self.fixture.repo.ontology_audit(self.parent['audit_id'])['votes'])
        votes[0]['decision']='revise'
        self.assertNotEqual(consensus(votes,self.parent,semantic_competency=proof)['result'],'approve')

    def test_changed_inputs_are_blocked_before_any_new_model_dispatch(self):
        self.review();changed=deepcopy(self.request);changed['source_documents'][0]['text']+=' changed'
        changed['request_hash']=sha256_json({k:v for k,v in changed.items() if k!='request_hash'})
        voters=[{**m,'client':MockLLMClient(lambda p:self.answer,model_id=m['model_id'],model_family=m['model_family'])} for m in self.parent['committee_members']]
        with patch.object(semantic,'request_for',return_value=(changed,[])):
            with self.assertRaisesRegex(ValueError,'before dispatch'):
                semantic.review(self.fixture.repo,self.fixture.root,self.parent,voters,reason='must block changed frozen input')
        self.assertTrue(all(not v['client'].calls for v in voters))

    def test_new_task_namespace_cannot_bypass_unknown_parent_review(self):
        f=self.fixture
        with f.repo.transaction(immediate=True) as c:
            row=c.execute('SELECT vote_json FROM ontology_votes WHERE audit_id=? LIMIT 1',(self.parent['audit_id'],)).fetchone()
            vote=json.loads(row[0]);vote.update(response_status='timeout',outcome_unknown=True)
            c.execute('UPDATE ontology_votes SET vote_json=? WHERE audit_id=? AND voter_id=?',(json.dumps(vote),self.parent['audit_id'],vote['voter_id']))
        with patch.object(semantic,'request_for',return_value=(self.request,[])):
            with self.assertRaisesRegex(ValueError,'unknown requests'):
                semantic.review(f.repo,f.root,self.parent,f.voters,reason='cannot escape parent reconciliation')

    def test_real_source_binding_allows_publication_and_idempotent_read_after_rebind(self):
        from ontologyBuild.release_catalog import ReleaseCatalog
        from ontology_review.publisher import OntologyPublisher
        f=self.fixture
        artifact=f.artifact(f.schema)
        artifact.competency_questions=[{'id':'source-meaning','status':'requires_semantic_review','text':'Are named concepts and relationships grounded in the supplied source?'}]
        f.base=ReleaseCatalog(f.root).publish(artifact,approval={'status':'approved','approved_by':'fixture','reason':'fixture semantic competency setup'},expected_base_release_id=f.base.manifest['release_id'])
        f.candidate,f.source_result=f.source_candidate(ordinal=1);f.extension_id=f.source_result['pending_extension_id'];f.review()
        audit=f.repo.ontology_audit(f.runtime.state['audit_id']);parent=audit['proposals'][0]
        answer=deepcopy(self.answer);answer['assessments'][0]['quotes'][0]['document_id']=f.index['document_id']
        voters=[{**m,'client':MockLLMClient(lambda p:deepcopy(answer),model_id=m['model_id'],model_family=m['model_family'])} for m in parent['committee_members']]
        result=semantic.review(f.repo,f.root,parent,voters,reason='fixture full verified-source semantic execution')
        self.assertEqual(result['assessment']['status'],'pass')
        publisher=OntologyPublisher(f.repo,f.root,f.config);preview=publisher.preview(f.extension_id)
        request={'approved_by':'fixture','reason':'fixture exact reviewed publication','proposal_digest':preview['proposal_digest'],
            'expected_base_release_id':preview['base_release_id'],'expected_row_version':preview['expected_row_version'],
            'confirm_high_impact':True,'confirm_profile_differences':True}
        published=publisher.publish(f.extension_id,request)
        self.assertEqual(f.repo.extension_status(f.extension_id)['status'],'published')
        self.assertEqual(publisher.publish(f.extension_id,request),published)
        self.assertEqual(semantic.verified_result(f.repo,f.root,parent)['status'],'pass')

    def test_valid_semantic_votes_are_reused_byte_for_byte_without_new_calls(self):
        first,_,_=self.review();second,_,voters=self.review()
        self.assertEqual(first['votes'],second['votes'])
        self.assertTrue(all(not v['client'].calls for v in voters))

    def test_unknown_semantic_seat_is_held_while_valid_seats_remain_unchanged(self):
        first,_,_=self.review();f=self.fixture
        original=first['votes'][0];unknown={**original,'response_status':'timeout','outcome_unknown':True}
        with f.repo.transaction(immediate=True) as c:
            row=c.execute('SELECT result_json FROM ontology_semantic_competency').fetchone();result=json.loads(row[0])
            result['votes'][0]=unknown;c.execute('UPDATE ontology_semantic_competency SET result_json=?',(json.dumps(result),))
            c.execute("UPDATE validation_requests SET status='unknown' WHERE request_key=?",(original['request_key'],))
        second,_,voters=self.review()
        self.assertTrue(all(not v['client'].calls for v in voters))
        self.assertEqual(second['votes'][0],unknown)
        self.assertEqual(second['votes'][1:],first['votes'][1:])

    def test_received_malformed_json_preserves_raw_response_and_usage(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        member=self.parent['committee_members'][0]
        client=Mock();client.complete_json.return_value=SimpleNamespace(content='{"unterminated',input_tokens=17,output_tokens=9,
            returned_model_id=member['model_id'],response_id='received-response',latency_ms=5)
        vote=semantic.judge_once({**member,'client':client},self.request)
        self.assertEqual(vote['response_status'],'invalid_json');self.assertEqual(vote['raw_response'],'{"unterminated')
        self.assertEqual((vote['input_tokens'],vote['output_tokens']),(17,9));self.assertFalse(vote.get('outcome_unknown'))

    def test_known_failed_seat_gets_idempotent_additional_room_without_resetting_usage(self):
        first,_,_=self.review();f=self.fixture;case=f.repo.ensure_case(f.candidate['candidate_id'])
        failed={**first['votes'][0],'response_status':'invalid_json','error':'known JSON failure','outcome_unknown':False}
        with f.repo.transaction(immediate=True) as c:
            row=c.execute('SELECT result_json FROM ontology_semantic_competency').fetchone();result=json.loads(row[0]);result['votes'][0]=failed
            c.execute('UPDATE ontology_semantic_competency SET result_json=?',(json.dumps(result),))
            c.execute("UPDATE validation_requests SET status='failed',vote_json=? WHERE request_key=?",(json.dumps(failed),failed['request_key']))
            account=f.repo.case_status(case);usage=account['usage'];limits=account['limits'];usage['tokens']+=200000;usage['llm_calls']=limits['llm_calls']
            c.execute('UPDATE review_cases SET usage_json=? WHERE case_id=?',(json.dumps(usage),case))
        second,_,voters=self.review()
        self.assertEqual(sum(len(v['client'].calls) for v in voters),1)
        self.assertGreaterEqual(f.repo.case_status(case)['usage']['tokens'],usage['tokens'])
        self.assertEqual(second['votes'][1:],first['votes'][1:])
