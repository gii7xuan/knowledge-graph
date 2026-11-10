"""Run the existing revision service only in an explicitly isolated experiment database."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from .contracts import sha256_json
from .repository import ReviewRepository,ConflictError
from .revision_service import RevisionService

VERSION='isolated_shadow_revision_v1'


def clone(source, directory, *, allowed_root):
    source=Path(source).resolve();directory=Path(directory).resolve();root=Path(allowed_root).resolve()
    if not directory.is_relative_to(root) or directory==root or directory.exists() or directory.is_relative_to(source.parent):
        raise ValueError('shadow experiment requires a new separate directory inside the explicit output root')
    before=hashlib.sha256(source.read_bytes()).hexdigest();directory.mkdir(parents=True)
    target=directory/'review.sqlite3'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:src.backup(dst)
    manifest={'version':VERSION,'source':str(source),'source_sha256':before,'database':str(target),
        'allowed_root':str(root),'mode':'isolated_shadow','max_semantic_revision_rounds':1,'production_written':False}
    (directory/'shadow_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return ReviewRepository(target)


def copy_revision_ancestry(source, target, candidates):
    """Only immutable ancestry and applied draft evidence, never old votes or labels."""
    ids=[c['candidate_id'] for c in candidates]
    drafts={(c.get('revision_provenance') or {}).get('draft_id') for c in candidates}
    drafts|={(c.get('split_origin') or {}).get('draft_id') for c in candidates};drafts.discard(None)
    with source.connect() as src,target.transaction(immediate=True) as dst:
        columns=[r[1] for r in src.execute('PRAGMA table_info(candidate_revisions)')]
        for row in src.execute('SELECT * FROM candidate_revisions WHERE candidate_id IN ('+','.join('?' for _ in ids)+')',ids):
            dst.execute('INSERT OR IGNORE INTO candidate_revisions('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row))
        for identity in sorted(drafts):
            row=src.execute('SELECT * FROM revision_drafts WHERE draft_id=?',(identity,)).fetchone()
            if row is None or row['status']!='applied':raise ValueError('missing immutable applied draft source')
            columns=list(row.keys());values=[row[k] if k!='result_json' else '{}' for k in columns]
            dst.execute('INSERT OR IGNORE INTO revision_drafts('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values)
    return len(drafts)


def apply_reviewed(repository, *, draft_id=None, group_id=None):
    """One revision case per clone; existing independent approval/CAS checks are unchanged."""
    path=Path(repository.database_path).resolve();manifest_path=path.parent/'shadow_manifest.json'
    if not manifest_path.exists():raise ConflictError('shadow application requires a verified isolated clone')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if (manifest.get('database')!=str(path) or manifest.get('mode')!='isolated_shadow' or
        path==Path(manifest['source']).resolve() or not path.is_relative_to(Path(manifest['allowed_root']).resolve()) or
        hashlib.sha256(Path(manifest['source']).read_bytes()).hexdigest()!=manifest['source_sha256']):
        raise ConflictError('shadow source/isolation binding changed')
    marker=path.parent/'revision_started.json'
    if marker.exists():raise ConflictError('one semantic revision round per isolated experiment; no automatic repeat')
    if bool(draft_id)==bool(group_id):raise ValueError('select one exact reviewed draft or split group')
    service=RevisionService(repository)
    drafts=service.split(group_id)['drafts'] if group_id else [service.draft(draft_id)]
    if any(d['status']!='approved' for d in drafts):raise ConflictError('every child must be independently approved before shadow application')
    marker.write_text(json.dumps({'version':VERSION,'draft_ids':[d['draft_id'] for d in drafts],
        'changes':[d.get('change_analysis') for d in drafts],'old_votes_reusable':False}),encoding='utf-8')
    result=service.apply_split(group_id) if group_id else service.apply(draft_id,drafts[0]['draft_hash'])
    result.update(mode='isolated_shadow',production_written=False,verification_status='verification_incomplete',
        quality_success=None,new_rounds_limit=1,parent_case_id=drafts[0]['candidate_id'])
    return result


def verification_result(outcomes, *, previous=None):
    """Engineering result only; human success metrics require separate exact-version labels."""
    outcomes=list(outcomes)
    def confirmed(o,axis,status):return o['dimension_results'][axis].get('judgment_complete') and o['dimension_results'][axis]['status']==status
    if not outcomes:status='verification_incomplete'
    elif any(confirmed(o,'source','contradicted') for o in outcomes):
        status='introduced_new_problem' if previous and confirmed(previous,'source','supported') else 'original_issue_unresolved'
    elif any(confirmed(o,'source','insufficient') for o in outcomes):status='evidence_pending'
    elif any(confirmed(o,'expression','semantic_revision') for o in outcomes):
        status='introduced_new_problem' if previous and confirmed(previous,'expression','standard') else 'original_issue_unresolved'
    elif any(not o.get('review_completed') for o in outcomes):status='verification_incomplete'
    elif all(o.get('recommendation')=='admit' for o in outcomes):status='revision_valid'
    else:status='verification_incomplete'
    return {'version':VERSION,'verification_status':status,'basis':'automated_new_version_engineering_checks',
        'human_confirmed_success':None,'children':deepcopy(outcomes),'all_children_required':True,'old_votes_reused':False}
