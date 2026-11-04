from pathlib import Path
import json
import sqlite3
from ontology_review.review_payload import validator_payload,PAYLOAD_VERSION

ROOT=Path(__file__).resolve().parents[1]
c=sqlite3.connect((ROOT/'runs/v3/review.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=15)
row=c.execute("SELECT proposal_json FROM ontology_proposals p JOIN ontology_audits a ON a.audit_id=p.audit_id WHERE json_extract(a.context_json,'$.versions.planner_version')='ontology_extension_planner_v2' ORDER BY p.rowid DESC LIMIT 1").fetchone()
p=json.loads(row[0]);p['versions']['payload_version']=PAYLOAD_VERSION
payload=validator_payload(p)
result={'before_chars':len(row[0]),'after_chars':len(json.dumps(payload,ensure_ascii=False)),
    'base_graph_chars':len(payload['source_patch']['base_rdf']),'result_graph_chars':len(payload['source_patch']['merged_rdf']),
    'base_index_items':len(payload['base_schema']['items']),'result_index_items':payload['merged_schema']['item_count'],
    'both_complete_graph_hashes_verified':payload['source_patch']['graph_hashes_verified_before_delivery']}
(ROOT/'runs/review_followup_20261030/delivery_measurement.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2));c.close()
