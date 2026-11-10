"""Revision deltas and lineage; an old approval never certifies a new expression."""
from copy import deepcopy
import json
from .contracts import sha256_json

VERSION='revision_loop_v1'
FACETS={'relation':('surface_relation',),'entities':('head','tail','tail_kind','value','datatype','unit','language'),
    'negation':('qualifiers.polarity',),'frequency':('qualifiers.frequency','qualifiers.applicability','qualifiers.quantifier'),
    'condition':('qualifiers.condition','qualifiers.time_scope'),'modality':('qualifiers.modality',),
    'meaning_scope':('semantic_context',),'formal_mapping':('relation_id','head_type','tail_type')}


def _value(triple,path):
    obj=triple
    for key in path.split('.'):
        obj=obj.get(key) if isinstance(obj,dict) else None
    return deepcopy(obj)


def changes_between(before,after):
    def walk(a,b,path=''):
        if isinstance(a,dict) and isinstance(b,dict):
            return [v for k in sorted(set(a)|set(b)) for v in walk(a.get(k),b.get(k),(path+'.' if path else '')+k)]
        return [] if a==b else [{'field':path,'before':deepcopy(a),'after':deepcopy(b)}]
    return walk(before['triple'],after['triple'])


def analyze(before,after,bundle=None):
    changes=changes_between(before,after)
    meaningful=[v for v in changes if v['field'] not in {'mapping_status','relation_semantic_hash'}]
    kind='semantic_change'
    # Only exact duplicate removal with the original qualifier still retained is simplified.
    if meaningful and {v['field'] for v in meaningful}=={'surface_relation'} and bundle:
        a,b=before['triple'],after['triple'];q=a.get('qualifiers') or {};states=a.get('qualifier_status') or {}
        for field,value in q.items():
            if not isinstance(value,str) or not value.strip() or states.get(field)!='extracted' or field in {'polarity','modality'}:continue
            relation=a.get('surface_relation') or ''
            expected=(relation[len(value):] if relation.startswith(value) else relation[:-len(value)] if relation.endswith(value) else None)
            if (expected and expected.strip() and b.get('surface_relation')==expected and q==b.get('qualifiers')
                and states==b.get('qualifier_status') and any(e.get('anchor_valid') and value in e.get('quote','') for e in bundle.get('items',[]))):
                kind='lossless_duplicate_cleanup';break
    if meaningful and all(v['field'] in {'relation_id','head_type','tail_type','relation_label'} for v in meaningful):kind='mapping_change'
    facets=[{'facet':name,'changed':any(_value(before['triple'],p)!=_value(after['triple'],p) for p in paths),
        'required':'preservation_check' if kind=='lossless_duplicate_cleanup' and name!='formal_mapping' else 'new_version_evidence_check' if name!='formal_mapping' else 'new_version_mapping_check'}
        for name,paths in FACETS.items()]
    return {'version':VERSION,'kind':kind,'changes':changes,'facets':facets,
        'source_validation_mode':'bounded_preservation' if kind=='lossless_duplicate_cleanup' else 'full_expression',
        'requires_new_evidence_review':True,'requires_new_mapping_review':True,'old_votes_reusable':False,
        'new_revision_is_admission':False}


def lineage(before,after,*,operation='revision',bundle=None):
    return {'version':VERSION,'operation':operation,'parent_candidate_id':before['candidate_id'],
        'parent_revision':before['revision'],'parent_expression_hash':sha256_json(before['triple']),
        'candidate_id':after['candidate_id'],'revision':after['revision'],'expression_hash':sha256_json(after['triple']),
        'analysis':analyze(before,after,bundle),'old_votes_reusable':False,'source_document_id':before.get('document_id'),
        'source_document_version':before.get('document_version')}


def history(connection,candidate_id):
    baseline=connection.execute('SELECT current_revision,row_version FROM candidates WHERE candidate_id=?',(candidate_id,)).fetchone()
    rows=connection.execute('SELECT revision,candidate_json FROM candidate_revisions WHERE candidate_id=? ORDER BY revision',(candidate_id,)).fetchall()
    result=[];previous=None
    for row in rows:
        c=json.loads(row['candidate_json']);link=deepcopy(c.get('revision_lineage'))
        if not link and previous:link=lineage(previous,c)
        decision=connection.execute('SELECT decision_json,active FROM decisions WHERE candidate_id=? AND revision=? ORDER BY decision_version DESC LIMIT 1',(candidate_id,row['revision'])).fetchone()
        d=json.loads(decision[0]) if decision else {}
        result.append({'candidate_id':candidate_id,'revision':row['revision'],'expression':deepcopy(c['triple']),
            'lineage':link,'split_origin':deepcopy(c.get('split_origin')),'decision':{k:d.get(k) for k in ('decision','workflow_state','independent_review_completed','source_support_result','selected_proposal_digest')}|
                {'historical_inactive':bool(decision and not decision['active'])},
            'review_axes':deepcopy(d.get('review_axes') or {}),'is_current':row['revision']==rows[-1]['revision']})
        previous=c
    groups=[]
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='revision_split_groups'").fetchone():
        for row in connection.execute('SELECT group_json,status,result_json FROM revision_split_groups WHERE parent_candidate_id=?',(candidate_id,)):
            group=json.loads(row[0]);stale=row[1]!='applied' and baseline and (baseline['current_revision']!=group['parent_revision'] or baseline['row_version']!=group['expected_row_version'])
            groups.append({'group_id':group['group_id'],'parent_revision':group['parent_revision'],
                'status':'stale' if stale else row[1],'draft_ids':group['draft_ids'],'child_candidate_ids':group['child_candidate_ids'],
                'result':json.loads(row[2])})
    drafts=[]
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='revision_drafts'").fetchone():
        for row in connection.execute('SELECT draft_json,status,result_json FROM revision_drafts WHERE candidate_id=?',(candidate_id,)):
            draft=json.loads(row[0]);review=json.loads(row[2]);stale=row[1]!='applied' and baseline and (
                baseline['current_revision']!=draft['base_revision'] or baseline['row_version']!=draft['expected_row_version'])
            drafts.append({k:deepcopy(draft.get(k)) for k in ('draft_id','draft_hash','base_revision','changed_fields','change_analysis','evidence_ids','operation')}|
                {'before':draft['before']['triple'],'after':draft['after']['triple'],'status':'stale' if stale else row[1],
                 'reviewed_seats':len([v for v in review.get('votes',[]) if v.get('response_status')=='ok']),
                 'approved_seats':len(review.get('approved_voter_ids',[]))})
    return {'version':VERSION,'revisions':result,'split_groups':groups,'drafts':drafts,
        'steps':['original_candidate','revision_proposal','before_after_delta','new_version_evidence','new_version_mapping','final_decision'],
        'old_votes_reusable':False,'split_children_require_individual_review':True}
