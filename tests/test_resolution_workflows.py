"""Execute isolated mapping, semantic revision and no-information handoff end to end."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from uuid import uuid4

from document.hierarchy_index import build_document_index,split_markdown_text,persist_document_index
from extract_triplets.candidate_contract import normalize_candidate
from extract_triplets.pipeline_v4 import source_scope
from review_agent.admission import canonicalize_revision
from review_agent.contracts import sha256_json
from review_agent.experiment_profiles import build_config
from review_agent.llm_clients import MockLLMClient,LLMResponse
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.ontology_guard import OntologyGuard,load_snapshot


class ResolutionWorkflowTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ontology_extension_agent import OntologyExtensionTests
        self.f=OntologyExtensionTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def execute_case(self,mode):
        from review_agent.response_contract_v8 import CHECK_NAMES
        f=self.f
        quote=('Device A can connect Device B.' if mode=='revision' else
               'A device connects another device.' if mode=='handoff' else 'Device A connects Device B.')
        head,tail=('A device','another device') if mode=='handoff' else ('Device A','Device B')
        index=build_document_index(split_markdown_text(quote,'workflow-'+uuid4().hex,600,0))
        path=persist_document_index(index,f.folder/('source-'+mode+'.json'))
        run='workflow-'+mode
        f.repo.create_run({'run_id':run,'mode':'shadow','document_path':str(path),'source_index_hash':index['index_content_hash'],
            'ontology_path':str(f.base.snapshot_path),'ontology_hash':f.base.manifest['ontology_version'],'candidate_contract_version':'extraction_candidate_v4'})
        raw={**deepcopy(f.candidate['triple']),'head':head,'tail':tail,'surface_relation':'links' if mode=='mapping' else 'connects',
            'head_type_candidate':'C','tail_type_candidate':'C','evidence_quotes':[quote]}
        c=normalize_candidate(raw,index=index,scope=source_scope(index,index['chunks'][0]),ontology=f.base.ontology,
            release_binding=f.base.binding(),run_id=run,ordinal=0,prompt_hash='synthetic-workflow')
        if mode=='handoff':
            c['triple'].update(relation_id='R_old',head_type='C',tail_type='C');c=canonicalize_revision(f.repo,c)
        f.repo.add_candidate(c)
        original=f.folder/'immutable-source.sqlite3'
        with f.repo.connect() as src,sqlite3.connect(original) as dst:src.backup(dst)
        source_hash=hashlib.sha256(original.read_bytes()).hexdigest()
        (f.folder/'shadow_manifest.json').write_text(json.dumps({'source':str(original),'source_sha256':source_hash,
            'database':str(f.repo.database_path.resolve()),'allowed_root':str(f.folder.resolve()),'mode':'isolated_shadow',
            'revision_scope':'per_candidate','max_semantic_revision_rounds':1}),encoding='utf-8')
        config=build_config(f.config,profile='agent_resolution')
        config['planner'].update(model='fixture_planner',family='fixture_planner')
        config['budget']['max_retries_per_request']=0
        def respond(payload,identity):
            if payload.get('contract_version')=='candidate_revision_v1' or payload.get('task_type')=='candidate_revision_review':
                eid=payload['evidence_ids'][0]
                value={'decision':'approve','field_reviews':[{'field':field,'status':'supported','evidence_ids':[eid],
                    'note':'MOCK explicit source meaning and type'} for field in payload['changed_fields']],
                    'supporting_evidence_ids':[eid],'counter_evidence_ids':[],
                    'counterevidence_reviewed_ids':payload['protected_evidence_ids'],'rationale_summary':'MOCK full before/after semantic review'}
            else:
                eid=next(e['evidence_id'] for e in payload['evidence'] if quote in e['quote'])
                all_ids=payload['allowed_evidence_ids']
                dims={n:{'status':'pass','evidence_ids':[eid],'note':'MOCK complete independent source check','issue_code':None} for n in CHECK_NAMES}
                dims['triple_support']['status']='supported';dims['knowledge_scope']['status']='general'
                triple=payload['candidate']['triple'];assigned=triple.get('relation_id')
                dims['mapping'].update(status='matched' if assigned else 'unassigned',relation_id=assigned,competing_relation_ids=[])
                bad=mode=='revision' and triple['qualifiers']['modality']!='can'
                unknown=mode=='handoff';findings=[]
                if bad or unknown:
                    check='qualifiers' if bad else 'entity_reference';dims[check]['status']='fail' if bad else 'unknown'
                    findings=[{'check':check,'kind':'semantic_error' if bad else 'unresolved','normalization':None,
                        'rule_id':'MOD-001' if bad else 'MEANING-001','meaning_effect':'changed' if bad else 'unresolved',
                        'note':'MOCK preserve explicit ability' if bad else 'MOCK referent cannot be determined from available source',
                        'suggested_change':'Retain source can' if bad else 'Inspect context or keep unresolved','evidence_ids':[eid]}]
                if unknown:dims['triple_support']['status']='insufficient';dims['evidence_sufficiency']['status']='unknown'
                value={'dimensions':dims,'expression_findings':findings,
                    'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'MOCK source statement'}],
                    'primary_evidence_ids':[] if bad or unknown else [eid],'counterevidence_reviewed_ids':all_ids,
                    'requested_context':None,'suggested_revision':None}
            return LLMResponse(value,input_tokens=20,output_tokens=10,returned_model_id='fixture_'+identity,
                               response_id='local_'+sha256_json([identity,payload])[:16])
        voters=[{'id':v,'model_id':'fixture_'+v,'model_family':'fixture_'+v,
                 'client':MockLLMClient(lambda p,v=v:respond(p,v),model_id='fixture_'+v,model_family='fixture_'+v)} for v in 'abc']
        def plan(payload):
            ids=payload['valid_evidence_ids']
            if mode=='handoff':
                if not payload['history'] or not any(x.get('tool')=='search_evidence' for x in payload['history']):
                    name='search_evidence';args={'scope':'other_section','query':'referent identity','purpose':'resolve_reference','limit':3}
                else:name='finish_review';args={'stop_reason':'no_information_gain','unresolved_issues':[],'evidence_ids':[]}
            elif mode=='mapping':
                name='propose_mapping';args={'relation_id':'R_old','head_type':'C','tail_type':'C',
                    'identity_evidence':{'head':ids,'tail':ids},'type_evidence':{s:{'basis':'source','evidence_ids':ids,
                        'note':'MOCK the source explicitly calls both entities devices'} for s in ('head','tail')},
                    'competing_relations':[],'evidence_ids':ids,'reason':'source-grounded mapping proposal'}
            else:
                name='propose_revision';args={'changes':{'qualifiers':{'modality':'can'},'relation_id':'R_old','head_type':'C','tail_type':'C'},
                    'evidence_ids':ids,'reason':'retain explicit can, with independently checked mapping'}
            return {'action':name,'arguments':args,'target_issue':'source-bound-problem','rationale_summary':'MOCK choose a concrete bounded next step'}
        planner=MockLLMClient(plan,model_id='fixture_planner',model_family='fixture_planner')
        guard=OntologyGuard(load_snapshot(f.base.snapshot_path))
        o=ReviewOrchestrator(f.repo,guard,voters,config=config,planner_client=planner)
        parent=o.audit_candidate(c['candidate_id'],1,index)
        original_audit=f.repo.get_audit(c['candidate_id'],parent['audit_id'])
        original_votes=sha256_json(original_audit['votes'])
        final=parent
        if mode!='handoff':
            self.assertEqual(parent.get('status'),'revision_applied',{
                'stop_reason':parent.get('review_outcome',{}).get('execution_reason'),
                'issues':original_audit.get('context',{}).get('issue_history'),
                'history':((original_audit.get('agent_state') or {}).get('state') or {}).get('history')})
            job=f.repo.get_job(parent['reaudit_job_id']);self.assertEqual(job['status'],'queued')
            self.assertEqual(job['payload']['candidate_ids'],[c['candidate_id']])
            self.assertIsNone(f.repo.latest_decision(c['candidate_id']))
            final_config=deepcopy(config);final_config['agent_resolution']['final_review_only']=True
            final=ReviewOrchestrator(f.repo,guard,voters,config=final_config,planner_client=MockLLMClient([],model_id='fixture_planner',model_family='fixture_planner')).audit_candidate(c['candidate_id'],2,index)
            self.assertTrue(final['independent_review_completed'],final)
            self.assertEqual(final['review_outcome']['recommendation'],'admit',final['review_outcome'])
            audit=f.repo.get_audit(c['candidate_id'],final['audit_id'])
            self.assertEqual({v['object_revision'] for v in audit['votes']},{2})
            self.assertEqual(len(audit['votes']),3)
        else:
            self.assertNotEqual(final['review_outcome']['recommendation'],'admit')
            self.assertEqual(final['required_human_action'],'domain_adjudication')
            self.assertLess(len(planner.calls),4)
        self.assertEqual(sha256_json(f.repo.get_audit(c['candidate_id'],parent['audit_id'])['votes']),original_votes)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),source_hash)
        self.last_case={'mode':mode,'candidate':c,'parent':parent,'final':final,'source_index':index,
                        'original_votes_unchanged':True,'source_database_unchanged':True,'real_model_calls':0,'human_labels':0}
        return self.last_case

    def test_mapping_proposal_review_isolated_application_and_new_ballot(self):self.execute_case('mapping')
    def test_semantic_revision_keeps_modality_and_requires_new_ballot(self):self.execute_case('revision')
    def test_no_new_information_stops_without_forcing_agreement(self):self.execute_case('handoff')

    def test_late_subscription_rebind_preserves_original_publication_receipt(self):
        from ontology_review.publisher import OntologyPublisher
        f=self.f;f.config['ontology_extension']['budget']['per_call_timeout_seconds']=5
        reviewed=f.review()
        self.assertEqual(reviewed['recommendation'],'approve',[(v.get('response_status'),v.get('error')) for v in f.audit['votes']])
        publisher=OntologyPublisher(f.repo,f.root,f.config)
        publisher.publish(f.extension_id,f.publication_request())
        with f.repo.connect() as connection:
            before=connection.execute('SELECT result_json FROM ontology_publish_outbox WHERE extension_id=?',(f.extension_id,)).fetchone()[0]
        candidate=deepcopy(f.candidate);candidate['candidate_id']+='_late';f.repo.add_candidate(candidate)
        row=f.repo.get_candidate(candidate['candidate_id'])
        # A late subscription to an already published task is the only fixture state introduced here.
        with f.repo.transaction(immediate=True) as connection:
            connection.execute("UPDATE candidates SET pending_extension_id=?,workflow_state='reaudit_required' WHERE candidate_id=?",(f.extension_id,candidate['candidate_id']))
            connection.execute('INSERT INTO ontology_extension_subscriptions(extension_id,candidate_id,revision,expected_row_version,source_proposal_digest) VALUES(?,?,?,?,?)',
                (f.extension_id,candidate['candidate_id'],1,row['row_version'],'synthetic-late-source-review'))
        result=publisher.reconcile_subscribers(f.extension_id)
        self.assertEqual(result['rebound'],[{'candidate_id':candidate['candidate_id'],'revision':2}])
        self.assertFalse(result['old_votes_reused']);self.assertEqual(len(result['reaudit_jobs']),1)
        with f.repo.connect() as connection:
            after=connection.execute('SELECT result_json FROM ontology_publish_outbox WHERE extension_id=?',(f.extension_id,)).fetchone()[0]
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM validator_votes v JOIN audit_runs a ON a.audit_id=v.audit_id WHERE a.candidate_id=?',(candidate['candidate_id'],)).fetchone()[0],0)
        self.assertEqual(after,before)
        self.assertEqual(publisher.reconcile_subscribers(f.extension_id)['rebound'],[])

    def test_isolated_runner_drains_new_rebind_jobs_but_bounds_the_queue(self):
        from unittest.mock import patch
        from evaluation.agent_resolution import drain_reaudits
        f=self.f;payload={'run_id':'run','candidate_ids':[f.candidate['candidate_id']],'revision_draft_id':'synthetic-draft'}
        first=f.repo.create_job('audit',payload);calls=[]
        def execute(repository,run,config,**kwargs):
            calls.append(config['agent_resolution']['final_review_only'])
            if len(calls)==1:repository.create_job('audit',{**payload,'ontology_rebind':f.extension_id})
            return {'failed':0}
        with patch('evaluation.agent_resolution.run_review_batch',execute):
            result=drain_reaudits(f.repo,'run',build_config(f.config,profile='agent_resolution'),f.folder,max_jobs=2)
        self.assertEqual(len(result),2);self.assertEqual(calls,[True,False])
        f.repo.create_job('audit',payload)
        with self.assertRaises(ValueError):drain_reaudits(f.repo,'run',build_config(f.config,profile='agent_resolution'),f.folder,max_jobs=0)

    def test_imported_revision_ancestry_does_not_become_a_new_experiment_operation(self):
        from evaluation.agent_resolution import revision_review_materials
        from review_agent.repository import NotFoundError
        f=self.f;cid=f.candidate['candidate_id']
        old={'draft_id':'imported-applied-draft','candidate_id':cid,'source_audit_id':'historical-audit-not-copied'}
        with f.repo.transaction(immediate=True) as connection:
            connection.execute('INSERT INTO revision_drafts VALUES(?,?,?,?,?,?,?,?)',
                (old['draft_id'],cid,1,1,'historical-hash','applied',json.dumps(old),'{}'))
        self.assertEqual(revision_review_materials(f.repo,{cid},[],historical_applied_draft_ids={old['draft_id']}),([],[]))
        with self.assertRaises(NotFoundError):revision_review_materials(f.repo,{cid},[])
