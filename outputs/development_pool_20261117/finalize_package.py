"""Zero-call packaging correction: one immutable run per frozen source document."""
from pathlib import Path
import hashlib
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from evaluation.freeze_shared_ontology import read,write
from evaluation.development_pool import bind_source_runs,ordered_sample,build_pilot,TARGET
from review_agent.cli import _configured_validators
from ontologyBuild.release_service import load_release
from runs.regress_v8 import configuration

out=Path(__file__).resolve().parent
original=read(out/'all_extracted_candidates.json')
selected=ordered_sample(bind_source_runs(original),TARGET)
documents={d['document_id']:d for d in read(out/'source_manifest.json')['documents']}
release=load_release(read(out/'protocol.json')['fixed_release']['ontology_release_path'])
config=configuration(profile='weighted_diagnostic',directory=out)
voters=_configured_validators(config)
write(out/'candidates.json',selected)
build_pilot(out,selected,documents,release,voters,config)
summary=read(out/'packaging_v1_archive/intake_summary.json')
summary.update(dataset_packaging_version='document_run_binding_only_v1',packaging_model_calls=0,
    packaging_semantic_changes=0,initial_package_archive='packaging_v1_archive',
    binding_correction_reason='Controlled experiment loader requires one source index per run; original extracted objects and raw receipts remain unchanged.')
write(out/'intake_summary.json',summary)
write(out/'documents.json',sorted({d['index_path'] for d in documents.values()}))
write(out/'pilot/candidates.json',[s['candidate'] for s in read(out/'pilot/review_samples.json')['samples']])
files=[ROOT/'evaluation/development_pool.py',ROOT/'evaluation/pilot_annotations.py',Path(__file__)]
write(out/'packaging_code_hashes.json',{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
print({'selected':len(selected),'independent_source_runs':len({c['run_id'] for c in selected}),'new_model_calls':0})
