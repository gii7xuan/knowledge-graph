import copy
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_DIR = ROOT / "ontology"
BUILD_DIR = ROOT / "ontologyBuild"

BASE_ONTOLOGY_PATH = ONTOLOGY_DIR / "ontology.json"
BUILD_CANDIDATES_PATH = BUILD_DIR / "ontology_candidates.json"
BUILD_MODULES_PATH = BUILD_DIR / "ontology_modules.json"
BUILD_RAW_CANDIDATES_PATH = BUILD_DIR / "ontology_candidates_raw.json"

OUTPUT_ONTOLOGY_PREVIEW_PATH = BUILD_DIR / "ontology_preview.json"
OUTPUT_MODULES_PATH = ONTOLOGY_DIR / "ontology_modules.json"
OUTPUT_TOPIC_MAPPING_PATH = ONTOLOGY_DIR / "topic_schema_mapping.json"

RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)

RELATION_ALIASES = {
    "包含": "具有部件",
    "包括": "具有部件",
    "由...组成": "具有部件",
    "由……组成": "具有部件",
    "组成": "具有部件",
    "构成": "具有部件",
    "隶属于": "属于",
    "属于": "属于",
    "作用于": "作用于",
    "影响": "影响",
    "决定": "由……决定",
    "导致": "导致",
    "产生": "产生",
    "用于": "用于",
    "应用于": "用于",
}

MODULE_CLASS_HINTS = {
    "外弹道子本体": {"弹道", "弹丸", "空气阻力", "阻力", "射程", "速度", "坐标", "弹道方程"},
    "内弹道子本体": {"膛压", "装药", "发射药", "燃烧", "初速", "压力", "温度"},
    "火炮结构子本体": {"火炮", "炮身", "炮闩", "炮架", "机构", "装置", "部件", "自动机"},
    "火炮设计子本体": {"设计", "模型", "方法", "计算", "优化", "校核", "理论"},
}

INSTANCE_PATTERNS = (
    re.compile(r"[A-Z]{1,6}[-_]?\d{1,5}", re.IGNORECASE),
    re.compile(r"\d{2,4}\s*(式|型|mm|毫米|cm|厘米|m/s|MPa)", re.IGNORECASE),
    re.compile(r"(试验编号|样机|型号|编号|No\.|Type)\s*[\w-]+", re.IGNORECASE),
)


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def stable_id(prefix, value):
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def normalize_name(value):
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("……", "...").replace("—", "-")
    return text.casefold()


