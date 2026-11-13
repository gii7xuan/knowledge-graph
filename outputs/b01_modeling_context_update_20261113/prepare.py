"""Prepare unchanged nine-operation delta with verified context and new prompt."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[1]
sys.path.insert(0,str(ROOT))
from evidence.hierarchical_retriever import document_sentence_evidence
from evidence.verification import verify_evidence_bundle,make_evidence_bundle
from ontology_review import modeling_context,semantic_competency
from ontology_review.contracts import proposal_digest,versions,RESPONSE_SCHEMA
from ontology_review.evidence import load_sources
from ontology_review.runtime import OntologyReviewRuntime
from ontology_review.review_payload import validator_payload
from ontology_review.validators import prompt_for,response_errors
from ontology_review.publisher import current_release_id
from ontologyBuild.release_service import load_release
from review_agent.contracts import sha256_json
from review_agent.cli import _configured_validators
from review_agent.service import load_config
from review_agent.case_budget import CaseBudget
from review_agent.shadow_revision import clone

SOURCE=ROOT/'outputs/b01_revision_after_review_20261112'


def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    if (OUT/'started.json').exists():raise ValueError('Do not overwrite a frozen local preparation')
    previous=read(SOURCE/'prepared_manifest.json');old=read(SOURCE/'prepared_proposal.json')
    summary=read(SOURCE/'modeling_summary_20261113.json')
    assert summary['cumulative_model_calls']==19
    protected={**read(SOURCE/'protected_inputs.json'),**{str(p):digest(p) for p in [
        SOURCE/'prepared_manifest.json',SOURCE/'prepared_proposal.json',SOURCE/'prepared_competency_request.json',
        SOURCE/'modeling_votes_20261113.json',SOURCE/'modeling_summary_20261113.json',
        SOURCE/'user_authorization_20261113.json',SOURCE/'modeling_delivery_verification_20261113.json',
        Path(previous['database'])]}}
    write(OUT/'protected_inputs.json',protected)
    write(OUT/'started.json',{'client_date':'2026-11-13','observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'modeling_prompt_and_verified_context_update_only','new_model_calls_authorized':0})
    assert sha256_json(prompt_for(old))==old['versions']['prompt_hash']
    old_votes=read(SOURCE/'modeling_votes_20261113.json')
    for vote in old_votes:assert not response_errors({k:vote[k] for k in RESPONSE_SCHEMA['required']},old)
    diagnostics=modeling_context.suggestion_diagnostics(next(v for v in old_votes if v['voter_id']=='judge_a'),old)
    assert any(d['code']=='SUGGESTION_EQUALS_PROPOSED_STATE' and d['target']=='C_ad498a2e8a3eb22fdd25' for d in diagnostics)
    write(OUT/'historical_suggestion_diagnostics.json',{'diagnostics_only':True,'original_vote_modified':False,
        'original_valid_vote_remains':'revise','diagnostics':diagnostics})
    repo=clone(Path(previous['database']),OUT/'workspace',allowed_root=ROOT/'outputs')
    releases=OUT/'workspace/releases';releases.mkdir()
    base_id=old['base_release_id']
    shutil.copytree(Path(previous['release_root'])/base_id,releases/base_id)
    with sqlite3.connect((Path(previous['release_root'])/'release_catalog.sqlite3').as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(releases/'release_catalog.sqlite3') as dst:src.backup(dst)
    task=repo.extension_status(old['task_id']);evidence,documents,candidates,cases=load_sources(repo,task)
    document=next(iter(documents.values()));candidate=next(iter(candidates.values()))
    text=document['source_snapshot']['canonical_text']
    quoted=read(SOURCE/'review_findings_20261113.json')['form_factor_exact_source']
    start,end=quoted['start'],quoted['end'];assert text[start:end]==quoted['quote']
    section=min([s for s in document['sections_by_id'].values() if s.get('start',0)<=start<end<=s.get('end',0)
        and any(c['section_id']==s['section_id'] and c['document_start']<end and c['document_end']>start for c in document['chunks'])],key=lambda s:s['end']-s['start'])
    raw=document_sentence_evidence(document,{'document_start':start,'document_end':end,'section_id':section['section_id'],
        'sentence_id':'source-form-factor-ratio-definition','text':text[start:end]},level='other_section')
    verified=verify_evidence_bundle(make_evidence_bundle([raw],['other_section']),document,candidate)
    assert not verified['rejected_items'] and len(verified['items'])==1
    fresh={e['evidence_id']:e for e in [*evidence,*verified['items']]}
    write(OUT/'new_source_evidence.json',verified['items'])
    repo.extension_update(task['extension_id'],task['row_version'],changes={
        'review_evidence':list(fresh.values()),'review_invalidated_by_human':True,
        'human_revision_inputs':{'delta_hash':old['delta_hash'],'evidence_hash':old['evidence_hash']},
        'human_events':[*task.get('human_events',[]),{'action':'review_context_update','actor_kind':'ai',
            'operator':'codex_modeling_context_preparation','reason':'明确类型、端点和建议可执行性核验，补充系数比值原文；未改9项本体操作，未运行外部审核。'}]})
    task=repo.extension_status(task['extension_id'])
    assert task['delta']['operations']==old['operations'] and task['delta']['owl_patch']==old['source_patch']
    evidence,documents,candidates,cases=load_sources(repo,task)
    config=load_config();voters=_configured_validators(config)
    runtime=OntologyReviewRuntime(repo,releases,voters,None,config)
    runtime.base=load_release(releases/base_id);runtime.documents=documents;runtime.candidates=candidates
    runtime.context={'versions':versions(),'committee_members':old['committee_members'],'committee_policy':old['committee_policy'],
        'planner_model':None,'planner_family':None,'options_hash':sha256_json(runtime.options),'mode':'shadow',
        'configured_strategy':'operator_prepared','executed_strategy':'verified_modeling_context_update','independent_approval':False}
    audit=repo.start_ontology_audit(task['extension_id'],runtime.context,'ontology_audit_'+uuid4().hex)
    runtime.budget=CaseBudget({**runtime.options['budget'],'max_llm_calls_per_candidate':3,'max_total_tokens':300000,
        'total_timeout_seconds':600},repo,cases,task_type='ontology_extension',task_id=audit)
    runtime.state={'audit_id':audit,'task':task,'delta':task['delta'],'evidence':evidence,'checks':{},'history':[],
        'validations':{},'validation':None,'fingerprints':{},'critical_issues':[],'full_schema_searched':False,
        'stop_reason':'local_modeling_context_update_no_network','step_no':0,'prior_counter_evidence_ids':[]}
    for number,(name,execute) in enumerate([
        ('existing_schema',lambda:runtime.tools.search_existing_schema(' '.join(sorted({o['target'] for o in old['operations']})))),
        ('structure',runtime.tools.validate_schema_structure),('impact',runtime.tools.analyze_schema_impact),
        ('competency',runtime.tools.run_competency_checks)],1):
        result=execute();repo.ontology_step(audit,number,'tool',{'tool':name,**result})
    assert runtime.state['checks']['structure']['status']=='pass'
    runtime.state['checks']['regression']=deepcopy(old['check_records']['regression'])
    runtime.budget.usage['validation_rounds']=1
    proposal=runtime.freeze_proposal(runtime.state)
    proposal.update(semantic_context_delivery=old['semantic_context_delivery'],semantic_coverage_mode=old['semantic_coverage_mode'])
    proposal['proposal_digest']=proposal_digest(proposal)
    modeling_context.verify(proposal)
    assert proposal['operations']==old['operations'] and proposal['source_patch']==old['source_patch']
    assert proposal['versions']['prompt_version']==modeling_context.PROMPT_VERSION
    assert len(proposal['evidence'])==18
    repo.ontology_proposal(proposal);runtime.checkpoint('ready')
    request,_=semantic_competency.request_for(repo,releases,proposal)
    write(OUT/'prepared_proposal.json',proposal)
    write(OUT/'prepared_validator_payload.json',validator_payload(proposal,delivery_version='ontology_validator_payload_v3'))
    write(OUT/'prepared_competency_request.json',request)
    write(OUT/'modeling_context.json',proposal['modeling_context'])
    (OUT/'review_prompt_v3.txt').write_text(prompt_for(proposal),encoding='utf-8')
    codes={str(p.relative_to(ROOT)):digest(p) for folder in ('ontology_review','ontologyBuild','review_agent','evidence','document','schema','prompts')
        for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.json','.txt'}}
    codes['config/review_agent.json']=digest(ROOT/'config/review_agent.json')
    with repo.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM ontology_votes WHERE audit_id=?',(audit,)).fetchone()[0]==0
    assert all(digest(Path(path))==value for path,value in protected.items())
    assert current_release_id(ROOT/'runs/ontology_releases')==current_release_id(releases)==base_id
    manifest={'client_date':'2026-11-13','database':str(Path(repo.database_path).resolve()),'release_root':str(releases),
        'source_reviewed_proposal_digest':old['proposal_digest'],'proposal_digest':proposal['proposal_digest'],'audit_id':audit,
        'extension_id':task['extension_id'],'object_revision':proposal['object_revision'],'round_id':proposal['round_id'],
        'operations':9,'ontology_operations_changed':False,'rdf_patch_changed':False,'evidence_spans':18,
        'prompt_version':proposal['versions']['prompt_version'],'prompt_hash':proposal['versions']['prompt_hash'],
        'modeling_context_hash':proposal['modeling_context']['context_hash'],
        'local_structure':'pass','prior_valid_votes_unchanged':True,'old_approvals_reused':False,
        'new_model_calls':0,'cumulative_model_calls':19,'additional_calls_authorized':0,
        'production_written':False,'published':False,'code_fingerprints':codes,
        'preparation_script_sha256':digest(Path(__file__))}
    write(OUT/'prepared_manifest.json',manifest)
    print(json.dumps({k:manifest[k] for k in ('operations','ontology_operations_changed','evidence_spans','prompt_version','new_model_calls','cumulative_model_calls')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
