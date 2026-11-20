"""Offline regressions for concrete recovery, combined drafts and scoped classification."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from review_agent.agent_state import AgentState,AgentBudget
from review_agent.action_policy import ActionPolicy
from review_agent.action_availability import search_key,required_budget
from review_agent.contracts import sha256_json
from review_agent.experiment_profiles import build_config,describe
from review_agent.planner_contracts import PolicyRejection
from review_agent.resolution_obligations import resolution_obligations,ready_action,targeted_supplement


class FundedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.state=AgentState({'candidate_id':'case','revision':1,'document_version':'v','triple':{
            'head':'A','surface_relation':'connects','tail':'B'}},{
                'resolution_snapshots_enabled':True,'resolution_allowed_tools':['search_evidence','finish_review'],
                'agent_resolution_policy':{'max_attempts_per_issue':2,'reserve_final_committee_calls':3},
                'dispute_baseline':{'proposal_digest':'p'},'dispute_source_evidence_ids':['e'],
                'dispute_report':{'disputes':[{'dispute_id':'question','check':'triple_support','status':'open'}]}},'audit')
        self.state.ontology_checked=True
        self.state.archive=[{'evidence_id':'e','quote':'A connects B.','anchor_valid':True}]
        self.state.bundle={'items':deepcopy(self.state.archive),'evidence_hash':'source'}
        self.budget=AgentBudget({'max_llm_calls_per_candidate':0,'max_tool_calls':4,'total_timeout_seconds':20,'max_planner_steps':0})
        self.policy=ActionPolicy({'allowed_tools':['search_evidence','finish_review']})
        self.finish={'action':'finish_review','arguments':{'stop_reason':'done','unresolved_issues':[],'evidence_ids':[]},
            'target_issue':'finish','rationale_summary':'done'}

    def test_local_supplement_needs_no_model_calls_and_survives_planner_exhaustion(self):
        obligations=resolution_obligations(self.state,self.budget)
        self.assertEqual(obligations[0]['required_budget']['model_calls'],0)
        action=ready_action(self.state,self.budget)
        self.assertEqual(action['action'],'search_evidence')
        self.policy.validate_request(action,self.state,self.budget)
        self.assertEqual(self.budget.usage['llm_calls'],0)
        self.budget.usage['tool_calls']=4
        self.assertFalse(any(o['legal_next_tool'] for o in resolution_obligations(self.state,self.budget)))

    def test_schema_and_runtime_reject_the_same_premature_finish_with_targets(self):
        from review_agent.planner_view import planner_view
        view=planner_view(self.state,self.budget,self.policy.definitions())
        self.assertNotIn('finish_review',view['output_schema']['properties']['action']['enum'])
        with self.assertRaises(PolicyRejection) as error:self.policy.validate_request(self.finish,self.state,self.budget)
        self.assertEqual(error.exception.feedback['legal_actions'][0]['target_issue'],'question')
        from review_agent.planner_requests import proposal_for,current_action
        proposal=proposal_for(self.state,view,self.policy.definitions())
        receipt={'response_status':'ok','input_hash':proposal['input_hash'],'action':self.finish}
        action,feedback=current_action(receipt,proposal,self.state,lambda a:self.policy.validate_request(a,self.state,self.budget))
        self.assertIsNone(action);self.assertEqual(feedback['code'],'funded_recovery_remaining')
        self.assertEqual(receipt['response_status'],'ok')

    def test_empty_scope_does_not_close_other_scope_issue_or_new_evidence(self):
        first=targeted_supplement(self.state,'one')
        self.state.context['resolution_search_history']={search_key(first,self.state):{'status':'empty'}}
        self.assertEqual(targeted_supplement(self.state,'one')['arguments']['scope'],'parent_section')
        self.assertEqual(targeted_supplement(self.state,'two')['arguments']['scope'],'same_section')
        self.state.archive.append({'evidence_id':'new','quote':'A and B are devices.','anchor_valid':True})
        self.assertEqual(targeted_supplement(self.state,'one')['arguments']['scope'],'same_section')

    def test_focus_cost_uses_actual_seats_and_whitelist_is_obeyed(self):
        self.state.context['committee_members']=[{'id':str(i)} for i in range(5)]
        self.assertEqual(required_budget('review_dispute',self.state)['model_calls'],5)
        self.assertFalse(any(o['legal_next_tool'] for o in resolution_obligations(self.state,self.budget,{'finish_review'})))


class QualifierAndProfileTests(unittest.TestCase):
    def test_unassigned_mapping_can_be_proposed_from_recalled_definitions_and_source(self):
        from tests.test_mapping_preflight import MappingPreflightTests
        from review_agent.mapping_preflight import prepare,policy_for
        fixture=MappingPreflightTests('runTest');fixture.setUp()
        fixture.schema['classes']['gun'].update(aliases=['弹丸'],definition='弹丸是此处运动的设备。')
        fixture.schema['classes']['force']['definition']='阻力是阻碍运动的力。'
        fixture.candidate['triple'].update(relation_id=None,head_type=None,tail_type=None)
        fixture.policy=policy_for({'mapping_preflight':{'enabled':True,'revision_proposals':True}})
        before=sha256_json(fixture.candidate)
        guard,context=fixture.setup_context()
        # setup_context binds the version, so compare after that explicit fixture binding.
        before=sha256_json(fixture.candidate)
        selected,report=prepare(guard,fixture.candidate,fixture.bundle,context,fixture.context_policy,fixture.policy)
        self.assertTrue(report['proposal_ready'],report['proposal_blocking_information'])
        self.assertFalse(report['judgment_ready']);self.assertEqual(report['version'],'mapping_preflight_v2')
        self.assertTrue({'gun','force'}<={c['id'] for c in selected['classes']})
        self.assertEqual(sha256_json(fixture.candidate),before)
        event=deepcopy(fixture.candidate);event['triple']['tail_kind']='event'
        _,event_report=prepare(guard,event,fixture.bundle,context,fixture.context_policy,fixture.policy)
        self.assertFalse(event_report['proposal_ready'])
        self.assertIn('bound_property_signatures_cannot_represent_event_or_proposition',event_report['proposal_blocking_information'])
        missing=deepcopy(fixture.bundle);missing['items'][0]['quote']='未给出实体指代。';missing['evidence_hash']=sha256_json(missing['items'])
        _,report=prepare(guard,fixture.candidate,missing,context,fixture.context_policy,fixture.policy)
        self.assertFalse(report['proposal_ready'])

    def test_not_stated_requires_real_scope_and_polarity_cannot_be_defaulted(self):
        from review_agent.qualifier_completion import validate_completion_action
        from extract_triplets.candidate_contract import QUALIFIER_FIELDS
        candidate={'contract_version':'extraction_candidate_v4','triple':{'head':'A','surface_relation':'connects','tail':'B',
            'qualifiers':{k:'unknown' if k=='polarity' else None for k in QUALIFIER_FIELDS},
            'qualifier_status':{k:'unknown' for k in QUALIFIER_FIELDS}}}
        action={'action':'propose_revision','target_issue':'condition','rationale_summary':'source attachment',
            'arguments':{'changes':{'qualifiers':{'condition':None},'qualifier_status':{'condition':'not_stated'}},
                'evidence_ids':['e'],'reason':'complete source reviewed','field_evidence':{'condition':['e']},
                'field_notes':{'condition':'Complete statement states no condition.'}}}
        bundle={'items':[{'evidence_id':'e','anchor_valid':True,'quote':'A connects B.'}]}
        with self.assertRaisesRegex(ValueError,'checked source scope'):
            validate_completion_action(action,candidate,bundle,require_grounding=True,checked_scopes={'same_section'})
        action['arguments']['reviewed_scopes']={'condition':{'scope':'same_section','evidence_ids':['e']}}
        validate_completion_action(action,candidate,bundle,require_grounding=True,checked_scopes={'same_section'})
        with self.assertRaisesRegex(ValueError,'has not been inspected'):
            validate_completion_action(action,candidate,bundle,require_grounding=True,checked_scopes=set())
        action['arguments']['changes']={'qualifiers':{'polarity':'unknown'},'qualifier_status':{'polarity':'extracted'}}
        with self.assertRaisesRegex(ValueError,'polarity requires'):
            validate_completion_action(action,candidate,bundle)

    def test_profile_freezes_contracts_permissions_and_whole_revision_budget(self):
        base={'planner':{},'budget':{},'case_budget':{}}
        config=build_config(base,profile='handoff_revision');description=describe(config)
        self.assertGreaterEqual(config['case_budget']['llm_calls'],3+8+3+6)
        self.assertIn('apply_reviewed_revision',description['effective_tools'])
        self.assertNotIn('request_ontology_extension',description['effective_tools'])
        self.assertFalse(config['ontology_extension']['enabled']);self.assertFalse(config['neo4j']['auto_import_enabled'])
        self.assertEqual(description['scope_review_contract']['version'],'candidate_scope_review_v2')
        self.assertTrue(config['mapping_preflight']['revision_proposals'])
        self.assertEqual(base,{'planner':{},'budget':{},'case_budget':{}})

    def test_combined_mapping_and_qualifier_draft_has_one_review_and_fresh_final_ballot(self):
        from tests.test_resolution_workflows import ResolutionWorkflowTests
        import tests.test_resolution_workflows as workflows
        fixture=ResolutionWorkflowTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        original_normalize=workflows.normalize_candidate;original_client=workflows.MockLLMClient
        def normalize(*args,**kwargs):
            candidate=original_normalize(*args,**kwargs)
            candidate['triple']['qualifiers']['modality']=None
            candidate['triple']['qualifier_status']['modality']='unknown'
            return candidate
        def client(responses,**kwargs):
            if kwargs.get('model_id')=='fixture_planner' and callable(responses):
                original=responses
                def wrapped(payload):
                    action=original(payload)
                    if action['action']=='propose_mapping':
                        ids=action['arguments']['evidence_ids']
                        action['arguments'].update(qualifier_changes={'qualifiers':{'modality':'asserted'},
                            'qualifier_status':{'modality':'extracted'}},field_evidence={'modality':ids},
                            field_notes={'modality':'The complete source states an affirmative connection between these same devices.'})
                    return action
                responses=wrapped
            return original_client(responses,**kwargs)
        with (patch.object(workflows,'build_config',lambda base,**kw:build_config(base,profile='handoff_revision')),
                patch.object(workflows,'normalize_candidate',normalize),patch.object(workflows,'MockLLMClient',client)):
            result=fixture.execute_case('mapping')
        self.assertEqual(result['final']['review_outcome']['recommendation'],'admit')
        with fixture.f.repo.connect() as c:
            drafts=c.execute('SELECT draft_json FROM revision_drafts WHERE candidate_id=?',(result['candidate']['candidate_id'],)).fetchall()
        import json
        self.assertEqual(len(drafts),1);draft=json.loads(drafts[0][0])
        self.assertIn('qualifiers.modality',draft['field_evidence']);self.assertIn('mapping_rationale',draft)
        self.assertEqual(draft['before']['triple']['surface_relation'],draft['after']['triple']['surface_relation'])
        case_id=fixture.f.repo.ensure_case(result['candidate']['candidate_id'])
        account=fixture.f.repo.case_status(case_id)
        self.assertEqual(account['limits']['llm_calls'],32)
        self.assertGreaterEqual(account['usage']['llm_calls'],9)
        self.assertLessEqual(account['usage']['llm_calls'],32)

    def test_isolated_prepare_supports_multiple_source_runs_without_model_calls(self):
        from tests.test_ontology_extension_agent import OntologyExtensionTests
        from evaluation.agent_resolution import prepare,verify
        from pathlib import Path
        from uuid import uuid4
        import json,shutil
        fixture=OntologyExtensionTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        source=fixture.repo;second=deepcopy(source.get_revision(fixture.candidate['candidate_id']))
        second.update(candidate_id='isolated-second-candidate',run_id='second-source-run')
        source.create_run({**source.get_run('run'),'run_id':second['run_id']});source.add_candidate(second)
        candidates=[source.get_revision(fixture.candidate['candidate_id']),source.get_revision(second['candidate_id'])]
        data=fixture.folder/'bound-population.json'
        data.write_text(json.dumps({'source_database':str(source.database_path),'selected_raw_candidates':candidates,
            'bindings':[{'sample_id':'s'+str(i),'candidate_id':c['candidate_id'],'revision':c['revision'],
                'candidate_content_hash':sha256_json(c)} for i,c in enumerate(candidates)]}),encoding='utf-8')
        output=Path(__file__).resolve().parents[1]/'outputs'/('handoff_prepare_test_'+uuid4().hex)
        self.addCleanup(lambda:shutil.rmtree(output) if output.exists() else None)
        frozen=prepare(output,limit=2,data_path=data,profile='handoff_revision')
        self.assertEqual(verify(output)[0]['freeze_hash'],frozen['freeze_hash'])
        self.assertEqual(frozen['limits']['total_calls_upper_bound'],64)
        from review_agent.repository import ReviewRepository
        isolated=ReviewRepository(output/'review.sqlite3')
        self.assertEqual({isolated.get_revision(c['candidate_id'])['run_id'] for c in candidates},{'run','second-source-run'})
        with isolated.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM request_attempts').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM audit_runs').fetchone()[0],0)


class ScopeResolutionTests(unittest.TestCase):
    def setUp(self):
        from tests.test_review_four_axes import FourAxisIntegrationTests
        from review_agent.runtime import AgentRuntime
        from review_agent.orchestrator import ReviewOrchestrator
        f=FourAxisIntegrationTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        def disagree(payload,raw):
            raw['dimensions']['knowledge_scope']['status']='example' if payload['fixture_voter_id']=='c' else 'general'
            return raw
        candidate,_,old=f.run_case(version='validator_response_v8',opinion_override=disagree)
        config=build_config(f.fixture.config,profile='handoff_revision')
        if self._testMethodName=='test_weighted_exclusion_completes_without_mapping_but_pending_evidence_blocks_it':
            config['vote_weighting']={'mode':'dynamic'}
        o=ReviewOrchestrator(f.fixture.repo,old.ontology_guard,old.validators,config=config,planner_client=None)
        runtime=AgentRuntime(o);runtime.initialize(candidate['candidate_id'],1,f.fixture.index,{})
        runtime.tools.inspect_ontology(None,'frozen definitions');runtime.tools.run_validation()
        audit=f.fixture.repo.get_audit(candidate['candidate_id'],runtime.state.audit_id)
        self.base=audit['rounds'][0];self.votes=audit['votes'];self.committee=o.committee
        self.runtime=runtime
        self.bundle=deepcopy(self.base['evidence_bundle'])
        self.bundle['items'].append({'evidence_id':'extra','quote':'For this example only, A regulates B.',
            'anchor_valid':True,'substantive':True})
        self.bundle['evidence_hash']=sha256_json({k:v for k,v in self.bundle.items() if k!='evidence_hash'})

    def focus(self,scopes=('example','example','example')):
        from review_agent.disputes import build_report
        from review_agent.dispute_review import binding,SCOPE_VERSION
        from review_agent.resolution_state import VERSION
        dispute=next(d for d in build_report(self.base,self.votes)['disputes'] if d['check']=='knowledge_scope')
        self.assertEqual(dispute['status'],'open')
        p={'version':SCOPE_VERSION,'audit_id':self.base['audit_id'],'candidate_id':self.base['candidate']['candidate_id'],
            'candidate_revision':1,'candidate_hash':sha256_json(self.base['candidate']),
            'source_proposal_digest':self.base['proposal_digest'],
            'dispute':{k:deepcopy(dispute[k]) for k in ('dispute_id','claim','check','issue_type','question','route')},
            'evidence_bundle':deepcopy(self.bundle),'protected_evidence_ids':[e['evidence_id'] for e in self.base['evidence_bundle']['items']],
            'ontology_context':deepcopy(self.base.get('ontology_context')),'committee_members':deepcopy(self.base['committee_members']),
            'committee_policy':deepcopy(self.base['committee_policy']),'resolution_policy':VERSION,
            'scope_policy':deepcopy(self.base['review_scope_policy'])}
        p['proposal_digest']=sha256_json(p)
        votes=[{**binding(m,p),'response_status':'ok','dispute_id':dispute['dispute_id'],'knowledge_scope':s,
            'evidence_ids':['extra'],'reviewed_evidence_ids':[e['evidence_id'] for e in self.bundle['items']],
            'other_affected_checks':[],'note':'Checked exact claim and example boundary.'}
            for m,s in zip(p['committee_members'],scopes)]
        return {'review_proposal':p,'dispute_id':dispute['dispute_id'],'votes':votes}

    def snapshot(self,result):
        from review_agent.resolution_state import resolve_review_state
        return resolve_review_state(self.base,self.votes,[result],[],self.committee,bundle=self.bundle)

    def test_confirmed_exclusion_updates_category_and_skips_main_graph_mapping(self):
        from review_agent.resolution_state import apply_consensus
        before=sha256_json(self.votes);snapshot=self.snapshot(self.focus())
        self.assertTrue(snapshot['scope_exclusion_complete'],snapshot)
        result=apply_consensus({'review_axes':{}},snapshot)
        self.assertEqual(result['knowledge_scope'],'example');self.assertTrue(result['scope_confirmed'])
        self.assertEqual(result['review_axes']['scope']['status'],'exclude')
        self.assertEqual(sha256_json(self.votes),before)

    def test_include_exclude_disagreement_and_changed_frozen_policy_stay_unresolved(self):
        from review_agent.dispute_review import focused_consensus
        result=self.focus(('example','example','general'))
        self.assertEqual(focused_consensus(result['votes'],result['review_proposal'])['status'],'human_review')
        self.assertFalse(self.snapshot(result)['accepted_resolutions'])
        result=self.focus();result['review_proposal']['scope_policy']['excluded_scopes']=[]
        p=result['review_proposal'];p['proposal_digest']=sha256_json({k:v for k,v in p.items() if k!='proposal_digest'})
        self.assertFalse(self.snapshot(result)['accepted_resolutions'])

    def test_general_and_conditional_are_equivalent_only_when_both_included(self):
        from review_agent.dispute_review import focused_consensus
        result=self.focus(('general','conditional','general'))
        consensus=focused_consensus(result['votes'],result['review_proposal'])
        self.assertEqual(consensus['classification']['status'],'include');self.assertTrue(consensus['policy_equivalent'])
        self.assertEqual(self.snapshot(result)['dimension_results']['scope']['status'],'include')

    def test_scope_request_uses_versioned_contract_and_three_independent_mock_receipts(self):
        from review_agent.dispute_review import review,SCOPE_VERSION
        from review_agent.resolution_state import VERSION
        from review_agent.llm_clients import MockLLMClient,LLMResponse
        proposal=self.focus()['review_proposal'];voters=[]
        for member in self.base['committee_members']:
            def respond(payload,identity=member['id']):
                self.assertEqual(payload['version'],SCOPE_VERSION)
                self.assertIn('knowledge_scope',payload['output_contract']['required'])
                return LLMResponse({'dispute_id':payload['dispute']['dispute_id'],'knowledge_scope':'example',
                    'evidence_ids':['extra'],'reviewed_evidence_ids':[e['evidence_id'] for e in payload['evidence_bundle']['items']],
                    'other_affected_checks':[],'note':'The section explicitly limits this claim to the example.'},
                    input_tokens=20,output_tokens=10,returned_model_id=identity)
            voters.append({**member,'client':MockLLMClient(respond,model_id=member['model_id'],model_family=member['model_family'])})
        budget=AgentBudget({'max_llm_calls_per_candidate':3,'max_evidence_chars':24000,'total_timeout_seconds':30})
        result=review(self.runtime.repository,self.base,proposal['dispute'],self.bundle,voters,self.committee,budget,
            ontology_context=self.base.get('ontology_context'),resolution_policy=VERSION)
        self.assertEqual(result['status'],'resolved_scope',result)
        self.assertEqual(result['model_calls'],3);self.assertEqual(budget.remaining_calls(),0)
        self.assertTrue(self.snapshot(result)['scope_exclusion_complete'])

    def test_weighted_exclusion_completes_without_mapping_but_pending_evidence_blocks_it(self):
        from review_agent.resolution_state import apply_consensus
        from review_agent.review_observability import outcome
        result=self.focus();snapshot=self.snapshot(result)
        self.assertTrue(snapshot['scope_exclusion_complete'],snapshot)
        consensus=apply_consensus({},snapshot)
        self.assertTrue(consensus['weighted_review_complete'])
        context={'resolution_snapshots_enabled':True,'focused_review_results':[result],
            'resolution_bundle':self.bundle,'issue_history':[]}
        report=outcome(self.base,self.votes,self.committee,context=context)
        self.assertTrue(report['review_completed'],report['blocking_reasons'])
        self.assertEqual(report['research_operation'],'discard')
        incomplete=deepcopy(self.bundle)
        incomplete['items'].append({'evidence_id':'unreviewed','quote':'An additional unresolved condition applies.',
            'anchor_valid':True,'substantive':True})
        incomplete['evidence_hash']=sha256_json({k:v for k,v in incomplete.items() if k!='evidence_hash'})
        report=outcome(self.base,self.votes,self.committee,context={**context,'resolution_bundle':incomplete})
        self.assertFalse(report['review_completed']);self.assertEqual(report['research_operation'],'manual_review')


if __name__=='__main__':unittest.main()