def clean_value(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def item_name(item):
    return (
        item.get("name")
        or item.get("property_name")
        or item.get("text_mention")
        or item.get("normalized_name")
        or ""
    )


def item_confidence(item):
    for key in ("confidence", "mapping_confidence"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return 0.0


def ensure_candidate_id(section, item, index):
    if item.get("candidate_id"):
        return item["candidate_id"]
    return f"{section}_{stable_id('cand', item_name(item) + str(index))}"


def add_issue(item, rule, message, severity="review"):
    issues = item.setdefault("normalization_issues", [])
    issues.append({"rule": rule, "severity": severity, "message": message})
    if severity in {"review", "error"}:
        item["need_review"] = True


def class_indexes(ontology):
    by_id = {}
    by_name = {}
    for class_id, data in (ontology.get("classes") or {}).items():
        if not isinstance(data, dict):
            continue
        values = {class_id, data.get("id"), data.get("label"), data.get("name"), data.get("field")}
        by_id[class_id] = data
        for value in values:
            key = normalize_name(value)
            if key:
                by_name[key] = class_id
    return by_id, by_name


def property_indexes(ontology):
    by_label = {}
    by_id = {}
    for prop_id, data in (ontology.get("object_properties") or {}).items():
        if not isinstance(data, dict):
            continue
        by_id[prop_id] = data
        for value in (prop_id, data.get("id"), data.get("label"), data.get("name")):
            key = normalize_name(value)
            if key:
                by_label[key] = prop_id

    for class_data in (ontology.get("classes") or {}).values():
        if not isinstance(class_data, dict):
            continue
        for prop in class_data.get("object_properties") or []:
            if not isinstance(prop, dict):
                continue
            prop_id = prop.get("id") or prop.get("label") or prop.get("name")
            if prop_id:
                by_id.setdefault(prop_id, prop)
            for value in (prop_id, prop.get("label"), prop.get("name")):
                key = normalize_name(value)
                if key:
                    by_label.setdefault(key, prop_id)
    return by_id, by_label


def resolve_class_id(value, class_name_index):
    key = normalize_name(value)
    return class_name_index.get(key)


def relation_label(item):
    return item.get("name") or item.get("property_name") or item.get("text_mention") or ""


def canonical_relation_name(value):
    value = clean_value(value)
    if not value:
        return None
    normalized = normalize_name(value)
    for alias, canonical in RELATION_ALIASES.items():
        if normalize_name(alias) == normalized:
            return canonical
    return value


def is_instance_like(name):
    text = str(name or "")
    return any(pattern.search(text) for pattern in INSTANCE_PATTERNS)


def merge_duplicate_candidates(candidates):
    canonical_by_key = {}
    result = {field: [] for field in RESULT_FIELDS}

    for section in RESULT_FIELDS:
        for index, original in enumerate(candidates.get(section) or []):
            if not isinstance(original, dict):
                continue
            item = copy.deepcopy(original)
            item["candidate_id"] = ensure_candidate_id(section, item, index)
            name = item_name(item)
            key = (section, normalize_name(name))
            if section in {"candidate_object_properties", "candidate_constraints"}:
                key = (
                    section,
                    normalize_name(item.get("property_name") or item.get("name")),
                    normalize_name(item.get("domain_candidate")),
                    normalize_name(item.get("range_candidate") or item.get("range_datatype")),
                )
            elif section == "candidate_datatype_properties":
                key = (
                    section,
                    normalize_name(name),
                    normalize_name(item.get("domain_candidate")),
                    normalize_name(item.get("range_datatype")),
                )
            if not key[1]:
                result[section].append(item)
                continue
            previous = canonical_by_key.get(key)
            if previous is None:
                item.setdefault("normalization", {})["duplicate_role"] = "canonical"
                canonical_by_key[key] = item
                result[section].append(item)
                continue

            previous.setdefault("merged_duplicates", []).append(
                {
                    "candidate_id": item["candidate_id"],
                    "name": name,
                    "confidence": item_confidence(item),
                    "source_module": item.get("source_module"),
                    "evidence": item.get("evidence", []),
                }
            )
            if item_confidence(item) > item_confidence(previous):
                previous["confidence"] = item_confidence(item)
            previous.setdefault("normalization", {})["duplicate_role"] = "canonical"
    return result


def normalize_classes(candidates, ontology):
    _, class_name_index = class_indexes(ontology)
    candidate_class_names = {
        normalize_name(item.get("name")): item
        for item in candidates.get("candidate_classes", [])
        if isinstance(item, dict) and item.get("name")
    }

    for item in candidates.get("candidate_classes", []):
        name = item.get("name")
        item["normalized_name"] = clean_value(item.get("normalized_name")) or clean_value(name)
        class_id = resolve_class_id(name, class_name_index) or resolve_class_id(item.get("normalized_name"), class_name_index)
        if class_id:
            item["status"] = "existing"
            item["matched_existing_class"] = class_id
            item.setdefault("normalization", {})["matched_by"] = "label_or_id"
        elif is_instance_like(name):
            item["status"] = "instance_like"
            add_issue(item, "class_instance_distinction", "候选名称更像具体型号、编号或实验实例，应人工判断是否为实例。")
        else:
            item.setdefault("status", "new_candidate")

        parent = clean_value(item.get("parent_candidate"))
        if parent:
            parent_id = resolve_class_id(parent, class_name_index)
            parent_candidate = candidate_class_names.get(normalize_name(parent))
            if parent_id:
                item["parent_candidate"] = parent_id
                item.setdefault("normalization", {})["parent_resolved_to"] = parent_id
            elif parent_candidate:
                item["parent_candidate"] = parent_candidate.get("name")
                add_issue(item, "parent_candidate_is_new", "上位类也是本轮候选，需要人工审核后才能形成正式继承关系。")
            else:
                add_issue(item, "parent_candidate_missing", "上位类不在基础本体或本轮候选类中。")
    return candidates


def normalize_relations_and_constraints(candidates, ontology):
    _, class_name_index = class_indexes(ontology)
    property_by_id, property_by_label = property_indexes(ontology)
    candidate_class_names = {
        normalize_name(item.get("name")): item
        for item in candidates.get("candidate_classes", [])
        if isinstance(item, dict) and item.get("name")
    }

    for section in ("candidate_object_properties", "candidate_constraints"):
        for item in candidates.get(section, []):
            if not isinstance(item, dict):
                continue
            raw_name = relation_label(item)
            canonical = canonical_relation_name(raw_name)
            if section == "candidate_object_properties":
                item["name"] = canonical
                item["normalized_name"] = canonical
            else:
                item["property_name"] = canonical

            prop_id = property_by_label.get(normalize_name(canonical))
            if prop_id:
                if section == "candidate_object_properties":
                    item["status"] = "existing"
                    item["matched_existing_property"] = prop_id
                else:
                    item["constraint_status"] = "existing"
                    item["matched_existing_constraint"] = prop_id
                item.setdefault("normalization", {})["relation_canonicalized_to"] = prop_id
            elif raw_name != canonical:
                item.setdefault("normalization", {})["relation_canonicalized_to"] = canonical

            domain = clean_value(item.get("domain_candidate"))
            range_candidate = clean_value(item.get("range_candidate"))
            domain_id = resolve_class_id(domain, class_name_index)
            range_id = resolve_class_id(range_candidate, class_name_index)

            if domain and domain_id:
                item["domain_candidate"] = domain_id
            elif domain and normalize_name(domain) in candidate_class_names:
                add_issue(item, "domain_is_new_candidate", "domain 是本轮候选类，不能未经审核写入正式约束。")
            elif domain:
                add_issue(item, "domain_not_found", "domain 不在基础本体或本轮候选类中。")

            if range_candidate and range_id:
                item["range_candidate"] = range_id
            elif range_candidate and normalize_name(range_candidate) in candidate_class_names:
                add_issue(item, "range_is_new_candidate", "range 是本轮候选类，不能未经审核写入正式约束。")
            elif range_candidate:
                add_issue(item, "range_not_found", "range 不在基础本体或本轮候选类中。")

            if prop_id:
                prop = property_by_id.get(prop_id) or {}
                allowed_domain = set(prop.get("domain") or [])
                allowed_range = set(prop.get("range") or [])
                if allowed_domain and item.get("domain_candidate") in class_name_index.values():
                    if item.get("domain_candidate") not in allowed_domain:
                        add_issue(item, "domain_range_consistency", "domain 与已有关系约束不一致。")
                if allowed_range and item.get("range_candidate") in class_name_index.values():
                    if item.get("range_candidate") not in allowed_range:
                        add_issue(item, "domain_range_consistency", "range 与已有关系约束不一致。")

    for item in candidates.get("candidate_datatype_properties", []):
        domain = clean_value(item.get("domain_candidate"))
        domain_id = resolve_class_id(domain, class_name_index)
        if domain and domain_id:
            item["domain_candidate"] = domain_id
        elif domain and normalize_name(domain) in candidate_class_names:
            add_issue(item, "domain_is_new_candidate", "domain 是本轮候选类，不能未经审核写入正式数据属性。")
        elif domain:
            add_issue(item, "domain_not_found", "domain 不在基础本体或本轮候选类中。")
    return candidates


def apply_leakage_policy(candidates, corpus_role, allow_new_concepts):
    for section in RESULT_FIELDS:
        for item in candidates.get(section, []):
            if not isinstance(item, dict):
                continue
            item["corpus_role"] = corpus_role
            item["formal_write_policy"] = (
                "candidate_only"
                if corpus_role != "train" or not allow_new_concepts
                else "eligible_after_review"
            )
            is_new = item.get("status") in {"new_candidate", "instance_like"} or item.get("constraint_status") in {
                "new_candidate",
                "weak_candidate",
            }
            if is_new and item["formal_write_policy"] == "candidate_only":
                add_issue(
                    item,
                    "experiment_data_leakage_guard",
                    "该候选来自非训练写入模式，只允许用于主题定位或进入人工审核队列，不自动加入基础本体。",
                )
    return candidates


def source_module(item):
    module = item.get("source_module")
    return module if isinstance(module, dict) else {}


def topic_key_from_item(item):
    module = source_module(item)
    return (
        module.get("section_id")
        or module.get("breadcrumb_path")
        or first_evidence_chunk(item)
        or "unknown_topic"
    )


def first_evidence_chunk(item):
    evidence = item.get("evidence") or []
    if evidence and isinstance(evidence[0], dict):
        return evidence[0].get("chunk_id")
    return None


def display_class_ref(item):
    return item.get("matched_existing_class") or item.get("normalized_name") or item.get("name")


def build_topic_schema_mapping(candidates, modules):
    topics = {}
    for section in RESULT_FIELDS:
        for item in candidates.get(section, []):
            if not isinstance(item, dict):
                continue
            module = source_module(item)
            topic_id = topic_key_from_item(item)
            topic = topics.setdefault(
                topic_id,
                {
                    "topic_id": topic_id,
                    "topic_path": module.get("breadcrumb_path"),
                    "module_id": module.get("module_id"),
                    "module_name": module.get("module_name"),
                    "related_classes": [],
                    "related_relations": [],
                    "candidate_ids": [],
                    "new_concepts_as_candidates": [],
                    "coverage_status": "uncovered",
                    "coverage_notes": [],
                },
            )
            candidate_id = item.get("candidate_id")
            if candidate_id and candidate_id not in topic["candidate_ids"]:
                topic["candidate_ids"].append(candidate_id)

            if section == "candidate_classes":
                class_ref = display_class_ref(item)
                if class_ref and class_ref not in topic["related_classes"]:
                    topic["related_classes"].append(class_ref)
                if item.get("status") not in {"existing"}:
                    topic["new_concepts_as_candidates"].append(class_ref)
            elif section in {"candidate_object_properties", "candidate_constraints"}:
                rel = item.get("name") or item.get("property_name")
                if rel and rel not in topic["related_relations"]:
                    topic["related_relations"].append(rel)

    for topic in topics.values():
        has_classes = bool(topic["related_classes"])
        has_relations = bool(topic["related_relations"])
        if has_classes and has_relations:
            topic["coverage_status"] = "covered"
        elif has_classes:
            topic["coverage_status"] = "class_only"
            topic["coverage_notes"].append("主题已有相关类，但缺少关系覆盖。")
        elif has_relations:
            topic["coverage_status"] = "relation_only"
            topic["coverage_notes"].append("主题已有相关关系，但缺少类覆盖。")
        else:
            topic["coverage_notes"].append("主题未映射到本体类或关系。")

    module_lookup = {item.get("module_id"): item for item in modules.get("modules", []) if isinstance(item, dict)}
    for topic in topics.values():
        module = module_lookup.get(topic.get("module_id"))
        if module:
            hints = MODULE_CLASS_HINTS.get(module.get("name"), set())
            matched = [value for value in topic["related_classes"] if any(hint in str(value) for hint in hints)]
            if not matched and hints:
                topic["coverage_notes"].append("目录主题与模块关键词覆盖较弱，建议人工检查模块划分。")

    return {
        "generated_at": now_iso(),
        "principle": "章节主题用于定位相关本体模块；测试文档新概念只记录为候选项，不自动写入基础本体。",
        "mappings": sorted(topics.values(), key=lambda item: str(item.get("topic_id"))),
    }


def enrich_modules(modules, topic_mapping):
    enriched = copy.deepcopy(modules)
    module_stats = {}
    for topic in topic_mapping.get("mappings", []):
        module_id = topic.get("module_id") or "unknown"
        stats = module_stats.setdefault(
            module_id,
            {
                "topic_count": 0,
                "covered_topics": 0,
                "related_classes": set(),
                "related_relations": set(),
            },
        )
        stats["topic_count"] += 1
        if topic.get("coverage_status") == "covered":
            stats["covered_topics"] += 1
        stats["related_classes"].update(topic.get("related_classes") or [])
        stats["related_relations"].update(topic.get("related_relations") or [])

    for module in enriched.get("modules", []):
        if not isinstance(module, dict):
            continue
        stats = module_stats.get(module.get("module_id"), {})
        module["coverage"] = {
            "topic_count": stats.get("topic_count", 0),
            "covered_topics": stats.get("covered_topics", 0),
            "related_classes": sorted(stats.get("related_classes", set())),
            "related_relations": sorted(stats.get("related_relations", set())),
        }
        module.setdefault("directory_role", "module_partition")
        module.setdefault("note", "目录树只用于划分子本体模块，不自动生成 subClassOf。")
    enriched["generated_at"] = now_iso()
    return enriched


def build_normalized_ontology(base_ontology, candidates, corpus_role, allow_new_concepts):
    ontology = copy.deepcopy(base_ontology)
    ontology.setdefault("normalization", {})
    ontology["normalization"].update(
        {
            "generated_at": now_iso(),
            "corpus_role": corpus_role,
            "allow_new_concepts_without_review": allow_new_concepts,
            "leakage_guard": (
                "enabled"
                if corpus_role != "train" or not allow_new_concepts
                else "explicit_train_merge_mode"
            ),
            "candidate_policy": "new concepts remain candidates until human review",
        }
    )
    # Conservative default: do not mutate the formal class/property schema from
    # generated candidates. Human-reviewed updates can be applied in a later step.
    ontology["normalization"]["candidate_counts"] = {
        field: len(candidates.get(field, [])) for field in RESULT_FIELDS
    }
    return ontology


def normalize_candidate_data(
    base_ontology,
    raw_candidates,
    modules,
    *,
    corpus_role="build",
    allow_new_concepts=False,
):
    candidates = merge_duplicate_candidates(raw_candidates)
    candidates = normalize_classes(candidates, base_ontology)
    candidates = normalize_relations_and_constraints(candidates, base_ontology)
    candidates = apply_leakage_policy(candidates, corpus_role, allow_new_concepts)

    topic_mapping = build_topic_schema_mapping(candidates, modules)
    enriched_modules = enrich_modules(modules, topic_mapping)
    normalized_ontology = build_normalized_ontology(
        base_ontology,
        candidates,
        corpus_role,
        allow_new_concepts,
    )
    return {
        "candidates": candidates,
        "modules": enriched_modules,
        "topic_mapping": topic_mapping,
        "ontology_preview": normalized_ontology,
        "candidate_counts": {field: len(candidates.get(field, [])) for field in RESULT_FIELDS},
    }


def normalize_outputs(
    base_ontology_path=BASE_ONTOLOGY_PATH,
    candidates_path=BUILD_CANDIDATES_PATH,
    modules_path=BUILD_MODULES_PATH,
    corpus_role=None,
    allow_new_concepts=None,
    ontology_preview_path=OUTPUT_ONTOLOGY_PREVIEW_PATH,
    output_modules_path=OUTPUT_MODULES_PATH,
    output_topic_mapping_path=OUTPUT_TOPIC_MAPPING_PATH,
):
    corpus_role = corpus_role or os.environ.get("ONTOLOGY_CORPUS_ROLE", "build").strip().lower()
    allow_new_concepts = (
        allow_new_concepts
        if allow_new_concepts is not None
        else os.environ.get("ONTOLOGY_ALLOW_NEW_CONCEPTS", "").strip().lower() in {"1", "true", "yes"}
    )
    base_ontology = read_json(base_ontology_path, {"classes": {}, "object_properties": {}, "data_properties": {}})
    raw_candidates = read_json(candidates_path, {field: [] for field in RESULT_FIELDS})
    modules = read_json(
        modules_path,
        {
            "ontology_name": "火炮领域本体",
            "principle": "目录树用于划分子本体模块，不自动生成 subClassOf。",
            "modules": [],
            "chunks": {},
        },
    )

    if Path(candidates_path).exists():
        write_json(BUILD_RAW_CANDIDATES_PATH, raw_candidates)

    normalized = normalize_candidate_data(
        base_ontology,
        raw_candidates,
        modules,
        corpus_role=corpus_role,
        allow_new_concepts=allow_new_concepts,
    )

    write_json(candidates_path, normalized["candidates"])
    write_json(ontology_preview_path, normalized["ontology_preview"])
    write_json(output_modules_path, normalized["modules"])
    write_json(output_topic_mapping_path, normalized["topic_mapping"])
    return {
        "ontology_preview_path": str(ontology_preview_path),
        "modules_path": str(output_modules_path),
        "topic_mapping_path": str(output_topic_mapping_path),
        "candidate_counts": normalized["candidate_counts"],
        "corpus_role": corpus_role,
        "allow_new_concepts": allow_new_concepts,
        "formal_ontology_updated": False,
    }


def main():
    result = normalize_outputs()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
