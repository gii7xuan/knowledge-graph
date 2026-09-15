import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from pydantic import BaseModel, Field

try:
    from ontologyBuild.ontology_candidate_schema import (
        OntologyCandidateExtractionResult,
        validate_candidate_data,
    )
except ImportError:
    from ontology_candidate_schema import (
        OntologyCandidateExtractionResult,
        validate_candidate_data,
    )


DEFAULT_ONTOLOGY_PATH = Path(__file__).parents[1] / "ontology" / "ontology.json"


class BusinessValidationIssue(BaseModel):
    rule: str
    path: str
    message: str
    value: Any = None


class BusinessValidationReport(BaseModel):
    valid: bool
    issues: List[BusinessValidationIssue] = Field(default_factory=list)


class BusinessProcessingResult(BaseModel):
    """第二层业务校验后的处理结果。"""

    candidates: Dict[str, List[Dict[str, Any]]]
    manual_review: List[Dict[str, Any]] = Field(default_factory=list)
    discarded: List[Dict[str, Any]] = Field(default_factory=list)
    actions: List[Dict[str, Any]] = Field(default_factory=list)


def normalize_name(value: Optional[str]) -> str:
    """规范化名称，用于判断新候选是否与已有本体重复。"""
    if not value:
        return ""
    return "".join(str(value).split()).casefold()


def add_named_element(index: Set[str], element_id: Any, element: Any):
    """将本体元素的 ID、label、name 和 field 加入索引。"""
    if element_id:
        index.add(str(element_id))
    if isinstance(element, dict):
        for field in ("id", "label", "name", "field"):
            value = element.get(field)
            if value:
                index.add(str(value))


def build_ontology_indexes(ontology: Dict[str, Any]) -> Dict[str, Set[str]]:
    """从 ontology.json 构建业务校验所需的已有元素索引。"""
    classes: Set[str] = set()
    object_properties: Set[str] = set()
    datatype_properties: Set[str] = set()

    for class_id, class_data in ontology.get("classes", {}).items():
        add_named_element(classes, class_id, class_data)
        if not isinstance(class_data, dict):
            continue
        for prop in class_data.get("object_properties", []):
            add_named_element(object_properties, None, prop)
        for prop in class_data.get("data_properties", []):
            add_named_element(datatype_properties, None, prop)

    for property_id, property_data in ontology.get("object_properties", {}).items():
        add_named_element(object_properties, property_id, property_data)

    data_properties = ontology.get("data_properties", {})
    if isinstance(data_properties, dict):
        for property_id, property_data in data_properties.items():
            add_named_element(datatype_properties, property_id, property_data)
    elif isinstance(data_properties, list):
        for property_data in data_properties:
            add_named_element(datatype_properties, None, property_data)

    return {
        "classes": classes,
        "object_properties": object_properties,
        "datatype_properties": datatype_properties,
    }


