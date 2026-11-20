"""One shared definition decision, with release-bound subscribers and source evidence."""
import json
from .contracts import sha256_json,canonical_json


def sync(repository,state):
    from schema.relation_registry import RelationRegistry
    report=state.ontology_lookup.get('mapping_preflight') or {}
    direct=state.candidate['triple'].get('relation_id')
    exact={i for row in report.get('confusable_relations',[]) for i in row['candidate_ids']}
    exact|={i for row in report.get('search_trace',[]) for i in row.get('exact_match_ids',[])}
    assigned_types={state.candidate['triple'].get(side+'_type') for side in ('head','tail')}
    rows=list(report.get('relation_candidates',[]))
    seen={r['id'] for r in rows}
    for entity in report.get('entities',{}).values():
        for term in entity.get('type_candidates',[]):
            if not term.get('definition_available') and term['class_id'] not in seen:
                rows.append({'id':term['class_id'],'term_kind':'class',
                    'missing_information':['definition_missing_in_bound_release']})
                seen.add(term['class_id'])
    tasks=[]
    with repository.transaction(immediate=True) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS shared_definition_tasks(task_id TEXT PRIMARY KEY,release_id TEXT NOT NULL,term_id TEXT NOT NULL,change_kind TEXT NOT NULL,status TEXT NOT NULL,task_json TEXT NOT NULL)')
        for row in rows:
            if 'definition_missing_in_bound_release' not in row.get('missing_information',[]):continue
            release=state.candidate.get('ontology_release_id')
            key='definition_'+sha256_json([release,row['id'],'revise_definition'])[:32]
            previous=connection.execute('SELECT task_json FROM shared_definition_tasks WHERE task_id=?',(key,)).fetchone()
            task=json.loads(previous[0]) if previous else {'task_id':key,'ontology_release_id':release,
                'term_id':row['id'],'change_kind':'revise_definition','status':'evidence_collection',
                'term_kind':row.get('term_kind','property'),
                'subscribers':{},'publication_requires_human':True,'automatically_published':False}
            task['subscribers'][state.candidate['candidate_id']]={'revision':state.candidate['revision'],
                'candidate_content_hash':sha256_json(state.candidate),
                'audit_id':state.audit_id,'evidence_hash':state.bundle.get('evidence_hash'),
                'evidence_ids':[e['evidence_id'] for e in state.bundle.get('items',[])],
                'priority':'direct' if row['id']==direct or row['id'] in assigned_types else 'exact_competitor' if row['id'] in exact else 'weak_recall'}
            task['affected_candidates']=len(task['subscribers'])
            rank={'direct':0,'exact_competitor':1,'weak_recall':2}
            task['priority']=min((s['priority'] for s in task['subscribers'].values()),key=rank.get)
            task['weak_recall_is_change_necessity']=False
            connection.execute('INSERT INTO shared_definition_tasks VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET task_json=excluded.task_json',
                (key,release,row['id'],'revise_definition',task['status'],canonical_json(task)))
            tasks.append(task)
    return tasks


def link(repository,release_id,term_id,extension_id):
    key='definition_'+sha256_json([release_id,term_id,'revise_definition'])[:32]
    with repository.transaction(immediate=True) as connection:
        row=connection.execute('SELECT task_json FROM shared_definition_tasks WHERE task_id=?',(key,)).fetchone()
        if not row:return
        task=json.loads(row[0]);task.update(extension_id=extension_id,status='proposal_created')
        connection.execute('UPDATE shared_definition_tasks SET status=?,task_json=? WHERE task_id=?',
                           (task['status'],canonical_json(task),key))
