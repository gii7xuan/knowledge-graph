import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEFAULT_INPUT_PATH = Path(__file__).with_name("ontology_candidates.json")


def empty_to_none(v):
    """把 LLM 常见的空字符串、'null' 统一转成 None。"""
    if isinstance(v, str):
        v = v.strip()
    if v == "" or v == "null" or v == "None":
        return None
    return v


class StrictBaseModel(BaseModel):
    """
    extra='forbid' 表示不允许 LLM 输出 schema 之外的字段。
    这样可以避免模型随意增加字段。
    """
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class Evidence(StrictBaseModel):
    chunk_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class CandidateClass(StrictBaseModel):
    name: str = Field(min_length=1)
    normalized_name: Optional[str]
    parent_candidate: Optional[str]
    definition_candidate: Optional[str]
    status: Literal["existing", "new_candidate"]
    matched_existing_class: Optional[str]
    evidence: List[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reason: Optional[str]

    _normalize_empty = field_validator(
        "normalized_name",
        "parent_candidate",
        "definition_candidate",
        "matched_existing_class",
        "reason",
        mode="before",
    )(empty_to_none)

class CandidateObjectProperty(StrictBaseModel):
    name: str = Field(min_length=1)
    normalized_name: Optional[str]
    definition_candidate: Optional[str]
    status: Literal["existing", "new_candidate"]
    matched_existing_property: Optional[str]
    domain_candidate: Optional[str]
    range_candidate: Optional[str]
    evidence: List[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reason: Optional[str]

    _normalize_empty = field_validator(
        "normalized_name",
        "definition_candidate",
        "matched_existing_property",
        "domain_candidate",
        "range_candidate",
        "reason",
        mode="before",
    )(empty_to_none)

class CandidateDatatypeProperty(StrictBaseModel):
    name: str = Field(min_length=1)
    normalized_name: Optional[str]
    definition_candidate: Optional[str]
    status: Literal["existing", "new_candidate"]
    matched_existing_property: Optional[str]
    domain_candidate: Optional[str]
    range_datatype: Optional[
        Literal["string", "integer", "float", "boolean", "date", "datetime"]
    ]
    unit: Optional[str]
    example_value: Optional[str]
    evidence: List[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reason: Optional[str]

    _normalize_empty = field_validator(
        "normalized_name",
        "definition_candidate",
        "matched_existing_property",
        "domain_candidate",
        "range_datatype",
        "unit",
        "example_value",
        "reason",
        mode="before",
    )(empty_to_none)

class CandidateConstraint(StrictBaseModel):
    property_name: str = Field(min_length=1)
    property_type: Literal["object_property", "datatype_property"]
    domain_candidate: Optional[str]
    range_candidate: Optional[str]
    range_datatype: Optional[
        Literal["string", "integer", "float", "boolean", "date", "datetime"]
    ]
    constraint_status: Literal["existing", "new_candidate", "weak_candidate"]
    matched_existing_constraint: Optional[str]
    evidence: List[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reason: Optional[str]

    _normalize_empty = field_validator(
        "domain_candidate",
        "range_candidate",
        "range_datatype",
        "matched_existing_constraint",
        "reason",
        mode="before",
    )(empty_to_none)

class OntologyMapping(StrictBaseModel):
    text_mention: str = Field(min_length=1)
    mapped_to: str = Field(min_length=1)
    mapped_type: Literal["class", "object_property", "datatype_property"]
    mapping_confidence: float = Field(ge=0, le=1)
    evidence: List[Evidence] = Field(min_length=1)


class UncertainCandidate(StrictBaseModel):
    name: str = Field(min_length=1)
    candidate_type: Literal[
        "class",
        "object_property",
        "datatype_property",
        "constraint"
    ]
    why_uncertain: str = Field(min_length=1)
    evidence: List[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class OntologyCandidateExtractionResult(StrictBaseModel):
    candidate_classes: List[CandidateClass]
    candidate_object_properties: List[CandidateObjectProperty]
    candidate_datatype_properties: List[CandidateDatatypeProperty]
    candidate_constraints: List[CandidateConstraint]
    ontology_mapping: List[OntologyMapping]
    uncertain_candidates: List[UncertainCandidate]


class FormatValidationIssue(BaseModel):
    """第一层格式校验发现的单个问题。"""

    category: Literal[
        "invalid_json",
        "missing_field",
        "invalid_field_type",
        "invalid_enum",
        "confidence_out_of_range",
        "schema_violation",
    ]
    path: str
    message: str
    input_value: Any = None


class FormatValidationReport(BaseModel):
    """第一层格式校验报告。"""

    valid: bool
    json_valid: bool
    schema_valid: bool
    issues: List[FormatValidationIssue] = Field(default_factory=list)


def format_error_path(location: tuple) -> str:
    """将 Pydantic 错误位置转换为可读字段路径。"""
    path = "$"
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def classify_validation_error(error: dict) -> str:
    """将 Pydantic 错误分类为第一层格式校验类别。"""
    error_type = error.get("type", "")
    location = error.get("loc", ())
    field_name = str(location[-1]) if location else ""

    if error_type == "missing":
        return "missing_field"
    if error_type == "literal_error":
        return "invalid_enum"
    if field_name in {"confidence", "mapping_confidence"} and error_type in {
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
    }:
        return "confidence_out_of_range"
    if (
        error_type.endswith("_type")
        or error_type.endswith("_parsing")
        or error_type in {"list_type", "dict_type", "model_type"}
    ):
        return "invalid_field_type"
    return "schema_violation"


def build_schema_issues(error: ValidationError) -> List[FormatValidationIssue]:
    """将 Pydantic ValidationError 转换为结构化问题列表。"""
    issues = []
    for item in error.errors(include_url=False):
        issues.append(
            FormatValidationIssue(
                category=classify_validation_error(item),
                path=format_error_path(item.get("loc", ())),
                message=item.get("msg", "Pydantic schema 校验失败"),
                input_value=item.get("input"),
            )
        )
    return issues


def validate_format_data(data: Any) -> FormatValidationReport:
    """执行第一层格式校验：Schema、字段、类型、枚举和置信度。"""
    try:
        OntologyCandidateExtractionResult.model_validate(data)
    except ValidationError as error:
        return FormatValidationReport(
            valid=False,
            json_valid=True,
            schema_valid=False,
            issues=build_schema_issues(error),
        )

    return FormatValidationReport(valid=True, json_valid=True, schema_valid=True)


def validate_format_file(input_path: Union[str, Path]) -> FormatValidationReport:
    """读取文件并执行第一层格式校验，包括合法 JSON 检查。"""
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到候选本体文件: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        return FormatValidationReport(
            valid=False,
            json_valid=False,
            schema_valid=False,
            issues=[
                FormatValidationIssue(
                    category="invalid_json",
                    path="$",
                    message=str(error),
                )
            ],
        )

    return validate_format_data(data)


def validate_candidate_data(data: Any) -> OntologyCandidateExtractionResult:
    """使用 Pydantic 校验候选本体抽取结果。"""
    return OntologyCandidateExtractionResult.model_validate(data)


def validate_candidate_file(
    input_path: Union[str, Path],
) -> OntologyCandidateExtractionResult:
    """读取 JSON 文件并校验候选本体抽取结果。"""
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到候选本体文件: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    return validate_candidate_data(data)


def count_candidates(result: OntologyCandidateExtractionResult) -> int:
    """统计六类候选元素的总数。"""
    return sum(
        len(getattr(result, field_name))
        for field_name in OntologyCandidateExtractionResult.model_fields
    )


def main():
    parser = argparse.ArgumentParser(description="执行候选本体第一层格式校验")
    parser.add_argument(
        "input_path",
        nargs="?",
        default=str(DEFAULT_INPUT_PATH),
        help="待校验 JSON 文件路径",
    )
    args = parser.parse_args()

    try:
        report = validate_format_file(args.input_path)
    except FileNotFoundError as error:
        print(f"校验失败: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(report.model_dump_json(indent=2))
    if not report.valid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
