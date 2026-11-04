"""Preserve prior votes; repair verified baseline definitions in a new dev delta."""
from copy import deepcopy
from importlib.util import spec_from_file_location, module_from_spec
import json
from pathlib import Path
import shutil
import sqlite3

from document.hierarchy_index import load_document_index
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from ontologyBuild.release_service import load_release
from ontology_review.contracts import proposal_digest
from ontology_review.delta import apply_delta
from ontology_review.runtime import OntologyReviewRuntime
from review_agent.human_evidence import source_span
from review_agent.repository import ReviewRepository
from review_agent.service import load_config
from review_agent.cli import _configured_validators
from review_agent.progress import write_status_snapshot

ROOT = Path(__file__).resolve().parents[1]
PREV = ROOT / 'outputs/dev020_release_20261103/revision2'
OUT = ROOT / 'outputs/dev020_release_20261103/revision3'
RELEASES = OUT / 'ontology_releases'


def main():
    OUT.mkdir(parents=True, exist_ok=True); RELEASES.mkdir(exist_ok=True)
    db = OUT / 'development_review.sqlite3'
    if not db.exists():
        with sqlite3.connect((PREV / 'development_review.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(db) as dest: src.backup(dest)
    previous = json.loads((PREV / 'frozen_proposal.json').read_text(encoding='utf-8')); extension = previous['task_id']
    if not (RELEASES / previous['base_release_id']).exists(): shutil.copytree(PREV / 'ontology_releases' / previous['base_release_id'], RELEASES / previous['base_release_id'])
    if not (RELEASES / 'release_catalog.sqlite3').exists():
        with sqlite3.connect((PREV / 'ontology_releases/release_catalog.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(RELEASES / 'release_catalog.sqlite3') as dest: src.backup(dest)
    repo = ReviewRepository(db); task = repo.extension_status(extension); base = load_release(RELEASES / task['base_release_id'])
    candidate = repo.get_revision('cand_52159eb442b708f4e860')
    document = load_document_index(repo.get_run(candidate['run_id'])['document_path'], candidate['source_index_hash'], require_verified=True)
    text = document['source_snapshot']['canonical_text']
    if task['delta_revision'] == previous['object_revision']:
        spans = []
        for first, last in [('摩擦阻力主要是由大气的黏性造成的。', '由于弹丸速度很高'),
                            ('积分的初始条件是', '# 3. 其他自变量'),
                            ('枪弹口径小，弹道系数大', '# 4. 空气弹道')]:
            assert text.count(first) == 1 and text.count(last) == 1
            spans.append(source_span(document, text.index(first), text.index(last, text.index(first))))
        items = {e['evidence_id']: e for e in [*previous['evidence'], *spans]}
        bundle = verify_evidence_bundle(make_evidence_bundle(items.values(), ['baseline_definition_investigation']), document, candidate)
        assert not bundle['rejected_items']
        ops = deepcopy(task['delta']['operations'])
        for op in ops:
            if op['kind'].startswith('add_'): op['target'] = None
            if op['kind'] == 'revise_definition' and op['target'] == 'R_2a66075367666d54e7e7':
                op['changes']['definition'] = '空气耗去弹丸动能的关系。'
                op['evidence_ids'] = [spans[0]['evidence_id']]
        ops.extend([
            {'kind': 'revise_definition', 'target': 'C_66413019bc25928cc9e2',
             'changes': {'definition': '本章弹道运动初始时刻t=0的速度，记为v0。', 'definition_language': 'zh'},
             'evidence_ids': [spans[1]['evidence_id']]},
            {'kind': 'revise_definition', 'target': 'C_0e4d3a5e81f1002bbd3e',
             'changes': {'definition': '本章所讨论的弹道高度，以最大弹道高H表述弹道所达到的最大高度。', 'definition_language': 'zh'},
             'evidence_ids': [spans[2]['evidence_id']]},
        ])
        delta = apply_delta(base, ops, set(items))
        repo.extension_update(extension, task['row_version'], changes={'delta': delta, 'delta_revision': task['delta_revision'] + 1,
            'review_evidence': bundle['items'], 'operator_revision_reason': '修复经原文核对的初速定义和弹道高定义；简化消耗定义避免被读成合并关系。保留原文支持的空气层带动空气层、弹丸具有质量系数关系。'}, status='in_review')
        write_status_snapshot(OUT / 'revised_delta.json', delta)
        write_status_snapshot(OUT / 'verified_context.json', bundle)
    spec = spec_from_file_location('dev020_helper', ROOT / 'runs/continue_dev020_release_20261103.py')
    helper = module_from_spec(spec); spec.loader.exec_module(helper)
    helper.OUT = OUT; helper.RELEASES = RELEASES; helper.SOURCE = PREV; helper.EXT = extension

    class ContextRuntime(OntologyReviewRuntime):
        def freeze_proposal(self, state):
            proposal = super().freeze_proposal(state)
            proposal['semantic_context_delivery'] = 'verified_frozen_spans_v1'
            proposal['proposal_digest'] = proposal_digest(proposal)
            return proposal
    helper.OntologyReviewRuntime = ContextRuntime
    config = deepcopy(load_config()); config.update(mode='shadow', database_path=str(db), ontology_release_root=str(RELEASES))
    config['ontology_extension']['auto_publish_enabled'] = False
    config['ontology_extension'].setdefault('budget', {}).update(per_call_timeout_seconds=300, total_timeout_seconds=600)
    for slot in config['validators']:
        if slot.get('family', '').casefold() in {'qwen', 'gemma'}: slot.update(request_timeout_seconds=300, http_timeout_seconds=300)
    voters = [{**v, 'ontology_delivery_version': 'ontology_validator_payload_v3'} for v in _configured_validators(config)]
    frozen = helper.freeze(repo, config, voters)
    helper.run(repo, config, voters, frozen)


if __name__ == '__main__': main()
