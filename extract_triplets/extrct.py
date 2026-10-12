import os
import sys
import json
import time
import re
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from difflib import get_close_matches

# Direct-script compatibility must be established before importing project packages.
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import httpx
from openai import OpenAI
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from tqdm import tqdm

from confidence.calibrator import TripleCalibrator, load_calibrator
from confidence.feature_extractor import TripleFeatureExtractor
from confidence.risk_policy import RiskPolicy, load_policy
from document.hierarchy_index import (
    build_document_index,
    normalize_document_id,
    split_markdown_text as split_markdown_with_hierarchy,
)
from evidence.hierarchical_retriever import HierarchicalEvidenceRetriever
from schema.subontology_retriever import SubOntologyRetriever
from ontologyBuild.ontology_adapter import (
    build_class_hierarchy as adapter_build_class_hierarchy,
    build_relation_constraints,
    iter_classes,
    iter_datatype_properties,
    iter_object_properties,
    normalize_name as normalize_ontology_name,
)
from review_agent.contracts import candidate_id_for, fact_key_for as v3_fact_key_for
from review_agent.repository import ReviewRepository


# ============= 新增：本体文件导入 =============
def load_ontology_from_json(json_path="ontology/ontology.json"):
    """
    从 ontology.json 文件加载本体数据

    Args:
        json_path: 本体 JSON 文件路径

    Returns:
        dict: 本体数据字典
    """
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"找不到本体文件: {json_path}")

    with open(json_path, 'r', encoding='utf-8') as f:
        ontology = json.load(f)
    return ontology


def get_ontology_relations(ontology):
    """
    从本体中提取允许的关系列表（基于对象属性）

    Args:
        ontology: 本体数据字典

    Returns:
        list: 关系名称列表
    """
    relations = []
    seen = set()
    for prop_data in iter_object_properties(ontology):
        label = prop_data.get("label") or prop_data.get("name") or prop_data.get("id")
        if label and label not in seen:
            seen.add(label)
            relations.append(label)
    return relations


def get_entity_to_type_map(ontology):
    """
    从本体中提取类名/类标签映射。
    文档中的具体实体类型由 ArtilleryProcessor 的两阶段流程预测和校验。

    Args:
        ontology: 本体数据字典

    Returns:
        dict: {实体名称: 类型} 映射
    """
    entity_to_type = {}
    for class_data in iter_classes(ontology):
        class_id = class_data.get("id")
        if not class_id:
            continue
        label = class_data.get("label") or class_id
        entity_to_type[label] = class_id
    return entity_to_type


def get_class_label_to_id(ontology):
    """构建本体类 label/id 到 class_id 的索引。"""
    result = {}
    for class_data in iter_classes(ontology):
        class_id = class_data.get("id")
        if not class_id:
            continue
        for value in (
            class_id,
            class_data.get("label"),
            class_data.get("name"),
            class_data.get("field"),
        ):
            if value:
                value = str(value).strip()
                result[value] = class_id
                result[normalize_ontology_name(value)] = class_id
    return result


def get_class_hierarchy(ontology):
    """构建 child_class_id -> parent_class_id 的层级索引。"""
    return adapter_build_class_hierarchy(ontology)


def resolve_class_id(class_ref, class_label_to_id):
    """将 class id 或中文 label 统一解析为 class_id。"""
    if not class_ref:
        return None
    value = str(class_ref).strip()
    return class_label_to_id.get(value) or class_label_to_id.get(normalize_ontology_name(value))


def get_relation_domain_range_constraints(ontology):
    """提取关系 label -> domain/range class_id 集合。"""
    return build_relation_constraints(ontology)


def get_ontology_text(ontology=None, ontology_name=None, sub_schema=None):
    """
    生成本体文本描述，用于 Prompt

    Args:
        ontology: 本体数据字典（可选）
        ontology_name: 本体名称（可选，用于从文件加载）

    Returns:
        str: 本体描述文本
    """
    if ontology is None and ontology_name:
        ontology = load_ontology_from_json(f"ontology/{ontology_name}.json")
    elif ontology is None:
        ontology = load_ontology_from_json()

    if sub_schema:
        return json.dumps(sub_schema, ensure_ascii=False, indent=2)

    class_items = list(iter_classes(ontology))
    relation_items = list(iter_object_properties(ontology))
    datatype_items = list(iter_datatype_properties(ontology))

    text = ["[Ontology Structure]", "", "### Entity Classes:"]
    for class_data in class_items:
        class_id = class_data.get("id") or class_data.get("label")
        label = class_data.get("label") or class_id
        desc = class_data.get("description", "")
        parent = class_data.get("parent") or ""
        text.append(f"  - {class_id} ({label}), parent={parent}: {desc[:120]}")
    text.append(f"  ... total {len(class_items)} classes")
    text.append("")

    text.append("### Object Properties:")
    for prop_data in relation_items:
        label = prop_data.get("label") or prop_data.get("name") or prop_data.get("id")
        domain = prop_data.get("domain", [])
        range_val = prop_data.get("range", [])
        desc = prop_data.get("description") or prop_data.get("definition") or ""
        text.append(f"  - {label}: {domain} -> {range_val}. {desc[:120]}")
    text.append(f"  ... total {len(relation_items)} object properties")
    text.append("")

    text.append("### Datatype Properties:")
    for prop_data in datatype_items:
        label = prop_data.get("label") or prop_data.get("name") or prop_data.get("field") or prop_data.get("id")
        domain = prop_data.get("domain", [])
        datatype = prop_data.get("datatype") or "string"
        desc = prop_data.get("description") or prop_data.get("definition") or ""
        text.append(f"  - {label}: domain={domain}, datatype={datatype}. {desc[:120]}")
    text.append(f"  ... total {len(datatype_items)} datatype properties")
    text.append("")

    return "\n".join(text)
    '''

    text = []
    text.append("【火炮领域本体结构】")
    text.append("")

    # 类定义
    classes = ontology.get("classes", {})
    text.append("### 实体类型（类）：")
    for class_id, class_data in classes.items():
        label = class_data.get("label", class_id)
        desc = class_data.get("description", "")
        parent = class_data.get("parent") or ""
        text.append(f"  - {class_id} ({label}), parent={parent}: {desc[:120]}")
    text.append(f"  ... 共 {len(classes)} 个类")
    text.append("")

    # 对象属性 - 遍历所有类的 object_properties
    all_relations = []
    classes = ontology.get("classes", {})
    for class_id, class_data in classes.items():
        if isinstance(class_data, dict):
            object_properties = class_data.get("object_properties", [])
            if isinstance(object_properties, list):
                for prop_data in object_properties:
                    if isinstance(prop_data, dict):
                        all_relations.append(prop_data)

    text.append("### 关系类型（对象属性）：")
    for prop_data in all_relations:
        label = prop_data.get("label", "")
        domain = prop_data.get("domain", [])
        range_val = prop_data.get("range", [])
        desc = prop_data.get("description") or prop_data.get("definition") or ""
        text.append(f"  - {label}: {domain} -> {range_val}。{desc[:120]}")
    text.append(f"  ... 共 {len(all_relations)} 个关系")
    text.append("")

    return "\n".join(text)

# ================= 配置区域 =================
'''
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("QWEN_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL") or os.environ.get("QWEN_BASE_URL", "https://43.143.151.41:3000/v1")
MODEL = (
    os.environ.get("TRIPLET_MODEL")
    or os.environ.get("OPENAI_MODEL")
    or os.environ.get("QWEN_MODEL")
    or "Qwen"
)


# 置信度阈值（根据置信度机制.md）
# 高置信度区 (Confidence > 0.8): 直接保留
# 较高信度区 (0.6 < Confidence ≤ 0.8): 自动映射
# 中置信度区 (0.4 < Confidence ≤ 0.6): 人工审核
# 低置信度区 (Confidence ≤ 0.4): 丢弃
CONFIDENCE_THRESHOLD_HIGH = 0.8       # >0.8: 直接保留
CONFIDENCE_THRESHOLD_MEDIUM_HIGH = 0.6  # 0.6-0.8: 自动映射
CONFIDENCE_THRESHOLD_MEDIUM_LOW = 0.4   # 0.4-0.6: 人工审核

