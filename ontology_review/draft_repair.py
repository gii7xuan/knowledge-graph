"""Explicit local repairs of draft modeling errors, without rewriting reviews."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

from ontologyBuild.release_service import load_release
from review_agent.contracts import canonical_json, sha256_json
from review_agent.repository import ConflictError, ValidationError, utc_now
from .delta import apply_delta, catalog
from .evidence import load_sources
from .publisher import current_release_id
from .tools import OntologyTools


def repair_draft(repository, release_root, extension_id, *, expected_row_version,
                 repair_key, action, reason, actor, evidence_ids, resolution,
                 operations=None, review_references=None):
    """Replace/withdraw a draft or record an unresolved reviewer obligation.

    The caller is the local operator. This is not independent approval, source
    truth adjudication, request reconciliation, publication, or candidate edit.
    """
    if action not in {'replace', 'withdraw', 'annotate'}:
        raise ValidationError('unsupported draft repair action')
    if not all(isinstance(value, str) and value.strip() for value in (repair_key, reason, actor)):
        raise ValidationError('repair key, operator and modeling reason required')
    if not isinstance(resolution, dict) or len(canonical_json(resolution)) > 12000:
        raise ValidationError('bounded explicit resolution required')
    request = dict(expected_row_version=expected_row_version, repair_key=repair_key,
        action=action, reason=reason, actor=actor, evidence_ids=evidence_ids,
        resolution=resolution, operations=operations, review_references=review_references or [])
    fingerprint = sha256_json(request)
    task = repository.extension_status(extension_id)
    previous = next((entry for entry in task.get('draft_repair_history', [])
                     if entry['repair_key'] == repair_key), None)
    if previous:
        if previous['request_hash'] != fingerprint:
            raise ConflictError('repair key was already used for a different correction')
        return {'extension_id': extension_id, 'reused': True, 'repair': previous, 'model_calls': 0}
    if task['row_version'] != expected_row_version:
        raise ConflictError('draft repair has a stale task version')
    if task['status'] in {'publishing', 'published', 'rejected', 'needs_rebase'}:
        raise ConflictError('task is not editable as a draft')
    if current_release_id(release_root) != task['base_release_id']:
        raise ConflictError('base release changed; rebase required')
    base = load_release(Path(release_root) / task['base_release_id'])
    if (base.manifest['ontology_version'] != task['base_ontology_version'] or
            base.manifest['profile_hash'] != task['base_profile_hash']):
        raise ConflictError('draft base/profile binding mismatch')
    task = deepcopy(task)
    counter = repository.ontology_counter_history(extension_id)
    combined = {item['evidence_id']: item for item in [*task.get('review_evidence', []), *counter]}
    task['review_evidence'] = list(combined.values())
    evidence, documents, candidates, _ = load_sources(repository, task)
    allowed = {item['evidence_id'] for item in evidence}
    if not isinstance(evidence_ids, list) or not evidence_ids or not set(evidence_ids) <= allowed:
        raise ValidationError('repair requires current verified source evidence IDs')
    if not set(resolution.get('reuse_schema_ids', [])) <= set(catalog(base.ontology)):
        raise ValidationError('reuse resolution references an unknown formal schema ID')
    refs = []
    for reference in review_references or []:
        audit = repository.ontology_audit(reference['audit_id'])
        vote = next((vote for vote in audit['votes'] if vote['voter_id'] == reference['voter_id']
                     and vote['round_id'] == reference['round_id']), None)
        if audit['extension_id'] != extension_id or not vote or vote.get('response_status') != 'ok':
            raise ValidationError('response must refer to an original valid ontology review')
        refs.append({**reference, 'decision': vote['decision'], 'vote_hash': sha256_json(vote),
                     'counter_evidence_ids': vote['counter_evidence_ids']})
    old_delta = deepcopy(task.get('delta'))
    if action == 'replace':
        delta = apply_delta(base, operations, allowed)
        if delta == old_delta:
            raise ValidationError('replacement has no modeling change; annotate the obligation instead')
    elif operations is not None:
        raise ValidationError('only a replacement may contain operations')
    else:
        delta = None if action == 'withdraw' else old_delta
    checks = {}
    if delta:
        runtime = SimpleNamespace(repository=repository, base=base, documents=documents,
            candidates=candidates, state={'task': task, 'delta': delta, 'evidence': evidence,
                                         'checks': {}, 'validation': None})
        tools = OntologyTools(runtime)
        checks = {'structure': tools.validate_schema_structure(),
                  'impact': tools.analyze_schema_impact(),
                  'competency': tools.run_competency_checks()}
        if action == 'replace' and checks['structure']['status'] == 'fail':
            raise ValidationError('replacement failed structure checks')
    next_revision = task['delta_revision'] + (action != 'annotate')
    entry = {'repair_key': repair_key, 'request_hash': fingerprint, 'action': action,
        'reason': reason, 'actor': actor, 'created_at': utc_now(),
        'previous_task_row_version': expected_row_version,
        'previous_delta_revision': task['delta_revision'], 'delta_revision': next_revision,
        'previous_delta': old_delta, 'previous_resolution': task.get('draft_repair_resolution'),
        'new_delta_hash': (delta or {}).get('delta_hash'), 'evidence_ids': evidence_ids,
        'verified_evidence_hash': sha256_json(evidence), 'resolution': resolution,
        'review_references': refs, 'checks': checks, 'independent_approval': False,
        'model_calls': 0, 'publication_performed': False}
    changes = {'delta': delta, 'delta_revision': next_revision,
        'review_evidence': task['review_evidence'],
        'draft_repair_history': [*task.get('draft_repair_history', []), entry],
        'draft_repair_resolution': resolution,
        'human_events': [*task.get('human_events', []), {'action': 'revise' if action != 'annotate' else 'review_response',
            'reason': reason, 'operator': actor, 'delta_revision': next_revision, 'repair_key': repair_key}]}
    if action != 'annotate':
        changes.update(review_invalidated_by_human=True,
            human_revision_inputs={'delta_hash': (old_delta or {}).get('delta_hash'),
                                   'evidence_hash': sha256_json(task['review_evidence'])})
    with repository.transaction(immediate=True) as connection:
        row = connection.execute('SELECT * FROM ontology_extension_tasks WHERE extension_id=?', (extension_id,)).fetchone()
        if row['row_version'] != expected_row_version:
            raise ConflictError('draft changed before repair commit')
        active = connection.execute("SELECT 1 FROM ontology_audits WHERE extension_id=? AND status='running' UNION ALL SELECT 1 FROM jobs WHERE job_type='ontology_extension' AND status IN ('queued','running') AND json_extract(payload_json,'$.extension_id')=? LIMIT 1", (extension_id, extension_id)).fetchone()
        if active:
            raise ConflictError('pause the active ontology task before repairing its draft')
        for subscription in task['subscriptions']:
            if subscription['status'] != 'active':
                continue
            current = connection.execute('SELECT current_revision,row_version,human_status FROM candidates WHERE candidate_id=?', (subscription['candidate_id'],)).fetchone()
            if (current['current_revision'] != subscription['revision'] or
                    current['row_version'] != subscription['expected_row_version'] or current['human_status'] != 'pending'):
                raise ConflictError('trigger candidate changed before repair commit')
        saved = json.loads(row['task_json'])
        saved.update(changes, status='needs_human')
        connection.execute("UPDATE ontology_extension_tasks SET task_json=?,status='needs_human',row_version=row_version+1 WHERE extension_id=?", (canonical_json(saved), extension_id))
    return {'extension_id': extension_id, 'reused': False, 'repair': entry,
            'task_row_version': expected_row_version + 1, 'model_calls': 0}
