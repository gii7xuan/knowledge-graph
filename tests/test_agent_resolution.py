"""Cross-module closures, immutable originals and state-bound durable planning."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import threading
import time
import unittest
from uuid import uuid4

from review_agent.agent_state import AgentState
from review_agent.contracts import sha256_json
from review_agent.experiment_profiles import build_config
from review_agent.llm_clients import MockLLMClient,LLMResponse
from review_agent.planner_requests import proposal_for,request,current_action
from review_agent.repository import ReviewRepository


def action():
    return {'action':'inspect_ontology','arguments':{'relation_id':None,'issue':'check source-bound schema'},
            'target_issue':'schema','rationale_summary':'inspect before review'}


class DurablePlannerTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(__file__).resolve().parents[1]/'outputs'/('planner_task_test_'+uuid4().hex)
        self.folder.mkdir();self.addCleanup(shutil.rmtree,self.folder)
        self.repo=ReviewRepository(self.folder/'review.sqlite3');self.repo.initialize()
        self.state=AgentState({'candidate_id':'synthetic-sensor','revision':1,'triple':{}},{},'audit-test')
        self.payload={'audit_id':self.state.audit_id,'state_version':0,'candidate':{'head':'sensor'},'available_tools':['inspect_ontology']}
        self.proposal=proposal_for(self.state,self.payload,{'inspect_ontology':{}})
        self.client=MockLLMClient([LLMResponse(action(),input_tokens=8,output_tokens=4)],model_id='planner',model_family='planner')
        self.voter={'id':'planner','model_id':'planner','model_family':'planner','client':self.client,'request_channel':'planner'}

    def test_completed_receipt_is_reused_and_duplicate_completion_is_fenced(self):
        first=request(self.repo,self.voter,self.proposal,1)
        second=request(self.repo,self.voter,self.proposal,1)
        self.assertEqual(first['response_status'],'ok');self.assertTrue(second['cached'])
        self.assertEqual(len(self.client.calls),1)
        row=self.repo.request_attempt(first['attempt_id'])
        self.assertFalse(self.repo.finish_request_attempt(first['attempt_id'],row['owner_token'],first,late=True))
        self.assertEqual(current_action(first,self.proposal,self.state)[0],action())

    def test_late_receipt_is_saved_unknown_is_not_resent_and_stale_action_is_not_executed(self):
        release=threading.Event();self.addCleanup(release.set)
        def slow(payload):
            release.wait(2)
            return LLMResponse(action(),input_tokens=8,output_tokens=4)
        self.client._callable=slow
        first=request(self.repo,self.voter,self.proposal,.3)
        self.assertTrue(first['outcome_unknown'])
        request(self.repo,self.voter,self.proposal,.03)
        self.state.state_version+=1
        # Audit/namespace changes cannot release the semantic unknown-request fence.
        self.state.audit_id='other-audit'
        payload={**self.payload,'audit_id':'other-audit','state_version':1}
        other=proposal_for(self.state,payload,{'inspect_ontology':{}})
        blocked=request(self.repo,self.voter,other,.03)
        self.assertEqual(blocked['actual_calls'],0);self.assertEqual(len(self.client.calls),1)
        release.set()
        deadline=time.monotonic()+2
        while self.repo.request_attempt(first['attempt_id'])['state']=='unknown' and time.monotonic()<deadline:time.sleep(.02)
        late=self.repo.request_attempt(first['attempt_id'])
        self.assertEqual(late['state'],'completed');self.assertTrue(late['result']['late_response'])
        self.assertEqual(current_action(late['result'],self.proposal,self.state),(None,'stale_planner_state'))

    def test_gateway_504_remains_unknown_and_channel_breaker_does_not_pause_other_models(self):
        self.client._callable=lambda p: (_ for _ in ()).throw(RuntimeError('HTTP 504 gateway timeout'))
        for n in range(3):
            p=proposal_for(self.state,{**self.payload,'candidate':{'head':'sensor-'+str(n)}},{'inspect_ontology':{}})
            result=request(self.repo,self.voter,p,1)
            self.assertTrue(result['outcome_unknown'])
        from review_agent.request_execution import service_key
        self.assertIsNotNone(self.repo.service_pause(service_key(self.voter)))
        self.assertIsNone(self.repo.service_pause(service_key({**self.voter,'request_channel':'committee'})))


class ResolutionSnapshotTests(unittest.TestCase):
    def setUp(self):
        from tests.test_review_four_axes import FourAxisIntegrationTests
        from tests.test_claim_disputes import FrozenDisputeIntegrationTests
        self.f=FourAxisIntegrationTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        c,result,self.o=self.f.run_case(version='validator_response_v8',dispute_review=True,
            opinion_override=FrozenDisputeIntegrationTests.disagree)
        self.audit=self.f.fixture.repo.get_audit(c['candidate_id'],result['audit_id'])
        self.base=self.audit['rounds'][0];self.votes=self.audit['votes']
        self.bundle=deepcopy(self.base['evidence_bundle'])
        self.bundle['items'].append({'evidence_id':'additional-source','quote':'A regulates B directly.',
                                     'anchor_valid':True,'substantive':True})
        self.bundle['evidence_hash']=sha256_json(self.bundle['items'])

    def focused(self,check='relation_semantics',answer='supports',other=()):
        from review_agent.disputes import build_report
        from review_agent.dispute_review import binding
        from review_agent.resolution_state import VERSION
        dispute=next(d for d in build_report(self.base,self.votes)['disputes'] if d['check']==check)
        p={'version':'candidate_dispute_review_v1','audit_id':self.base['audit_id'],
            'candidate_id':self.base['candidate']['candidate_id'],'candidate_revision':self.base['candidate']['revision'],
            'candidate_hash':sha256_json(self.base['candidate']),'source_proposal_digest':self.base['proposal_digest'],
            'dispute':{k:deepcopy(dispute[k]) for k in ('dispute_id','claim','check','issue_type','question','route')},
            'evidence_bundle':deepcopy(self.bundle),'protected_evidence_ids':[e['evidence_id'] for e in self.base['evidence_bundle']['items']],
            'ontology_context':deepcopy(self.base.get('ontology_context')),'committee_members':deepcopy(self.base['committee_members']),
            'committee_policy':deepcopy(self.base['committee_policy']),'resolution_policy':VERSION}
        p['proposal_digest']=sha256_json(p)
        votes=[{**binding(v,p),'response_status':'ok','dispute_id':dispute['dispute_id'],'answer':answer,
            'positive_support':{'status':'found' if answer=='supports' else 'not_found','evidence_ids':['additional-source'] if answer=='supports' else []},
            'counterevidence_search':{'status':'found' if answer=='opposes' else 'none_found_in_reviewed_scope',
                                    'evidence_ids':['additional-source'] if answer=='opposes' else []},
            'reviewed_evidence_ids':[e['evidence_id'] for e in self.bundle['items']],
            'definition_ids':[],'note':'MOCK independently checks one exact question','other_affected_checks':list(other)} for v in p['committee_members']]
        return {'review_proposal':p,'dispute_id':dispute['dispute_id'],'votes':votes}

    def snapshot(self,results,issues=(),candidate=None):
        from review_agent.resolution_state import resolve_review_state
        return resolve_review_state(self.base,self.votes,results,list(issues),self.o.committee,
                                    bundle=self.bundle,candidate=candidate)

    def test_focused_support_updates_only_covered_checks_and_preserves_original_votes(self):
        before=sha256_json(self.votes)
        result=self.snapshot([self.focused()])
        self.assertTrue(result['accepted_resolutions'],result)
        self.assertEqual(result['dimension_results']['source']['status'],'supported')
        self.assertEqual(result['dimension_results']['expression']['status'],'standard')
        self.assertEqual(result['dimension_results']['mapping']['status'],self.base['mapping_preflight']['judgment_ready'] and 'unassigned' or 'context_incomplete') if 'mapping_preflight' in self.base else None
        self.assertEqual(sha256_json(self.votes),before)
        self.assertFalse(result['whole_candidate_admitted'])

    def test_other_unresolved_obligation_and_evidence_spillover_still_block_closure(self):
        issue={'issue_id':'unrelated-condition','critical':True,'status':'open'}
        result=self.snapshot([self.focused(other=['qualifiers'])],[issue])
        self.assertFalse(result['semantic_obligations_complete'])
        self.assertIn('unrelated-condition',result['unresolved_issue_ids'])
        self.assertTrue(result['new_obligations'])

    def test_opposition_routes_to_revision_and_changed_candidate_invalidates_old_snapshot(self):
        result=self.snapshot([self.focused(answer='opposes')])
        self.assertEqual(result['dimension_results']['expression']['status'],'semantic_revision')
        c=deepcopy(self.base['candidate']);c['revision']+=1
        stale=self.snapshot([self.focused()],candidate=c)
        self.assertFalse(stale['accepted_resolutions']);self.assertTrue(stale['new_obligations'])


class ProfileAndAdapterTests(unittest.TestCase):
    def test_profiles_are_independent_and_fund_the_final_full_committee(self):
        base={'budget':{},'case_budget':{},'planner':{}}
        diagnostic=build_config(base);resolution=build_config(base,profile='agent_resolution')
        self.assertEqual(diagnostic['budget']['max_planner_steps'],3)
        self.assertNotIn('apply_reviewed_revision',diagnostic['planner']['allowed_tools'])
        self.assertIn('apply_reviewed_revision',resolution['planner']['allowed_tools'])
        self.assertGreater(resolution['case_budget']['llm_calls'],diagnostic['budget']['max_llm_calls_per_candidate'])
        self.assertEqual(base,{'budget':{},'case_budget':{},'planner':{}})

    def test_datatype_definitions_survive_the_adapter(self):
        from ontologyBuild.ontology_adapter import iter_datatype_properties
        ontology={'data_properties':{'temperature':{'definition':'A measured thermal quantity.',
                                                   'description':'Measured in kelvins.','datatype':'float'}}}
        prop=list(iter_datatype_properties(ontology))[0]
        self.assertEqual(prop['definition'],'A measured thermal quantity.')
        self.assertEqual(prop['description'],'Measured in kelvins.')

    def test_same_code_different_targets_are_not_closed_together(self):
        from review_agent.issue_ledger import IssueLedger
        state=AgentState({'candidate_id':'sensor','revision':1},{},'audit')
        ledger=IssueLedger(state)
        a=ledger.open('DIRECTION_MISMATCH','head direction',True,target={'field':'head'},dimensions=['expression'])
        b=ledger.open('DIRECTION_MISMATCH','tail direction',True,target={'field':'tail'},dimensions=['expression'])
        self.assertNotEqual(a['issue_id'],b['issue_id'])
        ledger.resolve_target(a['issue_id'],['expression'],resolution_id='exact-proof',
                              proposal={'proposal_digest':'p'},evidence_ids=['source'])
        self.assertEqual(a['status'],'resolved');self.assertEqual(b['status'],'open')

    def test_technical_unknown_is_visible_without_overriding_complete_semantic_judgment(self):
        from review_agent.resolution_status import classify
        result=classify({'review_completed':True,'recommendation':'admit'},[
            {'task_type':'planner_action','execution_unknown':True,'usage_unknown':True,'semantic_dependency':False}])
        self.assertEqual(result['semantic_status'],'completed')
        self.assertEqual(result['required_human_action'],'quality_sampling')
        self.assertTrue(result['technical_incomplete']);self.assertTrue(result['end_to_end_incomplete'])

    def test_known_invalid_committee_material_is_a_technical_task_even_with_known_usage(self):
        from review_agent.resolution_status import classify
        result=classify({'review_completed':False,'recommendation':'manual_review',
            'dimension_results':{'source':{'required_seats':3}},'whole_vote_eligible_voter_ids':['a','b']},[])
        self.assertEqual(result['required_human_action'],'technical_recovery')
        self.assertTrue(result['technical_incomplete'])


class ResolutionRuntimeTests(unittest.TestCase):
    def setUp(self):
        from tests.test_review_four_axes import FourAxisIntegrationTests
        self.f=FourAxisIntegrationTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_fixed_initial_steps_use_no_planner_calls_and_technical_cost_is_separate(self):
        from review_agent.orchestrator import ReviewOrchestrator
        c,_,old=self.f.run_case(version='validator_response_v8')
        config=build_config(self.f.fixture.config,profile='agent_resolution')
        config['budget']['max_retries_per_request']=0
        def finish(payload):
            self.assertTrue(payload['ontology_checked']);self.assertIsNotNone(payload['current_validation'])
            return {'action':'finish_review','arguments':{'stop_reason':'await formal mapping','unresolved_issues':[],'evidence_ids':[]},
                    'target_issue':'mapping','rationale_summary':'cannot invent a definition'}
        planner=MockLLMClient(finish,model_id='planner',model_family='planner')
        o=ReviewOrchestrator(self.f.fixture.repo,old.ontology_guard,old.validators,config=config,planner_client=planner)
        result=o.audit_candidate(c['candidate_id'],c['revision'],self.f.fixture.index)
        self.assertEqual(len(planner.calls),1,result)
        self.assertEqual(result['required_human_action'],'shared_definition_or_mapping_review')
        self.assertEqual(result['review_outcome']['technical_status'],'pending') # Mock usage is deliberately missing.
        self.assertEqual(result['review_outcome']['semantic_status'],'waiting_mapping')
        self.assertFalse(result['review_outcome']['writes_authorized'])

    def test_missing_finding_gets_one_separate_semantic_reassessment_without_changing_invalid_original(self):
        from review_agent.runtime import AgentRuntime
        from review_agent.orchestrator import ReviewOrchestrator
        c,_,old=self.f.run_case(version='validator_response_v8')
        config=build_config(self.f.fixture.config,profile='agent_resolution');config['budget']['max_retries_per_request']=0
        # A completed, intelligible failure is distinct from a 504 or absent response.
        for member in old.validators:
            base=member['client']._callable
            def response(payload,base=base):
                raw=base(payload)
                if payload.get('correction_feedback',{}).get('kind')!='independent_semantic_reassessment':
                    raw['dimensions']['entity_reference']['status']='unknown'
                    raw['expression_findings']=[]
                return raw
            member['client']._callable=response
        o=ReviewOrchestrator(self.f.fixture.repo,old.ontology_guard,old.validators,config=config,planner_client=None)
        runtime=AgentRuntime(o);runtime.initialize(c['candidate_id'],c['revision'],self.f.fixture.index,{})
        runtime.tools.inspect_ontology(None,'initial source-bound check');runtime.tools.run_validation()
        before=deepcopy(runtime.repository.get_audit(c['candidate_id'],runtime.state.audit_id)['votes'])
        self.assertTrue(all(v['response_status']=='invalid_contract' for v in before),before)
        result=runtime.tools.review_semantic_contract('a')
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(runtime.repository.get_audit(c['candidate_id'],runtime.state.audit_id)['votes'],before)
        count=len(old.validators[0]['client'].calls)
        from review_agent.planner_contracts import ActionError
        with self.assertRaises(ActionError):runtime.tools.review_semantic_contract('a')
        self.assertEqual(len(old.validators[0]['client'].calls),count)

    def test_mapping_type_must_be_proposed_from_source_and_legal_ids(self):
        from review_agent.runtime import AgentRuntime
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.planner_contracts import ActionError
        c,_,old=self.f.run_case(version='validator_response_v8')
        config=build_config(self.f.fixture.config,profile='agent_resolution')
        o=ReviewOrchestrator(self.f.fixture.repo,old.ontology_guard,old.validators,config=config,planner_client=None)
        runtime=AgentRuntime(o);runtime.initialize(c['candidate_id'],c['revision'],self.f.fixture.index,{})
        runtime.tools.inspect_ontology('R_old','read complete target definition')
        ids=[e['evidence_id'] for e in runtime.state.bundle['items']]
        common={'relation_id':'R_old','head_type':'C','tail_type':'C','identity_evidence':{'head':ids,'tail':ids},
            'type_evidence':{s:{'basis':'domain_range','evidence_ids':ids,'note':'only a constraint'} for s in ('head','tail')},
            'competing_relations':[],'evidence_ids':ids,'reason':'test typed mapping'}
        with self.assertRaises(ActionError):runtime.tools.propose_mapping(**common)
        common['type_evidence']={s:{'basis':'source','evidence_ids':ids,'note':'MOCK explicit source entity type requiring independent review'} for s in ('head','tail')}
        result=runtime.tools.propose_mapping(**common)
        self.assertEqual(result['status'],'draft_proposed')
        self.assertEqual(runtime.repository.get_revision(c['candidate_id'])['revision'],c['revision'])
        self.assertIn('mapping_rationale',result['draft'])

    def execute_source_closure(self,affects_qualifiers=False):
        from document.hierarchy_index import build_document_index,split_markdown_text,persist_document_index
        from extract_triplets.candidate_contract import normalize_candidate
        from extract_triplets.pipeline_v4 import source_scope
        from review_agent.admission import canonicalize_revision
        from review_agent.orchestrator import ReviewOrchestrator
        from tests.test_claim_disputes import FrozenDisputeIntegrationTests
        _,_,old=self.f.run_case(version='validator_response_v8')
        f=self.f.fixture
        text='# Initial statement\nA regulates B.\n\n# Mechanism\nThe mechanism of A regulates B directly under the stated operating conditions.'
        index=build_document_index(split_markdown_text(text,'resolution-source',90,0))
        path=persist_document_index(index,f.folder/'resolution-source.json')
        f.repo.create_run({'run_id':'resolution-source','mode':'shadow','document_path':str(path),
            'source_index_hash':index['index_content_hash'],'ontology_path':str(f.base.snapshot_path),
            'ontology_hash':f.base.manifest['ontology_version'],'candidate_contract_version':'extraction_candidate_v4'})
        raw={**deepcopy(f.candidate['triple']),'evidence_quotes':['A regulates B.']}
        c=normalize_candidate(raw,index=index,scope=source_scope(index,index['chunks'][0]),ontology=f.base.ontology,
            release_binding=f.base.binding(),run_id='resolution-source',ordinal=0,prompt_hash='fixture')
        c['triple'].update(relation_id='R_old',head_type='C',tail_type='C')
        c=canonicalize_revision(f.repo,c);f.repo.add_candidate(c)
        for member in old.validators:
            base=member['client']._callable
            def respond(payload,base=base):
                if payload.get('version')=='candidate_dispute_review_v1':
                    items=payload['evidence_bundle']['items'];eid=next(e['evidence_id'] for e in items if 'mechanism' in e['quote'].lower())
                    return {'dispute_id':payload['dispute']['dispute_id'],'answer':'supports',
                        'positive_support':{'status':'found','evidence_ids':[eid]},
                        'counterevidence_search':{'status':'none_found_in_reviewed_scope','evidence_ids':[]},
                        'reviewed_evidence_ids':[e['evidence_id'] for e in items],'definition_ids':[],
                        'note':'MOCK direct predicate confirmed against the additional indexed mechanism',
                        'other_affected_checks':['qualifiers'] if affects_qualifiers and payload['dispute']['check']=='relation_semantics' else []}
                result=base(payload);result['dimensions']['mapping'].update(status='matched',relation_id='R_old')
                return FrozenDisputeIntegrationTests.disagree(payload,result)
            member['client']._callable=respond
        hashes=[]
        def plan(payload):
            if not hashes:
                audit=f.repo.get_audit(c['candidate_id'],payload['audit_id']);hashes.append(sha256_json(audit['votes']))
                return {'action':'search_evidence','arguments':{'scope':'other_section','query':'mechanism','purpose':'check_support','limit':3},
                        'target_issue':'specific-relation','rationale_summary':'read the related mechanism in the next source section'}
            return {'action':'finish_review','arguments':{'stop_reason':'all covered obligations checked','unresolved_issues':[],'evidence_ids':[]},
                    'target_issue':'finish','rationale_summary':'program validates remaining obligations'}
        config=build_config(f.config,profile='agent_resolution');config['budget']['max_retries_per_request']=0
        o=ReviewOrchestrator(f.repo,old.ontology_guard,old.validators,config=config,planner_client=MockLLMClient(plan,model_id='planner',model_family='planner'))
        result=o.audit_candidate(c['candidate_id'],1,index)
        audit=f.repo.get_audit(c['candidate_id'],result['audit_id'])
        self.assertEqual(sha256_json(audit['votes']),hashes[0])
        self.assertTrue(result['resolution_snapshot']['accepted_resolutions'],result)
        self.assertEqual(result['review_outcome']['recommendation'],'admit',result['review_outcome'])
        self.assertEqual(result['review_outcome']['semantic_status'],'completed')
        self.assertEqual(result['decision'],'manual_review') # Existing production policy remains unchanged.
        if affects_qualifiers:
            self.assertIn('qualifiers',{r['check'] for r in result['resolution_snapshot']['accepted_resolutions']})
        from review_agent.resolution_recovery import project_context,ReadView
        from review_agent.review_observability import outcome
        from evaluation.review_samples import ReadOnlyRepository
        import hashlib
        before=hashlib.sha256(f.repo.database_path.read_bytes()).hexdigest()
        calls=sum(len(v['client'].calls) for v in old.validators)
        interrupted=deepcopy(audit);interrupted['status']='timeout'
        repository=ReadOnlyRepository(f.repo.database_path)
        context=project_context(repository,interrupted,o.committee)
        projection=outcome(audit['rounds'][0],audit['votes'],o.committee,context=context,audit_status='timeout')
        self.assertEqual(projection['recommendation'],'admit',projection['blocking_reasons'])
        self.assertEqual(projection['execution_status'],'timed_out')
        self.assertEqual(projection['technical_status'],'pending')
        self.assertEqual(context['model_calls_during_projection'],0)
        with repository.connect() as connection:
            read_context=project_context(ReadView(connection),interrupted,o.committee)
        self.assertEqual(read_context['resolution_snapshot'],context['resolution_snapshot'])
        self.assertEqual(sum(len(v['client'].calls) for v in old.validators),calls)
        self.assertEqual(hashlib.sha256(f.repo.database_path.read_bytes()).hexdigest(),before)
        self.assertEqual(sha256_json(f.repo.get_audit(c['candidate_id'],audit['audit_id'])['votes']),hashes[0])
        self.last_case={'mode':'source_closure','candidate':c,'final':result,'source_index':index,
            'original_votes_unchanged':True,'read_only_projection':projection,'real_model_calls':0,'human_labels':0}

    def test_agent_supplements_a_real_index_then_closes_exact_dispute_without_replacing_original_votes(self):
        self.execute_source_closure()

    def test_new_source_dependency_gets_its_own_scoped_review_before_closure(self):
        self.execute_source_closure(affects_qualifiers=True)

    def test_shared_definition_tasks_aggregate_and_enter_publication_impact_without_copying_votes(self):
        from review_agent.definition_tasks import sync
        from ontology_review.impact import analyze_impact
        from ontology_review.tools import remap
        c,_,_=self.f.run_case(version='validator_response_v8')
        f=self.f.fixture;states=[]
        for n in range(2):
            candidate=deepcopy(c);candidate['candidate_id']+='_shared_'+str(n);f.repo.add_candidate(candidate)
            state=AgentState(candidate,{},'source-audit-'+str(n))
            state.bundle={'evidence_hash':'bound-source','items':[]}
            state.ontology_lookup={'mapping_preflight':{'relation_candidates':[{'id':'R_old','missing_information':['definition_missing_in_bound_release']}],
                'search_trace':[{'exact_match_ids':['R_old']}]}}
            states.append(state)
        first=sync(f.repo,states[0])[0];second=sync(f.repo,states[1])[0]
        self.assertEqual(first['task_id'],second['task_id']);self.assertEqual(second['affected_candidates'],2)
        after=deepcopy(f.base.ontology);after['object_properties']['R_old']['definition']='MOCK revised shared definition'
        impact=analyze_impact(f.repo,c['ontology_release_id'],f.base.ontology,after,remap)
        affected={r['candidate_id'] for r in impact['affected_candidates']}
        self.assertTrue({s.candidate['candidate_id'] for s in states}<=affected)
        self.assertTrue(all(not r['old_admission_reused'] for r in impact['affected_candidates']))
