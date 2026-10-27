"""Missing acceptance scenarios, with production state kept outside the fixtures."""
import copy
import itertools
import json
import unittest
from pathlib import Path
from uuid import uuid4


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
        result=h.run_script([ontology(),action('run_validation'),action('request_ontology_extension',change_kind='revise_constraint',
            description='Must not expand without source support',proposed_changes={'review_relation_id':'R_measures'},evidence_ids=[]),finish()])
        self.assertEqual(result['source_support_result'],'insufficient')
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
        def plan(payload):
            step=h.plan(payload)
            if step['action']=='propose_schema_delta':
                step['arguments']['operations'].append({'kind':'revise_definition','target':'C',
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