# 向量模型配置
EMBEDDING_MODEL = "Qwen3-Embedding-4B"

MARKDOWN_FILE_PATH = os.environ.get("TRIPLET_MARKDOWN_FILE", 'mdfiles\\MinerU_markdown_Ballistics.md')
OUTPUT_JSON_PATH = os.environ.get("TRIPLET_OUTPUT_JSON", 'triplets\\Ballistics.json')
EVIDENCE_MD_PATH = os.environ.get("TRIPLET_EVIDENCE_MD", 'evidence\\Ballistics_evidence.md')
BOOK_NAME = os.environ.get("TRIPLET_BOOK_NAME", "Ballistics")
DISCARDED_TRIPLETS_PATH = os.environ.get(
    "TRIPLET_REJECTED_JSON",
    'rejected_triplets\\Ballistics_rejected_triplets.json',
)
RUNS_V2_DIR = os.environ.get("KG_RUNS_V2_DIR", "runs/v2")
REVIEW_DATABASE_PATH = os.environ.get("KG_REVIEW_DATABASE", "runs/v3/review.sqlite3")
REVIEW_MODE = os.environ.get("KG_REVIEW_MODE", "shadow")
REVIEW_CONFIG_PATH = os.environ.get("KG_REVIEW_CONFIG")
REVIEW_CALIBRATOR_PATH = None
REVIEW_POLICY_PATH = None
REVIEW_ROUTING = {"auto_accept_enabled": False, "semantic_auto_reject_enabled": False}
REVIEW_CONFIG = {}

# ===========================================

# 导入新模块
try:
    from confidence.confidence_scorer import ConfidenceScorer
except ImportError:
    from confidence.confidence_scorer import ConfidenceScorer

# 初始化客户端
client = OpenAI(api_key=API_KEY or "EMPTY", base_url=BASE_URL, http_client=httpx.Client(trust_env=False))


def parse_cli_args(argv=None):
    parser = argparse.ArgumentParser(description="Extract triples and register an immutable v3 review run.")
    parser.add_argument("--config", help="v3 review configuration JSON")
    parser.add_argument("--review-mode", choices=["legacy", "assist_only", "shadow", "active", "offline_demo"])
    parser.add_argument("--input", help="Markdown input path (overrides TRIPLET_MARKDOWN_FILE)")
    parser.add_argument("--output-stem", help="legacy JSON/evidence output stem")
    return parser.parse_args(argv)


def configure_from_cli(args):
    """Resolve explicit CLI values before config and compatibility environment values."""
    global API_KEY, BASE_URL, MODEL, MARKDOWN_FILE_PATH, OUTPUT_JSON_PATH
    global EVIDENCE_MD_PATH, BOOK_NAME, DISCARDED_TRIPLETS_PATH, RUNS_V2_DIR
    global REVIEW_DATABASE_PATH, REVIEW_MODE, REVIEW_CONFIG_PATH, client
    global REVIEW_CALIBRATOR_PATH, REVIEW_POLICY_PATH, REVIEW_ROUTING
    global REVIEW_CONFIG

    config = {}
    default_config = Path(project_root) / "config" / "review_agent.json"
    if not default_config.exists():
        default_config = Path(project_root) / "config" / "review_agent.example.json"
    config_value = args.config or REVIEW_CONFIG_PATH or (str(default_config) if default_config.exists() else None)
    if config_value:
        config_path = Path(config_value)
        if not config_path.is_absolute():
            config_path = Path(project_root) / config_path
        if not config_path.is_file():
            raise FileNotFoundError(f"找不到审核配置: {config_path}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        REVIEW_CONFIG_PATH = str(config_path)
    REVIEW_CONFIG = config

    REVIEW_MODE = args.review_mode or config.get("mode") or os.environ.get("KG_REVIEW_MODE", "shadow")
    calibration_config = dict(config.get("calibration") or {})
    REVIEW_CALIBRATOR_PATH = calibration_config.get("model_path")
    REVIEW_POLICY_PATH = calibration_config.get("policy_path")
    if REVIEW_CALIBRATOR_PATH and not Path(REVIEW_CALIBRATOR_PATH).is_absolute():
        REVIEW_CALIBRATOR_PATH = str(Path(project_root) / REVIEW_CALIBRATOR_PATH)
    if REVIEW_POLICY_PATH and not Path(REVIEW_POLICY_PATH).is_absolute():
        REVIEW_POLICY_PATH = str(Path(project_root) / REVIEW_POLICY_PATH)
    REVIEW_ROUTING = {
        "auto_accept_enabled": bool((config.get("routing") or {}).get("auto_accept_enabled", False)),
        "semantic_auto_reject_enabled": bool(
            (config.get("routing") or {}).get("semantic_auto_reject_enabled", False)
        ),
    }
    database_value = config.get("database_path") or os.environ.get("KG_REVIEW_DATABASE", "runs/v3/review.sqlite3")
    database_path = Path(database_value)
    REVIEW_DATABASE_PATH = str(database_path if database_path.is_absolute() else Path(project_root) / database_path)
    MARKDOWN_FILE_PATH = args.input or os.environ.get("TRIPLET_MARKDOWN_FILE", MARKDOWN_FILE_PATH)
    RUNS_V2_DIR = os.environ.get("KG_RUNS_V2_DIR", RUNS_V2_DIR)

    if args.output_stem:
        safe_stem = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "_", args.output_stem).strip("._")
        if not safe_stem:
            raise ValueError("output-stem 不能为空")
        BOOK_NAME = safe_stem
        OUTPUT_JSON_PATH = str(Path(project_root) / "triplets" / f"{safe_stem}.json")
        EVIDENCE_MD_PATH = str(Path(project_root) / "evidence" / f"{safe_stem}_evidence.md")
        DISCARDED_TRIPLETS_PATH = str(
            Path(project_root) / "rejected_triplets" / f"{safe_stem}_rejected_triplets.json"
        )

    API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("QWEN_API_KEY", "")
    BASE_URL = os.environ.get("OPENAI_BASE_URL") or os.environ.get("QWEN_BASE_URL", "https://43.143.151.41:3000/v1")
    MODEL = os.environ.get("TRIPLET_MODEL") or os.environ.get("OPENAI_MODEL") or os.environ.get("QWEN_MODEL") or "Qwen"
    try:
        client.close()
    except Exception:
        pass
    client = OpenAI(api_key=API_KEY or "EMPTY", base_url=BASE_URL, http_client=httpx.Client(trust_env=False))
    return config

