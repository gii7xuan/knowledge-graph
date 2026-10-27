"""Isolated OWL publication, source preservation and real Neo4j validation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4
from scripts.isolated_neo4j import isolated_neo4j
from scripts.validate_acceptance_t01_t40 import fingerprint_production

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output'); parser.add_argument('--neo4j-home',default='D:/ProgramData/neo4j-community-2025.11.2')
    args=parser.parse_args()
    folder=Path(args.output or ROOT/'runs/owl_extension_validation'/uuid4().hex).resolve()
    folder.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(ROOT/'tests'))
    production=fingerprint_production()
    report={'scope':'OWL/Turtle full-source delta publication; test fixtures only','real_model_calls':0,'model_accuracy_evaluated':False}
    prior=os.environ.get('KG_TEST_NEO4J_URI')
    try:
        with isolated_neo4j(folder/'neo4j',args.neo4j_home) as graph:
            os.environ['KG_TEST_NEO4J_URI']=graph['uri']
            with (folder/'tests.log').open('w',encoding='utf-8') as log:
                result=unittest.TextTestRunner(stream=log,verbosity=2,buffer=True).run(unittest.defaultTestLoader.loadTestsFromName('tests.test_owl_extension_publication'))
            report.update(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),success=result.wasSuccessful())
            if result.wasSuccessful():
                from tests.test_owl_extension_publication import OwlPublicationFlowTests
                from to_neo4j.fact_projection import Neo4jImporter
                from to_neo4j.reconciler import apply_outbox
                from to_neo4j.query_facts import query_facts
                case=OwlPublicationFlowTests(); case.fixture_output=folder/'demo'
                # Retain this dedicated source/audit/release fixture for review.
                case.test_full_owl_review_publish_rebind_fresh_review_and_human_admission()
                h,release=case.flow
                importer=Neo4jImporter(graph['uri'],user='',repository=h.repo)
                try:
                    sync=apply_outbox(h.repo,importer)
                    candidate=h.repo.get_candidate(h.candidate['candidate_id'])
                    facts=query_facts(importer.driver,entity_id=candidate['triple']['head_entity_id'])
                finally: importer.close()
                assert sync['failed']==0 and len(facts)==1
                (folder/'graph_facts.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
                for name,data in (('ontology_audit',h.audit),('new_triple_audit',h.repo.get_audit(candidate['candidate_id'])),('candidate',candidate)):
                    (folder/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                report['demo']={'base_release':str(h.base.path),'new_release':str(release.path),'candidate_id':candidate['candidate_id'],
                    'revision':candidate['revision'],'new_ontology_path':str(release.snapshot_path),'sync':sync,'active_fact_count':len(facts),
                    'base_source_unchanged':hashlib.sha256((h.base.path/'ontology.ttl').read_bytes()).hexdigest()==h.base.manifest['artifacts']['ontology.ttl'],
                    'source_format':'turtle','owl_patch_path':str(release.path/'owl_patch.json')}
            report['neo4j']=graph
    finally:
        if prior is None: os.environ.pop('KG_TEST_NEO4J_URI',None)
        else: os.environ['KG_TEST_NEO4J_URI']=prior
        report['production_unchanged']=production==fingerprint_production()
        (folder/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(folder/'validation_report.json'),**report},ensure_ascii=False,indent=2))
    if not report.get('success') or not report['production_unchanged']: raise SystemExit(1)


if __name__=='__main__': main()
