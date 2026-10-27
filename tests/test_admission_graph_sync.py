"""Verified v4 source/release admission and real Neo4j regression scenarios."""
import copy
import json
import os
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from document.hierarchy_index import build_document_index, split_markdown_text, persist_document_index
from extract_triplets.candidate_contract import normalize_candidate
from extract_triplets.pipeline_v4 import source_scope
from ontologyBuild.providers.base import OntologyArtifact
from ontologyBuild.release_catalog import ReleaseCatalog
from review_agent.repository import ReviewRepository, UnprocessableEntityError, ConflictError
from review_agent.feedback import FeedbackService
from review_agent.human_evidence import create_human_bundle
from review_agent.contracts import fact_key_for
from schema.fact_identity import relation_semantic_hash, literal_identity
from to_neo4j.fact_projection import Neo4jImporter, direct_edge_eligible
from to_neo4j.reconciler import apply_outbox, reconcile_candidate
from to_neo4j.query_facts import query_facts

ROOT = Path(__file__).resolve().parents[1]


class AdmissionFixture:
    def __init__(self, folder):
        self.folder = folder
        folder.mkdir(parents=True)
        self.repo = ReviewRepository(folder / 'review.sqlite3')
        self.repo.initialize()
        self.schema = {'domain': 'fixture', 'classes': {'C': {'id':'C','label':'device','definition':'A controllable device.'}},
            'object_properties': {'R': {'id':'R','label':'connects','definition':'A connection.','domain':['C'],'range':['C']}},
            'datatype_properties': {'D': {'id':'D','label':'mass','definition':'Mass measurement.','domain':['C'],'datatype':'decimal'}},
            'application_profile': {'profile_version':'project_json_v1','constraint_semantics':'allowed_any','auto_review_blocked':False}}
        artifact = OntologyArtifact(copy.deepcopy(self.schema), {'method':'fixture'}, {}, {'errors':[], 'unsupported_axioms':[]},
            copy.deepcopy(self.schema['application_profile']), source_json=copy.deepcopy(self.schema))
        self.release = ReleaseCatalog(folder / 'releases').publish(artifact, approval={'status':'approved','approved_by':'fixture','reason':'test'})
        self.index = build_document_index(split_markdown_text('A connects B. A does not connect B. If enabled, A can connect B. A has mass 1.00 kg.', folder.name, 600, 20))
        path = persist_document_index(self.index, folder / 'document.json')
        self.repo.create_run({'run_id':'run','mode':'shadow','document_path':str(path),'source_index_hash':self.index['index_content_hash'],
            'ontology_path':str(self.release.snapshot_path),'ontology_hash':self.release.manifest['ontology_version'],'candidate_contract_version':'extraction_candidate_v4'})
        self.feedback = FeedbackService(self.repo)

    def candidate(self, ordinal=0, *, qualifiers=None, literal=False):
        q = {'polarity':'positive','condition':None,'time_scope':None,'applicability':None,'modality':'asserted','quantifier':None}
        q.update(qualifiers or {})
        raw = {'head':'A','tail':'B','surface_relation':'mass' if literal else 'connects','evidence_quotes':['A has mass 1.00 kg.' if literal else 'A connects B.'],
               'qualifiers':q,'qualifier_status':{k:'not_stated' if v is None else 'extracted' for k,v in q.items()}}
        if literal: raw.update(tail_kind='literal',tail='1.00',value='1.00',datatype='decimal',unit='kg')
        c = normalize_candidate(raw, index=self.index, scope=source_scope(self.index,self.index['chunks'][0]),ontology=self.release.ontology,
            release_binding=self.release.binding(),run_id='run',ordinal=ordinal,prompt_hash='fixture')
        c['triple'].update(head_type='C',tail_type=None if literal else 'C')
        c['fact_key'] = fact_key_for(c)
        self.repo.add_candidate(c)
        return c

    def request(self, cid, action='accept', **changes):
        c = self.repo.get_candidate(cid)
        return {'candidate_id':cid,'revision':c['revision'],'expected_row_version':c['row_version'], 'action':action,
                'idempotency_key':uuid4().hex,'reason_code':'HUMAN_CONFIRMED' if action=='accept' else 'REVISION_REQUIRED',**changes}

    def accept(self, cid, quote='A connects B.'):
        return self.feedback.submit(self.request(cid, spans=[{'quote':quote,'role':'support','note':'Explicit source statement matches this fact and its scope.'}]))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / 'runs/_test_admission' / uuid4().hex
        self.f = AdmissionFixture(self.folder)
        self.addCleanup(shutil.rmtree,self.folder)
        self.c = self.f.candidate()

    def test_nonempty_exact_human_evidence_required(self):
        for changes in ({}, {'spans':[]}, {'spans':[{'quote':'invented source','role':'support','note':'x'}]},
                        {'spans':[{'quote':'A connects B.','role':'context','note':'x'}]},
                        {'spans':[{'quote':'A connects B.','role':'support','note':'x','resolution_evidence_ids':['fake']}]}):
            with self.assertRaises(UnprocessableEntityError): self.f.feedback.submit(self.f.request(self.c['candidate_id'],**changes))
        self.assertIsNone(self.f.repo.latest_decision(self.c['candidate_id']))

    def test_bound_acceptance_and_idempotency(self):
        request = self.f.request(self.c['candidate_id'],spans=[{'quote':'A connects B.','role':'support','note':'Explicit statement.'}])
        event = self.f.feedback.submit(request)['event']
        self.assertEqual(event,self.f.feedback.submit(request)['event'])
        state = self.f.repo.current_admitted_contributions(self.c['candidate_id'])
        self.assertEqual(len(state['desired']),1,state)
        self.assertTrue(state['desired'][0]['admission']['allowed'])

    def test_bundle_stale_and_foreign(self):
        request = self.f.request(self.c['candidate_id'],spans=[{'quote':'A connects B.','role':'support','note':'Explicit statement.'}])
        bundle = create_human_bundle(self.f.repo,request)
        other = self.f.candidate(1)
        with self.assertRaises(UnprocessableEntityError): self.f.feedback.submit(self.f.request(other['candidate_id'],human_bundle_id=bundle['bundle_id']))
        self.f.feedback.submit(self.f.request(self.c['candidate_id'],'modify',modifications={'tail':'B'}))
        with self.assertRaises(ConflictError): self.f.feedback.submit({**request,'human_bundle_id':bundle['bundle_id']})

    def test_modify_recanonicalizes_and_old_authority_retires(self):
        self.f.accept(self.c['candidate_id'])
        old = self.f.repo.current_admitted_contributions(self.c['candidate_id'])
        self.f.feedback.submit(self.f.request(self.c['candidate_id'],'modify',modifications={'qualifiers':{**self.c['triple']['qualifiers'],'polarity':'negative'}}))
        current = self.f.repo.get_candidate(self.c['candidate_id'])
        self.assertEqual(current['fact_key_state'],'canonical')
        self.assertFalse(self.f.repo.current_admitted_contributions(self.c['candidate_id'])['desired'])
        self.f.accept(self.c['candidate_id'],'A does not connect B.')
        new = self.f.repo.current_admitted_contributions(self.c['candidate_id'])
        self.assertGreater(new['projection_version'],old['projection_version'])
        self.assertEqual(new['desired'][0]['candidate']['revision'],2)

    def test_source_tamper_revokes_desired_contribution(self):
        self.f.accept(self.c['candidate_id'])
        old = self.f.repo.current_admitted_contributions(self.c['candidate_id'])
        path = self.folder / 'document.json'
        data = json.loads(path.read_text(encoding='utf-8')); data['source_snapshot']['canonical_text'] += 'tamper'
        path.write_text(json.dumps(data),encoding='utf-8')
        new = self.f.repo.current_admitted_contributions(self.c['candidate_id'])
        self.assertFalse(new['desired']); self.assertGreater(new['projection_version'],old['projection_version'])

    def test_semantic_identity_and_typed_literals(self):
        schema = copy.deepcopy(self.f.release.ontology)
        old = relation_semantic_hash(schema,'R')
        schema['object_properties']['R']['label'] = 'display changed'
        self.assertEqual(old,relation_semantic_hash(schema,'R'))
        schema['object_properties']['R']['definition'] = 'A different relation.'
        self.assertNotEqual(old,relation_semantic_hash(schema,'R'))
        a = {'value':'1.00','datatype':'decimal','unit':'kg'}
        self.assertEqual(literal_identity(a),literal_identity({**a,'value':'1.0'}))
        self.assertNotEqual(literal_identity(a),literal_identity({**a,'unit':'m'}))
        self.assertFalse(Neo4jImporter.__new__(Neo4jImporter).is_importable({'candidate_id':'fake','revision':1,'latest_decision':{'decision':'human_accept','review_event_id':'fake'}}))

    def test_literal_admission_and_qualified_edges(self):
        c = self.f.candidate(1,literal=True)
        self.f.accept(c['candidate_id'],'A has mass 1.00 kg.')
        self.assertTrue(self.f.repo.current_admitted_contributions(c['candidate_id'])['desired'])
        for q in ({'polarity':'negative'},{'condition':'enabled'},{'time_scope':'during setup'},{'modality':'can'},{'quantifier':'some'}):
            self.assertFalse(direct_edge_eligible({**self.c['triple'],'qualifiers':{**self.c['triple']['qualifiers'],**q}}))

    def test_automatic_admission_uses_real_frozen_proposal_and_policy(self):
        from types import SimpleNamespace
        from confidence.risk_policy import RiskPolicy
        from review_agent.orchestrator import ReviewOrchestrator
        from review_agent.ontology_guard import OntologyGuard, load_snapshot
        from review_agent.llm_clients import MockLLMClient
        from tests.test_review_planner import valid_response
        voters = [{'id':v,'model_id':'mock_'+v,'model_family':v,'client':MockLLMClient(valid_response,model_id='mock_'+v,model_family=v)} for v in 'abc']
        config = {'mode':'active','planner':{'enabled':False,'strategy':'deterministic'},
            'routing':{'auto_accept_enabled':True},'consensus':{'committee_size':3,'required_votes':3,'minimum_distinct_families':3},
            'budget':{'max_llm_calls_per_candidate':8,'total_timeout_seconds':30}}
        policy = RiskPolicy({'enabled':True,'validated':True,'auto_accept_enabled':True,'accept_threshold':.9,'policy_version':'fixture_validated_policy'})
        calibrator = SimpleNamespace(ready=True,predict_if_compatible=lambda *_:SimpleNamespace(probability=.99,status='ok',model_version='mock_fixture_calibrator',compatibility=True))
        result = ReviewOrchestrator(self.f.repo,OntologyGuard(load_snapshot(self.f.release.snapshot_path)),voters,config=config,
            risk_policy=policy,calibrator=calibrator).audit_candidate(self.c['candidate_id'],1,self.f.index)
        self.assertEqual(result['decision'],'auto_accept',result)
        state = self.f.repo.current_admitted_contributions(self.c['candidate_id'])
        self.assertEqual(len(state['desired']),1,state)
        # A cached probability/accept flag cannot authorize another revision or tampered proposal.
        with self.f.repo.transaction(immediate=True) as conn:
            conn.execute("UPDATE proposals SET proposal_digest='tampered' WHERE audit_id=?",(result['audit_id'],))
        self.assertFalse(self.f.repo.current_admitted_contributions(self.c['candidate_id'])['desired'])

    def test_http_evidence_errors_and_conflicts(self):
        import threading
        import urllib.request
        import urllib.error
        from http.server import ThreadingHTTPServer
        from unittest.mock import patch
        from review_agent.service import ReviewService
        from workbench_app import WorkbenchHandler
        service = ReviewService(self.f.repo,config={'mode':'shadow'})
        server = ThreadingHTTPServer(('127.0.0.1',0),WorkbenchHandler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            def post(path,body):
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                    data=json.dumps(body).encode(),headers={'Content-Type':'application/json'},method='POST')
                try:
                    with urllib.request.urlopen(request,timeout=5) as response: return response.status,json.load(response)
                except urllib.error.HTTPError as exc: return exc.code,json.load(exc)
            with patch('workbench_app.get_default_service',return_value=service):
                path = '/api/triplets/candidates/'+self.c['candidate_id']+'/human-bundles'
                request = self.f.request(self.c['candidate_id'])
                self.assertEqual(post(path,{**request,'spans':[]})[0],422)
                self.assertEqual(post(path,{**request,'expected_row_version':999})[0],409)
                status,bundle = post(path,{**request,'spans':[{'quote':'A connects B.','role':'support','note':'Exact statement.'}]})
                self.assertEqual(status,201,bundle)
                status,_ = post('/api/triplets/decision',{**request,'human_bundle_id':bundle['bundle_id']})
                self.assertEqual(status,200)
                self.assertEqual(post('/api/triplets/decision',{**request,'idempotency_key':uuid4().hex,'human_bundle_id':bundle['bundle_id']})[0],409)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)