class ArtilleryProcessor:
    """火炮学知识图谱三元组后置处理器（支持多本体和置信度评分）"""

    def __init__(self, ontology_name: str = "", ontology=None, relations=None, entity_to_type=None):
        """
        初始化后置处理器

        Args:
            ontology_name: 本体名称（可选，用于从文件加载）
            ontology: 本体数据字典（可选，如果提供则优先使用）
            relations: 关系列表（可选，如果提供则优先使用）
            entity_to_type: 实体类型映射（可选，如果提供则优先使用）
        """
        # 如果直接提供了本体数据，使用它
        if ontology is not None:
            self.ontology = ontology
            self.ontology_name = ontology_name

            # 使用传入的关系列表和实体映射
            if relations is not None:
                self.allowed_relations = relations
            else:
                self.allowed_relations = get_ontology_relations(ontology)

            if entity_to_type is not None:
                self.entity_to_type = entity_to_type
            else:
                self.entity_to_type = get_entity_to_type_map(ontology)
        else:
            # 否则使用默认的 load_ontology_from_json 方式
            self.ontology = load_ontology_from_json(f"ontology/{ontology_name}.json")
            self.ontology_name = ontology_name
            self.allowed_relations = get_ontology_relations(self.ontology)
            self.entity_to_type = get_entity_to_type_map(self.ontology)

        # 实体黑名单正则过滤规则（用于过滤局部变量）
        self.entity_blacklist_patterns = [
            r'\b[a-zA-Z]\d+\b',           # 单个字母+数字 (如 xA, yB, P1)
            r'\b[a-zA-Z][a-zA-Z]\d+\b',   # 字母+字母+数字 (如 xA1, yB2)
            r'\b[a-zA-Z]_[a-zA-Z]+\b',    # 字母_字母 (如 x_A, y_B)
            r'\b[a-zA-Z]_\d+\b',          # 字母_数字 (如 x_1, y_2)
            r'点[a-zA-Z\d]',              # "点"后接字母或数字 (如 点A, 点P1)
            r'线段[a-zA-Z\d]',            # "线段"后接字母或数字 (如 线段AB)
            r'图[\d一-十]',               # "图"后接数字或汉字数字 (如 图1, 图一)
            r'表[\d一-十]',               # "表"后接数字或汉字数字 (如 表1, 表一)
            r'式[\d一-十]',               # "式"后接数字或汉字数字 (如 式3-1, 式一)
            r'^[A-Z]{2,}$',               # 纯大写字母组合 (如 AB, BC, ABC)
            r'^[a-z]{2,}$',               # 纯小写字母组合 (如 ab, bc)
        ]

        # 初始化置信度评分器（使用新的接口）
        thresholds = {
            "high": CONFIDENCE_THRESHOLD_HIGH,
            "medium_high": CONFIDENCE_THRESHOLD_MEDIUM_HIGH,
            "medium_low": CONFIDENCE_THRESHOLD_MEDIUM_LOW,
            "low": 0.0,
        }
        # 证据匹配方式：'entailment'/'llm' 或 'embedding'（从配置文件或环境变量读取）
        # 默认使用 'entailment'，判断三元组陈述是否被证据句语义蕴含。
        evidence_matching_method = os.environ.get("EVIDENCE_MATCHING_METHOD", "entailment")
        self.confidence_scorer = ConfidenceScorer(
            api_key=API_KEY,
            base_url=BASE_URL,
            embedding_model=EMBEDDING_MODEL,
            relations=self.allowed_relations,
            entity_to_type=self.entity_to_type,
            thresholds=thresholds,
            evidence_matching_method=evidence_matching_method,
        )

        self.class_label_to_id = get_class_label_to_id(self.ontology)
        self.class_hierarchy = get_class_hierarchy(self.ontology)
        self.relation_constraints = get_relation_domain_range_constraints(self.ontology)
        self.entity_type_cache = {}
        self.evidence_retriever = HierarchicalEvidenceRetriever()
        self.feature_extractor = TripleFeatureExtractor()
        self.calibrator = load_calibrator(REVIEW_CALIBRATOR_PATH) if REVIEW_CALIBRATOR_PATH else TripleCalibrator()
        self.risk_policy = load_policy(REVIEW_POLICY_PATH) if REVIEW_POLICY_PATH else RiskPolicy()

    def _class_options_text(self):
        """生成实体类型预测用的本体类清单。"""
        lines = []
        for class_data in iter_classes(self.ontology):
            class_id = class_data.get("id")
            if not class_id:
                continue
            label = class_data.get("label") or class_id
            desc = class_data.get("description", "")
            parent = class_data.get("parent") or "None"
            lines.append(f"- {class_id} ({label}), parent={parent}: {desc}")
        return "\n".join(lines)

    def _predict_entity_type(self, entity, relation, role, other_entity, evidence_text):
        """第一阶段：用 LLM 预测文档实体对应的本体 class_id。"""
        entity = str(entity or "").strip()
        direct_type = self.class_label_to_id.get(entity) or self.class_label_to_id.get(normalize_ontology_name(entity))
        if direct_type:
            return {
                "entity": entity,
                "class_id": "Unknown",
                "class_concept_candidate": direct_type,
                "confidence": 0.0,
                "method": "class_concept_match",
                "reason": "名称命中本体类概念，不能据此把文本提及自动断言为该类实例",
            }

        ontology_version = hashlib.sha256(
            json.dumps(self.ontology, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        evidence_version = hashlib.sha256(str(evidence_text or "").encode("utf-8")).hexdigest()
        cache_key = (entity, relation, role, other_entity, evidence_version, ontology_version)
        if cache_key in self.entity_type_cache:
            return self.entity_type_cache[cache_key]

        prompt = f"""请判断文档实体最可能属于哪个火炮领域本体类。

只能从下列 class_id 中选择；如果证据不足，返回 "Unknown"。

【本体类清单】
{self._class_options_text()}

【待分类实体】
entity: {entity}
role: {role}
relation: {relation}
other_entity: {other_entity}

【证据文本】
{evidence_text[:1200]}

请只输出 JSON：
{{"class_id": "C_xxx 或 Unknown", "confidence": 0.0, "reason": "简短理由"}}
"""
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "你是火炮领域知识图谱实体类型分类器。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            parsed = json.loads(response.choices[0].message.content)
            predicted = resolve_class_id(parsed.get("class_id"), self.class_label_to_id)
            confidence = parsed.get("confidence", 0.0)
            try:
                confidence = max(0.0, min(1.0, float(confidence)))
            except (TypeError, ValueError):
                confidence = 0.0
            result = {
                "entity": entity,
                "class_id": predicted or "Unknown",
                "confidence": confidence,
                "method": "llm",
                "reason": parsed.get("reason", ""),
            }
        except Exception as e:
            result = {
                "entity": entity,
                "class_id": "Unknown",
                "confidence": 0.0,
                "method": "llm_failed",
                "reason": f"实体类型预测失败: {e}",
            }

        self.entity_type_cache[cache_key] = result
        if result["class_id"] != "Unknown":
            self.entity_to_type[entity] = result["class_id"]
        return result

    def _is_class_compatible(self, predicted_class, allowed_classes):
        """第二阶段：用本体层级判断预测类型是否满足 domain/range。"""
        if not allowed_classes:
            return True
        if not predicted_class or predicted_class == "Unknown":
            return False

        for allowed_class in allowed_classes:
            if self._is_subclass_or_same(predicted_class, allowed_class):
                return True
        return False

    def _is_subclass_or_same(self, class_id, possible_parent):
        current = class_id
        visited = set()
        while current and current not in visited:
            if current == possible_parent:
                return True
            visited.add(current)
            current = self.class_hierarchy.get(current)
        return False

    def _predict_triplet_entity_types(self, triplet, evidence_text):
        relation = triplet.get("relation", "")
        head = triplet.get("head", "")
        tail = triplet.get("tail", "")
        head_prediction = self._predict_entity_type(
            head, relation, "head", tail, evidence_text
        )
        tail_prediction = self._predict_entity_type(
            tail, relation, "tail", head, evidence_text
        )
        return {
            "head": head_prediction,
            "tail": tail_prediction,
        }

    def _validate_domain_range_types(self, relation, head_type, tail_type):
        constraints = self.relation_constraints.get(relation, {})
        domain = constraints.get("domain", [])
        range_ = constraints.get("range", [])
        domain_ok = self._is_class_compatible(head_type, domain)
        range_ok = self._is_class_compatible(tail_type, range_)
        errors = []
        if not domain_ok:
            errors.append(f"head_type {head_type} 不满足 {relation} 的 domain {domain}")
        if not range_ok:
            errors.append(f"tail_type {tail_type} 不满足 {relation} 的 range {range_}")
        return {
            "relation": relation,
            "head_type": head_type,
            "tail_type": tail_type,
            "domain_allowed": domain,
            "range_allowed": range_,
            "domain_ok": domain_ok,
            "range_ok": range_ok,
            "passed": domain_ok and range_ok,
            "errors": errors,
        }

    def _type_validation_info(self, validation):
        if validation.get("passed"):
            return "实体类型通过本体层级和 domain/range 校验"
        return "；".join(validation.get("errors", []))

    def _apply_type_validation_to_shunt(self, shunt_result, validation):
        if validation.get("passed"):
            return shunt_result

        result = dict(shunt_result)
        reason = self._type_validation_info(validation)
        old_reason = result.get("reason", "")
        result["reason"] = f"{old_reason}；{reason}" if old_reason else reason
        if result.get("shunt") in {"high", "medium_high"}:
            result["shunt"] = "medium_low"
            result["action"] = "进入人工审核队列"
        return result

    def _is_entity_in_blacklist(self, entity):
        """
        检查实体是否匹配黑名单模式（局部变量）

        Args:
            entity: 实体名称字符串

        Returns:
            bool: True 表示匹配黑名单模式，需要过滤
        """
        if not entity:
            return True

        for pattern in self.entity_blacklist_patterns:
            if re.search(pattern, entity):
                return True

        return False

    def _map_relation(self, surface_relation, head="", tail=""):
        """Map an original surface relation to the closest ontology relation."""
        surface_relation = str(surface_relation or "").strip()
        if not surface_relation:
            return {
                "surface_relation": "",
                "relation": "",
                "score": 0.0,
                "margin": 0.0,
                "method": "empty",
                "candidates": [],
            }
        if surface_relation in self.allowed_relations:
            return {
                "surface_relation": surface_relation,
                "relation": surface_relation,
                "score": 1.0,
                "margin": 1.0,
                "method": "exact",
                "candidates": [{"relation": surface_relation, "score": 1.0}],
            }

        ranked = []
        for relation in self.allowed_relations:
            score = get_close_matches(surface_relation, [relation], n=1, cutoff=0.0)
            if score:
                similarity = 1.0 if surface_relation == relation else self._string_similarity(surface_relation, relation)
                ranked.append({"relation": relation, "score": similarity})
        ranked.sort(key=lambda item: item["score"], reverse=True)
        best = ranked[0] if ranked else {"relation": "", "score": 0.0}
        second_score = ranked[1]["score"] if len(ranked) > 1 else 0.0
        return {
            "surface_relation": surface_relation,
            "relation": "",
            "score": best["score"],
            "margin": best["score"] - second_score,
            "method": "candidate_recall" if ranked else "unmatched",
            "candidates": ranked[:5],
            "requires_semantic_confirmation": True,
        }

    def _string_similarity(self, left, right):
        import difflib

        return difflib.SequenceMatcher(None, str(left), str(right)).ratio()

    def _decision_to_legacy_shunt(self, decision, probability):
        action = {
            "auto_accept": "自动接纳",
            "human_accept": "人工接纳",
            "manual_review": "进入人工审核队列",
            "auto_reject": "自动拒绝",
        }.get(decision, "进入人工审核队列")
        shunt = "high" if decision == "auto_accept" else "medium_low"
        if decision == "auto_reject":
            shunt = "low"
        return {
            "shunt": shunt,
            "reason": (
                f"校准概率 {probability:.4f}，风险策略决策为 {decision}"
                if probability is not None else f"校准器未就绪，风险策略决策为 {decision}"
            ),
            "action": action,
        }

    def _build_v2_record(
        self,
        triplet,
        evidence_result,
        mapping_result,
        type_predictions,
        type_validation,
        confidence_result,
        features,
        probability,
        decision,
        source_chunk,
    ):
        best_evidence = evidence_result.get("best_evidence") or {}
        relation = mapping_result.get("relation") or triplet.get("relation", "")
        record = dict(triplet)
        record["head"] = str(record.get("head", "")).strip()
        record["tail"] = str(record.get("tail", "")).strip()
        record["surface_relation"] = mapping_result.get("surface_relation") or record.get("surface_relation") or record.get("relation", "")
        record["relation"] = relation
        record["head_type"] = type_predictions["head"]["class_id"]
        record["tail_type"] = type_predictions["tail"]["class_id"]
        record["document_id"] = source_chunk.get("document_id")
        record["source_id"] = source_chunk.get("chunk_id")
        record["breadcrumb_path"] = source_chunk.get("breadcrumb_path")
        record["evidence"] = best_evidence.get("text") or record.get("evidence") or record.get("evidence_quote", "")
        record["evidence_items"] = evidence_result.get("support_items") or ([best_evidence] if best_evidence else [])
        record["relation_mapping"] = mapping_result
        record["entity_type_prediction"] = type_predictions
        record["type_validation"] = type_validation
        record["confidence_features"] = features
        record["raw_score"] = confidence_result.get("raw_final_score", confidence_result.get("final_score", 0.0))
        record["calibrated_probability"] = round(probability, 4) if probability is not None else None
        record["calibration_status"] = "ok" if probability is not None else "unavailable"
        record["legacy_score"] = confidence_result.get("raw_final_score", confidence_result.get("final_score", 0.0))
        record["confidence"] = record["legacy_score"]
        record["decision"] = decision
        record["calibrator_version"] = self.calibrator.version
        record["policy_version"] = self.risk_policy.version
        record["confidence_detail"] = {
            "raw_confidence": confidence_result,
            "relation_mapping": mapping_result,
            "evidence_result": evidence_result,
            "entity_type_prediction": type_predictions,
            "type_validation_info": self._type_validation_info(type_validation),
        }
        record["confidence_decision"] = self._decision_to_legacy_shunt(decision, probability)
        return record

    def validate_and_fix(self, triplets, evidence_text="", source_chunk=None, document_index=None):
        """
        按 v2 顺序处理三元组：
        证据检索 -> 关系映射 -> 实体分类 -> domain/range 校验 ->
        特征生成 -> 概率校准 -> 风险分流。

        仍返回旧接口形态，便于现有审核页面继续读取 review 文件。
        """
        valid_triplets = []
        discarded_triplets = []

        if source_chunk is None:
            source_chunk = {
                "document_id": "legacy_document",
                "chunk_id": "legacy_chunk",
                "section_id": "legacy_section",
                "parent_section_id": None,
                "breadcrumb_path": "",
                "content": evidence_text or "",
                "sentences": [
                    {
                        "sentence_id": "legacy_chunk_s0",
                        "text": evidence_text or "",
                        "start": 0,
                        "end": len(evidence_text or ""),
                    }
                ],
            }
        if document_index is None:
            document_index = build_document_index([source_chunk])

        for t in triplets:
            if not isinstance(t, dict):
                continue

            if "triple" in t and isinstance(t["triple"], list) and len(t["triple"]) >= 3:
                t = {
                    "head": t["triple"][0],
                    "surface_relation": t["triple"][1],
                    "relation": t["triple"][1],
                    "tail": t["triple"][2],
                    "source_id": t.get("source_id", ""),
                    "thought": t.get("thought", ""),
                    "evidence": t.get("evidence", ""),
                    "evidence_quote": t.get("evidence_quote") or t.get("evidence", ""),
                    "confidence": t.get("confidence", 0.5),
                }
            elif "triple" in t and isinstance(t["triple"], list) and len(t["triple"]) == 2:
                t = {
                    "head": t["triple"][0],
                    "surface_relation": "相关",
                    "relation": "相关",
                    "tail": t["triple"][1],
                    "source_id": t.get("source_id", ""),
                    "thought": t.get("thought", ""),
                    "evidence": t.get("evidence", ""),
                    "evidence_quote": t.get("evidence_quote") or t.get("evidence", ""),
                    "confidence": t.get("confidence", 0.5),
                }

            head = str(t.get("head", "")).strip()
            tail = str(t.get("tail", "")).strip()
            surface_relation = str(t.get("surface_relation") or t.get("relation") or t.get("predicate") or "").strip()
            t["head"] = head
            t["tail"] = tail
            t["surface_relation"] = surface_relation
            t["relation"] = surface_relation
            t.setdefault("evidence_quote", t.get("evidence", ""))

            if not head or not tail:
                discarded_triplets.append({
                    "head": head,
                    "surface_relation": surface_relation,
                    "relation": surface_relation,
                    "tail": tail,
                    "source_id": source_chunk.get("chunk_id"),
                    "document_id": source_chunk.get("document_id"),
                    "discard_reason": "head 或 tail 为空",
                    "confidence": 0.0,
                    "calibrated_probability": 0.0,
                    "decision": "auto_reject",
                    "confidence_detail": {},
                    "confidence_decision": {"shunt": "low", "reason": "head 或 tail 为空", "action": "自动拒绝"},
                })
                continue

            blacklist_reason = None
            if self._is_entity_in_blacklist(head):
                blacklist_reason = f"head '{head}' 匹配黑名单模式"
            elif self._is_entity_in_blacklist(tail):
                blacklist_reason = f"tail '{tail}' 匹配黑名单模式"
            if blacklist_reason:
                record = dict(t)
                record.update({
                    "source_id": source_chunk.get("chunk_id"),
                    "document_id": source_chunk.get("document_id"),
                    "discard_reason": blacklist_reason,
                    "confidence": 0.0,
                    "calibrated_probability": 0.0,
                    "decision": "auto_reject",
                    "confidence_decision": {"shunt": "low", "reason": blacklist_reason, "action": "自动拒绝"},
                })
                discarded_triplets.append(record)
                print(f"  过滤：{blacklist_reason}")
                continue

            evidence_result = self.evidence_retriever.retrieve(t, source_chunk, document_index)
            best_evidence = evidence_result.get("best_evidence") or {}
            evidence_for_scoring = best_evidence.get("text") or t.get("evidence_quote") or evidence_text

            mapping_result = self._map_relation(surface_relation, head=head, tail=tail)
            if not mapping_result.get("relation"):
                record = self._build_v2_record(
                    t,
                    evidence_result,
                    mapping_result,
                    {
                        "head": {"class_id": "Unknown", "confidence": 0.0},
                        "tail": {"class_id": "Unknown", "confidence": 0.0},
                    },
                    {
                        "relation": "",
                        "head_type": "Unknown",
                        "tail_type": "Unknown",
                        "domain_ok": False,
                        "range_ok": False,
                        "passed": False,
                        "errors": [f"关系词 '{surface_relation}' 无法映射到本体关系"],
                    },
                    {"final_score": 0.0, "raw_final_score": 0.0, "S_evidence": 0.0},
                    {},
                    None,
                    "manual_review",
                    source_chunk,
                )
                record["discard_reason"] = f"关系词 '{surface_relation}' 需要语义映射确认或本体修订"
                record["reason_codes"] = ["SCHEMA_GAP"]
                record["needs_review"] = True
                discarded_triplets.append(record)
                print(f"  警告：舍弃非法关系 -> '{surface_relation}'")
                continue

            t["relation"] = mapping_result["relation"]

            type_predictions = self._predict_triplet_entity_types(t, evidence_for_scoring)
            head_type = type_predictions["head"]["class_id"]
            tail_type = type_predictions["tail"]["class_id"]
            type_validation = self._validate_domain_range_types(mapping_result["relation"], head_type, tail_type)
            confidence_result = self.confidence_scorer.compute_confidence(t, evidence_text=evidence_for_scoring)
            features = self.feature_extractor.extract(
                triplet=t,
                evidence_result=evidence_result,
                mapping_result=mapping_result,
                type_result=type_predictions,
                ontology_result=type_validation,
                confidence_result=confidence_result,
            )
            raw_score = confidence_result.get("final_score", 0.0)
            probability = self.calibrator.predict_proba(features, raw_score=raw_score)
            decision = self.risk_policy.route(probability)
            if decision == "auto_accept" and (
                REVIEW_MODE != "active" or not REVIEW_ROUTING.get("auto_accept_enabled", False)
            ):
                decision = "manual_review"
            if decision == "auto_reject" and (
                REVIEW_MODE != "active" or not REVIEW_ROUTING.get("semantic_auto_reject_enabled", False)
            ):
                decision = "manual_review"

            if not type_validation.get("passed") and decision == "auto_accept":
                decision = "manual_review"

            record = self._build_v2_record(
                t,
                evidence_result,
                mapping_result,
                type_predictions,
                type_validation,
                confidence_result,
                features,
                probability,
                decision,
                source_chunk,
            )
            if mapping_result.get("method") != "exact":
                record["corrected_relation"] = mapping_result.get("relation")

            print(
                f"  v2处理: {head} - {mapping_result['relation']} - {tail} "
                f"(校准概率: {probability if probability is not None else '未就绪'}, 决策: {decision})"
            )
            if decision in {"auto_accept", "human_accept"}:
                valid_triplets.append(record)
            else:
                record["needs_review"] = decision == "manual_review"
                record["discard_reason"] = record["confidence_decision"]["reason"]
                discarded_triplets.append(record)

        return valid_triplets, discarded_triplets


def is_example_or_calculation_paragraph(text):
    """
    检测段落是否为例题/计算段落（知识屏蔽）
    只匹配标题或行首的例题特征，避免误伤“要求”“解决”等正文内容。

    Args:
        text: 待检测的段落文本

    Returns:
        bool: True 表示是例题/计算段落，需要屏蔽
    """
    # 只在行首或 Markdown 标题开头识别例题/计算标记。
    # "求"、"解"必须接冒号，避免误匹配“要求”“解决”。
    example_keywords = [
        r'例\s*[0-9一二三四五六七八九十百]+(?:[：:、.\s]|$)',
        r'例题(?:[：:、.\s]|$)',
        r'习题\s*[0-9一二三四五六七八九十百]*(?:[：:、.\s]|$)',
        r'练习\s*[0-9一二三四五六七八九十百]*(?:[：:、.\s]|$)',
        r'计算题(?:[：:、.\s]|$)',
        r'如图\s*[0-9一二三四五六七八九十百]+(?:[：:、.\s]|$)',
        r'如图\s*所示(?:[：:、.\s]|$)',
        r'已知(?:[：:、.\s]|$)',
        r'假设(?:[：:、.\s]|$)',
        r'求解(?:[：:]|\s*$)',
        r'求\s*[：:]',
        r'解\s*[：:]',
        r'计算(?:[：:、.\s]|$)',
        r'试算(?:[：:、.\s]|$)',
        r'试求(?:[：:、.\s]|$)',
        r'试分析(?:[：:、.\s]|$)',
    ]

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r'^#{1,6}\s*', '', line)
        for pattern in example_keywords:
            if re.match(pattern, line):
                return True

    return False


def read_markdown():
    """读取并预处理markdown文件"""
    if not os.path.exists(MARKDOWN_FILE_PATH):
        raise FileNotFoundError(f"找不到文件: {MARKDOWN_FILE_PATH}")

    with open(MARKDOWN_FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # ====== 文本切分 ======
    # 1. 把 MinerU 特有的 ###HEADER#### 替换为标准的 Markdown 一级或二级标题
    # （这里统一替换为 # ，配合后面的切分器）
    content = content.replace("###HEADER#### ", "# ")
    content = content.replace("###HEADER####", "# ")


    # 保留其他的基本清洗逻辑
    content = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', content) # 移除链接
    content = re.sub(r'^[\s]*[-\*\+]\s+', '', content, flags=re.MULTILINE) # 移除无序列表标记
    content = re.sub(r'```[^\n]*\n', '', content)
    content = re.sub(r'```', '', content)
    content = re.sub(r'`([^`]+)`', r'\1', content)
    content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content)

    return content.strip()


def split_markdown_text(text):
    """
    使用 LangChain 进行文本切分：
    1. 首先使用 MarkdownHeaderTextSplitter 按标题层级切分（#、##、###）
    2. 然后使用 RecursiveCharacterTextSplitter 进行二次切分（chunk_size=1000）
    关键点：二次切分后的片段会自动继承父级的标题 Metadata
    """
    # 定义要切分的标题层级
    headers_to_split_on = [
        ("#", "Header-1"),
        ("##", "Header-2"),
        ("###", "Header-3"),
    ]

    # 第一步：使用 MarkdownHeaderTextSplitter 按标题切分
    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False  # 保留标题在内容中
    )

    doc_chunks = markdown_splitter.split_text(text)
    print(f"   MarkdownHeaderTextSplitter 切分后得到 {len(doc_chunks)} 个文档片段")

    # 第二步：使用 RecursiveCharacterTextSplitter 进行二次切分
    # chunk_size=1000 确保内容不会过长
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,  # 保持一定重叠以保证上下文连续性
        separators=["\n\n", "\n", "。", "！", "？", "，", "、", ""]
    )

    # 二次切分，Document 对象会自动继承 metadata
    final_docs = text_splitter.split_documents(doc_chunks)
    print(f"   RecursiveCharacterTextSplitter 二次切分后得到 {len(final_docs)} 个最终片段")

    # 转换为字典列表，包含 breadcrumb_path 和 content
    chunks = []
    for doc in final_docs:
        # 从 metadata 中提取所有层级标题
        metadata = doc.metadata

        # 构建面包屑路径
        breadcrumb_path = build_breadcrumb_path(metadata)

        chunks.append({
            "breadcrumb_path": breadcrumb_path,
            "content": doc.page_content,
            "metadata": metadata
        })

    return chunks


