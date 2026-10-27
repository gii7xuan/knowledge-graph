"""Conservative transitive schema impact, frozen with candidate CAS bindings."""
import json
from ontologyBuild.ontology_adapter import class_reference_index, normalize_name
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from .delta import catalog


def dependent_schema_ids(before_schema,after_schema):
    before,after=catalog(before_schema),catalog(after_schema)
    changed={i for i in set(before)|set(after) if before.get(i) != after.get(i)}
    dependents={}
    for schema,items in ((before_schema,before),(after_schema,after)):
        class_refs=class_reference_index(schema)
        for identity,item in items.items():
            references=[]
            for field in ('parent','parents','domain','range','equivalent_to','disjoint_with','inverse_of'):
                values=item.get(field) or []
                references.extend(values if isinstance(values,list) else [values])
            for reference in references:
                resolved=class_refs.get(normalize_name(reference),reference)
                if resolved in items: dependents.setdefault(resolved,set()).add(identity)
            for child in item.get('subclasses') or []:
                resolved=class_refs.get(normalize_name(child),child)
                if resolved in items: dependents.setdefault(identity,set()).add(resolved)
    closure=set(changed); pending=list(changed)
    while pending:
        for identity in dependents.get(pending.pop(),set())-closure:
            closure.add(identity); pending.append(identity)
    return sorted(changed),sorted(closure)


def analyze_impact(repository,base_release_id,before,after,remap):
    changed,dependencies=dependent_schema_ids(before,after)
    old_guard,new_guard=OntologyGuard(load_snapshot(before)),OntologyGuard(load_snapshot(after))
    with repository.connect() as connection:
        rows=connection.execute('SELECT c.candidate_id,c.current_revision,c.row_version,c.human_status,r.candidate_json FROM candidates c JOIN candidate_revisions r ON r.candidate_id=c.candidate_id AND r.revision=c.current_revision ORDER BY c.candidate_id').fetchall()
    affected=[]
    for row in rows:
        candidate=json.loads(row['candidate_json']); triple=candidate.get('triple',{})
        reasons=sorted(set(dependencies)&{triple.get('relation_id'),triple.get('head_type'),triple.get('tail_type')})
        if candidate.get('ontology_release_id') != base_release_id or not reasons: continue
        affected.append({'candidate_id':row['candidate_id'],'revision':row['current_revision'],'row_version':row['row_version'],
            'human_status':row['human_status'],'candidate_content_hash':sha256_json(candidate),'dependency_ids':reasons,
            'old_guard':old_guard.check(candidate),'new_guard':new_guard.check(remap(candidate,after)),
            'required_action':'new_revision_and_reaudit','old_admission_reused':False})
    return {'status':'pass','coverage':'all_current_candidates_with_transitive_schema_dependencies','candidate_rows_scanned':len(rows),
            'changed_schema_ids':changed,'dependent_schema_ids':dependencies,'affected_candidates':affected}
