"""Missing acceptance scenarios, with production state kept outside the fixtures."""
import copy
import itertools
import json
import unittest
from pathlib import Path
from uuid import uuid4


def ready_triple_runner(fixture,voters,*,config=None,planner=None):
    from types import SimpleNamespace
    from confidence.risk_policy import RiskPolicy
    from review_agent.orchestrator import ReviewOrchestrator
    from review_agent.ontology_guard import OntologyGuard,load_snapshot
    settings={'mode':'active','planner':{'enabled':False,'strategy':'deterministic'},'routing':{'auto_accept_enabled':True},
        'consensus':{'committee_size':3,'required_votes':3,'minimum_distinct_families':3},
        'budget':{'max_llm_calls_per_candidate':20,'max_planner_steps':6,'max_validation_rounds':2,'max_tool_calls':8,'max_retries_per_request':0,'total_timeout_seconds':60}}
    settings.update(config or {})
    return ReviewOrchestrator(fixture.repo,OntologyGuard(load_snapshot(fixture.release.snapshot_path)),voters,config=settings,
        planner_client=planner,risk_policy=RiskPolicy({'enabled':True,'validated':True,'auto_accept_enabled':True,'accept_threshold':.9,'policy_version':'mock_validated_fixture'}),
        calibrator=SimpleNamespace(ready=True,predict_if_compatible=lambda *_:SimpleNamespace(probability=.99,status='ok',model_version='mock_fixture',compatibility=True)))


class AcceptanceEvaluationTests(unittest.TestCase):
    def test_t36_tied_majority_is_unresolved_for_every_order(self):
        from evaluation.adapters.baselines import simple_majority
        votes = [{'response_status':'ok','verdict':v} for v in ('valid','valid','invalid','invalid','uncertain')]
        for order in itertools.permutations(votes):
            result = simple_majority(order)
            self.assertEqual(result['verdict'],'unresolved',result)
            self.assertEqual(result['population_count'],5)

    def test_t37_unlabeled_failed_and_timeout_rows_keep_population_denominator(self):
        from evaluation.evaluate_review import evaluate
        rows = [{'candidate_id':'a','revision':1,'label':1,'decision':'auto_accept','calibrated_probability':.9},
                {'candidate_id':'b','revision':1,'label':0,'decision':'manual_review'},
                {'candidate_id':'c','revision':1,'status':'timeout'},
                {'candidate_id':'d','revision':1,'status':'failed'},
                {'candidate_id':'e','revision':1,'decision':'manual_review','label':None}]
        result = evaluate(rows)
        self.assertEqual(result['population_count'],5)
        self.assertEqual(result['labeled_count'],2)
        self.assertEqual(result['unlabeled_count'],3)
        self.assertEqual(result['auto_accept_coverage'],.2)
        self.assertEqual(result['system_error_rate'],.4)
        self.assertEqual(result['auto_accept_precision'],1)

    def test_t37_frozen_manifest_retains_missing_results_and_rejects_tampering(self):
        from evaluation.manifest import freeze_manifest, align_results, validate_manifest
        from evaluation.evaluate_review import evaluate
        candidates = [{'candidate_id':str(n),'revision':1,'document_id':'doc','document_version':'v','triple':{'head':'A'}} for n in range(5)]
        manifest = freeze_manifest(candidates,run_id='fixture')
        results = [{'candidate_id':'0','revision':1,'decision':'auto_accept','label':1},
                   {'candidate_id':'1','revision':1,'status':'timeout','label':None}]
        rows = align_results(manifest,results)
        self.assertEqual(len(rows),5)
        self.assertEqual(sum(r['prediction_missing'] for r in rows),3)
        self.assertEqual(evaluate(results,manifest=manifest)['auto_accept_coverage'],.2)
        with self.assertRaises(ValueError): align_results(manifest,[*results,results[0]])
        with self.assertRaises(ValueError): align_results(manifest,[{'candidate_id':'foreign','revision':1}])
        changed = copy.deepcopy(manifest); changed['candidates'].pop()
        with self.assertRaises(ValueError): validate_manifest(changed)


def ontology_fixture(test, *, hierarchy=False):
    from tests.test_ontology_extension_agent import OntologyExtensionTests
    class Fixture(OntologyExtensionTests):
        def artifact(self, schema):
            schema = copy.deepcopy(schema)
            if hierarchy:
                schema['classes']['C_leaf'] = {'id':'C_leaf','label':'special device','definition':'A specialized controllable device.','parents':['C']}
            return super().artifact(schema)

        def source_candidate(self, ordinal=0):
            if not getattr(self,'acceptance_source',False):
                from document.hierarchy_index import build_document_index, split_markdown_text, persist_document_index
                self.index = build_document_index(split_markdown_text("Regulation is a device changing another device's operating state. A regulates B. A connects B.", 'doc',600,20))
                path = persist_document_index(self.index,self.folder/'document.json')
                manifest = self.repo.get_run('run')
                manifest.update(document_path=str(path),source_index_hash=self.index['index_content_hash'])
                with self.repo.transaction(immediate=True) as conn:
                    conn.execute('UPDATE runs SET manifest_json=? WHERE run_id=?',(json.dumps(manifest),'run'))
                self.acceptance_source=True
            return super().source_candidate(ordinal)
    fixture=Fixture(); fixture.setUp(); test.addCleanup(fixture.doCleanups)
    return fixture