def build_breadcrumb_path(metadata):
    """
    从 metadata 中提取所有层级标题，用 > 拼接成面包屑路径
    Metadata 中可能包含 Header-1, Header-2, Header-3 等键
    """
    parts = []

    # 按顺序提取各层级标题
    for key in ["Header-1", "Header-2", "Header-3"]:
        if key in metadata and metadata[key]:
            # 提取标题文本（去除 markdown 标记符号）
            header_text = metadata[key].strip()
            # 移除开头的 # 符号
            header_text = re.sub(r'^#+\s*', '', header_text)
            parts.append(header_text)

    # 如果没有找到任何标题，使用默认值
    if not parts:
        return "正文"

    # 用 > 拼接成路径
    return " > ".join(parts)


def call_qwen_traditional(chunk, breadcrumb_path, retry_count=3, ontology=None, sub_schema=None):
    """
    GraphRAG
    整合了火炮学知识图谱本体，强制使用本体定义的实体类型、关系类型和抽取规则。
    """

    # 从 ontology_data.py 动态加载本体信息
    if ontology is None:
        # 如果没有传入 ontology，则使用默认的 JSON 文件加载
        ontology_info = get_ontology_text()
    else:
        ontology_info = get_ontology_text(ontology=ontology, sub_schema=sub_schema)

    prompt = f"""
Goal:
You are an expert in Artillery Weapon Systems. Transform the following technical text into a semantic graph (a list of triples) based on the provided entity list.
Your task is to identify precise engineering and physical relations between these entities.

Attention:
1. Accuracy over Quantity: Only extract triples that are explicitly supported by the technical text.
2. Entity Integrity: Do not modify the names in the Entity List.
3. Concise Relations: Use professional engineering terms for relations (e.g., "composed_of", "determines", "proportional_to", "increases", "constrains").
4. Avoid Descriptive Objects: Do not use "factor of..." as an entity. Instead, use the action as a relation (e.g., instead of [A, is, factor of B], use [A, affects, B]).
5. Evidence First: For each triple, provide the original surface relation and an exact evidence quote from the text. Do not provide confidence or thought fields.

Example (Artillery Domain):
Text: "制退机产生制退阻力，制退阻力决定火炮后坐长度。"
Semantic Graph:
{{
  "triplets": [
    {{
      "head": "制退机",
      "surface_relation": "产生",
      "tail": "制退阻力",
      "evidence_quote": "制退机产生制退阻力"
    }},
    {{
      "head": "制退阻力",
      "surface_relation": "决定",
      "tail": "火炮后坐长度",
      "evidence_quote": "制退阻力决定火炮后坐长度"
    }}
  ]
}}

**你必须严格遵守本体定义的实体类型、关系类型和抽取规则。**
{ontology_info}
你必须**绝对遵守**以下铁律，任何违背将导致系统崩溃：

### 铁律一：知识屏蔽 - 忽略例题与计算段落！
- **规则**：你必须忽略所有例题、习题、具体的数值计算过程。
- **屏蔽特征**：只有当标题或行首出现"例1"、"例题"、"习题"、"练习"、"计算题"、"已知："、"假设："、"求："、"解："、"求解："、"试算"等标记时，才将该片段视为例题/计算段落并跳过。
- **避免误删**：正文中出现"要求"、"解决"、"求得"、"解算"等正常术语时，不得据此跳过该段落。
- **理论边界**：仅提取通用的、教科书定义的理论知识。例题中的具体数值、特定计算过程、虚构情境均不得入库。
- ❌ 错误：从例题"已知某炮弹的初速为800 m/s，试分析其射程。"中提取任何三元组。
- ❌ 错误：从"如图12-3所示的炮架结构"中提取结构参数。
- ✅ 正确：如果正文明确描述"初速是影响射程的关键因素"，则可以提取 {{"head": "初速", "surface_relation": "影响", "tail": "射程", "evidence_quote": "初速是影响射程的关键因素"}}。

### 铁律二：去重三元组
- **全局去重**：在整个文档范围内，完全相同的三元组只保留一条。
- **输出前自行去重**，绝对禁止让重复三元组进入后续的 Neo4j 导入环节！

【抽取前检查】
在输出 JSON 前自行完成以下检查，但不要输出 thought 字段：
1. **实体识别与分类**：识别文中提到的参数（如 $i_c$）、过程（如膛内压力）或模型。判断它们属于哪14类实体。
2. **指代消解（Rule 1）**：检查文中是否有"该"、"其"、"此"或省略主语的情况。必须回溯并替换为具体的实体名词（如将"其"替换为"弹道系数"）。
3. **符号转中文（Rule 9 - 新增）**：检查所有提取的实体，如果包含数学符号（如 v0, x, A, p），必须替换为对应的中文概念名称。
4. **实体名称校验（Rule 9 - 新增）**：检查实体长度是否 ≤ 2 个字符，或是否为纯 LaTeX 符号。如果满足任一条件，必须将其合并到父级概念中或丢弃。
5. **关系保真（核心步骤）**：
   - 分析实体间的物理逻辑。
   - surface_relation 尽量保留原文关系表达。
   - 不要把所有关系强行改写成本体标准关系，标准 relation 由后置映射模块生成。
6. **嵌套分解（Rule 2）**：如果遇到复合参数（如弹道系数 $C$），检查文中是否提到了它的组成部分，并拆解为多条"由...组成"关系。
---

【待处理文本】
{chunk}

【输出格式要求】
严格输出 JSON 对象。如果无高价值结构化知识，返回 `{{"triplets": []}}`。

必须使用且只能使用以下 JSON schema。不要输出 `triple` 数组字段，不要输出 `subject/predicate/object`，不要在 JSON 外添加任何文本。
{{
  "triplets": [
    {{
      "head": "string，非空名词或名词短语",
      "surface_relation": "string，原文中的关系表达或最接近的短语",
      "tail": "string，非空名词或名词短语",
      "evidence_quote": "string，必须是待处理文本中的原文片段，不得改写",
      "head_type_candidate": "string，可为空",
      "tail_type_candidate": "string，可为空"
    }}
  ]
}}

输出示例：
{{
  "triplets": [
    {{
      "head": "空气密度",
      "surface_relation": "成正比",
      "tail": "空气阻力加速度",
      "evidence_quote": "空气阻力加速度与空气密度成正比",
      "head_type_candidate": "C_PhysicalParameter",
      "tail_type_candidate": "C_PhysicalParameter"
    }},
    {{
      "head": "初速",
      "surface_relation": "决定",
      "tail": "射程",
      "evidence_quote": "初速决定射程",
      "head_type_candidate": "C_PhysicalParameter",
      "tail_type_candidate": "C_PhysicalParameter"
    }}
  ]
}}
"""
    # ====== API 调用代码 ======
    for i in range(retry_count):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "You are a GraphRAG extraction expert. Preserve the original surface relation from the source text, require exact evidence quotes, and output only JSON. Do not output thought or confidence fields."},
                    {"role": "user", "content": prompt}
                ],
                response_format={'type': 'json_object'},
                temperature=0.0
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)
            # 确保返回的是列表
            result = parsed.get("triplets", [])
            if not isinstance(result, list):
                return []
            return result
        except Exception as e:
            print(f"请求失败 (第{i+1}次重试): {e}")
            import time
            time.sleep(2)

    return []