def load_ontology_indexes(
    ontology_path: Union[str, Path] = DEFAULT_ONTOLOGY_PATH,
) -> Dict[str, Set[str]]:
    path = Path(ontology_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到已有本体文件: {path}")
    with path.open("r", encoding="utf-8") as file:
        return build_ontology_indexes(json.load(file))


def collect_candidate_class_names(result: OntologyCandidateExtractionResult) -> Set[str]:
    names = set()
    for item in result.candidate_classes:
        names.add(item.name)
        if item.normalized_name:
            names.add(item.normalized_name)
    return names


def validate_evidence(
    result: OntologyCandidateExtractionResult,
    chunk_texts: Dict[str, str],
) -> List[BusinessValidationIssue]:
    """校验证据 chunk_id 是否存在，以及证据文本是否出现在对应片段中。"""
    issues = []
    sections = (
        "candidate_classes",
        "candidate_object_properties",
        "candidate_datatype_properties",
        "candidate_constraints",
        "ontology_mapping",
        "uncertain_candidates",
    )

    for section_name in sections:
        for item_index, item in enumerate(getattr(result, section_name)):
            for evidence_index, evidence in enumerate(item.evidence):
                path = f"$.{section_name}[{item_index}].evidence[{evidence_index}]"
                chunk_text = chunk_texts.get(evidence.chunk_id)
                if chunk_text is None:
                    issues.append(
                        BusinessValidationIssue(
                            rule="evidence_chunk_id_exists",
                            path=f"{path}.chunk_id",
                            message="evidence.chunk_id 不属于本次抽取的任何文档片段",
                            value=evidence.chunk_id,
                        )
                    )
                    continue
                if evidence.text not in chunk_text:
                    issues.append(
                        BusinessValidationIssue(
                            rule="evidence_text_in_chunk",
                            path=f"{path}.text",
                            message="evidence.text 不在对应 chunk_text 中",
                            value=evidence.text,
                        )
                    )
    return issues


def validate_domain_range(
    result: OntologyCandidateExtractionResult,
    known_classes: Set[str],
) -> List[BusinessValidationIssue]:
    """校验 domain/range 是否属于已有类别或本次候选类别。"""
    issues = []
    allowed = known_classes | collect_candidate_class_names(result)
    normalized_allowed = {normalize_name(name) for name in allowed}

    checks = [
        ("candidate_object_properties", "domain_candidate"),
        ("candidate_object_properties", "range_candidate"),
        ("candidate_datatype_properties", "domain_candidate"),
        ("candidate_constraints", "domain_candidate"),
        ("candidate_constraints", "range_candidate"),
    ]
    for section_name, field_name in checks:
        for index, item in enumerate(getattr(result, section_name)):
            value = getattr(item, field_name, None)
            if value and normalize_name(value) not in normalized_allowed:
                issues.append(
                    BusinessValidationIssue(
                        rule="domain_range_exists",
                        path=f"$.{section_name}[{index}].{field_name}",
                        message=f"{field_name} 不属于已有类别或候选类别",
                        value=value,
                    )
                )
    return issues


def validate_existing_elements(
    result: OntologyCandidateExtractionResult,
    indexes: Dict[str, Set[str]],
) -> List[BusinessValidationIssue]:
    """校验 existing 元素和 ontology_mapping 是否能在已有本体中找到。"""
    issues = []
    checks = [
        ("candidate_classes", "status", "matched_existing_class", "classes"),
        (
            "candidate_object_properties",
            "status",
            "matched_existing_property",
            "object_properties",
        ),
        (
            "candidate_datatype_properties",
            "status",
            "matched_existing_property",
            "datatype_properties",
        ),
    ]
    for section_name, status_field, mapping_field, index_name in checks:
        for index, item in enumerate(getattr(result, section_name)):
            if getattr(item, status_field) != "existing":
                continue
            value = getattr(item, mapping_field)
            if not value or value not in indexes[index_name]:
                issues.append(
                    BusinessValidationIssue(
                        rule="existing_element_exists",
                        path=f"$.{section_name}[{index}].{mapping_field}",
                        message="existing 类型未在已有本体中找到匹配元素",
                        value=value,
                    )
                )

    mapping_indexes = {
        "class": indexes["classes"],
        "object_property": indexes["object_properties"],
        "datatype_property": indexes["datatype_properties"],
    }
    for index, mapping in enumerate(result.ontology_mapping):
        if mapping.mapped_to not in mapping_indexes[mapping.mapped_type]:
            issues.append(
                BusinessValidationIssue(
                    rule="ontology_mapping_exists",
                    path=f"$.ontology_mapping[{index}].mapped_to",
                    message="ontology_mapping 指向的元素不存在于已有本体",
                    value=mapping.mapped_to,
                )
            )

    for index, constraint in enumerate(result.candidate_constraints):
        if constraint.constraint_status != "existing":
            continue
        existing_index = (
            indexes["object_properties"]
            if constraint.property_type == "object_property"
            else indexes["datatype_properties"]
        )
        value = constraint.matched_existing_constraint
        if not value or value not in existing_index:
            issues.append(
                BusinessValidationIssue(
                    rule="existing_constraint_exists",
                    path=(
                        f"$.candidate_constraints[{index}]"
                        ".matched_existing_constraint"
                    ),
                    message="existing 约束未在已有本体属性中找到",
                    value=value,
                )
            )
    return issues


def validate_new_candidates(
    result: OntologyCandidateExtractionResult,
    indexes: Dict[str, Set[str]],
) -> List[BusinessValidationIssue]:
    """校验 new_candidate 是否与已有本体元素重复。"""
    issues = []
    checks = [
        ("candidate_classes", "classes"),
        ("candidate_object_properties", "object_properties"),
        ("candidate_datatype_properties", "datatype_properties"),
    ]
    for section_name, index_name in checks:
        known_names = {normalize_name(name) for name in indexes[index_name]}
        for index, item in enumerate(getattr(result, section_name)):
            if item.status != "new_candidate":
                continue
            candidate_names = {normalize_name(item.name)}
            if item.normalized_name:
                candidate_names.add(normalize_name(item.normalized_name))
            duplicates = sorted(name for name in candidate_names if name in known_names)
            if duplicates:
                issues.append(
                    BusinessValidationIssue(
                        rule="new_candidate_not_duplicate",
                        path=f"$.{section_name}[{index}]",
                        message="new_candidate 与已有本体元素重复",
                        value=item.normalized_name or item.name,
                    )
                )

    for index, constraint in enumerate(result.candidate_constraints):
        if constraint.constraint_status != "new_candidate":
            continue
        known_properties = (
            indexes["object_properties"]
            if constraint.property_type == "object_property"
            else indexes["datatype_properties"]
        )
        if normalize_name(constraint.property_name) in {
            normalize_name(name) for name in known_properties
        }:
            issues.append(
                BusinessValidationIssue(
                    rule="new_constraint_not_duplicate",
                    path=f"$.candidate_constraints[{index}].property_name",
                    message="new_candidate 约束对应的属性已存在于本体中",
                    value=constraint.property_name,
                )
            )
    return issues


def validate_weak_candidates(
    result: OntologyCandidateExtractionResult,
    for_formal_write: bool,
) -> List[BusinessValidationIssue]:
    """正式本体写入前禁止 weak_candidate。"""
    if not for_formal_write:
        return []

    issues = []
    for index, constraint in enumerate(result.candidate_constraints):
        if constraint.constraint_status == "weak_candidate":
            issues.append(
                BusinessValidationIssue(
                    rule="weak_candidate_not_formal",
                    path=f"$.candidate_constraints[{index}].constraint_status",
                    message="weak_candidate 禁止直接写入正式本体",
                    value=constraint.property_name,
                )
            )
    return issues


def validate_business_rules(
    result: OntologyCandidateExtractionResult,
    chunk_texts: Dict[str, str],
    ontology_indexes: Dict[str, Set[str]],
    for_formal_write: bool = False,
) -> BusinessValidationReport:
    """执行第二层业务校验。"""
    issues = []
    issues.extend(validate_evidence(result, chunk_texts))
    issues.extend(validate_domain_range(result, ontology_indexes["classes"]))
    issues.extend(validate_existing_elements(result, ontology_indexes))
    issues.extend(validate_new_candidates(result, ontology_indexes))
    issues.extend(validate_weak_candidates(result, for_formal_write))
    return BusinessValidationReport(valid=not issues, issues=issues)


def validate_business_data(
    data: Any,
    chunks: List[Dict[str, Any]],
    ontology_path: Union[str, Path] = DEFAULT_ONTOLOGY_PATH,
    for_formal_write: bool = False,
) -> BusinessValidationReport:
    """校验原始候选字典；调用前应先通过第一层格式校验。"""
    result = validate_candidate_data(data)
    chunk_texts = {chunk["chunk_id"]: chunk["content"] for chunk in chunks}
    indexes = load_ontology_indexes(ontology_path)
    return validate_business_rules(result, chunk_texts, indexes, for_formal_write)


def validate_before_formal_write(
    data: Any,
    chunks: List[Dict[str, Any]],
    ontology_path: Union[str, Path] = DEFAULT_ONTOLOGY_PATH,
) -> BusinessValidationReport:
    """正式本体写入前执行第二层校验，并阻止 weak_candidate。"""
    return validate_business_data(
        data,
        chunks,
        ontology_path=ontology_path,
        for_formal_write=True,
    )


def item_to_uncertain(
    section_name: str,
    item: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    """将 domain/range 等业务校验失败项转换为 uncertain_candidate。"""
    candidate_type_map = {
        "candidate_classes": "class",
        "candidate_object_properties": "object_property",
        "candidate_datatype_properties": "datatype_property",
        "candidate_constraints": "constraint",
    }
    name = (
        item.get("name")
        or item.get("normalized_name")
        or item.get("property_name")
        or "未命名候选"
    )
    return {
        "name": name,
        "candidate_type": candidate_type_map[section_name],
        "why_uncertain": reason,
        "evidence": item.get("evidence", []),
        "confidence": min(float(item.get("confidence", 0.5)), 0.59),
    }


def parse_issue_item_path(path: str) -> Optional[tuple]:
    """从业务错误路径中提取顶层 section 和候选索引。"""
    if not path.startswith("$."):
        return None
    section_and_rest = path[2:].split("[", 1)
    if len(section_and_rest) != 2:
        return None
    section_name, rest = section_and_rest
    try:
        index = int(rest.split("]", 1)[0])
    except (TypeError, ValueError):
        return None
    return section_name, index


def process_business_issues(
    data: Dict[str, Any],
    report: BusinessValidationReport,
    ontology_indexes: Dict[str, Set[str]],
) -> BusinessProcessingResult:
    """
    根据第二层校验结果执行处理策略。

    evidence 失败删除候选；domain/range 失败转 uncertain；existing 映射失败
    转 new_candidate，若名称与已有本体重复则转 uncertain；weak_candidate 转人工审核。
    """
    candidates = deepcopy(data)
    manual_review = []
    discarded = []
    actions = []
    delete_indexes: Dict[str, Set[int]] = {}
    uncertain_indexes: Dict[str, Dict[int, str]] = {}

    evidence_rules = {"evidence_chunk_id_exists", "evidence_text_in_chunk"}
    existing_rules = {"existing_element_exists", "existing_constraint_exists"}

    for issue in report.issues:
        location = parse_issue_item_path(issue.path)
        if not location:
            continue
        section_name, index = location
        if section_name not in candidates or index >= len(candidates[section_name]):
            continue

        if issue.rule in evidence_rules:
            delete_indexes.setdefault(section_name, set()).add(index)
        elif issue.rule == "domain_range_exists":
            uncertain_indexes.setdefault(section_name, {})[index] = issue.message
        elif issue.rule in {
            "new_candidate_not_duplicate",
            "new_constraint_not_duplicate",
        }:
            uncertain_indexes.setdefault(section_name, {})[index] = issue.message
        elif issue.rule in existing_rules:
            item = candidates[section_name][index]
            if section_name == "candidate_constraints":
                item["constraint_status"] = "new_candidate"
                item["matched_existing_constraint"] = None
                index_name = (
                    "object_properties"
                    if item.get("property_type") == "object_property"
                    else "datatype_properties"
                )
                candidate_name = item.get("property_name")
            else:
                item["status"] = "new_candidate"
                mapping_field = (
                    "matched_existing_class"
                    if section_name == "candidate_classes"
                    else "matched_existing_property"
                )
                item[mapping_field] = None
                index_name = {
                    "candidate_classes": "classes",
                    "candidate_object_properties": "object_properties",
                    "candidate_datatype_properties": "datatype_properties",
                }[section_name]
                candidate_name = item.get("normalized_name") or item.get("name")

            known_names = {
                normalize_name(name) for name in ontology_indexes[index_name]
            }
            if normalize_name(candidate_name) in known_names:
                uncertain_indexes.setdefault(section_name, {})[index] = (
                    "existing 映射失败，且候选名称与已有本体重复"
                )
            else:
                actions.append(
                    {
                        "action": "existing_to_new_candidate",
                        "section": section_name,
                        "index": index,
                        "reason": issue.message,
                    }
                )
        elif issue.rule == "ontology_mapping_exists":
            delete_indexes.setdefault(section_name, set()).add(index)

    for section_name, indexes in uncertain_indexes.items():
        for index, reason in indexes.items():
            if index in delete_indexes.get(section_name, set()):
                continue
            item = candidates[section_name][index]
            candidates["uncertain_candidates"].append(
                item_to_uncertain(section_name, item, reason)
            )
            delete_indexes.setdefault(section_name, set()).add(index)
            actions.append(
                {
                    "action": "move_to_uncertain",
                    "section": section_name,
                    "index": index,
                    "reason": reason,
                }
            )

    for index, constraint in enumerate(candidates["candidate_constraints"]):
        if (
            constraint.get("constraint_status") == "weak_candidate"
            and index not in delete_indexes.get("candidate_constraints", set())
        ):
            manual_review.append(
                {
                    "review_type": "weak_candidate",
                    "source_section": "candidate_constraints",
                    "candidate": deepcopy(constraint),
                    "reason": "weak_candidate 禁止直接写入正式本体",
                }
            )
            delete_indexes.setdefault("candidate_constraints", set()).add(index)
            actions.append(
                {
                    "action": "move_to_manual_review",
                    "section": "candidate_constraints",
                    "index": index,
                    "reason": "weak_candidate",
                }
            )

    for section_name, indexes in delete_indexes.items():
        original_items = candidates[section_name]
        for index in sorted(indexes):
            if any(
                action.get("section") == section_name
                and action.get("index") == index
                and action.get("action") in {"move_to_uncertain", "move_to_manual_review"}
                for action in actions
            ):
                continue
            discarded.append(
                {
                    "source_section": section_name,
                    "candidate": deepcopy(original_items[index]),
                    "reason": "业务校验失败，删除对应候选",
                }
            )
            actions.append(
                {
                    "action": "discard_candidate",
                    "section": section_name,
                    "index": index,
                    "reason": "evidence 或 mapping 校验失败",
                }
            )
        candidates[section_name] = [
            item for index, item in enumerate(original_items) if index not in indexes
        ]

    return BusinessProcessingResult(
        candidates=candidates,
        manual_review=manual_review,
        discarded=discarded,
        actions=actions,
    )


def validate_and_process_business_data(
    data: Dict[str, Any],
    chunks: List[Dict[str, Any]],
    ontology_path: Union[str, Path] = DEFAULT_ONTOLOGY_PATH,
) -> BusinessProcessingResult:
    """执行第二层业务校验并应用校验后处理策略。"""
    report = validate_business_data(data, chunks, ontology_path=ontology_path)
    indexes = load_ontology_indexes(ontology_path)
    return process_business_issues(data, report, indexes)
