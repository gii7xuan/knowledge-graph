"""Scope isolation, partial labels and real controlled-runner integration."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import unittest
from uuid import uuid4
from review_agent.memory import ReviewMemory
from review_agent.memory_scope import normalize_scope,resolve_scope,scope_from_split,scope_hash
from review_agent.contracts import sha256_json,fact_key_for
from review_agent.feedback import FeedbackService
from review_agent.repository import UnprocessableEntityError
from evaluation.annotations import export_annotations
from evaluation.evaluate_calibration import duplicate_rate,probability
from evaluation.fact_matching import identity_summary,match_facts
from evaluation.evaluate_review import evaluate
from evaluation.experiment import run_experiment,configure
from evaluation.metrics import usage_metrics


class MemoryDatabase:
    def __init__(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE memory_cases(case_id TEXT,memory_version INTEGER,case_json TEXT,active INTEGER,relation_id TEXT,domain TEXT,issue_type TEXT)')
    @contextmanager
    def connect(self): yield self.db
    def add(self,number,doc,run='train',cid=None):
        case={'candidate':{'candidate_id':cid or str(number),'document_id':doc,'run_id':run},'labels':{'valid_for_kg':1}}
        self.db.execute('INSERT INTO memory_cases VALUES(?,?,?,1,?,?,?)',(str(number),number,json.dumps(case),'R','domain','HUMAN_CONFIRMED'))


class MemoryScopeTests(unittest.TestCase):
    def setUp(self): self.repo=MemoryDatabase();self.addCleanup(self.repo.db.close)
    def test_train_scope_excludes_held_out_cases_before_top_k_and_snapshot(self):
        self.repo.add(1,'train_doc')
        for n in range(2,70): self.repo.add(n,'test_doc')
        scope=scope_from_split({'train_document_ids':['train_doc'],'test_document_ids':['test_doc']})
        result=ReviewMemory(self.repo).lookup(scope=scope,snapshot_id=68,max_positive=1)
        self.assertEqual([r['candidate']['document_id'] for r in result['positive']],['train_doc'])
        self.assertEqual(result['scope_hash'],scope_hash(scope))
        self.assertEqual(ReviewMemory(self.repo).lookup(scope=normalize_scope({'mode':'restricted','allowed_document_ids':[]}))['positive'],[])
    def test_intersection_cannot_widen_configured_scope_or_disabled_memory(self):
        scope=resolve_scope({'memory':{'scope':{'mode':'restricted','allowed_document_ids':['a']}}},
            {'memory_scope':{'mode':'restricted','allowed_document_ids':['a','b']}})
        self.assertEqual(scope['allowed_document_ids'],['a'])
        self.assertEqual(resolve_scope({'memory':{'enabled':False}}, {'memory_scope':{'mode':'all_published'}})['mode'],'disabled')
        with self.assertRaises(ValueError): scope_from_split({'train_document_ids':['same'],'test_document_ids':['same']})
        with self.assertRaises(ValueError): normalize_scope({'mode':'restricted'})
    def test_run_candidate_and_document_permissions_all_apply(self):
        self.repo.add(1,'train_doc','allowed','c1');self.repo.add(2,'train_doc','forbidden','c2')
        scope={'mode':'restricted','allowed_document_ids':['train_doc'],'allowed_run_ids':['allowed'],'excluded_candidate_ids':['c2']}
        result=ReviewMemory(self.repo).lookup(scope=scope)
        self.assertEqual([x['candidate']['candidate_id'] for x in result['positive']],['c1'])

    def test_checkpoint_preserves_scope_and_refuses_expansion(self):
        from review_agent.runtime import AgentRuntime
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.ontology_guard import OntologyGuard,load_snapshot
        from review_agent.llm_clients import MockLLMClient
        h=fixture(self)
        owner=ReviewOrchestrator(h.repo,OntologyGuard(load_snapshot(h.base.snapshot_path)),h.source_voters,config=h.config,planner_client=MockLLMClient([]))
        audit_id='scope_'+uuid4().hex;scope={'mode':'restricted','allowed_document_ids':[]}
        runtime=AgentRuntime(owner);runtime.initialize(h.candidate['candidate_id'],1,h.index,{'audit_id':audit_id,'memory_scope':scope})
        resumed=AgentRuntime(owner);resumed.initialize(h.candidate['candidate_id'],1,h.index,{'audit_id':audit_id})
        self.assertEqual(resumed.state.context['memory_scope']['allowed_document_ids'],[])
        with self.assertRaisesRegex(ValueError,'memory scope'):
            AgentRuntime(owner).initialize(h.candidate['candidate_id'],1,h.index,{'audit_id':audit_id,'memory_scope':{'mode':'all_published'}})

    def test_changed_memory_scope_invalidates_cache_and_calibration(self):
        from confidence.calibrator import TripleCalibrator
        from review_agent.request_cache import validation_request_key
        from tests.test_review_v2 import proposal,response
        from review_agent.llm_clients import MockLLMClient
        item=proposal();client=MockLLMClient(lambda _:response(),model_id='m',model_family='m');voter={'id':'m','client':client}
        self.assertNotEqual(validation_request_key(voter,{**item,'memory_scope_hash':'a'}),validation_request_key(voter,{**item,'memory_scope_hash':'b'}))
        calibrator=TripleCalibrator(model=object(),metadata={'memory_scope_hash':'a'})
        self.assertFalse(calibrator.predict_if_compatible({}, {'memory_scope_hash':'b'}).compatibility)


def fixture(test):
    from tests.test_ontology_extension_agent import OntologyExtensionTests
    h=OntologyExtensionTests()
    if getattr(test,'fixture_output',None): h.output_folder=test.fixture_output
    h.setUp();test.addCleanup(h.doCleanups)
    return h


class AnnotationTests(unittest.TestCase):
    def test_freeze_cli_exports_exact_candidate_inputs_and_blank_truth_template(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from evaluation.manifest import main
        from evaluation.annotations import annotation_template
        h=fixture(self)
        with patch('sys.argv',['manifest','--database',str(h.repo.database_path),'--run-id','run','--output',str(h.folder/'population.json'),
            '--candidates-output',str(h.folder/'population_candidates.json'),'--documents-output',str(h.folder/'population_documents.json')]),redirect_stdout(io.StringIO()): main()
        manifest=json.loads((h.folder/'population.json').read_text(encoding='utf-8'))
        candidates=json.loads((h.folder/'population_candidates.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['candidates'][0]['candidate_content_hash'],sha256_json(candidates[0]))
        template=annotation_template(manifest)
        self.assertIsNone(template['annotations'][0]['label']);self.assertIsNone(template['annotations'][0]['source_support'])

    def test_modify_keeps_old_and_new_revision_annotations_separate(self):
        h=fixture(self);current=h.repo.get_candidate(h.candidate['candidate_id'])
        service=FeedbackService(h.repo)
        changed=service.submit({'candidate_id':current['candidate_id'],'revision':1,'expected_row_version':current['row_version'],
            'idempotency_key':uuid4().hex,'action':'modify','reason_code':'CONDITION_MISSING',
            'modifications':{'tail':'C'},'labels':{'source_support':'insufficient','schema_valid':0,'task_relevance':1}})['candidate']
        service.submit({'candidate_id':current['candidate_id'],'revision':2,'expected_row_version':changed['row_version'],
            'idempotency_key':uuid4().hex,'action':'defer','labels':{'source_support':'supported','schema_valid':1,'task_relevance':1}})
        rows=export_annotations(h.repo.database_path)['annotations']
        self.assertEqual([r['revision'] for r in rows],[1,2])
        self.assertEqual([r['label'] for r in rows],[0,None])
        self.assertNotEqual(rows[0]['candidate_content_hash'],rows[1]['candidate_content_hash'])

    def test_partial_dimensions_survive_defer_and_readonly_export(self):
        h=fixture(self);current=h.repo.get_candidate(h.candidate['candidate_id'])
        FeedbackService(h.repo).submit({'candidate_id':current['candidate_id'],'revision':1,'expected_row_version':current['row_version'],
            'idempotency_key':uuid4().hex,'action':'defer','labels':{'source_support':'supported','schema_valid':0,'task_relevance':1}})
        self.assertEqual(h.repo.feedback_rows(),[])
        self.assertEqual(h.repo.feedback_rows(include_partial=True)[0]['schema_valid'],0)
        exported=export_annotations(h.repo.database_path)
        self.assertEqual(len(exported['annotations']),1)
        row=exported['annotations'][0]
        self.assertEqual((row['source_support'],row['schema_valid'],row['task_relevance'],row['label']),('supported',0,1,None))
        self.assertEqual(row['revision'],1)
    def test_label_enums_and_acceptance_conflicts_fail(self):
        h=fixture(self);service=FeedbackService(h.repo)
        for labels in ({'schema_valid':'pass'},{'task_relevance':True},{'source_support':'looks_true'}):
            with self.subTest(labels=labels),self.assertRaises(UnprocessableEntityError): service._labels({'action':'defer','labels':labels})
        with self.assertRaises(UnprocessableEntityError): service._labels({'action':'accept','labels':{'schema_valid':0}})


def canonical_candidate(polarity='positive',condition=None):
    c={'candidate_id':'c','revision':1,'contract_version':'extraction_candidate_v4','fact_identity_version':'fact_identity_v2',
       'fact_key_state':'canonical','fact_namespace':'fixture','triple':{'head':'A','head_entity_id':'A_id','relation_id':'R',
       'relation_semantic_hash':'meaning','tail':'B','tail_kind':'entity','tail_entity_id':'B_id',
       'qualifiers':{'polarity':polarity,'condition':condition,'time_scope':None,'applicability':None,'modality':'asserted','quantifier':None},
       'qualifier_status':{'polarity':'extracted','condition':'extracted' if condition else 'not_stated','modality':'extracted'}}}
    c['fact_key']=fact_key_for(c);return c


class ResearchMetricTests(unittest.TestCase):
    def test_exact_fact_identity_retains_negation_conditions_and_deduplicates_sources(self):
        a,b,c=canonical_candidate(),canonical_candidate('negative'),canonical_candidate(condition='calibrated')
        self.assertEqual(duplicate_rate([a,b,c]),0)
        self.assertEqual(duplicate_rate([a,a,b,c]),.25)
        self.assertIsNone(duplicate_rate([{'head':'A','relation':'R','tail':'B'}]))
        matched=match_facts([{'candidate':a,'decision':'accept'},{'candidate':a,'decision':'accept'},{'candidate':b,'decision':'accept'}],[a,b,c])
        self.assertEqual(matched['matched_fact_count'],2);self.assertEqual(matched['gold_fact_count'],3)
        self.assertEqual(matched['fact_recall'],2/3)
        altered=copy.deepcopy(a);altered['triple']['qualifiers']['polarity']='negative'
        with self.assertRaises(ValueError): identity_summary([altered])
        self.assertIsNone(probability({'calibrated_probability':'bad'}))
    def test_revision_truth_not_inherited_and_gold_binding_is_mandatory(self):
        final=canonical_candidate();final['revision']=2
        row={'candidate_id':'c','revision':1,'label':0,'predicted_decision':'accept','final_candidate':final,'ontology_gap':1}
        missing=evaluate([row]);self.assertEqual(missing['labeled_count'],0)
        truth={'candidate_id':'c','revision':2,'candidate_content_hash':sha256_json(final),'label':1}
        report=evaluate([row],revision_labels=[truth])
        self.assertEqual(report['research']['revision_success_rate_labeled'],1)
        self.assertEqual(report['research']['gap_known_correct_acceptance_rate'],1)
        with self.assertRaises(ValueError): evaluate([row],revision_labels=[{**truth,'candidate_content_hash':'wrong'}])
    def test_usage_does_not_multiply_shared_requests_or_invent_unknown_cost(self):
        requests=[{'request_id':'r','role':'ontology_validator','case_ids':['a','b'],'model_id':'m','status':'completed','input_tokens':10,'output_tokens':5,'latency_ms':20}]
        result=usage_metrics(requests);self.assertEqual(result['total']['calls'],1);self.assertIsNone(result['total']['cost'])
        result=usage_metrics(requests,{'m':{'input_per_million':1,'output_per_million':2}})
        self.assertEqual(result['total']['cost'],.00002)
        with self.assertRaises(ValueError): usage_metrics(requests*2)


def mock_services(h):
    from review_agent.llm_clients import MockLLMClient,LLMResponse
    from review_agent.response_contract import CHECK_NAMES
    from tests.test_ontology_extension_agent import action,ontology_vote
    def vote(payload):
        if payload.get('task_type')=='ontology_extension': value=ontology_vote(payload)
        else:
            eid=payload['allowed_evidence_ids'][0];known=payload['ontology_check'].get('relation_known')
            value={'verdict':'valid' if known else 'uncertain','source_support':'supported','schema_status':'pass' if known else 'unknown',
                'schema_issue':'none' if known else 'not_covered','schema_change_hint':None,'supporting_evidence_ids':[eid],'counter_evidence_ids':[],
                'reason_codes':[] if known else ['SCHEMA_GAP'],'checks':[{'check':name,'status':'unknown' if name=='ontology' and not known else 'pass','evidence_ids':[eid],'note':'MOCK protocol only'} for name in CHECK_NAMES],
                'requested_context':None,'suggested_revision':None}
        return LLMResponse(value,input_tokens=2,output_tokens=3)
    def plan(payload):
        if not payload['ontology_checked']: return action('inspect_ontology',relation_id=None,surface_relation='regulates',issue='Full lookup')
        if not payload['current_validation']: return action('run_validation')
        if not payload['ontology_check'].get('relation_known') and 'request_ontology_extension' in payload['available_tools']:
            return action('request_ontology_extension',change_kind='add_object_property',description='Missing regulation relation',proposed_changes={'label':'regulates'},evidence_ids=payload['selected_evidence_ids'][:1])
        return action('finish_review',stop_reason='Investigation completed',unresolved_issues=[],evidence_ids=[])
    def services(config):
        return {'validators':[{'id':v,'model_id':'model_'+v,'model_family':v,'client':MockLLMClient(vote,model_id='model_'+v,model_family=v)} for v in 'abc'],
            'planner':MockLLMClient(lambda p:LLMResponse(plan(p),input_tokens=2,output_tokens=3),model_id='planner',model_family='planner'),
            'ontology_planner':MockLLMClient(lambda p:LLMResponse(h.plan(p),input_tokens=2,output_tokens=3),model_id='ontology_planner',model_family='planner')}
    return services


class ExperimentTests(unittest.TestCase):
    def test_all_methods_fixed_dynamic_and_ablation_use_one_frozen_pool(self):
        h=fixture(self)
        spec={'split':{'train_document_ids':[],'test_document_ids':[h.index['document_id']]},'limits':{'llm_calls':80,'tool_calls':48},
            'groups':[{'id':m,'method':m,'ontology_mode':'fixed'} for m in ('rules','single','majority','fixed_workflow','agent')]+
                [{'id':'dynamic','method':'agent','ontology_mode':'dynamic'},
                 {'id':'without_extension','method':'agent','ontology_mode':'dynamic','ablations':['no_extension']},
                 *[{'id':name,'method':'agent','ontology_mode':'dynamic','ablations':[name]} for name in ('no_targeted_retrieval','no_issue_resolution','no_counterevidence_resolution')]]}
        config=copy.deepcopy(h.config);config['ontology_extension']['budget']['max_planner_steps']=16
        output=h.folder/'controlled_experiment'
        report=run_experiment(spec,[h.candidate],[h.index],h.base.path,output,config,services_factory=mock_services(h))
        self.assertTrue(all(g['failed']==0 for g in report['groups']),report)
        self.assertTrue(all(g['formal_admission_count']==0 for g in report['groups']))
        self.assertTrue(all(g['unhandled_jobs']==0 for g in report['groups']))
        self.assertEqual(len({g['population_hash'] for g in report['groups']}),1)
        dynamic=json.loads((output/'dynamic/predictions.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(dynamic['final_revision'],2,dynamic)
        self.assertEqual(dynamic['predicted_decision'],'accept',dynamic)
        for group in spec['groups']:
            prediction=json.loads((output/group['id']/'predictions.json').read_text(encoding='utf-8'))[0]
            dynamic_enabled=group.get('ontology_mode')=='dynamic' and 'no_extension' not in group.get('ablations',[])
            self.assertEqual(prediction['final_revision'],2 if dynamic_enabled else 1)
        requests=json.loads((output/'dynamic/requests.json').read_text(encoding='utf-8'))
        self.assertTrue(any(r['role']=='ontology_validator' for r in requests))
        self.assertTrue(any(r['role']=='triple_planner' for r in requests))
        from evaluation.evaluate_experiment import evaluate_experiment
        truth=[{'candidate_id':h.candidate['candidate_id'],'revision':1,'candidate_content_hash':sha256_json(h.candidate),'label':0,'ontology_gap':1}]
        revised={'candidate_id':h.candidate['candidate_id'],'revision':2,'candidate_content_hash':sha256_json(dynamic['final_candidate']),'label':1}
        metrics=evaluate_experiment(output,truth,revision_labels={'dynamic':[revised]},gold_facts={'dynamic':[dynamic['final_candidate']]})
        improved=next(g for g in metrics['groups'] if g['id']=='dynamic')
        self.assertEqual(improved['metrics']['research']['recommendation_precision'],1)
        self.assertEqual(improved['metrics']['research']['fact_matching']['matched_fact_count'],1)
        save_path=output/'fixture_metrics.json';save_path.write_text(json.dumps(metrics,indent=2),encoding='utf-8')
        self.flow=(h,output,report)
    def test_wrong_partition_labels_and_pool_mutation_are_rejected_before_calls(self):
        h=fixture(self)
        spec={'split':{'train_document_ids':[],'test_document_ids':['other']},'groups':[{'id':'rules','method':'rules'}]}
        with self.assertRaises(ValueError): run_experiment(spec,[h.candidate],[h.index],h.base.path,h.folder/'invalid',h.config)
        spec['split']['test_document_ids']=[h.index['document_id']]
        with self.assertRaises(ValueError): run_experiment(spec,[{**h.candidate,'label':1}],[h.index],h.base.path,h.folder/'invalid_labels',h.config)
    def test_ablations_change_real_capabilities_and_issue_resolution(self):
        from review_agent.agent_state import AgentState
        from review_agent.issue_ledger import IssueLedger
        scope=scope_from_split({'train_document_ids':[],'test_document_ids':['doc']})
        cfg=configure({}, {'method':'agent','ontology_mode':'dynamic','ablations':['no_targeted_retrieval','no_extension']},scope,
            {'llm_calls':24,'tokens':1000,'tool_calls':16,'extension_requests':1,'rebinds':2,'seconds':60})
        self.assertNotIn('search_evidence',cfg['planner']['allowed_tools'])
        self.assertNotIn('request_ontology_extension',cfg['planner']['allowed_tools'])
        state=AgentState(candidate={'candidate_id':'c','revision':1},context={'experimental_policy':{'no_issue_resolution':True}},audit_id='a')
        ledger=IssueLedger(state);issue=ledger.open('EVIDENCE_INSUFFICIENT','Missing support',True)
        ledger.resolve(issue,{'proposal_digest':'p'},'independent_recheck',['e'])
        self.assertEqual(issue['status'],'open');self.assertEqual(state.issue_events[-1]['action'],'resolution_disabled_by_experiment')

    def test_call_budget_exhaustion_keeps_the_population_and_zero_admission(self):
        h=fixture(self)
        spec={'split':{'train_document_ids':[],'test_document_ids':[h.index['document_id']]},'limits':{'llm_calls':1},
              'groups':[{'id':'limited','method':'agent'}]}
        output=h.folder/'budget_experiment'
        report=run_experiment(spec,[h.candidate],[h.index],h.base.path,output,h.config,services_factory=mock_services(h))
        prediction=json.loads((output/'limited/predictions.json').read_text(encoding='utf-8'))
        self.assertEqual(len(prediction),1)
        self.assertLessEqual(report['groups'][0]['usage']['total']['calls'],1)
        self.assertEqual(report['groups'][0]['formal_admission_count'],0)


if __name__=='__main__': unittest.main()
