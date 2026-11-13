"""Offline bounded proposal preparation. This program has no dispatch/publish path."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from types import SimpleNamespace
from uuid import uuid4

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
sys.path.insert(0, str(ROOT))
from document.hierarchy_index import load_document_index
from evidence.hierarchical_retriever import document_sentence_evidence, _evidence
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from evaluation.review_samples import ReadOnlyRepository
from ontologyBuild.release_service import load_release
from ontologyBuild.rdf_artifact import canonical_turtle, graph_hash, validate_owl_view
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.owl_to_project_schema import convert_turtle
from ontology_review import coverage_inventory, semantic_competency
from ontology_review.contracts import versions, proposal_digest
from ontology_review.delta import catalog, check_structure
from ontology_review.draft_repair import repair_draft
from ontology_review.evidence import load_sources
from ontology_review.impact import analyze_impact
from ontology_review.publisher import current_release_id
from ontology_review.review_payload import validator_payload
from ontology_review.runtime import OntologyReviewRuntime
from ontology_review.tools import remap
from review_agent.agent_state import AgentState
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.contracts import sha256_json
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.service import load_config
from review_agent.shadow_revision import clone
from review_agent.tools import AgentTools
from rdflib import BNode

OLD = ROOT / 'outputs/formal_definitions_20261111_ready'
REVIEW = ROOT / 'outputs/offline_objection_review_20261112'
SOURCE = ROOT / 'outputs/dev30_v8_full_20261111'


def read(path): return json.loads(path.read_text(encoding='utf-8-sig'))
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_proposal(repo, packet):
    decision = repo.latest_decision(packet['candidate']['candidate_id'])
    audit = repo.get_audit(packet['candidate']['candidate_id'], decision['audit_id'])
    return next(p for p in audit['rounds'] if p['proposal_digest'] == packet['binding']['proposal_digest'])


def code_hashes():
    paths = [p for folder in ('ontology_review', 'ontologyBuild', 'review_agent', 'evidence', 'document', 'schema', 'prompts')
             for p in (ROOT / folder).rglob('*') if p.is_file() and p.suffix in {'.py', '.txt', '.json'}]
    paths += [Path(__file__), ROOT / 'config/review_agent.json', ROOT / 'tests/test_competency_inventory.py']
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def verified_offline_anchors(document, candidate):
    anchors, mapping = {}, {}
    for old in read(REVIEW / 'source_evidence.json'):
        start, end = old['start'], old['end']
        sections = [s for s in document['sections_by_id'].values()
                    if s.get('start', 0) <= start < end <= s.get('end', 0)
                    and any(c['section_id'] == s['section_id'] and c['document_start'] < end and c['document_end'] > start for c in document['chunks'])]
        fresh = []
        if sections:
            section = min(sections, key=lambda s: s['end'] - s['start'])
            fresh.append(document_sentence_evidence(document, {'document_start': start, 'document_end': end,
                'section_id': section['section_id'], 'sentence_id': old['paragraph_id'], 'text': old['quote']}, level='other_section'))
        else:
            for c in document['chunks']:
                left, right = max(start, c['document_start']), min(end, c['document_end'])
                if left < right:
                    fresh.append(_evidence(c, left - c['document_start'], right - c['document_start'], 'other_section'))
        bundle = verify_evidence_bundle(make_evidence_bundle(fresh, ['other_section']), document, candidate)
        assert bundle['items'] and not bundle['rejected_items'], (old['evidence_id'], bundle['rejected_items'])
        mapping[old['evidence_id']] = [e['evidence_id'] for e in bundle['items']]
        anchors.update({e['evidence_id']: e for e in bundle['items']})
    return anchors, mapping


def main():
    if (OUT / 'started.json').exists():
        raise ValueError('Preparation already started; inspect artifacts instead of overwriting frozen packets')
    protected = {**read(OLD / 'protected_inputs.json'), **read(REVIEW / 'input_manifest.json')['protected_input_hashes']}
    protected.update({str(p): digest(p) for p in REVIEW.iterdir() if p.is_file()})
    write(OUT / 'started.json', {'at': datetime.now(timezone.utc).isoformat(), 'model_calls_authorized': 0,
        'network_dispatch_implemented': False, 'publish_implemented': False})
    write(OUT / 'protected_inputs.json', protected)
    old = read(OLD / 'prepared_proposal.json')
    base = load_release(ROOT / 'runs/ontology_releases' / old['base_release_id'])
    assert current_release_id(ROOT / 'runs/ontology_releases') == base.manifest['release_id']
    config = load_config()
    voters = _configured_validators(config)  # Lazy clients only; never invoked here.
    roster = [{k: v[k] for k in ('id', 'model_id', 'model_family')} | {
        'model_identity': str(v.get('model_identity') or v['model_id']).casefold()} for v in voters]
    policy = {k: config['consensus'][k] for k in ('committee_size', 'required_votes', 'minimum_distinct_families')}
    assert policy == {'committee_size': 3, 'required_votes': 3, 'minimum_distinct_families': 3}
    packets = {p['sample_id']: p for p in read(SOURCE / 'review_packets.json')['samples']}
    candidate = packets['DEV-010']['candidate']
    readonly = ReadOnlyRepository(SOURCE / 'review.sqlite3')
    run = readonly.get_run(candidate['run_id'])
    document = load_document_index(run['document_path'], candidate['source_index_hash'], require_verified=True)
    source = read(OLD / 'prepared_competency_request.json')['source_documents'][0]
    assert document['source_snapshot']['canonical_text'] == source['text']
    anchors, mapping = verified_offline_anchors(document, candidate)
    anchors.update({e['evidence_id']: deepcopy(e) for e in old['evidence']})
    write(OUT / 'verified_evidence.json', list(anchors.values()))
    write(OUT / 'evidence_id_mapping.json', mapping)
    rows = {r['schema_id']: r for r in read(REVIEW / 'per_id_review.json')}

    def evidence_ids(identity):
        return list(dict.fromkeys(e for previous in rows[identity]['evidence_ids'] for e in mapping[previous]))

    def operation(kind, identity, changes):
        return {'kind': kind, 'target': identity, 'changes': changes, 'evidence_ids': evidence_ids(identity)}

    drafts = {}
    for op in read(REVIEW / 'unapproved_definition_drafts.json')['operations']:
        drafts[op['target']] = operation(op['kind'], op['target'], deepcopy(op['changes']))
    structures = [
        operation('revise_constraint', 'R_2a66075367666d54e7e7', {'range': ['C_a7877ae0fe8183f8b986']}),
        operation('revise_definition', 'R_2a66075367666d54e7e7', {'definition': '表示本章弹丸表面附近空气因黏性作用被带动时消耗弹丸动能的关系；方向为空气到被消耗的动能，所用边界层及飞行条件随来源保留。', 'definition_language': 'zh'}),
        operation('revise_constraint', 'R_e1de72d5c68b765c28b9', {'domain': ['C_c89817eb0fc6a2a0b963'], 'range': ['C_426d7ce8c711071adac8']}),
        operation('revise_definition', 'R_e1de72d5c68b765c28b9', {'definition': '表示本章分离波具有正激波这一组成部分的关系，方向为分离波到正激波；该端点不表示其全部组成部分，也不排除原文同时说明的斜激波。', 'definition_language': 'zh'}),
        operation('revise_constraint', 'R_3c75dd89923bc714630d', {'range': ['C_cbcb15d49fba77017748']}),
        operation('revise_definition', 'R_3c75dd89923bc714630d', {'definition': '表示本章超声速流动讨论中，波动阻力大小主要取决于弹头部形状的关系；方向从波动阻力到头部形状，不断言形状为唯一决定因素。', 'definition_language': 'zh'}),
        operation('revise_constraint', 'C_88ecbdb51d6e44007aec', {'parents': []}),
        deepcopy(drafts['C_afb01b87134088624d72']),
        deepcopy(drafts['C_ad498a2e8a3eb22fdd25']),
        deepcopy(drafts['R_37918d1714e9945397f3']),
    ]
    excluded = {'R_dea5c49458b0cebab6ea', 'R_a66ceee55ad57847f8f4'}
    used = {o['target'] for o in structures}
    remainder = [op for identity, op in drafts.items() if identity not in used | excluded]
    batches = [('B01', '端点、方向、父类和关键定义', structures), ('B02', '原8项定义重新冻结；不继承旧票', deepcopy(old['operations']))]
    pending = []
    for op in remainder:
        ev = {e for item in [*pending, op] for e in item['evidence_ids']}
        if pending and (len(pending) == 16 or len(ev) > 18):
            batches.append((f'B{len(batches)+1:02d}', '既有ID定义增补', pending)); pending = []
        pending.append(op)
    if pending: batches.append((f'B{len(batches)+1:02d}', '既有ID定义增补', pending))
    assert all(1 <= len(ops) <= 16 for _, _, ops in batches)
    write(OUT / 'bounded_operations.json', [{'batch_id': b, 'description': d, 'operations': ops} for b, d, ops in batches])
    write(OUT / 'reuse_decisions.json', {
        'deferred_definition_ids': sorted(excluded), 'automatic_merges': 0,
        'decisions': [
            {'target': 'R_dea5c49458b0cebab6ea', 'reuse_candidate': 'R_0e6aeaed149fdb07cfac', 'decision': 'defer_duplicate_definition', 'reason': '产生/形成具有相同端点，优先审核原形成ID；需另核既有事实迁移，未自动退休或合并。'},
            {'target': 'R_a66ceee55ad57847f8f4', 'reuse_candidate': 'R_2b18880939600d01a389', 'decision': 'defer_scope_and_reuse', 'reason': '来源中的阻力在该段指空气阻力，但两个range类ID不同，不能未经全局指称核验直接合并。'},
            {'targets': ['C_fd01dcbe8ca57c240de4', 'C_24e3afc65ab3b44c6c6e', 'C_3d1e92e945b2c3dbbd16'], 'decision': 'preserve_existing_ids_and_scope_synonym_definitions', 'reason': '仅准备本章同指说明，不增加类、不自动加入等价公理，也不迁移历史事实。'}]})
    candidates = read(REVIEW / 'structural_revision_candidates.json')['candidates']
    fully = {'C_afb01b87134088624d72', 'C_ad498a2e8a3eb22fdd25', 'R_2a66075367666d54e7e7', 'R_e1de72d5c68b765c28b9', 'R_3c75dd89923bc714630d', 'R_37918d1714e9945397f3'}
    dispositions = [{**item, 'preparation_status': 'candidate_prepared' if item['schema_id'] in fully else
        'parent_repair_prepared_definition_unresolved' if item['schema_id'] == 'C_88ecbdb51d6e44007aec' else 'held_for_argument_or_identity_modeling',
        'semantic_approved': False} for item in candidates]
    write(OUT / 'structural_disposition.json', dispositions)
    write(OUT / 'held_items.json', {'original_holds': [r for r in rows.values() if r['recommendation'].startswith('hold_')],
        'structural_holds': [d for d in dispositions if d['preparation_status'] != 'candidate_prepared'],
        'reuse_holds': sorted(excluded), 'original_page_images_available': False,
        'resolution': '原书页图未在本工作区找到；不按常识消解术语冲突，不改旧票或旧未知请求。'})
    repo = clone(SOURCE / 'review.sqlite3', OUT / 'workspace', allowed_root=ROOT / 'outputs')
    repo.initialize()
    releases = OUT / 'workspace/releases'; releases.mkdir()
    shutil.copytree(base.path, releases / base.manifest['release_id'])
    with sqlite3.connect((ROOT / 'runs/ontology_releases/release_catalog.sqlite3').as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(releases / 'release_catalog.sqlite3') as dst:
        src.backup(dst)
    eligible = sorted((p for p in packets.values() if p['sample_id'] not in {'DEV-019','DEV-022','DEV-024','DEV-028'}), key=lambda p: (len(p['evidence_bundle']['items']), p['sample_id']))
    created = []
    # Create every subscription before freezing impact CAS bindings.
    for (batch_id, description, ops), primary in zip(batches, eligible):
        folder = OUT / batch_id; folder.mkdir()
        candidate = primary['candidate']; original_proposal = source_proposal(repo, primary)
        guard = OntologyGuard(load_snapshot(base.ontology))
        helper = SimpleNamespace(state=AgentState(candidate, {}, original_proposal['audit_id']),
            orchestrator=SimpleNamespace(ontology_guard=guard, config={}), issue=lambda *a, **k: None)
        lookup = AgentTools(helper).inspect_ontology(relation_id=candidate['triple']['relation_id'],
            surface_relation=candidate['triple']['surface_relation'], issue='核对现有ID和约束；本批仅在新隔离库准备修订')['existing_schema_search']
        current = repo.get_candidate(candidate['candidate_id'])
        result = repo.create_extension_request(candidate, expected_row_version=current['row_version'], proposal=original_proposal,
            change_kind='revise_constraint' if any(o['kind'] == 'revise_constraint' for o in ops) else 'revise_definition',
            description=description, proposed_changes={'operations': ops},
            evidence_ids=[original_proposal['evidence_bundle']['items'][0]['evidence_id']], full_lookup=lookup)
        identity = result['task']['extension_id']
        with repo.connect() as c:
            job_ids = [r['job_id'] for r in c.execute("SELECT job_id,payload_json FROM jobs WHERE status IN ('queued','running')") if json.loads(r['payload_json']).get('extension_id') == identity]
        for job_id in job_ids: repo.request_cancel(job_id)
        needed = {e for op in ops for e in op['evidence_ids']}
        task = repo.extension_status(identity)
        repo.extension_update(identity, task['row_version'], changes={'review_evidence': [anchors[e] for e in sorted(needed)]})
        task = repo.extension_status(identity)
        receipt = repair_draft(repo, releases, identity, expected_row_version=task['row_version'],
            repair_key='offline-' + batch_id, action='replace', reason='用户要求继续；AI按冻结原文准备有界修订，尚无独立批准。',
            actor='codex_ai_offline_revision', evidence_ids=sorted(needed), operations=ops,
            resolution={'status': 'unapproved_preparation', 'reuse_schema_ids': sorted({o['target'] for o in ops}),
                        'published': False, 'model_calls': 0})
        write(folder / 'draft_preparation_receipt.json', receipt)
        created.append((folder, identity, description))
    assert len(created) == len(batches)
    records = []
    for folder, identity, description in created:
        task = repo.extension_status(identity)
        evidence, documents, candidates, cases = load_sources(repo, task)
        assert len(evidence) <= runtime_evidence_limit(config), (folder.name, len(evidence))
        runtime = OntologyReviewRuntime(repo, releases, voters, None, config)
        runtime.base = load_release(releases / task['base_release_id']); runtime.documents = documents; runtime.candidates = candidates
        runtime.context = {'versions': versions(), 'committee_members': roster, 'committee_policy': policy,
            'planner_model': None, 'planner_family': None, 'options_hash': sha256_json(runtime.options),
            'mode': 'shadow', 'configured_strategy': 'operator_prepared', 'executed_strategy': 'offline_source_bound_delta',
            'no_semantic_vote_fabricated': True}
        audit = repo.start_ontology_audit(identity, runtime.context, 'ontology_audit_' + uuid4().hex)
        runtime.budget = CaseBudget({**runtime.options['budget'], 'max_llm_calls_per_candidate': 3,
            'max_total_tokens': 450000, 'total_timeout_seconds': 600}, repo, cases, task_type='ontology_extension', task_id=audit)
        runtime.state = {'audit_id': audit, 'task': task, 'delta': task['delta'], 'evidence': evidence, 'checks': {},
            'history': [], 'validations': {}, 'validation': None, 'fingerprints': {}, 'critical_issues': [],
            'full_schema_searched': False, 'stop_reason': 'prepared_without_network', 'step_no': 0, 'prior_counter_evidence_ids': []}
        for number, (name, execute) in enumerate([
            ('existing_schema', lambda: runtime.tools.search_existing_schema(' '.join(sorted({o['target'] for o in task['delta']['operations']})))),
            ('structure', runtime.tools.validate_schema_structure), ('impact', runtime.tools.analyze_schema_impact),
            ('competency', runtime.tools.run_competency_checks)], 1):
            result = execute(); repo.ontology_step(audit, number, 'tool', {'tool': name, **result})
        assert runtime.state['checks']['structure']['status'] == 'pass'
        runtime.budget.usage['validation_rounds'] = 1
        proposal = runtime.freeze_proposal(runtime.state)
        proposal.update(semantic_context_delivery='verified_frozen_spans_v1', semantic_coverage_mode=coverage_inventory.MODE)
        proposal['proposal_digest'] = proposal_digest(proposal)
        repo.ontology_proposal(proposal); runtime.checkpoint('ready')
        request, _ = semantic_competency.request_for(repo, releases, proposal)
        assert len(request['coverage_inventory']) == 270 and request['response_contract_version'] == coverage_inventory.VERSION
        write(folder / 'prepared_proposal.json', proposal)
        write(folder / 'prepared_validator_payload.json', validator_payload(proposal, delivery_version='ontology_validator_payload_v3'))
        write(folder / 'prepared_competency_request.json', request)
        write(folder / 'coverage_output_contract.json', semantic_competency.response_schema(request))
        patch = proposal['source_patch']; write(folder / 'owl_patch.json', patch)
        (folder / 'UNAPPROVED_ontology.ttl').write_text(patch['merged_rdf'], encoding='utf-8')
        record = {'batch_id': folder.name, 'description': description, 'extension_id': identity, 'audit_id': audit,
            'proposal_digest': proposal['proposal_digest'], 'delta_hash': proposal['delta_hash'],
            'operation_count': len(proposal['operations']), 'evidence_count': len(evidence),
            'added_triples': len(patch['added']), 'removed_triples': len(patch['removed']),
            'competency_inventory_count': len(request['coverage_inventory']), 'competency_request_hash': request['request_hash'],
            'model_votes': 0, 'semantic_votes': 0, 'approved': False, 'published': False,
            'proposal_sha256': digest(folder / 'prepared_proposal.json'),
            'competency_request_sha256': digest(folder / 'prepared_competency_request.json')}
        records.append(record)
        print(json.dumps({k: record[k] for k in ('batch_id', 'operation_count', 'evidence_count', 'added_triples', 'removed_triples')}, ensure_ascii=False), flush=True)
    # Integrated preview is a union of independent, nonoverlapping named patches,
    # not a >16-operation review task and not a new release base.
    combined = parse_turtle(read(OUT / 'B01/owl_patch.json')['base_rdf'])
    target_batches, all_ops, additions, removals = {}, [], set(), set()
    for batch in records:
        proposal = read(OUT / batch['batch_id'] / 'prepared_proposal.json')
        for op in proposal['operations']:
            assert target_batches.get(op['target'], batch['batch_id']) == batch['batch_id']
            target_batches[op['target']] = batch['batch_id']; all_ops.append(op)
        patch = proposal['source_patch']
        added, removed = parse_turtle('\n'.join(patch['added'])), parse_turtle('\n'.join(patch['removed']))
        assert not any(isinstance(t, BNode) for g in (added, removed) for triple in g for t in triple)
        for triple in removed: assert triple in combined
        combined = combined - removed + added
        additions.update(patch['added']); removals.update(patch['removed'])
    ttl = canonical_turtle(combined)
    converted = convert_turtle(ttl, iri_id_map=read(base.path / 'iri_id_map.json'))
    validate_owl_view(ttl, converted['project_schema'], converted['application_profile'], converted['iri_id_map'], converted['mapping_report'])
    structure = check_structure(converted['project_schema'], base.ontology['application_profile'], base.ontology)
    assert structure['status'] == 'pass'
    impact = analyze_impact(ReadOnlyRepository(SOURCE / 'review.sqlite3'), base.manifest['release_id'], base.ontology, converted['project_schema'], remap)
    (OUT / 'INTEGRATED_UNAPPROVED_PREVIEW.ttl').write_text(ttl, encoding='utf-8')
    write(OUT / 'integrated_schema_preview.json', converted['project_schema'])
    write(OUT / 'integrated_impact.json', impact)
    inventory = coverage_inventory.inventory(converted['project_schema'])
    write(OUT / 'full_coverage_inventory.json', inventory)
    missing = [x['id'] for x in inventory if not x.get('description') and not x.get('definitions')]
    write(OUT / 'remaining_definition_gaps.json', {'count': len(missing), 'schema_ids': missing,
        'formal_definition_coverage_complete': False, 'note': 'Metadata gaps are recorded separately from semantic source support; unresolved structural/source items still block publication.'})
    assert current_release_id(releases) == current_release_id(ROOT / 'runs/ontology_releases') == base.manifest['release_id']
    assert all(digest(Path(path)) == expected for path, expected in protected.items())
    manifest = {'at': datetime.now(timezone.utc).isoformat(), 'base_release_id': base.manifest['release_id'],
        'database': str(Path(repo.database_path).resolve()), 'release_root': str(releases), 'batches': records,
        'total_operations': len(all_ops), 'operation_kinds': dict(Counter(o['kind'] for o in all_ops)),
        'independent_target_ids': len(target_batches), 'integrated_graph_hash': graph_hash(combined),
        'integrated_triple_count': len(combined), 'integrated_added_triples': len(additions), 'integrated_removed_triples': len(removals),
        'integrated_structure': structure, 'missing_definitions_in_integrated_preview': len(missing),
        'all_batches_use_same_approved_base': True, 'integrated_preview_is_release': False,
        'structural_dispositions': dict(Counter(d['preparation_status'] for d in dispositions)),
        'code_fingerprints': code_hashes(), 'annotated_by': 'ai', 'human_gold': False,
        'new_model_api_calls': 0, 'new_external_network_calls': 0, 'cumulative_model_calls': 13,
        'authorized_additional_model_calls': 0, 'committee_votes_reused': False,
        'old_deepseek_fail_replaced': False, 'old_unknown_requests_modified': False,
        'production_written': False, 'published': False}
    write(OUT / 'prepared_manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ('total_operations', 'operation_kinds', 'independent_target_ids', 'missing_definitions_in_integrated_preview', 'structural_dispositions', 'new_model_api_calls')}, ensure_ascii=False), flush=True)


def runtime_evidence_limit(config):
    return int(config.get('ontology_extension', {}).get('max_evidence_items', 24))


if __name__ == '__main__': main()
