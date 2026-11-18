"""Mechanism tests: real contracts and persistence, deterministic mock model responses."""
from copy import deepcopy
import unittest

from review_agent.consensus import Committee, aggregate_votes
from review_agent.contracts import proposal_digest, sha256_json
from review_agent.vote_weighting import normalize_policy, capped_weights, committee_policy
from review_agent.review_observability import summarize_dimensions, outcome
from tests import test_review_four_axes as fixtures


class WeightPolicyTests(unittest.TestCase):
    def test_invalid_modes_numbers_caps_and_history_are_rejected(self):
        invalid = [{'mode':'made_up'}, {'mode':'dynamic','max_seat_weight':.8},
                   {'mode':'dynamic','thresholds':dict.fromkeys(('source','expression','mapping','scope'),float('nan'))},
                   {'mode':'dynamic','min_opinions':1}, {'mode':'dynamic','min_available_mass':0},
                   {'mode':'dynamic','adaptation_floor':0}, {'mode':'dynamic','extra':1},
                   {'mode':'dynamic','reliability':{'data_version':'x','partition':'test','models':{}}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):normalize_policy(value)
        for values in ({'a':0,'b':0,'c':0},{'a':-1,'b':1,'c':1},{'a':float('nan'),'b':1,'c':1}):
            with self.assertRaises(ValueError):capped_weights(values,.49)

    def test_projection_is_bounded_normalized_and_deterministic(self):
        for values in ({'a':100,'b':1,'c':1},{'a':0,'b':0,'c':1},{'a':1,'b':2,'c':3}):
            result=capped_weights(values,.49)
            self.assertAlmostEqual(sum(result.values()),1)
            self.assertLessEqual(max(result.values()),.49+1e-12)
            self.assertEqual(result,capped_weights(values,.49))

    def test_unsupported_legacy_boolean_fails_explicitly(self):
        from review_agent.configuration import normalize_config
        with self.assertRaises(ValueError):normalize_config({'consensus':{'weighted_quorum_enabled':True}})

    def test_experiment_modes_are_explicit_and_versioned(self):
        from evaluation.experiment import configure
        from review_agent.vote_weighting import policy_from_config
        base={'validator_contract_version':'validator_response_v8','consensus':{'committee_size':3},'vote_weighting':{'mode':'dynamic'}}
        limits={'llm_calls':8,'tokens':10000,'tool_calls':8,'extension_requests':1,'rebinds':1,'seconds':60}
        hashes=[]
        for mode in ('equal','static','dynamic'):
            configured=configure(base,{'method':'agent','weighting_mode':mode},{'mode':'disabled'},limits)
            hashes.append(sha256_json(policy_from_config(configured)))
        self.assertEqual(len(set(hashes)),3)
        self.assertEqual(base['vote_weighting'],{'mode':'dynamic'})


class WeightingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FourAxisIntegrationTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_case(self, mode='dynamic', **kwargs):
        c, result, owner = self.fixture.run_case(version='validator_response_v8',model_mapping='matched',
            weighting={'mode':mode},assigned_mapping=True,**kwargs)
        audit=owner.repository.get_audit(c['candidate_id'],result['audit_id'])
        return c,result,owner,audit['rounds'][0],audit['votes']

    @staticmethod
    def uncertain_third(payload, response):
        if payload['fixture_voter_id']=='c':
            for name in ('evidence_sufficiency','counterevidence'):
                response['dimensions'][name]['status']='unknown'
        return response

    def test_dynamic_weights_change_operation_and_are_replayable(self):
        c,result,owner,proposal,votes=self.run_case(opinion_override=self.uncertain_third,dispute_review=True)
        self.assertEqual(result['research_operation'],'accept',result['research_operation_basis'])
        self.assertEqual(result['consensus']['result'],'accept_consensus')
        self.assertEqual(result['decision'],'manual_review') # production is still cold-start/shadow
        snap=result['weight_snapshot']
        self.assertLess(snap['axes']['source']['seats']['c']['weight'],snap['axes']['source']['seats']['a']['weight'])
        before=sha256_json(votes)
        replay=aggregate_votes(votes,owner.committee,proposal=proposal,proposal_digest=proposal['proposal_digest'])
        self.assertEqual(replay['weight_snapshot']['snapshot_id'],snap['snapshot_id'])
        self.assertEqual(sha256_json(votes),before)
        # The identical recorded opinions under a different aggregation policy are an
        # explicit offline replay, never cached new model votes.
        report=summarize_dimensions(votes,proposal,owner.committee)
        from evaluation.vote_weighting_replay import replay
        replayed=replay(proposal,votes)
        self.assertTrue(replayed['dynamic_static_operation_differs'])
        self.assertTrue(all(not r['new_votes_created'] for r in replayed['modes'].values()))
        from review_agent.vote_weighting import weight_report
        for mode in ('equal','static'):
            cp=deepcopy(proposal)
            cm=Committee(owner.committee.voter_ids,weighting_policy={'mode':mode})
            cp['committee_policy']=committee_policy(cm);cp['committee_policy_hash']=sha256_json(cp['committee_policy'])
            cp['proposal_digest']=proposal_digest(cp)
            alternative=weight_report(report,votes,cp,cm)
            self.assertEqual(alternative['dimension_results']['source']['status'],'unresolved')

    def test_equal_weights_match_explicit_mass_baseline(self):
        _,result,_,_,_=self.run_case(mode='equal')
        self.assertEqual(result['research_operation'],'accept')
        for axis in result['weight_snapshot']['axes'].values():
            self.assertTrue(all(abs(s['weight']-1/3)<1e-12 for s in axis['seats'].values()))

    def test_missing_or_duplicate_model_cannot_become_full_mass(self):
        _,result,owner,p,votes=self.run_case(missing=True)
        self.assertNotEqual(result['research_operation'],'accept')
        self.assertLess(result['weight_snapshot']['axes']['source']['available_mass'],.7)
        duplicated=deepcopy(votes);duplicated[1]=deepcopy(duplicated[0]);duplicated[1]['voter_id']='b'
        replay=aggregate_votes(duplicated[:2],owner.committee,proposal=p)
        self.assertNotEqual(replay['result'],'accept_consensus')
        self.assertLessEqual(replay['weight_snapshot']['axes']['source']['available_mass'],.49)

    def test_semantic_repair_and_scope_exclusion_are_separate_operations(self):
        _,result,_,_,_=self.run_case(semantic=True)
        self.assertEqual(result['research_operation'],'revise',result['research_operation_basis'])

    def test_scope_exclusion_is_discard(self):
        _,result,_,_,_=self.run_case(scope='example')
        self.assertEqual(result['research_operation'],'discard',result['research_operation_basis'])

    def test_scope_exclusion_does_not_require_an_irrelevant_formal_mapping(self):
        _,result,_=self.fixture.run_case(version='validator_response_v8',scope='example',weighting={'mode':'dynamic'})
        self.assertEqual(result['research_operation'],'discard',result['research_operation_basis'])

    def test_planner_view_exposes_weight_reasons_without_repeating_full_snapshots(self):
        _,_,owner,base,votes=self.run_case()
        from review_agent.planner_view import validation_summary
        consensus=aggregate_votes(votes,owner.committee,proposal=base)
        consensus['resolution_snapshot']={'snapshot_id':'resolved','large_unused_history':'x'*10000}
        view=validation_summary({'consensus':consensus,'votes':votes})
        self.assertEqual(view['consensus']['weight_snapshot']['snapshot_id'],consensus['weight_snapshot']['snapshot_id'])
        self.assertEqual(view['consensus']['resolution_snapshot'],{'snapshot_id':'resolved'})
        self.assertNotIn('observations',view['consensus']['review_axes']['source'])
        self.assertTrue(view['consensus']['weight_snapshot']['axes']['source']['seats']['a']['known_checks'])

    def test_low_weight_material_counterevidence_still_blocks(self):
        def oppose(payload,response):
            if payload['fixture_voter_id']=='c':
                response['dimensions']['triple_support']['status']='contradicted'
                response['dimensions']['counterevidence']['status']='fail'
                response['evidence_assessments'][0]['role']='counter'
                response['primary_evidence_ids']=[]
            return response
        _,result,_,_,_=self.run_case(opinion_override=oppose,dispute_review=True)
        self.assertNotEqual(result['research_operation'],'accept')
        self.assertTrue(result['weight_snapshot']['critical_counter_evidence_ids'])

    def test_strategy_and_candidate_revisions_invalidate_old_bindings(self):
        _,_,owner,p,votes=self.run_case()
        other=Committee(owner.committee.voter_ids,weighting_policy={'mode':'static'})
        with self.assertRaises(ValueError):aggregate_votes(votes,other,proposal=p)
        revised=deepcopy(p);revised['candidate']['revision']+=1
        revised['candidate_review_hash']=sha256_json(revised['candidate'])
        revised['revision']+=1;revised['proposal_digest']=proposal_digest(revised)
        replay=aggregate_votes(votes,owner.committee,proposal=revised)
        self.assertNotEqual(replay['result'],'accept_consensus')
        self.assertEqual(replay['weight_snapshot']['axes']['source']['available_mass'],0)
        tampered=deepcopy(p);tampered['evidence_bundle']['items'][0]['quote']='Changed quote'
        with self.assertRaises(ValueError):aggregate_votes(votes,owner.committee,proposal=tampered)

    def test_static_weights_ignore_current_features_but_use_frozen_history(self):
        _,_,owner,p,votes=self.run_case()
        from review_agent.vote_weighting import weight_report
        from review_agent.validator_contracts import raw_from_vote
        config={'mode':'static','reliability':{'data_version':'development-fixture-v1','partition':'development',
            'models':{'a':{'model_identity':'a','model_family':'a','axes':{'source':{'correct':9,'evaluated':10}}},
                      'b':{'model_identity':'b','model_family':'b','axes':{'source':{'correct':5,'evaluated':10}}}}}}
        committee=Committee(owner.committee.voter_ids,weighting_policy=config)
        report=summarize_dimensions(votes,p,owner.committee)
        cp=deepcopy(p);cp['committee_policy']=committee_policy(committee)
        cp['committee_policy_hash']=sha256_json(cp['committee_policy']);cp['proposal_digest']=proposal_digest(cp)
        first=weight_report(report,votes,cp,committee)
        raw=raw_from_vote(votes[0]);raw['dimensions']['evidence_sufficiency']['status']='unknown'
        second=weight_report(report,votes,cp,committee,raw_overrides={'a':raw})
        weights=lambda r:{v:s['weight'] for v,s in r['weight_snapshot']['axes']['source']['seats'].items()}
        self.assertEqual(weights(first),weights(second))
        self.assertGreater(weights(first)['a'],weights(first)['b'])
        config['reliability']['models']['a']['model_identity']='replaced-model'
        mismatched=Committee(owner.committee.voter_ids,weighting_policy=config)
        cp['committee_policy']=committee_policy(mismatched);cp['committee_policy_hash']=sha256_json(cp['committee_policy'])
        cp['proposal_digest']=proposal_digest(cp)
        fallback=weight_report(report,votes,cp,mismatched)
        self.assertEqual(fallback['weight_snapshot']['axes']['source']['seats']['a']['history']['reason'],
                         'historical_model_identity_mismatch')

    def test_agent_runtime_and_resolution_snapshot_use_the_same_weighting(self):
        candidate,_,owner,_,_=self.run_case(opinion_override=self.uncertain_third,dispute_review=True)
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.llm_clients import MockLLMClient
        config=deepcopy(owner.config)
        config['planner']={'enabled':True,'strategy':'llm_react'}
        config['agent_resolution']={'enabled':True,'max_attempts_per_issue':1,'reserve_final_committee_calls':3}
        def plan(payload):
            if not payload['ontology_checked']:name,args='inspect_ontology',{'relation_id':'R_old','issue':'verify frozen definitions'}
            elif not payload['current_validation']:name,args='run_validation',{}
            else:name,args='finish_review',{'stop_reason':'weighted review complete','unresolved_issues':[],'evidence_ids':payload['source_supported_evidence_ids']}
            return {'action':name,'arguments':args,'target_issue':'current evidence','rationale_summary':'MOCK bounded review'}
        agent=ReviewOrchestrator(owner.repository,owner.ontology_guard,owner.validators,config=config,
            planner_client=MockLLMClient(plan,model_id='planner',model_family='planner'))
        result=agent.audit_candidate(candidate['candidate_id'],1,self.fixture.fixture.index)
        self.assertEqual(result['research_operation'],'accept',result['research_operation_basis'])
        self.assertEqual(result['resolution_snapshot']['dimension_results']['source']['status'],'supported')
        self.assertEqual(result['weight_snapshot']['snapshot_id'],result['resolution_snapshot']['weight_snapshot']['snapshot_id'])

    def focused_packet(self, base, check, bundle, *, uncertain=False):
        from review_agent.disputes import target_for, TYPE_FOR
        from review_agent.dispute_review import binding
        from review_agent.resolution_state import VERSION
        claim=target_for(base['candidate'],check)
        did='dispute_'+sha256_json([base['proposal_digest'],claim['claim_id']])[:24]
        p={'version':'candidate_dispute_review_v1','audit_id':base['audit_id'],
           'candidate_id':base['candidate']['candidate_id'],'candidate_revision':base['candidate']['revision'],
           'candidate_hash':sha256_json(base['candidate']),'source_proposal_digest':base['proposal_digest'],
           'dispute':{'dispute_id':did,'claim':claim,'check':check,'issue_type':TYPE_FOR[check],
                      'question':claim['question'],'route':'supplement_source_context'},
           'evidence_bundle':deepcopy(bundle),'protected_evidence_ids':[e['evidence_id'] for e in base['evidence_bundle']['items']],
           'ontology_context':deepcopy(base.get('ontology_context')),'committee_members':deepcopy(base['committee_members']),
           'committee_policy':deepcopy(base['committee_policy']),'resolution_policy':VERSION}
        p['proposal_digest']=sha256_json(p)
        votes=[]
        for member in p['committee_members']:
            abstain=uncertain and member['id']=='c'
            votes.append({**binding(member,p),'response_status':'ok','dispute_id':did,
                'answer':'insufficient' if abstain else 'supports',
                'positive_support':{'status':'not_found' if abstain else 'found','evidence_ids':[] if abstain else ['new-source']},
                'counterevidence_search':{'status':'none_found_in_reviewed_scope','evidence_ids':[]},
                'reviewed_evidence_ids':[e['evidence_id'] for e in bundle['items']], 'definition_ids':[],
                'note':'MOCK verified focused claim against new context','other_affected_checks':[]})
        return {'review_proposal':p,'dispute_id':did,'votes':votes}

    def test_focused_weighting_and_new_evidence_recompute_without_rewriting_votes(self):
        _,_,owner,base,votes=self.run_case()
        from review_agent.dispute_review import focused_consensus
        from review_agent.resolution_state import resolve_review_state, apply_consensus
        bundle=deepcopy(base['evidence_bundle'])
        bundle['items'].append({'evidence_id':'new-source','quote':'A regulates B directly.',
                               'anchor_valid':True,'substantive':True})
        bundle['evidence_hash']=sha256_json({k:v for k,v in bundle.items() if k!='evidence_hash'})
        focused=self.focused_packet(base,'relation_semantics',bundle,uncertain=True)
        decision=focused_consensus(focused['votes'],focused['review_proposal'])
        self.assertEqual(decision['status'],'resolved_supports')
        self.assertLess(decision['weight_snapshot']['axes']['source']['seats']['c']['weight'],1/3)
        before=sha256_json(votes)
        full=self.focused_packet(base,'relation_semantics',bundle)
        snapshot=resolve_review_state(base,votes,[full],[],owner.committee,bundle=bundle)
        self.assertTrue(snapshot['semantic_obligations_complete'],snapshot['new_obligations'])
        self.assertNotEqual(snapshot['weight_snapshot']['snapshot_id'],summarize_dimensions(votes,base,owner.committee)['weight_snapshot']['snapshot_id'])
        final=apply_consensus(aggregate_votes(votes,owner.committee,proposal=base),snapshot)
        self.assertEqual(final['result'],'accept_consensus')
        self.assertEqual(sha256_json(votes),before)
        unresolved=resolve_review_state(base,votes,[full],[{'issue_id':'unrelated','critical':True,'status':'open'}],owner.committee,bundle=bundle)
        self.assertNotEqual(apply_consensus(final,unresolved)['result'],'accept_consensus')

    def test_verified_counterevidence_resolution_closes_only_the_bound_obligations(self):
        def oppose(payload,response):
            if payload['fixture_voter_id']=='c':
                response['dimensions']['triple_support']['status']='contradicted'
                response['dimensions']['counterevidence']['status']='fail'
                response['evidence_assessments'][0]['role']='counter'
                response['primary_evidence_ids']=[]
            return response
        _,_,owner,base,votes=self.run_case(opinion_override=oppose,dispute_review=True)
        from review_agent.resolution_state import resolve_review_state, apply_consensus
        bundle=deepcopy(base['evidence_bundle'])
        bundle['items'].append({'evidence_id':'new-source','quote':'The original context confirms A regulates B.',
                               'anchor_valid':True,'substantive':True})
        bundle['evidence_hash']=sha256_json({k:v for k,v in bundle.items() if k!='evidence_hash'})
        reviews=[self.focused_packet(base,check,bundle) for check in ('triple_support','counterevidence')]
        before=sha256_json(votes)
        snapshot=resolve_review_state(base,votes,reviews,[],owner.committee,bundle=bundle)
        self.assertTrue(snapshot['semantic_obligations_complete'],snapshot['unresolved_dispute_ids'])
        self.assertFalse(snapshot['weight_snapshot']['critical_counter_evidence_ids'])
        self.assertTrue(snapshot['retained_counter_evidence_ids'])
        self.assertEqual(apply_consensus(aggregate_votes(votes,owner.committee,proposal=base),snapshot)['result'],'accept_consensus')
        self.assertEqual(sha256_json(votes),before)


if __name__=='__main__':unittest.main()
