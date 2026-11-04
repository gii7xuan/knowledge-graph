"""Verify applied-draft provenance and the actual frozen new-audit source archive."""
from copy import deepcopy
from pathlib import Path
from datetime import datetime,timezone
import json
import sqlite3
from document.hierarchy_index import load_document_index
from evidence.verification import make_evidence_bundle,verify_evidence_bundle
from review_agent.agent_state import AgentState
from review_agent.issue_ledger import IssueLedger
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService,revision_consensus
from review_agent.contracts import sha256_json
from review_agent.proposal_resolver import selected_proposal

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'
summary=json.loads((OUT/'resolution_summary.json').read_text(encoding='utf-8'))
repo=ReviewRepository(summary['backup']);items=[];documents={}
baseline=sqlite3.connect(Path(summary['baseline_backup']).as_uri()+'?mode=ro',uri=True)
old_applied={r[0] for r in baseline.execute("SELECT draft_id FROM revision_drafts WHERE status='applied'")};baseline.close()
service=RevisionService(repo);applied_proofs=[]
with repo.connect() as connection:
    identities=[r[0] for r in connection.execute("SELECT draft_id FROM revision_drafts WHERE status='applied'") if r[0] not in old_applied]
for identity in identities:
    draft=service.draft(identity);result=draft['review_result']
    original={k:v for k,v in draft.items() if k not in {'status','review_result','draft_hash'}}
    if sha256_json(original)!=draft['draft_hash']:raise RuntimeError('applied draft content changed')
    proposal={k:v for k,v in draft.items() if k not in {'status','review_result'}}
    proposal.update(committee_members=result['committee_members'],committee_policy=result['committee_policy'])
    if sha256_json(proposal)!=result['proposal_digest']:raise RuntimeError('revision committee binding changed')
    proposal['proposal_digest']=result['proposal_digest']
    if result['committee_policy']!={'committee_size':3,'required_votes':3,'minimum_distinct_families':3}:
        raise RuntimeError('applied draft reduced the original committee')
    if {v['model_family'].casefold() for v in result['votes']}!={'qwen','gemma','deepseek'}:
        raise RuntimeError('applied draft changed the configured model families')
    if not revision_consensus(result['votes'],proposal)['approved']:raise RuntimeError('applied draft lacks exact unanimous approval')
    with repo.connect() as connection:
        saved={r['voter_id']:json.loads(r['vote_json']) for r in connection.execute('SELECT voter_id,vote_json FROM revision_votes WHERE draft_id=?',(identity,))}
    if saved!={v['voter_id']:v for v in result['votes']}:raise RuntimeError('revision result does not match persisted original votes')
    applied_proofs.append({'draft_id':identity,'candidate_id':draft['candidate_id'],'base_revision':draft['base_revision'],
        'exact_three_model_unanimous_approval':True,'original_vote_rows_match_frozen_result':True})
for current in repo.list_candidates(summary['run_id'],limit=1000000):
    candidate=repo.get_revision(current['candidate_id'])
    if not (candidate.get('revision_provenance') or {}).get('draft_id'):continue
    audit=repo.get_audit(candidate['candidate_id'])
    if audit['revision']!=candidate['revision']:raise RuntimeError('no audit for current revision: '+candidate['candidate_id'])
    state=AgentState(candidate,deepcopy(audit['context']),audit['audit_id'])
    IssueLedger(state).inherit_reviewed_revision_evidence(repo)
    manifest=repo.get_run(candidate['run_id']);key=(manifest['document_path'],candidate['source_index_hash'])
    if key not in documents:documents[key]=load_document_index(*key,require_verified=True)
    document=documents[key]
    expected=verify_evidence_bundle(make_evidence_bundle(state.archive,['applied_draft']),document,candidate)
    if expected.get('rejected_items'):raise RuntimeError('draft archive contains invalid document anchors')
    actual=[e for p in audit['rounds'] for e in p['evidence_bundle']['items']]
    actual.extend((audit.get('agent_state') or {}).get('state',{}).get('archive',[]))
    verified=verify_evidence_bundle(make_evidence_bundle(actual,['frozen_new_revision_audit']),document,candidate)
    if verified.get('rejected_items'):raise RuntimeError('persisted audit contains invalid document anchors')
    allowed={e['evidence_id'] for e in verified['items']}
    if not set(state.protected_ids)<=allowed:raise RuntimeError('material source not carried to actual audit: '+candidate['candidate_id'])
    complete=bool(audit['context'].get('independent_review_completed'))
    if complete:
        proposal,votes=selected_proposal(repo,candidate,repo.latest_decision(candidate['candidate_id']))
        if len(votes)!=3 or {v['model_family'].casefold() for v in votes}!={'qwen','gemma','deepseek'}:
            raise RuntimeError('completed new revision lacks three eligible original-model votes')
    items.append({'candidate_id':candidate['candidate_id'],'revision':candidate['revision'],'audit_id':audit['audit_id'],
        'verified_archive_count':len(verified['items']),'protected_count':len(state.protected_ids),
        'prior_approval_reused_for_current_revision':False,'frozen_new_audit_contains_all_protected_source_ids':True,
        'independent_completed':complete,'eligible_three_model_binding_verified':complete})
result={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'backup':summary['backup'],
    'production_database_modified':False,'model_calls':0,'all_protected_material_verified':True,
    'new_applied_draft_count':len(applied_proofs),'all_new_applied_drafts_have_exact_original_three_model_approval':True,
    'applied_revision_proofs':applied_proofs,'count':len(items),'items':items}
(OUT/'evidence_handoff_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in {'items','applied_revision_proofs'}},ensure_ascii=False))
