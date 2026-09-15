import os
import sys
import json
import time
import re
import hashlib
from difflib import get_close_matches
from openai import OpenAI
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from tqdm import tqdm

# 将当前脚本所在目录的父目录添加到 Python 路径
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, os.pardir))  # 项目根目录
parent_dir = os.path.dirname(script_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# 切换到项目根目录，确保文件路径配置正确
if os.getcwd() != project_root:
    os.chdir(project_root)
    print(f"工作目录切换到: {project_root}")


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

    # ontology.json 的结构是每个类（class）内部有 object_properties
    # 需要遍历所有类的 object_properties 来提取关系
    classes = ontology.get("classes", {})

    for class_id, class_data in classes.items():
        if isinstance(class_data, dict):
            # 获取该类的 object_properties
            object_properties = class_data.get("object_properties", [])

            # 如果 object_properties 是列表格式（如 [{...}, {...}]）
            if isinstance(object_properties, list):
                for prop_data in object_properties:
                    if isinstance(prop_data, dict):
                        label = prop_data.get("label", "")
                        if label and label not in relations:
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
    classes = ontology.get("classes", {})

    for class_id, class_data in classes.items():
        label = class_data.get("label", class_id)
        entity_to_type[label] = class_id

        # 添加子类名称到映射
        for subclass_id in class_data.get("subclasses", []):
            subclass_data = classes.get(subclass_id, {})
            subclass_label = subclass_data.get("label", subclass_id)
            if subclass_label:
                entity_to_type[subclass_label] = subclass_id

    return entity_to_type


def get_class_label_to_id(ontology):
    """构建本体类 label/id 到 class_id 的索引。"""
    result = {}
    classes = ontology.get("classes", {})
    for class_id, class_data in classes.items():
        if not isinstance(class_data, dict):
            continue
        result[class_id] = class_id
        label = class_data.get("label")
        if label:
            result[label] = class_id
    return result


def get_class_hierarchy(ontology):
    """构建 child_class_id -> parent_class_id 的层级索引。"""
    hierarchy = {}
    classes = ontology.get("classes", {})
    for class_id, class_data in classes.items():
        if not isinstance(class_data, dict):
            continue
        parent = class_data.get("parent")
        if parent:
            hierarchy[class_id] = parent
        for subclass_id in class_data.get("subclasses", []) or []:
            hierarchy.setdefault(subclass_id, class_id)
    return hierarchy


def resolve_class_id(class_ref, class_label_to_id):
    """将 class id 或中文 label 统一解析为 class_id。"""
    if not class_ref:
        return None
    return class_label_to_id.get(str(class_ref).strip())


def get_relation_domain_range_constraints(ontology):
    """提取关系 label -> domain/range class_id 集合。"""
    class_label_to_id = get_class_label_to_id(ontology)
    constraints = {}

    def add_constraint(prop_data):
        if not isinstance(prop_data, dict):
            return
        label = prop_data.get("label")
        if not label:
            return
        item = constraints.setdefault(label, {"domain": set(), "range": set()})
        for class_ref in prop_data.get("domain", []) or []:
            class_id = resolve_class_id(class_ref, class_label_to_id)
            if class_id:
                item["domain"].add(class_id)
        for class_ref in prop_data.get("range", []) or []:
            class_id = resolve_class_id(class_ref, class_label_to_id)
            if class_id:
                item["range"].add(class_id)

    object_properties = ontology.get("object_properties", {})
    if isinstance(object_properties, dict):
        for prop_data in object_properties.values():
            add_constraint(prop_data)
    elif isinstance(object_properties, list):
        for prop_data in object_properties:
            add_constraint(prop_data)

    for class_data in ontology.get("classes", {}).values():
        if not isinstance(class_data, dict):
            continue
        for prop_data in class_data.get("object_properties", []) or []:
            add_constraint(prop_data)

    return {
        relation: {
            "domain": sorted(value["domain"]),
            "range": sorted(value["range"]),
        }
        for relation, value in constraints.items()
    }


def get_ontology_text(ontology=None, ontology_name=None):
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

    text = []
    text.append("【火炮领域本体结构】")
    text.append("")

    # 类定义
    classes = ontology.get("classes", {})
    text.append("### 实体类型（类）：")
    for class_id, class_data in list(classes.items())[:10]:  # 限制显示前10个类
        label = class_data.get("label", class_id)
        desc = class_data.get("description", "")
        text.append(f"  - {label}: {desc[:100]}...")
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
    for prop_data in list(all_relations)[:20]:  # 限制显示前20个关系
        label = prop_data.get("label", "")
        domain = prop_data.get("domain", [])
        range_val = prop_data.get("range", [])
        text.append(f"  - {label}: {domain} -> {range_val}")
    text.append(f"  ... 共 {len(all_relations)} 个关系")
    text.append("")

    return "\n".join(text)

# ================= 配置区域 =================
API_KEY = "sk-45jfj2wN89d0hwLFA18c71D7D3A3462b9eBe96F6Ea7d8cF5"
BASE_URL = "http://43.143.151.41:3000/v1"
MODEL = "Qwen3-Coder-Next"


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

MARKDOWN_FILE_PATH = 'mdfiles\\MinerU_markdown_Ballistics.md'
OUTPUT_JSON_PATH = 'triplets\\Ballistics.json'
EVIDENCE_MD_PATH = 'evidence\\Ballistics_evidence.md'
BOOK_NAME = "《Ballistics》"
DISCARDED_TRIPLETS_PATH = 'rejected_triplets\\Ballistics_rejected_triplets.json'  # 被丢弃三元组的输出文件路径

# ===========================================

# 导入新模块
try:
    from confidence.confidence_scorer import ConfidenceScorer
except ImportError:
    from confidence.confidence_scorer import ConfidenceScorer

# 初始化客户端
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

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

    def _class_options_text(self):
        """生成实体类型预测用的本体类清单。"""
        lines = []
        for class_id, class_data in self.ontology.get("classes", {}).items():
            if not isinstance(class_data, dict):
                continue
            label = class_data.get("label", class_id)
            desc = class_data.get("description", "")
            parent = class_data.get("parent") or "None"
            lines.append(f"- {class_id} ({label}), parent={parent}: {desc}")
        return "\n".join(lines)

    def _predict_entity_type(self, entity, relation, role, other_entity, evidence_text):
        """第一阶段：用 LLM 预测文档实体对应的本体 class_id。"""
        entity = str(entity or "").strip()
        direct_type = self.class_label_to_id.get(entity)
        if direct_type:
            return {
                "entity": entity,
                "class_id": direct_type,
                "confidence": 1.0,
                "method": "ontology_label_exact",
                "reason": "实体名称直接命中本体类 label/id",
            }

        cache_key = (entity, relation, role, other_entity)
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

    def validate_and_fix(self, triplets, evidence_text=""):
        """
        验证并修复三元组中的关系词，同时过滤黑名单实体，并计算置信度

        Args:
            triplets: 三元组列表
            evidence_text: 原文证据文本（用于证据匹配）

        Returns:
            tuple: (valid_triplets, discarded_triplets)
                valid_triplets: 通过验证的三元组列表（包含 confidence 字段）
                discarded_triplets: 被丢弃的三元组列表（包含 confidence 和 discard_reason）
        """
        valid_triplets = []
        discarded_triplets = []

        for t in triplets:
            # ====== 格式转换：兼容不同格式的三元组 ======
            # 格式1: {"triple": ["a", "b", "c"]} -> {"head": "a", "relation": "b", "tail": "c"}
            # 格式2: {"head": "a", "relation": "b", "tail": "c"} (标准格式)
            if "triple" in t and isinstance(t["triple"], list) and len(t["triple"]) >= 3:
                head = t["triple"][0]
                relation = t["triple"][1]
                tail = t["triple"][2]
                # 转换为标准格式
                t = {
                    "head": head,
                    "relation": relation,
                    "tail": tail,
                    "source_id": t.get("source_id", ""),
                    "thought": t.get("thought", ""),
                    "evidence": t.get("evidence", ""),
                    "confidence": t.get("confidence", 0.5)
                }
            elif "triple" in t and isinstance(t["triple"], list) and len(t["triple"]) == 2:
                # 只有 head 和 tail 的情况（可能是遗漏了 relation）
                head = t["triple"][0]
                tail = t["triple"][1]
                relation = "相关"  # 默认关系
                t = {
                    "head": head,
                    "relation": relation,
                    "tail": tail,
                    "source_id": t.get("source_id", ""),
                    "thought": t.get("thought", ""),
                    "evidence": t.get("evidence", ""),
                    "confidence": t.get("confidence", 0.5)
                }
            else:
                # 已经是标准格式或无法识别的格式
                head = t.get("head", "")
                relation = t.get("relation", "")
                tail = t.get("tail", "")

            # 如果 head 或 tail 为空，丢弃该三元组
            if not head or not tail:
                discarded_triplets.append({
                    "head": head,
                    "relation": relation,
                    "tail": tail,
                    "source_id": t.get("source_id", ""),
                    "discard_reason": "head 或 tail 为空",
                    "confidence": 0.0,
                    "confidence_detail": {},
                    "confidence_decision": {"shunt": "low", "reason": "head 或 tail 为空", "action": "丢弃"},
                })
                continue

            source_id = t.get("source_id", "")

            # 添加实体类别信息（用于 Neo4j 导入时的颜色区分）
            head_type = self.entity_to_type.get(head, "Unknown")
            tail_type = self.entity_to_type.get(tail, "Unknown")
            t["head_type"] = head_type
            t["tail_type"] = tail_type

            # 保存原始关系词用于记录
            if "relation" in t:
                original_pred = t.get("relation", "")
            elif "predicate" in t:
                original_pred = t.get("predicate", "")
            else:
                original_pred = ""

             # 检查 head 是否在黑名单中
            if self._is_entity_in_blacklist(head):
                # 计算置信度（使用新的接口）
                confidence_result = self.confidence_scorer.compute_confidence(t, evidence_text=evidence_text)
                confidence = confidence_result["final_score"]
                detail = {
                    "logic_info": confidence_result["logic_info"],
                    "map_info": confidence_result["map_info"],
                    "evidence_info": confidence_result["evidence_info"],
                }

                reason = f"head '{head}' 匹配黑名单模式"
                print(f"  过滤：{reason} (置信度: {confidence:.3f})")

                discarded_triplets.append({
                    "head": head,
                    "head_type": head_type,
                    "relation": original_pred,
                    "tail": tail,
                    "tail_type": tail_type,
                    "source_id": source_id,
                    "discard_reason": reason,
                    "confidence": confidence,
                    "confidence_detail": detail,
                })
                continue

            # 检查 tail 是否在黑名单中
            if self._is_entity_in_blacklist(tail):
                # 计算置信度（使用新的接口）
                confidence_result = self.confidence_scorer.compute_confidence(t, evidence_text=evidence_text)
                confidence = confidence_result["final_score"]
                detail = {
                    "logic_info": confidence_result["logic_info"],
                    "map_info": confidence_result["map_info"],
                    "evidence_info": confidence_result["evidence_info"],
                }

                reason = f"tail '{tail}' 匹配黑名单模式"
                print(f"  过滤：{reason} (置信度: {confidence:.3f})")

                discarded_triplets.append({
                    "head": head,
                    "head_type": head_type,
                    "relation": original_pred,
                    "tail": tail,
                    "tail_type": tail_type,
                    "source_id": source_id,
                    "discard_reason": reason,
                    "confidence": confidence,
                    "confidence_detail": detail,
                })
                continue

            # 处理不同格式的三元组（head/relation 或 subject/predicate）
            if "relation" in t:
                pred = t.get("relation", "")
            elif "predicate" in t:
                pred = t.get("predicate", "")
                t["relation"] = pred  # 统一字段名
            else:
                pred = ""

            type_predictions = self._predict_triplet_entity_types(t, evidence_text)
            head_type = type_predictions["head"]["class_id"]
            tail_type = type_predictions["tail"]["class_id"]
            t["head_type"] = head_type
            t["tail_type"] = tail_type
            t["entity_type_prediction"] = type_predictions

            # 计算置信度（使用新的接口）
            confidence_result = self.confidence_scorer.compute_confidence(t, evidence_text=evidence_text)
            confidence = confidence_result["final_score"]
            detail = {
                "logic_info": confidence_result["logic_info"],
                "map_info": confidence_result["map_info"],
                "evidence_info": confidence_result["evidence_info"],
                "entity_type_prediction": type_predictions,
            }

            # 分析分流决策（使用新的接口）
            shunt_result = self.confidence_scorer.analyze_shunt(confidence_result)

            # 检查关系是否在本体中
            if pred in self.allowed_relations:
                type_validation = self._validate_domain_range_types(pred, head_type, tail_type)
                detail["type_validation_info"] = self._type_validation_info(type_validation)
                shunt_result = self._apply_type_validation_to_shunt(shunt_result, type_validation)
                t["relation"] = pred
                t["confidence"] = confidence
                t["confidence_detail"] = detail
                t["confidence_decision"] = shunt_result
                t["type_validation"] = type_validation
                print(f"  本体关系三元组: {head} - {pred} - {tail} (置信度: {confidence:.3f})")
                print(f"  分流: {shunt_result['action']}")

                # 即使关系已在本体中，也必须统一经过置信度阈值分流。
                if shunt_result['shunt'] in ['high', 'medium_high']:
                    valid_triplets.append(t)
                elif shunt_result['shunt'] == 'medium_low':
                    discarded_triplets.append({
                        "head": head,
                        "head_type": head_type,
                        "relation": original_pred,
                        "tail": tail,
                        "tail_type": tail_type,
                        "source_id": source_id,
                        "discard_reason": shunt_result['reason'],
                        "confidence": confidence,
                        "confidence_detail": detail,
                        "confidence_decision": shunt_result,
                        "type_validation": type_validation,
                        "needs_review": True,
                    })
                else:
                    discarded_triplets.append({
                        "head": head,
                        "head_type": head_type,
                        "relation": original_pred,
                        "tail": tail,
                        "tail_type": tail_type,
                        "source_id": source_id,
                        "discard_reason": shunt_result['reason'],
                        "confidence": confidence,
                        "confidence_detail": detail,
                        "confidence_decision": shunt_result,
                        "type_validation": type_validation,
                    })
                continue

            # 模糊匹配修正（处理模型多字少字的问题）
            matches = get_close_matches(pred, self.allowed_relations, n=1, cutoff=0.6)
            if matches:
                corrected_relation = matches[0]
                t["relation"] = corrected_relation

                # 获取分流结果（使用新的接口）
                shunt_result = self.confidence_scorer.analyze_shunt(confidence_result)
                type_validation = self._validate_domain_range_types(corrected_relation, head_type, tail_type)
                detail["type_validation_info"] = self._type_validation_info(type_validation)
                shunt_result = self._apply_type_validation_to_shunt(shunt_result, type_validation)

                t["confidence"] = confidence
                t["confidence_detail"] = detail
                t["confidence_decision"] = shunt_result
                t["type_validation"] = type_validation

                print(f"  修正关系: '{pred}' -> '{corrected_relation}' (置信度: {confidence:.3f})")
                print(f"  分流: {shunt_result['action']}")

                # 根据分流结果处理
                if shunt_result['shunt'] in ['high', 'medium_high']:
                    # 高/中高置信度，直接保留
                    valid_triplets.append(t)
                elif shunt_result['shunt'] == 'medium_low':
                    # 中置信度，进入人工审核队列
                    discarded_triplets.append({
                        "head": head,
                        "head_type": head_type,
                        "relation": original_pred,
                        "tail": tail,
                        "tail_type": tail_type,
                        "corrected_relation": corrected_relation,
                        "source_id": source_id,
                        "discard_reason": shunt_result['reason'],
                        "confidence": confidence,
                        "confidence_detail": detail,
                        "confidence_decision": shunt_result,
                        "type_validation": type_validation,
                        "needs_review": True,
                    })
                else:
                    # 低置信度，丢弃
                    discarded_triplets.append({
                        "head": head,
                        "head_type": head_type,
                        "relation": original_pred,
                        "tail": tail,
                        "tail_type": tail_type,
                        "corrected_relation": corrected_relation,
                        "source_id": source_id,
                        "discard_reason": shunt_result['reason'],
                        "confidence": confidence,
                        "confidence_detail": detail,
                        "confidence_decision": shunt_result,
                        "type_validation": type_validation,
                    })
            else:
                # 无法匹配，丢弃
                shunt_result = self.confidence_scorer.analyze_shunt(confidence_result)

                reason = f"关系词 '{original_pred}' 无法匹配或修正（不在允许的关系列表中）"
                # 显示详细的置信度分解信息
                confidence_detail_full = {
                    "S_llm": confidence_result.get("S_llm", 0),
                    "S_sim_new": confidence_result.get("S_sim_new", 0),
                    "S_evidence": confidence_result.get("S_evidence", 0),
                    "M_logic": confidence_result.get("M_logic", 0),
                    "M_map": confidence_result.get("M_map", 0),
                    "logic_info": confidence_result.get("logic_info", ""),
                    "map_info": confidence_result.get("map_info", ""),
                    "evidence_info": confidence_result.get("evidence_info", ""),
                    "match_relation": confidence_result.get("match_relation", ""),
                }
                print(f"  警告：舍弃非法关系 -> '{original_pred}' (置信度: {confidence:.3f})")
                print(f"    置信度分解:")
                print(f"      base_score = 0.4 * S_llm({confidence_result.get('S_llm', 0):.3f}) + 0.3 * S_sim_new({confidence_result.get('S_sim_new', 0):.3f}) + 0.3 * S_evidence({confidence_result.get('S_evidence', 0):.3f}) = {0.4 * confidence_result.get('S_llm', 0) + 0.3 * confidence_result.get('S_sim_new', 0) + 0.3 * confidence_result.get('S_evidence', 0):.3f}")
                print(f"      Final = base_score * M_logic({confidence_result.get('M_logic', 0)}) * M_map({confidence_result.get('M_map', 0)}) = {confidence:.3f}")
                print(f"    {confidence_result.get('logic_info', '')}")
                print(f"    {confidence_result.get('map_info', '')}")
                print(f"    {confidence_result.get('evidence_info', '')}")
                print(f"    匹配关系: {confidence_result.get('match_relation', '')}")

                discarded_triplets.append({
                    "head": head,
                    "head_type": head_type,
                    "relation": original_pred,
                    "tail": tail,
                    "tail_type": tail_type,
                    "source_id": source_id,
                    "discard_reason": reason,
                    "confidence": confidence,
                    "confidence_detail": detail,
                    "confidence_decision": shunt_result,
                })

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


def call_qwen_traditional(chunk, breadcrumb_path, retry_count=3, ontology=None):
    """
    GraphRAG
    整合了火炮学知识图谱本体，强制使用本体定义的实体类型、关系类型和抽取规则。
    """

    # 从 ontology_data.py 动态加载本体信息
    if ontology is None:
        # 如果没有传入 ontology，则使用默认的 JSON 文件加载
        ontology_info = get_ontology_text()
    else:
        ontology_info = get_ontology_text(ontology=ontology)

    prompt = f"""
Goal:
You are an expert in Artillery Weapon Systems. Transform the following technical text into a semantic graph (a list of triples) based on the provided entity list.
Your task is to identify precise engineering and physical relations between these entities.

Attention:
1. Accuracy over Quantity: Only extract triples that are explicitly supported by the technical text.
2. Entity Integrity: Do not modify the names in the Entity List.
3. Concise Relations: Use professional engineering terms for relations (e.g., "composed_of", "determines", "proportional_to", "increases", "constrains").
4. Avoid Descriptive Objects: Do not use "factor of..." as an entity. Instead, use the action as a relation (e.g., instead of [A, is, factor of B], use [A, affects, B]).
5. Confidence Score: For each triple, provide a confidence score (0.0-1.0) and the original snippet from the text as evidence.

Example (Artillery Domain):
Text: "制退机产生制退阻力，制退阻力决定火炮后坐长度。"
Semantic Graph:
{{
  "triplets": [
    {{
      "head": "制退机",
      "relation": "导致",
      "tail": "制退阻力",
      "evidence": "制退机产生制退阻力",
      "reason": "原文明确说明制退机产生制退阻力，可归一为导致关系",
      "confidence": 0.95,
      "thought": "实体包括制退机和制退阻力；关系由原文的产生归一为导致。"
    }},
    {{
      "head": "制退阻力",
      "relation": "决定",
      "tail": "火炮后坐长度",
      "evidence": "制退阻力决定火炮后坐长度",
      "reason": "原文直接表达决定关系",
      "confidence": 0.95,
      "thought": "实体包括制退阻力和火炮后坐长度；关系为原文直接出现的决定。"
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
- ✅ 正确：如果正文明确描述"初速是影响射程的关键因素"，则可以在统一 schema 中提取 {{"head": "初速", "relation": "受...影响", "tail": "射程", "evidence": "初速是影响射程的关键因素", "reason": "原文说明初速影响射程", "confidence": 0.9, "thought": "初速为影响因素，射程为被影响对象。"}}。

### 铁律二：去重三元组
- **全局去重**：在整个文档范围内，完全相同的三元组只保留一条。
- **输出前自行去重**，绝对禁止让重复三元组进入后续的 Neo4j 导入环节！

【强制执行的思维链路 (CoT)】
在输出 JSON 前，请按以下步骤在 `thought` 字段中进行分析：
1. **实体识别与分类**：识别文中提到的参数（如 $i_c$）、过程（如膛内压力）或模型。判断它们属于哪14类实体。
2. **指代消解（Rule 1）**：检查文中是否有"该"、"其"、"此"或省略主语的情况。必须回溯并替换为具体的实体名词（如将"其"替换为"弹道系数"）。
3. **符号转中文（Rule 9 - 新增）**：检查所有提取的实体，如果包含数学符号（如 v0, x, A, p），必须替换为对应的中文概念名称。
4. **实体名称校验（Rule 9 - 新增）**：检查实体长度是否 ≤ 2 个字符，或是否为纯 LaTeX 符号。如果满足任一条件，必须将其合并到父级概念中或丢弃。
5. **关系对齐（核心步骤）**：
   - 分析实体间的物理逻辑。
   - **检查**：拟使用的关系名是否在本体预定义关系列表中？
   - **转换**：如果原文是"正相关"，必须对齐为"正比于"；如果原文是"取决于"，必须对齐为"决定"。
   - **剔除**：如果关系无法映射到本体的定义中，宁可不提取，也不得自创。
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
      "relation": "string，必须是本体允许关系之一",
      "tail": "string，非空名词或名词短语",
      "evidence": "string，必须是待处理文本中的原文片段，不得改写",
      "reason": "string，说明为什么该证据支持这个三元组",
      "confidence": 0.0,
      "thought": "string，按上方思维链路给出简短抽取依据"
    }}
  ]
}}

输出示例：
{{
  "triplets": [
    {{
      "head": "空气密度",
      "relation": "正比于",
      "tail": "空气阻力加速度",
      "evidence": "空气阻力加速度与空气密度成正比",
      "reason": "原文直接说明空气密度与空气阻力加速度的正比关系",
      "confidence": 0.95,
      "thought": "空气密度和空气阻力加速度均为物理量，关系为正比于。"
    }},
    {{
      "head": "初速",
      "relation": "决定",
      "tail": "射程",
      "evidence": "初速决定射程",
      "reason": "原文直接表达初速对射程的决定作用",
      "confidence": 0.95,
      "thought": "初速为影响弹道结果的参数，射程为结果参数。"
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
                    {"role": "system", "content": "You are a GraphRAG Expert. You strictly use ONLY relations defined in the Artillery Ontology Schema. You FORBID abstract disciplines (like '外弹道学') from causing/affecting physical metrics. You MUST auto-generate Aggregation Hubs (e.g., '影响X的因素') using the '属于' relation whenever a specific factor affects a core metric. Entities MUST be pure nouns and conform to the defined entity types. Relations MUST be one of: 决定, 正比于, 受...影响, 由...组成, 用于, 需满足, 导致, 通过...影响, 属于, 在...条件下, 需通过...反算, 可减小, 需重复, 替代...存在误差."},
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


def main():
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
    try:
        text = read_markdown()
        print(f"   成功读取文本，长度: {len(text)} 字符")
    except Exception as e:
        print(f"读取失败: {e}")
        return

    print("2. 执行基于层级的切分...")
    # 定义 LangChain 要识别的标题级别
    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
        ("####", "Header 4")
    ]

    # 第一步：按标题层级切分
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    md_header_splits = markdown_splitter.split_text(text)

    # 第二步：对超长内容进行二次切分（保留 Metadata）
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    final_docs = text_splitter.split_documents(md_header_splits)

    print(f"   共切分为 {len(final_docs)} 个逻辑片段。")

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

    for doc in tqdm(final_docs, desc="Processing"):
        content = doc.page_content.strip()
        if len(content) < 10:  # 跳过毫无意义的超短文本
            continue

        # ====== 知识屏蔽：检测例题/计算段落 ======
        if is_example_or_calculation_paragraph(content):
            print(f"  [知识屏蔽] 例题/计算段落，跳过提取")
            continue  # 跳过例题/计算段落，不进行三元组提取

        # ====== 提取面包屑路径 ======
        metadata = doc.metadata
        if metadata:
            # metadata 示例: {'Header 1': '第1章', 'Header 2': '1.1节'}
            breadcrumb_path = " > ".join(metadata.values())
            # 取最后一级标题用于生成 ID 前缀
            last_header = list(metadata.values())[-1]
        else:
            breadcrumb_path = "前言/正文"
            last_header = "正文"

        # ====== 组装大模型 Prompt ======
        # 直接传入 content 和 breadcrumb_path，函数内部会构建 Prompt
        triplets = call_qwen_traditional(content, breadcrumb_path, ontology=ontology)

        # ====== 如果提取成功，生成 ID 并落盘 ======
        if triplets and len(triplets) > 0:
            # 过滤掉非字典类型的元素（如字符串）
            valid_triplets_raw = [t for t in triplets if isinstance(t, dict)]
            if len(valid_triplets_raw) != len(triplets):
                print(f"  警告: 过滤掉 {len(triplets) - len(valid_triplets_raw)} 个非字典类型的三元组")
            triplets = valid_triplets_raw

            if not triplets:
                continue

            # 生成可读的标识（用于证据文件和三元组 reference）
            # 清洗标题中的非法字符（只保留汉字、字母、数字）
            clean_header = re.sub(r'[^\w\u4e00-\u9fa5]+', '_', last_header).strip('_')
            content_hash = hashlib.md5((breadcrumb_path + content).encode('utf-8')).hexdigest()[:8]
            chunk_id = f"{clean_header}_{content_hash}"

            # 为每个三元组打上 source_id 烙印
            for t in triplets:
                if isinstance(t, dict):
                    t['source_id'] = chunk_id

            # 后置处理：验证和修复三元组
            print(f"  后置处理：验证和修复三元组...")
            valid_triplets, discarded_triplets = processor.validate_and_fix(triplets, evidence_text=content)
            print(f"  有效三元组数量: {len(valid_triplets)} / {len(triplets)}")
            print(f"  被丢弃三元组数量: {len(discarded_triplets)}")

            all_triplets.extend(valid_triplets)

            # 收集所有被丢弃的三元组
            all_discarded.extend(discarded_triplets)

            # 写入 Markdown 证据库
            with open(EVIDENCE_MD_PATH, 'a', encoding='utf-8') as ev_file:
                ev_file.write(f"## ID: {chunk_id}\n")
                ev_file.write(f"**文献路径**： `{breadcrumb_path}`\n\n")
                ev_file.write(f"> {content}\n\n")
                ev_file.write("---\n\n")

    print(f"4. 提取完成，共获得 {len(all_triplets)} 个三元组。")
    with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_triplets, f, ensure_ascii=False, indent=2)

    print(f"4. 被丢弃的三元组已保存到: {DISCARDED_TRIPLETS_PATH}")
    with open(DISCARDED_TRIPLETS_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_discarded, f, ensure_ascii=False, indent=2)

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