def clean_header(header):
    """清理header中的非法字符，用于生成ID"""
    cleaned = re.sub(r'[^\w\s\-]', '_', header)
    cleaned = re.sub(r'\s+', '_', cleaned)
    return cleaned


def generate_chunk_id(breadcrumb_path, content):
    """
    生成 chunk_id：计算 md5(breadcrumb_path + content) 的前 8 位
    ID 格式：{breadcrumb_path最后的标题}_{哈希值}
    """
    # 计算 md5 哈希值
    combined = breadcrumb_path + content
    md5_hash = hashlib.md5(combined.encode('utf-8')).hexdigest()

    # 取前 8 位作为哈希值
    hash_prefix = md5_hash[:8]

    # 获取 breadcrumb_path 最后的标题
    parts = breadcrumb_path.split(" > ")
    last_title = parts[-1] if parts else "正文"

    # 清理标题中的非法字符，替换为下划线
    cleaned_title = re.sub(r'[^\w\s\-]', '_', last_title)
    cleaned_title = re.sub(r'\s+', '_', cleaned_title)

    # 组合成最终的 ID
    return f"{cleaned_title}_{hash_prefix}"


def write_evidence_library(chunk_id, breadcrumb_path, content, filename):
    """
    将证据写入到 evidence_library.md 文件中
    使用 Markdown 格式，以 chunk_id 为一级标题
    """
    with open(filename, 'a', encoding='utf-8') as f:
        # 以 chunk_id 为一级标题
        f.write(f"# {chunk_id}\n\n")

        # 写入面包屑路径
        f.write(f"**面包屑路径：** {breadcrumb_path}\n\n")

        # 写入正文内容
        f.write("## 正文内容\n\n")
        f.write(f"{content}\n\n")

        # 分隔线
        f.write("---\n\n")