@unittest.skipUnless(os.environ.get('KG_TEST_NEO4J_URI'),'isolated real Neo4j URI required')
class RealGraphTests(AdmissionTests):
    def setUp(self):
        super().setUp()
        self.graph = Neo4jImporter(os.environ['KG_TEST_NEO4J_URI'],user='',repository=self.f.repo)
        self.addCleanup(self.graph.close)
        # Each fixture has document-local IDs; use unique doc namespace for graph isolation.
        self.cid = self.c['candidate_id']

    def facts(self):
        rows = query_facts(self.graph.driver)
        return [r for r in rows if any(c['candidate_id'] in self.f.repo.graph_pending_candidates(scan=True) for c in r['contributions'])]

    def test_graph_duplicate_reorder_and_old_worker(self):
        self.f.accept(self.cid)
        old = self.f.repo.current_admitted_contributions(self.cid)
        self.graph.reconcile(old)
        self.f.feedback.submit(self.f.request(self.cid,'modify',modifications={'tail':'B'}))
        self.f.accept(self.cid)
        new = self.f.repo.current_admitted_contributions(self.cid)
        self.graph.reconcile(new)
        self.assertEqual(self.graph.reconcile(old)['status'],'stale')
        self.assertEqual(self.graph.reconcile(new)['status'],'unchanged')
        self.assertEqual(len(self.facts()),1)
        with self.graph.driver.session() as session:
            rows = list(session.run('MATCH (c:CandidateContribution {candidate_id:$cid,active:true}) RETURN c.revision AS revision',cid=self.cid))
        self.assertEqual([r['revision'] for r in rows],[2])

    def test_graph_changed_fact_retires_old_fact_even_with_stale_retract(self):
        self.f.accept(self.cid)
        first = self.f.repo.current_admitted_contributions(self.cid)
        self.graph.reconcile(first)
        self.f.feedback.submit(self.f.request(self.cid,'modify',modifications={'qualifiers':{**self.c['triple']['qualifiers'],'polarity':'negative'}}))
        self.f.accept(self.cid,'A does not connect B.')
        self.assertEqual(apply_outbox(self.f.repo,self.graph)['failed'],0)
        with self.graph.driver.session() as session:
            old = session.run('MATCH(f:Fact {fact_key:$key}) RETURN f.active AS active,f.contributor_count AS n',key=self.c['fact_key']).single()
            self.assertFalse(old['active']); self.assertEqual(old['n'],0)
        facts = self.facts()
        self.assertEqual(len(facts),1)
        self.assertEqual(facts[0]['fact']['qualifiers']['polarity'],'negative')
        self.assertEqual(self.graph.reconcile(first)['status'],'stale')

    def test_graph_new_human_decision_same_revision_retires_previous_assessment(self):
        self.f.accept(self.cid); apply_outbox(self.f.repo,self.graph)
        self.f.accept(self.cid); apply_outbox(self.f.repo,self.graph)
        with self.graph.driver.session() as session:
            rows = list(session.run('MATCH(c:CandidateContribution {candidate_id:$cid}) RETURN c.active AS active',cid=self.cid))
        self.assertEqual(sum(r['active'] for r in rows),1)
        self.assertEqual(len(rows),2)
        self.assertEqual(len(self.facts()[0]['assessments']),1)

    def test_graph_shared_fact_withdrawal_and_concurrency(self):
        other = self.f.candidate(1)
        self.f.accept(self.cid); self.f.accept(other['candidate_id'])
        states = [self.f.repo.current_admitted_contributions(cid) for cid in (self.cid,other['candidate_id'])]
        with ThreadPoolExecutor(2) as pool: list(pool.map(self.graph.reconcile,states))
        key = self.c['fact_key']
        with self.graph.driver.session() as session:
            self.assertEqual(session.run('MATCH(f:Fact {fact_key:$k}) RETURN f.contributor_count AS n',k=key).single()['n'],2)
        self.f.feedback.submit(self.f.request(self.cid,'reject',reason_code='FACT_INCORRECT'))
        apply_outbox(self.f.repo,self.graph)
        with self.graph.driver.session() as session:
            self.assertEqual(session.run('MATCH(f:Fact {fact_key:$k,active:true}) RETURN f.contributor_count AS n',k=key).single()['n'],1)

    def test_graph_qualified_literal_and_query(self):
        negative = self.f.candidate(1,qualifiers={'polarity':'negative'})
        conditional = self.f.candidate(2,qualifiers={'condition':'enabled','modality':'can'})
        literal = self.f.candidate(3,literal=True)
        for c, quote in ((negative,'A does not connect B.'),(conditional,'If enabled, A can connect B.'),(literal,'A has mass 1.00 kg.')):
            self.f.accept(c['candidate_id'],quote)
        result = apply_outbox(self.f.repo,self.graph)
        self.assertEqual(result['failed'],0,result)
        facts = self.facts()
        self.assertEqual(len(facts),3)
        self.assertTrue(all(r['assessments'] and r['fact']['release_ids'] for r in facts))
        keys = [r['fact']['fact_key'] for r in facts]
        with self.graph.driver.session() as session:
            self.assertEqual(session.run('MATCH (:Entity)-[r:KG_FACT]->(:Entity) WHERE r.fact_key IN $ks RETURN count(r) AS n',ks=keys).single()['n'],0)
            self.assertEqual(session.run('MATCH(e:Evidence) WHERE e.semantic_status IS NOT NULL RETURN count(e) AS n').single()['n'],0)

    def test_graph_atomic_rollback_and_crash_retry(self):
        self.f.accept(self.cid)
        state = self.f.repo.current_admitted_contributions(self.cid)
        def failing(tx):
            self.graph._transaction(tx,state)
            raise RuntimeError('injected crash before commit')
        with self.graph.driver.session() as session:
            with self.assertRaises(RuntimeError): session.execute_write(failing)
            self.assertEqual(session.run('MATCH(p:CandidateProjection {candidate_id:$cid}) RETURN count(p) AS n',cid=self.cid).single()['n'],0)
        self.graph.reconcile(state)  # Crash after graph commit, before SQLite ack.
        self.assertEqual(reconcile_candidate(self.f.repo,self.graph,self.cid)['status'],'unchanged')

    def test_graph_legacy_relationships_are_cleaned_by_current_authority(self):
        key = 'legacy_'+self.cid
        with self.graph.driver.session() as session:
            session.run('''CREATE(h:Entity {entity_key:$head}),(t:Entity {entity_key:$tail}),
                (f:Fact {fact_key:$key,active:true}),(c:CandidateContribution {contribution_key:$ck,candidate_id:$cid,revision:1,active:true})
                CREATE(h)-[:FACT_SUBJECT]->(f),(f)-[:FACT_OBJECT]->(t),(c)-[:SUPPORTS_FACT]->(f),
                      (h)-[:R_legacy {fact_key:$key,active:true}]->(t)''',
                head='oldhead_'+self.cid,tail='oldtail_'+self.cid,key=key,ck='old_'+self.cid,cid=self.cid).consume()
        self.assertFalse(self.f.repo.current_admitted_contributions(self.cid)['desired'])
        result = apply_outbox(self.f.repo,self.graph,scan=True)
        self.assertEqual(result['failed'],0,result)
        with self.graph.driver.session() as session:
            self.assertFalse(session.run('MATCH(f:Fact {fact_key:$key}) RETURN f.active AS active',key=key).single()['active'])
            self.assertEqual(session.run('MATCH(:Entity)-[r]->(:Entity) WHERE r.fact_key=$key RETURN count(r) AS n',key=key).single()['n'],0)

    def test_graph_periodic_repair_without_outbox_and_anchor_dedup(self):
        self.f.accept(self.cid)
        apply_outbox(self.f.repo,self.graph)
        with self.graph.driver.session() as session:
            session.run('MATCH (:Entity)-[r:KG_FACT {fact_key:$key}]->(:Entity) DELETE r',key=self.c['fact_key']).consume()
        self.assertEqual(self.f.repo.graph_pending_candidates(),[])
        apply_outbox(self.f.repo,self.graph,scan=True)
        with self.graph.driver.session() as session:
            self.assertEqual(session.run('MATCH (:Entity)-[r:KG_FACT {fact_key:$key}]->(:Entity) RETURN count(r) AS n',key=self.c['fact_key']).single()['n'],1)
        other = self.f.candidate(1); self.f.accept(other['candidate_id']); apply_outbox(self.f.repo,self.graph)
        with self.graph.driver.session() as session:
            row = session.run('MATCH(f:Fact {fact_key:$key}) RETURN f.source_anchor_count AS n,f.contributor_count AS cs',key=self.c['fact_key']).single()
        self.assertEqual((row['n'],row['cs']),(1,2))
