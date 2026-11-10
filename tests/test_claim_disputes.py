"""Disagreement is about a frozen claim, not a role or a majority ballot."""
import copy
import json
import unittest
from review_agent.disputes import build_report,target_for
from review_agent.dispute_review import review,response_errors,RESPONSE_SCHEMA
from review_agent.agent_state import AgentBudget,AgentState
from review_agent.contracts import sha256_json
from review_agent.action_policy import ActionPolicy
from tests import test_review_recovery_v4 as helpers


class DisputeProjectionTests(unittest.TestCase):
    def setUp(self):
        self.base={'candidate':{'candidate_id':'c','revision':1,'triple':{'head':'A','surface_relation':'包含','tail':'B'}},
            'proposal_digest':'p','evidence_bundle':{'evidence_hash':'e','items':[{'evidence_id':'e','quote':'A refers to B.','anchor_valid':True,'start':4,'end':18,'section_id':'s','chunk_id':'c'}]}}
        def vote(identity,status):
            return {'voter_id':identity,'model_id':identity,'response_status':'ok','source_support':'supported' if status=='supported' else 'insufficient',
                'expression_status':'faithful' if status=='supported' else 'unresolved','dimensions':{
                    'triple_support':{'status':status,'evidence_ids':['e'],'note':'MOCK specific containment vs association'},
                    'counterevidence':{'status':'pass','evidence_ids':['e'],'note':'No material counter recorded'}},
                'primary_evidence_ids':['e'] if status=='supported' else [],'counter_evidence_ids':[]}
        self.votes=[vote('a','insufficient'),vote('b','supported'),vote('c','supported')]

    def test_minor_opinion_keeps_its_exact_question_reasons_and_source_positions(self):
        result=build_report(self.base,self.votes)
        dispute=result['disputes'][0]
        self.assertIn('包含',dispute['question']);self.assertIn('共同出现',dispute['question'])
        self.assertEqual(len(dispute['opinions']),3)
        self.assertEqual(dispute['opinions'][0]['source_positions'][0]['start'],4)
        self.assertFalse(dispute['majority_is_resolution'])

    def test_no_counter_is_never_positive_support_and_failed_seats_are_not_opponents(self):
        self.votes[0]['response_status']='invalid_contract'
        result=build_report(self.base,self.votes)
        self.assertEqual(len(result['technical_seats']),1)
        self.assertFalse(result['counter_absence_is_support'])
        unsupported=copy.deepcopy(self.votes[1]);unsupported['dimensions']['triple_support']['status']='insufficient';unsupported['expression_status']='unresolved'
        result=build_report(self.base,[unsupported])
        self.assertEqual(result['evidence_observations'][0]['positive_support']['status'],'unresolved')
        self.assertEqual(result['evidence_observations'][0]['counterevidence_search']['status'],'reviewed_no_material_counter_recorded')

    def test_older_rounds_and_cosmetic_counter_pass_not_applicable_do_not_create_disputes(self):
        self.votes[0]=copy.deepcopy(self.votes[1]);self.votes[0]['voter_id']='a'
        self.votes[0]['dimensions']['counterevidence']['status']='not_applicable'
        self.votes.append({**self.votes[1],'proposal_digest':'old','dimensions':{'triple_support':{'status':'contradicted','evidence_ids':['e'],'note':'old'}}})
        self.assertEqual(build_report(self.base,self.votes)['disputes'],[])

    def test_after_context_changes_policy_blocks_full_revote_and_allows_manual_handoff(self):
        state=AgentState(self.base['candidate'],{'dispute_baseline':{'proposal_digest':'p'},'dispute_report':build_report(self.base,self.votes)},'a')
        state.ontology_checked=True;state.bundle=self.base['evidence_bundle']
        blocked=ActionPolicy.blocked_tools(state)
        self.assertIn('only the frozen dispute',blocked['run_validation'])
        self.assertNotIn('finish_review',blocked)
        self.assertNotIn('review_dispute',blocked)


class FocusedReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture=helpers.RevisionAndConsumerTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.repo=f.repo;c=f.fixture.candidate
        audit=f.repo.get_audit(c['candidate_id'],f.fixture.source_result['audit_id']);self.base=audit['rounds'][0]
        self.bundle=copy.deepcopy(self.base['evidence_bundle']);self.bundle['items'].append({'evidence_id':'added-context','quote':'A regulates B with a direct causal operation.','anchor_valid':True})
        self.bundle['evidence_hash']=sha256_json({k:v for k,v in self.bundle.items() if k!='evidence_hash'})
        self.dispute={'dispute_id':'d_'+sha256_json(self.base)[:12],'claim':target_for(c,'relation_semantics'),'check':'relation_semantics',
            'issue_type':'relation_meaning','question':'Does A specifically regulate B, rather than merely co-occur?','route':'supplement_source_context'}
        self.voters=f.fixture.voters
        from review_agent.consensus import Committee
        self.committee=Committee([v['id'] for v in self.voters])
        self.answers={v['id']:'supports' for v in self.voters}
        for voter in self.voters:
            identity=voter['id']
            voter['client']._callable=lambda p,identity=identity:self.response(p,self.answers[identity])

    def response(self,p,answer='supports'):
        return {'dispute_id':p['dispute']['dispute_id'],'answer':answer,
            'positive_support':{'status':'found' if answer=='supports' else 'not_found','evidence_ids':['added-context'] if answer=='supports' else []},
            'counterevidence_search':{'status':'none_found_in_reviewed_scope','evidence_ids':[]},
            'reviewed_evidence_ids':[e['evidence_id'] for e in p['evidence_bundle']['items']],
            'definition_ids':[],'note':'MOCK one frozen question; not measured semantic quality'}

    def run_review(self,bundle=None):
        return review(self.repo,self.base,self.dispute,bundle or self.bundle,self.voters,self.committee,AgentBudget({'total_timeout_seconds':30}))

    def test_targeted_review_asks_one_question_and_keeps_original_votes_unchanged(self):
        before=copy.deepcopy(self.repo.get_audit(self.base['candidate']['candidate_id'],self.base['audit_id'])['votes'])
        result=self.run_review();self.assertEqual(result['status'],'resolved_supports')
        self.assertFalse(result['whole_candidate_admitted'])
        for voter in self.voters:
            p=voter['client'].calls[-1]
            self.assertEqual(p['dispute']['question'],self.dispute['question'])
            self.assertNotIn('dimensions',p['output_contract']['properties'])
            self.assertNotIn('opinions',p['dispute'])
        self.assertEqual(before,self.repo.get_audit(self.base['candidate']['candidate_id'],self.base['audit_id'])['votes'])

    def test_two_to_one_does_not_settle_the_semantic_question_and_cannot_trigger_rerolls(self):
        self.answers[self.voters[-1]['id']]='insufficient'
        result=self.run_review();self.assertEqual(result['status'],'human_review')
        counts=[len(v['client'].calls) for v in self.voters]
        self.run_review();self.assertEqual(counts,[len(v['client'].calls) for v in self.voters])

    def test_missing_context_and_missing_mapping_metadata_make_no_model_calls(self):
        counts=[len(v['client'].calls) for v in self.voters]
        self.assertEqual(self.run_review(self.base['evidence_bundle'])['status'],'human_review')
        self.dispute['check']='mapping';self.dispute['route']='supplement_mapping_information'
        self.assertEqual(self.run_review()['status'],'human_review')
        self.assertEqual(counts,[len(v['client'].calls) for v in self.voters])

    def test_no_counter_alone_and_an_opposing_role_cannot_fake_valid_support_or_opposition(self):
        p={'dispute':self.dispute,'evidence_bundle':self.bundle,'protected_evidence_ids':[e['evidence_id'] for e in self.base['evidence_bundle']['items']]}
        raw=self.response(p);raw['positive_support']={'status':'not_found','evidence_ids':[]}
        self.assertTrue(response_errors(raw,p))
        raw['answer']='opposes';self.assertTrue(response_errors(raw,p))
        raw=self.response(p);raw['reviewed_evidence_ids']=[];self.assertTrue(response_errors(raw,p))
        raw=self.response(p)
        raw['answer']='opposes';raw['counterevidence_search']={'status':'found','evidence_ids':[self.base['evidence_bundle']['items'][0]['evidence_id']]}
        self.assertTrue(response_errors(raw,p))


class FrozenDisputeIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_review_four_axes import FourAxisIntegrationTests
        self.fixture=FourAxisIntegrationTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    @staticmethod
    def disagree(payload,raw):
        if payload['output_structure']['model']=='c':
            eid=raw['primary_evidence_ids'][0]
            raw['dimensions']['relation_semantics']['status']='unknown'
            raw['expression_findings']=[{'check':'relation_semantics','kind':'unresolved','note':'MOCK exact predicate not established by association',
                'evidence_ids':[eid],'normalization':None,'rule_id':'MEANING-001','meaning_effect':'unresolved',
                'suggested_change':'Supplement the surrounding predicate context'}]
        return raw

    def test_shared_claim_targets_are_frozen_and_a_semantic_minority_is_preserved(self):
        from review_agent.disputes import frozen
        c,result,o=self.fixture.run_case(version='validator_response_v8',dispute_review=True,opinion_override=self.disagree)
        audit=self.fixture.fixture.repo.get_audit(c['candidate_id'],result['audit_id']);p=audit['rounds'][0]
        self.assertTrue(result['independent_review_completed'],result)
        report=result['consensus']['dispute_report'];self.assertTrue(any(d['check']=='relation_semantics' for d in report['disputes']))
        self.assertNotEqual(result['admission_consensus']['result'],'accept_consensus')
        for voter in o.validators:self.assertEqual(voter['client'].calls[0]['claim_review_context'],frozen(p))
        altered=copy.deepcopy(p);altered['dispute_context']['targets'][0]['question']='approve silently'
        with self.assertRaises(ValueError):frozen(altered)

    def test_agent_cannot_revote_the_whole_candidate_after_supplementing_context(self):
        from review_agent.runtime import AgentRuntime
        c,r,o=self.fixture.run_case(version='validator_response_v8',dispute_review=True,opinion_override=self.disagree)
        runtime=AgentRuntime(o);runtime.initialize(c['candidate_id'],1,self.fixture.fixture.index,{})
        runtime.tools.inspect_ontology(None,'MOCK initial mapping');runtime.tools.run_validation()
        self.assertTrue(runtime.state.context.get('dispute_baseline'))
        before=[len(v['client'].calls) for v in o.validators]
        runtime.state.bundle['evidence_hash']='context-was-supplemented'
        self.assertEqual(runtime.tools.run_validation()['status'],'blocked')
        self.assertEqual(before,[len(v['client'].calls) for v in o.validators])
        self.assertNotIn('finish_review',ActionPolicy.blocked_tools(runtime.state))


if __name__=='__main__':unittest.main()