def normalize_fact_part(value):
    return "".join(str(value or "").split()).casefold()


def fact_key_for(item):
    return v3_fact_key_for(item, str(item.get("domain") or "firearm_artillery"))


def deduplicate_triplets(items):
    merged = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = fact_key_for(item)
        current = merged.get(key)
        if current is None:
            current = dict(item)
            current["fact_key"] = key
            current["evidence_items"] = list(item.get("evidence_items") or [])
            merged[key] = current
            continue

        current_items = current.setdefault("evidence_items", [])
        seen = {
            (
                ev.get("chunk_id"),
                ev.get("sentence_id"),
                ev.get("start"),
                ev.get("end"),
                ev.get("text"),
            )
            for ev in current_items
            if isinstance(ev, dict)
        }
        for ev in item.get("evidence_items") or []:
            if not isinstance(ev, dict):
                continue
            ev_key = (
                ev.get("chunk_id"),
                ev.get("sentence_id"),
                ev.get("start"),
                ev.get("end"),
                ev.get("text"),
            )
            if ev_key not in seen:
                current_items.append(ev)
                seen.add(ev_key)

        current.setdefault("candidate_sources", []).append({
            "source_id": item.get("source_id"), "document_id": item.get("document_id"),
            "evidence_items": item.get("evidence_items") or [],
        })

    return list(merged.values())