class AcceptanceOntologyTests(unittest.TestCase):
    def test_t21_human_can_review_rebound_revision_with_new_bound_source_bundle(self):
        from ontology_review.publisher import OntologyPublisher
        from review_agent.feedback import FeedbackService
        h=ontology_fixture(self); h.review()
        publication=OntologyPublisher(h.repo,h.root,h.config).publish(h.extension_id,h.publication_request())
        current=h.repo.get_candidate(h.candidate['candidate_id'])
        self.assertEqual(current['workflow_state'],'reaudit_required')
        result=FeedbackService(h.repo).submit({'action':'accept','candidate_id':current['candidate_id'],'revision':2,
            'expected_row_version':current['row_version'],'idempotency_key':uuid4().hex,
            'spans':[{'quote':'A regulates B.','role':'support','note':'Explicit current source statement, reviewed under the newly published regulation relation.'}]})
        self.assertEqual(result['candidate']['human_status'],'accepted')
        state=h.repo.current_admitted_contributions(current['candidate_id'])
        self.assertEqual(len(state['desired']),1,state)
        self.assertEqual(state['desired'][0]['candidate']['ontology_release_id'],publication['ontology_release_id'])
        self.assertEqual(state['desired'][0]['candidate']['revision'],2)
        self.assertEqual(state['desired'][0]['decision']['source'],'human_confirmed')
    def test_t38_shadow_triple_with_ready_policy_and_consensus_creates_no_contribution(self):
        from tests.test_admission_graph_sync import AdmissionFixture,ROOT
        from tests.test_review_planner import valid_response
        from review_agent.llm_clients import MockLLMClient
        import shutil
        f=AdmissionFixture(ROOT/'runs/_test_acceptance'/uuid4().hex); self.addCleanup(shutil.rmtree,f.folder)
        c=f.candidate()
        voters=[{'id':v,'model_id':'model_'+v,'model_family':v,'client':MockLLMClient(valid_response,model_id='model_'+v,model_family=v)} for v in 'abc']
        result=ready_triple_runner(f,voters,config={'mode':'shadow'}).audit_candidate(c['candidate_id'],1,f.index)
        self.assertEqual(result['consensus']['result'],'accept_consensus')
        self.assertEqual(result['calibrated_probability'],.99)
        self.assertEqual(result['decision'],'manual_review')
        self.assertIn('NON_ACTIVE_MODE_REQUIRES_HUMAN',result['reason_codes'])
        self.assertFalse(f.repo.current_admitted_contributions(c['candidate_id'])['desired'])
    def test_t39_published_revision_preserves_case_budget_and_stops_next_loop(self):
        from ontology_review.publisher import OntologyPublisher
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.ontology_guard import OntologyGuard,load_snapshot
        from review_agent.agent_state import AgentStopped
        h=ontology_fixture(self); cid=h.candidate['candidate_id']; case_id=h.repo.ensure_case(cid)
        h.review(); published=OntologyPublisher(h.repo,h.root,h.config).publish(h.extension_id,h.publication_request())
        self.assertEqual(h.repo.get_candidate(cid)['revision'],2)
        self.assertEqual(h.repo.ensure_case(cid),case_id)
        account=h.repo.case_status(case_id)
        self.assertEqual(account['usage']['extension_requests'],1)
        self.assertEqual(account['usage']['rebinds'],1)
        with self.assertRaises(AgentStopped): h.repo.charge_cases([case_id],{'extension_requests':1},'second_loop','fixture','new_revision')
        remaining=account['limits']['llm_calls']-account['usage']['llm_calls']
        h.repo.charge_cases([case_id],{'llm_calls':remaining},'consume_remaining','fixture','new_revision')
        calls=[len(v['client'].calls) for v in h.source_voters]
        result=ReviewOrchestrator(h.repo,OntologyGuard(load_snapshot(h.root/published['ontology_release_id']/'ontology_snapshot.json')),
            h.source_voters,config={**h.config,'planner':{'enabled':False,'strategy':'deterministic'}}).audit_candidate(cid,2,h.index)
        self.assertEqual(result['decision'],'manual_review')
        self.assertEqual([len(v['client'].calls) for v in h.source_voters],calls)
        self.assertEqual(h.repo.case_status(case_id)['usage']['llm_calls'],account['limits']['llm_calls'])
    def test_t11_insufficient_source_cannot_trigger_schema_expansion(self):
        from tests.test_review_planner import PlannerRuntimeTests,action,ontology,finish,valid_response
        from review_agent.llm_clients import MockLLMClient
        h=PlannerRuntimeTests(); h.setUp(); self.addCleanup(h.doCleanups)
        def insufficient(payload):
            result=valid_response(payload)
            result.update(verdict='uncertain',source_support='insufficient',supporting_evidence_ids=[],reason_codes=['EVIDENCE_INSUFFICIENT'])
            for check in result['checks']:
                if check['check']!='ontology': check['status']='unknown'
            return result
        for v in h.validators: v['client']=MockLLMClient(insufficient,model_id=v['model_id'],model_family=v['model_family'])
        def plan(payload):
            if not payload['history']: return ontology()
            if not payload['current_validation']: return action('run_validation')
            if payload['history'][-1]['tool']=='run_validation':
                return action('request_ontology_extension',change_kind='revise_constraint',description='Must not expand without source support',
                    proposed_changes={'review_relation_id':'R_measures'},evidence_ids=payload['selected_evidence_ids'][:1])
            return finish()
        result=h.run_script(plan)
        self.assertNotEqual(result['source_support_result'],'supported')
        self.assertTrue(all(v.get('source_support')=='insufficient' for v in h.audit['votes']))
        self.assertTrue(any('source-support' in str(record) for record in h.audit['tool_calls']),h.audit['tool_calls'])
        self.assertIsNone(result['pending_extension_id'])
        with h.repo.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM ontology_extension_tasks').fetchone()[0],0)

    def test_t26_three_seats_one_timeout_do_not_form_triple_consensus(self):
        from tests.test_admission_graph_sync import AdmissionFixture,ROOT
        from tests.test_review_planner import valid_response
        from review_agent.llm_clients import MockLLMClient
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.ontology_guard import OntologyGuard,load_snapshot
        import shutil
        f=AdmissionFixture(ROOT/'runs/_test_acceptance'/uuid4().hex); self.addCleanup(shutil.rmtree,f.folder)
        candidate=f.candidate()
        voters=[{'id':v,'model_id':'model_'+v,'model_family':v,'client':MockLLMClient(valid_response,model_id='model_'+v,model_family=v)} for v in 'abc']
        voters[-1]['client']=MockLLMClient([TimeoutError('fixture timeout')],model_id='model_c',model_family='c')
        result=ReviewOrchestrator(f.repo,OntologyGuard(load_snapshot(f.release.snapshot_path)),voters,
            config={'mode':'shadow','consensus':{'committee_size':3,'required_votes':3,'minimum_distinct_families':3},
            'budget':{'max_retries_per_request':0}}).audit_candidate(candidate['candidate_id'],1,f.index)
        self.assertEqual(result['consensus']['committee_size'],3)
        self.assertEqual(result['consensus']['valid_votes'],2)
        self.assertEqual(result['consensus']['error_votes'],1)
        self.assertEqual(result['admission_consensus']['result'],'unresolved')
    def test_t18_operator_rejection_updates_subscribers_without_false_truth_label(self):
        h=ontology_fixture(self)
        from review_agent.service import ReviewService
        from ontology_review.service import OntologyReviewService
        service=OntologyReviewService(ReviewService(h.repo,config={**h.config,'ontology_release_root':str(h.root)}))
        task=h.repo.extension_status(h.extension_id)
        service.decide(h.extension_id,{'action':'reject','reason':'Need better domain modeling, source fact remains supported.','expected_row_version':task['row_version']})
        candidate=h.repo.get_candidate(h.candidate['candidate_id'])
        self.assertEqual(candidate['workflow_state'],'schema_unresolved')
        self.assertEqual(candidate['human_status'],'pending')
        self.assertEqual(h.source_result['source_support_result'],'supported')
        self.assertEqual(h.repo.feedback_rows(),[])

    def test_t13_instance_only_cannot_approve_general_constraints(self):
        h=ontology_fixture(self)
        from tests.test_ontology_extension_agent import ontology_vote
        from review_agent.llm_clients import MockLLMClient
        def only_instance(payload):
            response=ontology_vote(payload)
            check=next(c for c in response['checks'] if c['check']=='constraint_strength')
            check['evidence_scope']='instance_only'
            return response
        for v in h.voters: v['client']=MockLLMClient(only_instance,model_id=v['model_id'],model_family=v['model_family'])
        result=h.review()
        self.assertEqual(result['recommendation'],'needs_human')
        self.assertEqual(h.runtime.state['validation']['consensus']['counts']['approve'],0)

    def test_t13_definition_grounding_is_required_and_can_pass(self):
        h=ontology_fixture(self)
        result=h.review()
        self.assertEqual(result['recommendation'],'approve')
        from ontology_review.validators import response_errors
        from tests.test_ontology_extension_agent import ontology_vote
        proposal=h.audit['proposals'][0]
        response=ontology_vote({'allowed_evidence_ids':[e['evidence_id'] for e in proposal['evidence']]})
        check=next(c for c in response['checks'] if c['check']=='constraint_strength')
        self.assertEqual(response_errors(response,proposal),[])
        check.pop('evidence_scope')
        self.assertTrue(any('general constraints' in e for e in response_errors(response,proposal)))
        check['evidence_scope']='instance_only'
        self.assertTrue(any('instance-only' in e for e in response_errors(response,proposal)))
        check['evidence_scope']='definition'; check['status']='not_applicable'
        self.assertTrue(response_errors(response,proposal))

    def test_t19_indirect_historical_impact_creates_revision_and_reaudit(self):
        h=ontology_fixture(self,hierarchy=True)
        from extract_triplets.candidate_contract import normalize_candidate
        from extract_triplets.pipeline_v4 import source_scope
        from review_agent.contracts import fact_key_for
        from review_agent.feedback import FeedbackService
        from ontology_review.publisher import OntologyPublisher
        raw={'head':'A','tail':'B','surface_relation':'connects','head_type_candidate':'C_leaf','tail_type_candidate':'C_leaf',
             'evidence_quotes':['A connects B.'],'qualifiers':copy.deepcopy(h.candidate['triple']['qualifiers']),
             'qualifier_status':copy.deepcopy(h.candidate['triple']['qualifier_status'])}
        history=normalize_candidate(raw,index=h.index,scope=source_scope(h.index,h.index['chunks'][0]),ontology=h.base.ontology,
            release_binding=h.base.binding(),run_id='run',ordinal=5,prompt_hash='fixture')
        history['fact_key']=fact_key_for(history); h.repo.add_candidate(history)
        current=h.repo.get_candidate(history['candidate_id'])
        FeedbackService(h.repo).submit({'action':'accept','candidate_id':history['candidate_id'],'revision':1,
            'expected_row_version':current['row_version'],'idempotency_key':uuid4().hex,
            'spans':[{'quote':'A connects B.','role':'support','note':'Exact connection statement under the current device schema.'}]})
        old_decision=h.repo.latest_decision(history['candidate_id'])['decision_id']
        if getattr(self,'before_publication',None): self.before_publication(h,history)
        def plan(payload):
            step=h.plan(payload)
            if step['action']=='propose_schema_delta':
                step['arguments']['operations'].append(getattr(self,'changed_constraint',None) or {'kind':'revise_definition','target':'C',
                    'changes':{'definition':'A device is a controllable unit with explicit operating-state scope.'},
                    'evidence_ids':[e['evidence_id'] for e in payload['evidence_catalog']]})
            return step
        h.review(plan)
        affected=h.audit['proposals'][0]['check_records']['impact']['affected_candidates']
        self.assertIn(history['candidate_id'],[c['candidate_id'] for c in affected])
        result=OntologyPublisher(h.repo,h.root,h.config).publish(h.extension_id,{**h.publication_request(),'confirm_high_impact':True})
        updated=h.repo.get_candidate(history['candidate_id'])
        self.assertEqual(updated['revision'],2)
        self.assertEqual(updated['workflow_state'],'reaudit_required')
        self.assertEqual(updated['human_status'],'pending')
        self.assertIsNone(h.repo.latest_decision(history['candidate_id']))
        self.assertFalse(h.repo.current_admitted_contributions(history['candidate_id'])['desired'])
        self.assertIn(history['candidate_id'],[c['candidate_id'] for c in result['affected_rebound']])
        with h.repo.connect() as conn:
            self.assertEqual(conn.execute('SELECT active FROM decisions WHERE decision_id=?',(old_decision,)).fetchone()[0],0)
            jobs=[json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM jobs WHERE job_type='audit'")]
        self.assertTrue(any(history['candidate_id'] in j.get('candidate_ids',[]) for j in jobs))
        self.assertTrue(h.repo.get_revision(history['candidate_id'],1)['ontology_release_id']==h.base.manifest['release_id'])
        self.impact_result=(h,history,result,old_decision)

    def test_t19_domain_range_change_reaudits_previously_accepted_history(self):
        self.changed_constraint={'kind':'revise_constraint','target':'R_old','changes':{'range':['C_leaf']},'evidence_ids':[]}
        # Evidence IDs are bound by the proposal builder, never invented here.
        def bind(h,history):
            self.changed_constraint['evidence_ids']=[e['evidence_id'] for e in h.repo.get_audit(h.candidate['candidate_id'])['rounds'][-1]['evidence_bundle']['items']]
        self.before_publication=bind
        self.test_t19_indirect_historical_impact_creates_revision_and_reaudit()
