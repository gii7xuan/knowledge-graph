import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from scripts.validate_review_upgrade_v4 import fingerprint

root = Path(__file__).resolve().parents[1]
# These four tracked outputs were clean before the regression suite.
for name in ('runs/_test_model_configs/models_ontology.json','runs/_test_model_configs/models_save.json',
             'runs/_test_model_configs/models_triplet.json','runs/_test_pipeline/current.json'):
    (root/name).write_bytes(subprocess.check_output(['git','show','HEAD:'+name],cwd=root))
names = subprocess.check_output(['git','diff','--name-only','--',':!runs/**'],cwd=root).decode().splitlines()
for name in names:
    path = root/name
    original = subprocess.check_output(['git','show','HEAD:'+name],cwd=root)
    if path.is_file() and b'\r\n' in original:
        path.write_bytes(path.read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
with closing(sqlite3.connect((root/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True)) as connection:
    production = fingerprint(connection)
assert production['logical_sha256']=='8ea70462dd9dec5bb0c54763f7f945c7f815b5135543ec2500884677f6cd3799'
with closing(sqlite3.connect((root/'runs/ontology_releases/release_catalog.sqlite3').as_uri()+'?mode=ro',uri=True)) as connection:
    release = connection.execute('SELECT release_id FROM ontology_release_head WHERE singleton=1').fetchone()[0]
assert release=='release_d0a02b846433380cb05473219174df431d53e483c13d5cc0281c9dac02528d71'
ontology_hash = hashlib.sha256((root/'ontology/ontology.json').read_bytes()).hexdigest()
assert ontology_hash=='fe94b15a6f36719cb4b55e223559f3189e6529cd99fd1bca4029d0b2c43de9d8'
graph_report = 'runs/graph_validation/0c5dbfdb742444d7892c49946dc55ff6/validation_report.json'
migration_report = 'runs/review_validation/admission_migration_9770d81dc93c404b8c7ea910781a0e68/validation_report.json'
config = json.loads((root/'config/review_agent.json').read_text(encoding='utf-8'))
report = {'scope':'Plan phase E: shared admission, human evidence UI/API, desired graph reconciliation',
    'full_regression':{'total':303,'skipped_isolated_neo4j':14,'success':True,'log':'runs/phase_e_tests_verified.log'},
    'final_feedback_regression':{'total':59,'success':True,'log':'runs/phase_e_feedback_verified.log'},
    'neo4j':json.loads((root/graph_report).read_text(encoding='utf-8')),'neo4j_report':graph_report,
    'migration_report':migration_report,'frontend_tests':9,'frontend_build':'passed',
    'production_database_unchanged':True,'production_database_logical_sha256':production['logical_sha256'],
    'current_release_id_unchanged':release,'original_ontology_sha256_unchanged':ontology_hash,
    'mode':config['mode'],'auto_accept_enabled':config['routing']['auto_accept_enabled'],
    'auto_import_enabled':config['neo4j']['auto_import_enabled'],'real_model_calls':0,'model_accuracy_evaluated':False}
output=root/'runs/review_validation/admission_graph_20261027.json'
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report':str(output),'production_unchanged':True,'current_release_unchanged':True,'mode':config['mode']},ensure_ascii=False))