def write_v2_outputs(candidates, accepted, review_queue, rejected):
    groups = {
        "candidates": candidates,
        "accepted": accepted,
        "review_queue": review_queue,
        "rejected": rejected,
    }
    for group_name, data in groups.items():
        output_dir = os.path.join(RUNS_V2_DIR, group_name)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{normalize_document_id(os.path.basename(MARKDOWN_FILE_PATH))}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def write_v3_run(candidates, document_index, ontology):
    """Persist immutable run inputs and candidate revisions; JSON is only an export."""
    run_id = os.environ.get("KG_RUN_ID") or (
        "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
    )
    run_root = Path(project_root) / "runs" / "v3"
    run_dir = run_root / run_id
    document_id = document_index.get("document_id") or normalize_document_id(os.path.basename(MARKDOWN_FILE_PATH))
    document_version = document_index.get("document_version") or hashlib.sha256(
        Path(MARKDOWN_FILE_PATH).read_bytes()
    ).hexdigest()
    ontology_text = json.dumps(ontology, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ontology_version = hashlib.sha256(ontology_text.encode("utf-8")).hexdigest()
    document_path = run_dir / "documents" / document_id / f"{document_version}.json"
    ontology_path = run_dir / "ontology" / "ontology_snapshot.json"
    config_path = run_dir / "config" / "review_config.json"
    export_path = run_dir / "exports" / "candidates.json"
    for path in (document_path, ontology_path, config_path, export_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_text(json.dumps({
        "document_id": document_id, "document_version": document_version,
        "chunks": document_index.get("chunks", []), "sections": document_index.get("sections", []),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    ontology_path.write_text(json.dumps(ontology, ensure_ascii=False, indent=2), encoding="utf-8")
    def redact(value):
        if isinstance(value, dict):
            return {
                key: ("<redacted>" if any(token in key.lower() for token in ("api_key", "password", "secret", "token"))
                      else redact(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value
    frozen_config = redact({**REVIEW_CONFIG, "mode": REVIEW_MODE})
    config_path.write_text(json.dumps(frozen_config, ensure_ascii=False, indent=2), encoding="utf-8")
    config_hash = hashlib.sha256(
        json.dumps(frozen_config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    repository = ReviewRepository(REVIEW_DATABASE_PATH)
    repository.initialize()
    manifest = {
        "run_id": run_id, "schema_version": "triple_review_v3", "mode": REVIEW_MODE,
        "source_hash": document_version, "ontology_hash": ontology_version,
        "document_id": document_id, "document_path": str(document_path),
        "ontology_path": str(ontology_path), "feature_schema_version": "review_features_v3",
        "calibrator_version": "untrained", "policy_version": "disabled_cold_start_v3",
        "review_config_path": str(config_path), "review_config_hash": config_hash,
        "planner_strategy": (REVIEW_CONFIG.get("planner") or {}).get("strategy", "deterministic"),
    }
    repository.create_run(manifest)
    exported = []
    for index, item in enumerate(candidates):
        candidate_id = candidate_id_for(run_id, document_id, str(item.get("source_id") or "unknown"), index)
        triple = {
            "head": item.get("head"), "surface_relation": item.get("surface_relation") or item.get("relation"),
            "relation_id": item.get("relation_id") or item.get("relation") or None,
            "relation_label": item.get("relation"), "tail": item.get("tail"),
            "head_type": item.get("head_type"), "tail_type": item.get("tail_type"),
            "tail_kind": item.get("tail_kind", "entity"),
            "qualifiers": item.get("qualifiers") or {"polarity": item.get("polarity", "positive"),
                                                        "condition": item.get("condition")},
        }
        candidate = {
            "schema_version": "triple_review_v3", "run_id": run_id, "candidate_id": candidate_id,
            "revision": 1, "document_id": document_id, "document_version": document_version,
            "source_chunk_id": item.get("source_id"), "section_id": item.get("section_id"),
            "domain": item.get("domain") or ontology.get("domain") or "default",
            "fact_namespace": item.get("fact_namespace") or item.get("domain") or ontology.get("domain") or "default",
            "ontology_version": ontology_version, "triple": triple,
            "extraction_quote": item.get("evidence_quote") or item.get("evidence"),
            "evidence_items": item.get("evidence_items") or [],
            "extraction_model": MODEL, "extraction_prompt_version": "extract_legacy_bridge_v3",
            "legacy_score": item.get("legacy_score", item.get("raw_score")),
        }
        candidate["fact_key"] = fact_key_for(candidate)
        repository.add_candidate(candidate)
        exported.append(candidate)
    export_path.write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv=None):
    args = parse_cli_args(argv)
    configure_from_cli(args)
    # 打印配置信息
    print("=" * 60)
    print("火炮学知识图谱三元组提取配置")
    print("=" * 60)
    print(f"  本体文件: ontology/ontology.json")
    print(f"  置信度阈值: >{CONFIDENCE_THRESHOLD_HIGH} 保留, {CONFIDENCE_THRESHOLD_MEDIUM_HIGH}-{CONFIDENCE_THRESHOLD_HIGH} 自动映射, {CONFIDENCE_THRESHOLD_MEDIUM_LOW}-{CONFIDENCE_THRESHOLD_MEDIUM_HIGH} 人工审核, ≤{CONFIDENCE_THRESHOLD_MEDIUM_LOW} 丢弃")
    print(f"  向量模型: {EMBEDDING_MODEL}")
    print("=" * 60)

    # 从文件路径中提取文件名作为 file_source
    file_source = os.path.basename(MARKDOWN_FILE_PATH)
    print(f"   处理文件: {file_source}")

    print("1. 读取并预处理markdown文件...")
    ontology = {}
    try:
        text = read_markdown()
        print(f"   成功读取文本，长度: {len(text)} 字符")
    except Exception as e:
        print(f"读取失败: {e}")
        return

    print("2. 执行基于层级的统一切分...")
    document_id = normalize_document_id(os.path.basename(MARKDOWN_FILE_PATH))
    chunks = split_markdown_with_hierarchy(text, document_id=document_id)
    document_index = build_document_index(chunks)
    print(f"   共切分为 {len(chunks)} 个逻辑片段。")

    all_triplets = []
    all_discarded = []  # 收集所有被丢弃的三元组

    # 加载本体信息（用于打印本体关系列表）
    try:
        # 从 JSON 文件加载本体
        ontology = load_ontology_from_json("ontology/ontology.json")
        relations = get_ontology_relations(ontology)
        entity_to_type = get_entity_to_type_map(ontology)
        print(f"   本体关系列表: {relations}")
        print(f"   实体类型映射数量: {len(entity_to_type)}")
    except Exception as e:
        print(f"   加载本体失败: {e}")
        # 如果 JSON 加载失败，尝试从 confidence 模块加载 Markdown 本体
        # 注意：ontology_loader.py 已移除，此分支保留作为 fallback，但会失败
        print(f"   由于 ontology_loader.py 已移除，无法从 Markdown 加载本体")
        relations = []
        entity_to_type = {}

    # 初始化后置处理器（使用新的本体加载方式）
    processor = ArtilleryProcessor(ontology_name="", ontology=ontology, relations=relations, entity_to_type=entity_to_type)

    print("3. 开始调用提取模型并构建溯源库...")
    # 打开证据文件（追加模式），先清空或写入表头
    with open(EVIDENCE_MD_PATH, 'w', encoding='utf-8') as ev_file:
        ev_file.write("# 知识溯源证据库\n\n")

    schema_retriever = SubOntologyRetriever(ontology)

    for chunk in tqdm(chunks, desc="Processing"):
        content = chunk["content"].strip()
        if len(content) < 10:  # 跳过毫无意义的超短文本
            continue

        # ====== 知识屏蔽：检测例题/计算段落 ======
        if is_example_or_calculation_paragraph(content):
            print(f"  [知识屏蔽] 例题/计算段落，跳过提取")
            continue  # 跳过例题/计算段落，不进行三元组提取

        breadcrumb_path = chunk.get("breadcrumb_path", "正文")
        last_header = breadcrumb_path.split(" > ")[-1] if breadcrumb_path else "正文"
        sub_schema = schema_retriever.retrieve(
            breadcrumb_path=breadcrumb_path,
            text=content,
            top_k_classes=8,
            top_k_relations=12,
        )

        # ====== 组装大模型 Prompt ======
        # 直接传入 content 和 breadcrumb_path，函数内部会构建 Prompt
        triplets = call_qwen_traditional(
            content,
            breadcrumb_path,
            ontology=ontology,
            sub_schema=sub_schema,
        )

        # ====== 如果提取成功，生成 ID 并落盘 ======
        if triplets and len(triplets) > 0:
            # 过滤掉非字典类型的元素（如字符串）
            valid_triplets_raw = [t for t in triplets if isinstance(t, dict)]
            if len(valid_triplets_raw) != len(triplets):
                print(f"  警告: 过滤掉 {len(triplets) - len(valid_triplets_raw)} 个非字典类型的三元组")
            triplets = valid_triplets_raw

            if not triplets:
                continue

            # 为每个三元组打上 source_id 烙印
            for t in triplets:
                if isinstance(t, dict):
                    t["source_id"] = chunk["chunk_id"]
                    t["document_id"] = chunk["document_id"]
                    t["breadcrumb_path"] = breadcrumb_path

            # 后置处理：验证和修复三元组
            print(f"  后置处理：验证和修复三元组...")
            valid_triplets, discarded_triplets = processor.validate_and_fix(
                triplets,
                evidence_text=content,
                source_chunk=chunk,
                document_index=document_index,
            )
            print(f"  有效三元组数量: {len(valid_triplets)} / {len(triplets)}")
            print(f"  被丢弃三元组数量: {len(discarded_triplets)}")

            all_triplets.extend(valid_triplets)

            # 收集所有被丢弃的三元组
            all_discarded.extend(discarded_triplets)

            # 写入 Markdown 证据库
            with open(EVIDENCE_MD_PATH, 'a', encoding='utf-8') as ev_file:
                ev_file.write(f"## ID: {chunk['chunk_id']}\n")
                ev_file.write(f"**文献路径**： `{breadcrumb_path}`\n\n")
                ev_file.write(f"> {content}\n\n")
                ev_file.write("---\n\n")
    all_candidates = deduplicate_triplets(all_triplets + all_discarded)
    all_triplets = deduplicate_triplets(all_triplets)
    all_discarded = deduplicate_triplets(all_discarded)
    review_queue = [item for item in all_discarded if item.get("needs_review") or item.get("decision") == "manual_review"]
    rejected = [item for item in all_discarded if item.get("decision") == "auto_reject"]

    print(f"4. 提取完成，共获得 {len(all_triplets)} 个自动接纳三元组。")
    with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_triplets, f, ensure_ascii=False, indent=2)

    print(f"4. 被丢弃的三元组已保存到: {DISCARDED_TRIPLETS_PATH}")
    with open(DISCARDED_TRIPLETS_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_discarded, f, ensure_ascii=False, indent=2)

    write_v2_outputs(all_candidates, all_triplets, review_queue, rejected)
    print(f"4. v2 分层结果已保存到: {RUNS_V2_DIR}")
    manifest = write_v3_run(all_candidates, document_index, ontology)
    print(f"4. v3 审核运行已登记: {manifest['run_id']}")

    # 打印置信度分布统计
    print("\n5. 置信度分布统计:")
    confidence_levels = {"high": 0, "medium_high": 0, "medium_low": 0, "low": 0}
    for t in all_triplets:
        decision = t.get("confidence_decision", {})
        shunt = decision.get("shunt", "low")
        confidence_levels[shunt] = confidence_levels.get(shunt, 0) + 1
    for t in all_discarded:
        decision = t.get("confidence_decision", {})
        shunt = decision.get("shunt", "low")
        confidence_levels[shunt] = confidence_levels.get(shunt, 0) + 1
    print(f"   high: {confidence_levels['high']}, medium_high: {confidence_levels['medium_high']}, medium_low: {confidence_levels['medium_low']}, low: {confidence_levels['low']}")

if __name__ == "__main__":
    main()
