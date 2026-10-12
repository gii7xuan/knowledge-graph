"""Audit the formal ontology before candidate extraction.

The audit is intentionally read-only. It reports dangling references and
structural risks, but it never repairs ontology/ontology.json automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from ontologyBuild.ontology_adapter import (
        iter_classes,
        iter_datatype_properties,
        iter_object_properties,
        normalize_name,
    )
except ImportError:
    from ontology_adapter import iter_classes, iter_datatype_properties, iter_object_properties, normalize_name


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ONTOLOGY_PATH = ROOT / "ontology" / "ontology.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _add_issue(issues: list[dict[str, Any]], severity: str, rule: str, path: str, message: str, value: Any = None) -> None:
    issues.append(
        {
            "severity": severity,
            "rule": rule,
            "path": path,
            "message": message,
            "value": value,
        }
    )


def _class_ref_index(ontology: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in iter_classes(ontology):
        class_id = str(item.get("id") or "")
        if not class_id:
            continue
        for value in (class_id, item.get("label"), item.get("name")):
            key = normalize_name(value)
            if key:
                result[key] = class_id
    return result


def _resolved_ref(value: Any, ref_index: dict[str, str]) -> str | None:
    if value is None:
        return None
    return ref_index.get(normalize_name(value))


def _detect_parent_cycle(parent_by_child: dict[str, str]) -> list[list[str]]:
    cycles: list[list[str]] = []
    seen_cycle_keys: set[tuple[str, ...]] = set()
    for start in parent_by_child:
        path: list[str] = []
        index_by_node: dict[str, int] = {}
        current = start
        while current:
            if current in index_by_node:
                cycle = path[index_by_node[current]:]
                key = tuple(sorted(cycle))
                if key not in seen_cycle_keys:
                    seen_cycle_keys.add(key)
                    cycles.append(cycle)
                break
            if current in path:
                break
            index_by_node[current] = len(path)
            path.append(current)
            current = parent_by_child.get(current, "")
    return cycles


def _audit_classes(ontology: dict[str, Any], issues: list[dict[str, Any]]) -> None:
    ref_index = _class_ref_index(ontology)
    parent_by_child: dict[str, str] = {}
    seen_labels: dict[str, str] = {}
    for item in iter_classes(ontology):
        class_id = str(item.get("id") or "")
        if not class_id:
            _add_issue(issues, "error", "class_id_missing", "$.classes", "类缺少 id", item)
            continue
        label = item.get("label") or item.get("name")
        if not label:
            _add_issue(issues, "warning", "class_label_missing", f"$.classes.{class_id}", "类缺少 label/name", class_id)
        else:
            label_key = normalize_name(label)
            previous = seen_labels.get(label_key)
            if previous and previous != class_id:
                _add_issue(
                    issues,
                    "warning",
                    "class_label_duplicate",
                    f"$.classes.{class_id}.label",
                    "类 label 与其他类重复，需要人工确认同义或重复类",
                    {"class_id": class_id, "other_class_id": previous, "label": label},
                )
            seen_labels[label_key] = class_id
        if not (item.get("definition") or item.get("description")):
            _add_issue(issues, "warning", "class_definition_missing", f"$.classes.{class_id}", "类缺少 definition/description", class_id)

        parent = item.get("parent")
        if parent:
            resolved_parent = _resolved_ref(parent, ref_index)
            if not resolved_parent:
                _add_issue(issues, "error", "class_parent_missing", f"$.classes.{class_id}.parent", "parent 引用不存在", parent)
            elif resolved_parent == class_id:
                _add_issue(issues, "error", "class_self_parent", f"$.classes.{class_id}.parent", "类不能以自身为 parent", parent)
            else:
                parent_by_child[class_id] = resolved_parent

        for index, subclass in enumerate(_list(item.get("subclasses"))):
            resolved_child = _resolved_ref(subclass, ref_index)
            if not resolved_child:
                _add_issue(
                    issues,
                    "error",
                    "class_subclass_missing",
                    f"$.classes.{class_id}.subclasses[{index}]",
                    "subclasses 引用不存在",
                    subclass,
                )
            elif resolved_child == class_id:
                _add_issue(
                    issues,
                    "error",
                    "class_self_subclass",
                    f"$.classes.{class_id}.subclasses[{index}]",
                    "类不能把自身列为 subclass",
                    subclass,
                )

    for cycle in _detect_parent_cycle(parent_by_child):
        _add_issue(issues, "error", "class_parent_cycle", "$.classes", "parent 层级存在循环", cycle)


def _audit_property_refs(
    issues: list[dict[str, Any]],
    property_type: str,
    properties: Iterable[dict[str, Any]],
    ref_index: dict[str, str],
) -> None:
    for prop in properties:
        prop_id = prop.get("id") or prop.get("label") or prop.get("name") or prop.get("field") or "unknown"
        if property_type == "object_property":
            for field in ("domain", "range"):
                values = _list(prop.get(field))
                if not values:
                    _add_issue(issues, "warning", f"{property_type}_{field}_missing", f"$.{property_type}.{prop_id}.{field}", f"{field} 为空", prop_id)
                for index, value in enumerate(values):
                    if not _resolved_ref(value, ref_index):
                        _add_issue(
                            issues,
                            "error",
                            f"{property_type}_{field}_dangling",
                            f"$.{property_type}.{prop_id}.{field}[{index}]",
                            f"{field} 引用不存在的类",
                            value,
                        )
        else:
            datatype = prop.get("datatype")
            if not datatype:
                _add_issue(issues, "warning", "datatype_property_type_missing", f"$.datatype_property.{prop_id}.datatype", "数据属性缺少 datatype", prop_id)
            for index, value in enumerate(_list(prop.get("domain"))):
                if value and not _resolved_ref(value, ref_index):
                    _add_issue(
                        issues,
                        "error",
                        "datatype_property_domain_dangling",
                        f"$.datatype_property.{prop_id}.domain[{index}]",
                        "数据属性 domain 引用不存在的类",
                        value,
                    )


def audit_ontology(ontology_path: str | Path = DEFAULT_ONTOLOGY_PATH) -> dict[str, Any]:
    path = Path(ontology_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到基础本体文件: {path}")
    with path.open("r", encoding="utf-8") as file:
        ontology = json.load(file)

    issues: list[dict[str, Any]] = []
    _audit_classes(ontology, issues)
    ref_index = _class_ref_index(ontology)
    _audit_property_refs(issues, "object_property", iter_object_properties(ontology), ref_index)
    _audit_property_refs(issues, "datatype_property", iter_datatype_properties(ontology), ref_index)

    severity_counts: dict[str, int] = {}
    for issue in issues:
        severity_counts[issue["severity"]] = severity_counts.get(issue["severity"], 0) + 1

    return {
        "generated_at": now_iso(),
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "passed": severity_counts.get("error", 0) == 0,
        "severity_counts": severity_counts,
        "issues": issues,
        "repair_proposals": [],
        "note": "基础本体审计只报告问题，不自动修改 ontology/ontology.json。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="审计基础本体结构，不修改输入文件")
    parser.add_argument("--input", default=str(DEFAULT_ONTOLOGY_PATH), help="ontology.json 路径")
    parser.add_argument("--report-dir", default="", help="可选报告目录")
    args = parser.parse_args()
    report = audit_ontology(args.input)
    if args.report_dir:
        report_dir = Path(args.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        output_path = report_dir / "base_audit.json"
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output_path)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
