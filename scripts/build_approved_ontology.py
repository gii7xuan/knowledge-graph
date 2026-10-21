"""Compile accepted ontology candidates into a run-scoped extraction snapshot."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ontologyBuild.ontology_adapter import (
    class_reference_index,
    iter_classes,
    iter_datatype_properties,
    iter_object_properties,
    normalize_name,
)
from review_agent.ontology_guard import validate_ontology


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_Ballistics_{digest}"


def add_unique(values: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and normalize_name(text) not in {normalize_name(item) for item in values}:
        values.append(text)


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def relation_index(ontology: dict[str, Any], kind: str) -> dict[str, str]:
    iterator = iter_object_properties if kind == "object_property" else iter_datatype_properties
    result: dict[str, str] = {}
    for item in iterator(ontology):
        relation_id = str(item.get("id") or "").strip()
        if not relation_id:
            continue
        for value in (relation_id, item.get("label"), item.get("name"), item.get("field")):
            if value:
                result.setdefault(normalize_name(value), relation_id)
    return result


def add_alias(class_item: dict[str, Any], alias: str) -> None:
    if not alias or normalize_name(alias) in {
        normalize_name(class_item.get("id")), normalize_name(class_item.get("label"))
    }:
        return
    aliases = class_item.setdefault("aliases", [])
    add_unique(aliases, alias)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="从已通过候选构建运行专用本体快照")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base", default="ontology/ontology.json")
    parser.add_argument("--output")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    run_dir = ROOT / "runs" / "ontology" / args.run_id
    review_path = run_dir / "review_result.json"
    base_path = (ROOT / args.base).resolve() if not Path(args.base).is_absolute() else Path(args.base)
    output_path = Path(args.output) if args.output else run_dir / "approved_ontology.json"
    if not output_path.is_absolute():
        output_path = (ROOT / output_path).resolve()
    review = json.loads(review_path.read_text(encoding="utf-8"))
    ontology = copy.deepcopy(json.loads(base_path.read_text(encoding="utf-8")))
    accepted = [item for item in review.get("accepted_candidates", []) if isinstance(item, dict)]
    if not accepted:
        raise ValueError("目标运行没有已通过候选")

    classes = ontology.setdefault("classes", {})
    object_properties = ontology.setdefault("object_properties", {})
    data_properties = ontology.setdefault("data_properties", {})
    if not isinstance(classes, dict) or not isinstance(object_properties, dict) or not isinstance(data_properties, dict):
        raise TypeError("基础本体必须使用字典形式的 classes/object_properties/data_properties")

    report: dict[str, Any] = {
        "run_id": args.run_id,
        "source_review": str(review_path),
        "base_ontology": str(base_path),
        "output": str(output_path),
        "accepted_candidates": len(accepted),
        "materialized": {"classes": 0, "object_properties": 0, "data_properties": 0, "constraints": 0, "mappings": 0},
        "matched_existing": {"classes": 0, "object_properties": 0, "data_properties": 0},
        "skipped": [],
        "base_repairs": [],
        "alias_conflicts": [],
    }

    # The checked-in base has dangling relation endpoints. Add explicit stubs in
    # this run-scoped snapshot so every relation reference is resolvable.
    refs = class_reference_index(ontology)
    for prop in list(iter_object_properties(ontology)):
        for field in ("domain", "range"):
            for value in as_list(prop.get(field)):
                text = str(value or "").strip()
                if text and normalize_name(text) not in refs:
                    classes[text] = {
                        "id": text,
                        "label": text,
                        "description": "从基础本体悬空关系端点补齐的占位类别。",
                        "parent": None,
                        "placeholder": True,
                    }
                    report["base_repairs"].append({"class_id": text, "source": "dangling_relation_endpoint"})
                    refs = class_reference_index(ontology)

    base_class_ids = {str(item.get("id")) for item in iter_classes(ontology) if item.get("id")}
    refs = class_reference_index(ontology)
    synonym_mapping = ontology.setdefault("synonym_mapping", {})

    # Apply reviewed mappings first so class candidates can reuse mapped targets.
    for candidate in accepted:
        if candidate.get("candidate_type") != "mapping":
            continue
        mention = str(candidate.get("text_mention") or "").strip()
        target = str(candidate.get("mapped_to") or "").strip()
        if not mention or not target:
            report["skipped"].append({"candidate_id": candidate.get("candidate_id"), "reason": "mapping_missing_endpoint"})
            continue
        synonym_mapping[mention] = target
        if candidate.get("mapped_type") == "class" and normalize_name(target) in refs:
            target_id = refs[normalize_name(target)]
            owner = refs.get(normalize_name(mention))
            if owner and owner != target_id:
                report["alias_conflicts"].append({"alias": mention, "existing": owner, "requested": target_id})
            else:
                add_alias(classes[target_id], mention)
                refs[normalize_name(mention)] = target_id
        report["materialized"]["mappings"] += 1

    pending_parents: list[tuple[str, str]] = []
    for candidate in accepted:
        if candidate.get("candidate_type") != "class":
            continue
        name = str(candidate.get("normalized_name") or candidate.get("name") or "").strip()
        if not name:
            report["skipped"].append({"candidate_id": candidate.get("candidate_id"), "reason": "class_missing_name"})
            continue
        matched = str(candidate.get("matched_existing_class") or "").strip()
        target_id = refs.get(normalize_name(matched)) if matched else refs.get(normalize_name(name))
        if target_id:
            owner = refs.get(normalize_name(name))
            if not owner or owner == target_id:
                add_alias(classes[target_id], name)
                refs[normalize_name(name)] = target_id
            report["matched_existing"]["classes"] += 1
            continue
        class_id = stable_id("C", name)
        if class_id not in classes:
            classes[class_id] = {
                "id": class_id,
                "label": name,
                "description": str(candidate.get("definition_candidate") or candidate.get("reason") or ""),
                "parent": None,
                "aliases": [],
                "approved_candidate_id": candidate.get("candidate_id"),
                "approved_from_run": args.run_id,
                "evidence": candidate.get("evidence") or [],
            }
            report["materialized"]["classes"] += 1
        refs[normalize_name(name)] = class_id
        refs[normalize_name(class_id)] = class_id
        parent = str(candidate.get("parent_candidate") or "").strip()
        if parent:
            pending_parents.append((class_id, parent))

    refs = class_reference_index(ontology)
    for class_id, proposed_parent in pending_parents:
        parent_id = refs.get(normalize_name(proposed_parent))
        # Only attach to base/stub classes; proposed links among generated classes
        # remain metadata to avoid introducing unreviewed cycles.
        classes[class_id]["proposed_parent"] = proposed_parent
        if parent_id in base_class_ids and parent_id != class_id:
            classes[class_id]["parent"] = parent_id

    object_index = relation_index(ontology, "object_property")
    data_index = relation_index(ontology, "datatype_property")
    object_catalog = {
        str(item.get("id")): item for item in iter_object_properties(ontology) if item.get("id")
    }
    data_catalog = {
        str(item.get("id")): item for item in iter_datatype_properties(ontology) if item.get("id")
    }

    def resolved_class_values(*values: Any) -> list[str]:
        resolved: list[str] = []
        current_refs = class_reference_index(ontology)
        for value in values:
            for part in as_list(value):
                target = current_refs.get(normalize_name(part))
                if target:
                    add_unique(resolved, target)
        return resolved

    for candidate in accepted:
        candidate_type = candidate.get("candidate_type")
        if candidate_type not in {"object_property", "datatype_property"}:
            continue
        name = str(candidate.get("normalized_name") or candidate.get("name") or "").strip()
        if not name:
            report["skipped"].append({"candidate_id": candidate.get("candidate_id"), "reason": f"{candidate_type}_missing_name"})
            continue
        if candidate_type == "object_property":
            index = object_index
            target = object_properties
            matched = str(candidate.get("matched_existing_property") or "").strip()
            relation_id = index.get(normalize_name(matched)) if matched else index.get(normalize_name(name))
            if relation_id:
                report["matched_existing"]["object_properties"] += 1
                prop = object_properties.get(relation_id)
                if isinstance(prop, dict):
                    aliases = prop.setdefault("aliases", [])
                    add_unique(aliases, name)
                continue
            relation_id = stable_id("R", name)
            target[relation_id] = {
                "id": relation_id,
                "label": name,
                "description": str(candidate.get("definition_candidate") or candidate.get("reason") or ""),
                "domain": resolved_class_values(candidate.get("domain_candidate")),
                "range": resolved_class_values(candidate.get("range_candidate")),
                "approved_candidate_id": candidate.get("candidate_id"),
                "approved_from_run": args.run_id,
            }
            object_index[normalize_name(name)] = relation_id
            object_index[normalize_name(relation_id)] = relation_id
            report["materialized"]["object_properties"] += 1
        else:
            index = data_index
            target = data_properties
            matched = str(candidate.get("matched_existing_property") or "").strip()
            relation_id = index.get(normalize_name(matched)) if matched else index.get(normalize_name(name))
            if relation_id:
                report["matched_existing"]["data_properties"] += 1
                continue
            relation_id = stable_id("D", name)
            target[relation_id] = {
                "id": relation_id,
                "label": name,
                "name": name,
                "field": relation_id,
                "datatype": candidate.get("range_datatype") or "string",
                "unit": candidate.get("unit") or None,
                "domain": resolved_class_values(candidate.get("domain_candidate")),
                "description": str(candidate.get("definition_candidate") or candidate.get("reason") or ""),
                "approved_candidate_id": candidate.get("candidate_id"),
                "approved_from_run": args.run_id,
            }
            data_index[normalize_name(name)] = relation_id
            data_index[normalize_name(relation_id)] = relation_id
            report["materialized"]["data_properties"] += 1

    for candidate in accepted:
        if candidate.get("candidate_type") != "constraint":
            continue
        name = str(candidate.get("property_name") or "").strip()
        kind = candidate.get("property_type")
        if not name or kind not in {"object_property", "datatype_property"}:
            report["skipped"].append({"candidate_id": candidate.get("candidate_id"), "reason": "constraint_missing_property"})
            continue
        if kind == "object_property":
            relation_id = object_index.get(normalize_name(name))
            if not relation_id:
                relation_id = stable_id("R", name)
                object_properties[relation_id] = {"id": relation_id, "label": name, "domain": [], "range": []}
                object_index[normalize_name(name)] = relation_id
                report["materialized"]["object_properties"] += 1
            elif relation_id not in object_properties:
                object_properties[relation_id] = copy.deepcopy(object_catalog[relation_id])
            prop = object_properties[relation_id]
            prop.setdefault("domain", [])
            prop.setdefault("range", [])
            for value in resolved_class_values(candidate.get("domain_candidate")):
                add_unique(prop["domain"], value)
            for value in resolved_class_values(candidate.get("range_candidate")):
                add_unique(prop["range"], value)
        else:
            relation_id = data_index.get(normalize_name(name))
            if not relation_id:
                relation_id = stable_id("D", name)
                data_properties[relation_id] = {
                    "id": relation_id, "label": name, "name": name, "field": relation_id,
                    "datatype": candidate.get("range_datatype") or "string", "domain": [],
                }
                data_index[normalize_name(name)] = relation_id
                report["materialized"]["data_properties"] += 1
            elif relation_id not in data_properties:
                data_properties[relation_id] = copy.deepcopy(data_catalog[relation_id])
            prop = data_properties[relation_id]
            prop.setdefault("domain", [])
            for value in resolved_class_values(candidate.get("domain_candidate")):
                add_unique(prop["domain"], value)
            if candidate.get("range_datatype"):
                prop["datatype"] = candidate["range_datatype"]
        report["materialized"]["constraints"] += 1

    ontology["version"] = f"ballistics-{args.run_id}-{review.get('bulk_approval', {}).get('approval_id', 'approved')}"
    ontology["created_date"] = now_iso()
    ontology["approved_source"] = {
        "ontology_run_id": args.run_id,
        "bulk_approval": review.get("bulk_approval"),
        "candidate_count": len(accepted),
        "note": "运行专用抽取快照；未覆盖仓库正式 ontology/ontology.json。",
    }
    validation = validate_ontology(ontology)
    report["validation"] = validation
    if not validation["valid"]:
        write_json(run_dir / "approved_ontology_build_report.json", report)
        raise RuntimeError(f"生成的本体快照校验失败，共 {len(validation['errors'])} 个错误")

    canonical = json.dumps(ontology, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    report["class_count"] = validation["class_count"]
    report["relation_count"] = validation["relation_count"]
    write_json(output_path, ontology)
    write_json(run_dir / "approved_ontology_build_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
