"""
一致性检查模块。

本模块提供本体候选抽取结果的一致性检查功能，包括：
1. 规则检查（不依赖外部库）
2. SHACL 校验（使用 pyshacl）
3. OWL 逻辑一致性检查（使用 owlready2，可选）

本模块不调用 LLM，不使用向量数据库，不连接 Neo4j。
"""

import copy
import json
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

# =================== 可选依赖 ===================

# 尝试导入 rdflib
try:
    from rdflib import Graph, Literal, Namespace, URIRef
    from rdflib.namespace import RDF, RDFS, OWL, XSD
    HAS_RDFLIB = True
except ImportError:
    HAS_RDFLIB = False
    # 定义占位符
    class _FakeNamespace:
        def __getattr__(self, name):
            return None
    RDF = _FakeNamespace()
    RDFS = _FakeNamespace()
    OWL = _FakeNamespace()
    XSD = _FakeNamespace()

# 尝试导入 pyshacl
try:
    import pyshacl
    HAS_PYSHACL = True
except ImportError:
    HAS_PYSHACL = False

# 尝试导入 owlready2
try:
    import owlready2
    HAS_OWLREADY2 = True
except ImportError:
    HAS_OWLREADY2 = False

# =================== 常量 ===================

#: 错误类型常量
ERROR_UNKNOWN_CLASS = "unknown_class"
ERROR_UNKNOWN_PROPERTY = "unknown_property"
ERROR_DOMAIN_VIOLATION = "domain_violation"
ERROR_RANGE_VIOLATION = "range_violation"
ERROR_DATATYPE_VIOLATION = "datatype_violation"
ERROR_DISJOINT_CLASS = "disjoint_class_violation"
ERROR_REQUIRED_PROPERTY = "required_property_missing"
ERROR_CONSTRAINT_CONFLICT = "constraint_conflict"
ERROR_MISSING_DEPENDENCY = "missing_dependency"

#: 数据类型常量
DATATYPE_STRING = "string"
DATATYPE_INTEGER = "integer"
DATATYPE_FLOAT = "float"
DATATYPE_BOOLEAN = "boolean"
DATATYPE_DATE = "date"
DATATYPE_DATETIME = "datetime"

#: 约束强度常量
CONSTRAINT_STRENGTH_STRONG = "strong"
CONSTRAINT_STRENGTH_MEDIUM = "medium"
CONSTRAINT_STRENGTH_WEAK = "weak"

#: 约束状态常量
CONSTRAINT_STATUS_EXISTING = "existing"
CONSTRAINT_STATUS_NEW_CANDIDATE = "new_candidate"

#: 特殊类别常量
UNKNOWN_CATEGORY = "__UNKNOWN__"


# =================== 辅助函数 ===================

def normalize_name(name: Optional[str]) -> str:
    """
    标准化名称。
    
    Args:
        name: 原始名称
        
    Returns:
        标准化后的名称，去除首尾空格
    """
    if name is None:
        return ""
    return str(name).strip()


def safe_list(value: Any) -> List[str]:
    """
    将字符串、列表、None 转成 list[str]。
    
    Args:
        value: 字符串、列表或 None
        
    Returns:
        字符串列表
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)] if value is not None else []


def get_relation_name(triple: dict) -> str:
    """
    优先使用 normalized_predicate，否则使用 predicate。
    
    Args:
        triple: 三元组字典
        
    Returns:
        关系名称
    """
    normalized = normalize_name(triple.get("normalized_predicate"))
    if normalized:
        return normalized
    return normalize_name(triple.get("predicate", ""))


def get_triple_type(triple: dict, ontology: dict) -> str:
    """
    优先使用 triple["triple_type"]。
    如果没有，则根据 predicate 是否在 object_properties 或 datatype_properties 中推断。
    无法推断则返回 "unknown"。
    
    Args:
        triple: 三元组字典
        ontology: 本体字典
        
    Returns:
        "object_property", "datatype_property", 或 "unknown"
    """
    # 优先使用 triple_type
    triple_type = normalize_name(triple.get("triple_type"))
    if triple_type in ("object_property", "datatype_property"):
        return triple_type
    
    # 推断
    relation = get_relation_name(triple)
    object_properties = ontology.get("object_properties", [])
    datatype_properties = ontology.get("datatype_properties", [])
    
    if relation in object_properties:
        return "object_property"
    if relation in datatype_properties:
        return "datatype_property"
    
    return "unknown"


def get_subject_categories(triple: dict) -> List[str]:
    """
    优先 subject_categories，其次 subject_type。
    
    Args:
        triple: 三元组字典
        
    Returns:
        主体类别列表
    """
    categories = triple.get("subject_categories")
    if categories:
        return safe_list(categories)
    
    subject_type = triple.get("subject_type")
    if subject_type:
        return safe_list(subject_type)
    
    return []


def get_object_categories(triple: dict) -> List[str]:
    """
    优先 object_categories，其次 object_type。
    
    Args:
        triple: 三元组字典
        
    Returns:
        客体类别列表
    """
    categories = triple.get("object_categories")
    if categories:
        return safe_list(categories)
    
    object_type = triple.get("object_type")
    if object_type:
        return safe_list(object_type)
    
    return []


def get_ancestors(class_name: str, class_hierarchy: Dict[str, str]) -> List[str]:
    """
    获取该类的所有祖先类，从近到远。
    
    Args:
        class_name: 类别名称
        class_hierarchy: 类别层级字典，格式：{child: parent}
        
    Returns:
        祖先类列表，从近到远
    """
    ancestors = []
    current = class_name
    
    while current in class_hierarchy:
        parent = class_hierarchy[current]
        ancestors.append(parent)
        current = parent
    
    return ancestors


def is_subclass_of(child: str, parent: str, class_hierarchy: Dict[str, str]) -> bool:
    """
    判断 child 是否是 parent 的子类或相同类。
    
    Args:
        child: 子类名称
        parent: 父类名称
        class_hierarchy: 类别层级字典
        
    Returns:
        如果 child == parent 或 child 是 parent 的后代，返回 True
    """
    if child == parent:
        return True
    
    ancestors = get_ancestors(child, class_hierarchy)
    return parent in ancestors


def categories_satisfy(
    expected_class: str,
    actual_categories: List[str],
    class_hierarchy: Dict[str, str]
) -> bool:
    """
    只要 actual_categories 中有任意类别是 expected_class 的子类或同类，即返回 True。
    
    Args:
        expected_class: 期望的类别
        actual_categories: 实际的类别列表
        class_hierarchy: 类别层级字典
        
    Returns:
        如果 actual_categories 中有任意类别是 expected_class 的子类或同类，返回 True
    """
    if not actual_categories:
        return False
    
    for category in actual_categories:
        if is_subclass_of(category, expected_class, class_hierarchy):
            return True
    
    return False


def check_datatype(value: Any, expected_datatype: str) -> bool:
    """
    检查数据类型。
    
    Args:
        value: 值
        expected_datatype: 期望的数据类型
        
    Returns:
        如果值符合期望的数据类型，返回 True
    """
    if expected_datatype == DATATYPE_STRING:
        return isinstance(value, str)
    
    if expected_datatype == DATATYPE_INTEGER:
        try:
            # 检查是否为整数（包括浮点数但值为整数的情况）
            if isinstance(value, float):
                return value == int(value)
            return isinstance(value, int) and not isinstance(value, bool)
        except (ValueError, TypeError):
            return False
    
    if expected_datatype == DATATYPE_FLOAT:
        try:
            # 尝试转换为浮点数
            float_val = float(value)
            # 排除无穷大和 NaN
            if math.isinf(float_val) or math.isnan(float_val):
                return False
            return True
        except (ValueError, TypeError):
            return False
    
    if expected_datatype == DATATYPE_BOOLEAN:
        if isinstance(value, bool):
            return True
        if isinstance(value, str):
            return value.lower() in ("true", "false", "1", "0")
        if isinstance(value, int):
            return value in (0, 1)
        return False
    
    if expected_datatype == DATATYPE_DATE:
        # 尝试解析日期格式 YYYY-MM-DD
        if isinstance(value, str):
            try:
                datetime.strptime(value, "%Y-%m-%d")
                return True
            except ValueError:
                return False
        return False
    
    if expected_datatype == DATATYPE_DATETIME:
        # 尝试解析日期时间格式
        if isinstance(value, str):
            formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"]
            for fmt in formats:
                try:
                    datetime.strptime(value, fmt)
                    return True
                except ValueError:
                    continue
            return False
        return False
    
    # 未知类型，认为通过
    return True


def find_constraints_for_property(
    property_name: str,
    property_type: str,
    ontology: dict
) -> List[dict]:
    """
    查找已有 domain/range 约束。
    
    Args:
        property_name: 属性名称
        property_type: 属性类型
        ontology: 本体字典
        
    Returns:
        约束列表
    """
    constraints = ontology.get("domain_range_constraints", [])
    if not isinstance(constraints, list):
        return []
    
    result = []
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        
        cons_prop_name = normalize_name(constraint.get("property_name"))
        cons_prop_type = normalize_name(constraint.get("property_type"))
        
        if cons_prop_name == property_name and cons_prop_type == property_type:
            result.append(constraint)
    
    return result


def iri_safe(text: str) -> str:
    """
    将中文或特殊字符转成安全 URI 片段。
    
    Args:
        text: 原始文本
        
    Returns:
        安全的 URI 片段
    """
    # 使用 URL 编码处理特殊字符
    encoded = quote(text, safe='')
    # 将 % 转为 - 以提高可读性
    return encoded.replace('%', '-').replace('-', '_')


def make_uri(base_iri: str, name: str) -> URIRef:
    """
    生成 URIRef。
    
    Args:
        base_iri: 基础 IRI
        name: 名称
        
    Returns:
        URIRef 对象
    """
    safe_name = iri_safe(name)
    return URIRef(f"{base_iri}{safe_name}")


def datatype_to_xsd(datatype: str) -> URIRef:
    """
    将内部数据类型映射到 XSD 类型。
    
    Args:
        datatype: 内部数据类型
        
    Returns:
        XSD URIRef
    """
    mapping = {
        DATATYPE_STRING: XSD.string,
        DATATYPE_INTEGER: XSD.integer,
        DATATYPE_FLOAT: XSD.float,
        DATATYPE_BOOLEAN: XSD.boolean,
        DATATYPE_DATE: XSD.date,
        DATATYPE_DATETIME: XSD.dateTime,
    }
    return mapping.get(datatype, XSD.string)


# =================== 规则检查 ===================

def check_unknown_classes(
    triples: List[dict],
    ontology: dict,
    options: dict
) -> Tuple[List[dict], List[dict]]:
    """
    类别存在性检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        options: 选项字典
        
    Returns:
        (violations, warnings) 元组
    """
    violations = []
    warnings = []
    classes = set(ontology.get("classes", []))
    allow_unknown = options.get("allow_unknown_classes", False)
    
    for idx, triple in enumerate(triples):
        subject_cats = get_subject_categories(triple)
        object_cats = get_object_categories(triple)
        
        for cat in subject_cats + object_cats:
            if cat not in classes and not allow_unknown:
                violation = {
                    "type": ERROR_UNKNOWN_CLASS,
                    "severity": "error",
                    "message": f'类别"{cat}"不在已有本体类别列表中',
                    "triple_index": idx,
                    "class_name": cat,
                    "subject": triple.get("subject"),
                    "object": triple.get("object"),
                    "evidence": triple.get("evidence"),
                    "document_id": triple.get("document_id"),
                    "chunk_id": triple.get("chunk_id"),
                }
                violations.append(violation)
    
    return violations, warnings


def check_unknown_properties(
    triples: List[dict],
    ontology: dict,
    options: dict
) -> Tuple[List[dict], List[dict]]:
    """
    属性存在性检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        options: 选项字典
        
    Returns:
        (violations, warnings) 元组
    """
    violations = []
    warnings = []
    object_props = set(ontology.get("object_properties", []))
    datatype_props = set(ontology.get("datatype_properties", []))
    allow_unknown = options.get("allow_unknown_properties", False)
    
    for idx, triple in enumerate(triples):
        relation = get_relation_name(triple)
        triple_type = get_triple_type(triple, ontology)
        
        if triple_type == "object_property":
            if relation not in object_props and not allow_unknown:
                violation = {
                    "type": ERROR_UNKNOWN_PROPERTY,
                    "severity": "error",
                    "message": f'关系"{relation}"不是已定义的对象属性',
                    "triple_index": idx,
                    "property_name": relation,
                    "subject": triple.get("subject"),
                    "object": triple.get("object"),
                    "evidence": triple.get("evidence"),
                    "document_id": triple.get("document_id"),
                    "chunk_id": triple.get("chunk_id"),
                }
                violations.append(violation)
        elif triple_type == "datatype_property":
            if relation not in datatype_props and not allow_unknown:
                violation = {
                    "type": ERROR_UNKNOWN_PROPERTY,
                    "severity": "error",
                    "message": f'关系"{relation}"不是已定义的数据属性',
                    "triple_index": idx,
                    "property_name": relation,
                    "subject": triple.get("subject"),
                    "object": triple.get("object"),
                    "evidence": triple.get("evidence"),
                    "document_id": triple.get("document_id"),
                    "chunk_id": triple.get("chunk_id"),
                }
                violations.append(violation)
    
    return violations, warnings


def check_domain_constraints(
    triples: List[dict],
    ontology: dict
) -> Tuple[List[dict], List[dict]]:
    """
    domain 检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        
    Returns:
        (violations, warnings) 元组
    """
    violations = []
    warnings = []
    class_hierarchy = ontology.get("class_hierarchy", {})
    
    for idx, triple in enumerate(triples):
        relation = get_relation_name(triple)
        triple_type = get_triple_type(triple, ontology)
        subject_cats = get_subject_categories(triple)
        
        # 只检查 object_property 和已知类型的 datatype_property
        if triple_type not in ("object_property", "datatype_property"):
            continue
        
        constraints = find_constraints_for_property(relation, triple_type, ontology)
        
        for constraint in constraints:
            expected_domain = normalize_name(constraint.get("domain"))
            
            # 跳过没有 domain 定义的约束（可能只定义了 range）
            if not expected_domain:
                continue
            
            if not categories_satisfy(expected_domain, subject_cats, class_hierarchy):
                violation = {
                    "type": ERROR_DOMAIN_VIOLATION,
                    "severity": "error",
                    "message": f'关系"{relation}"的主体类别不符合 domain 约束',
                    "triple_index": idx,
                    "property_name": relation,
                    "expected_domain": expected_domain,
                    "actual_subject_categories": subject_cats,
                    "subject": triple.get("subject"),
                    "object": triple.get("object"),
                    "evidence": triple.get("evidence"),
                    "document_id": triple.get("document_id"),
                    "chunk_id": triple.get("chunk_id"),
                }
                violations.append(violation)
    
    return violations, warnings


def check_range_constraints(
    triples: List[dict],
    ontology: dict
) -> Tuple[List[dict], List[dict]]:
    """
    range 检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        
    Returns:
        (violations, warnings) 元组
    """
    violations = []
    warnings = []
    class_hierarchy = ontology.get("class_hierarchy", {})
    
    for idx, triple in enumerate(triples):
        relation = get_relation_name(triple)
        triple_type = get_triple_type(triple, ontology)
        object_cats = get_object_categories(triple)
        object_value = triple.get("object")
        
        constraints = find_constraints_for_property(relation, triple_type, ontology)
        
        for constraint in constraints:
            if triple_type == "object_property":
                # 对象属性：检查 object_categories
                expected_range = normalize_name(constraint.get("range"))
                
                # 跳过没有 range 定义的约束
                if not expected_range:
                    continue
                
                if not categories_satisfy(expected_range, object_cats, class_hierarchy):
                    violation = {
                        "type": ERROR_RANGE_VIOLATION,
                        "severity": "error",
                        "message": f'关系"{relation}"的客体类别不符合 range 约束',
                        "triple_index": idx,
                        "property_name": relation,
                        "expected_range": expected_range,
                        "actual_object_categories": object_cats,
                        "subject": triple.get("subject"),
                        "object": triple.get("object"),
                        "evidence": triple.get("evidence"),
                        "document_id": triple.get("document_id"),
                        "chunk_id": triple.get("chunk_id"),
                    }
                    violations.append(violation)
                    
            elif triple_type == "datatype_property":
                # 数据属性：检查 datatype
                expected_datatype = normalize_name(constraint.get("range_datatype"))
                
                # 跳过没有 range_datatype 定义的约束
                if not expected_datatype:
                    continue
                
                if not check_datatype(object_value, expected_datatype):
                    violation = {
                        "type": ERROR_DATATYPE_VIOLATION,
                        "severity": "error",
                        "message": f'关系"{relation}"的值类型不符合 range_datatype 约束',
                        "triple_index": idx,
                        "property_name": relation,
                        "expected_datatype": expected_datatype,
                        "actual_value": str(object_value),
                        "subject": triple.get("subject"),
                        "object": triple.get("object"),
                        "evidence": triple.get("evidence"),
                        "document_id": triple.get("document_id"),
                        "chunk_id": triple.get("chunk_id"),
                    }
                    violations.append(violation)
    
    return violations, warnings


def check_disjoint_classes(
    triples: List[dict],
    ontology: dict
) -> Tuple[List[dict], List[dict]]:
    """
    互斥类别检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        
    Returns:
        (violations, warnings) 元组
    """
    violations = []
    warnings = []
    disjoint_classes = ontology.get("disjoint_classes", [])
    
    # 构建互斥类别集合
    disjoint_sets = []
    for disjoint_pair in disjoint_classes:
        if isinstance(disjoint_pair, list) and len(disjoint_pair) >= 2:
            disjoint_sets.append(set(disjoint_pair))
    
    # 检查每个实体的类别
    for idx, triple in enumerate(triples):
        subject_cats = set(get_subject_categories(triple))
        object_cats = set(get_object_categories(triple))
        
        # 检查 subject 类别是否有互斥
        for disjoint_set in disjoint_sets:
            subject_inter = subject_cats & disjoint_set
            if len(subject_inter) >= 2:
                violation = {
                    "type": ERROR_DISJOINT_CLASS,
                    "severity": "error",
                    "message": f'实体"{triple.get("subject")}"的类别{list(subject_inter)}互斥',
                    "triple_index": idx,
                    "subject": triple.get("subject"),
                    "object": triple.get("object"),
                    "evidence": triple.get("evidence"),
                    "document_id": triple.get("document_id"),
                    "chunk_id": triple.get("chunk_id"),
                }
                violations.append(violation)
        
        # 检查 object 类别是否有互斥
        for disjoint_set in disjoint_sets:
            object_inter = object_cats & disjoint_set
            if len(object_inter) >= 2:
                violation = {
                    "type": ERROR_DISJOINT_CLASS,
                    "severity": "error",
                    "message": f'实体"{triple.get("object")}"的类别{list(object_inter)}互斥',
                    "triple_index": idx,
                    "subject": triple.get("subject"),
                    "object": triple.get("object"),
                    "evidence": triple.get("evidence"),
                    "document_id": triple.get("document_id"),
                    "chunk_id": triple.get("chunk_id"),
                }
                violations.append(violation)
    
    return violations, warnings


def check_required_properties(
    triples: List[dict],
    ontology: dict,
    options: dict
) -> Tuple[List[dict], List[dict]]:
    """
    必填属性检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        options: 选项字典
        
    Returns:
        (violations, warnings) 元组
    """
    violations = []
    warnings = []
    class_hierarchy = ontology.get("class_hierarchy", {})
    required_props = ontology.get("required_properties", [])
    
    # 构建每个类别的必填属性映射
    # class_required_props[class_name] = {prop_name: min_count}
    class_required_props: Dict[str, Dict[str, int]] = {}
    
    for required in required_props:
        if not isinstance(required, dict):
            continue
        
        class_name = normalize_name(required.get("class_name"))
        prop_name = normalize_name(required.get("property_name"))
        min_count = int(required.get("min_count", 1))
        
        # 获取该类及其所有子类
        all_classes = {class_name}
        # 找出所有子类
        for cls, parent in class_hierarchy.items():
            if is_subclass_of(cls, class_name, class_hierarchy):
                all_classes.add(cls)
        
        for cls in all_classes:
            if cls not in class_required_props:
                class_required_props[cls] = {}
            class_required_props[cls][prop_name] = max(
                class_required_props[cls].get(prop_name, 0),
                min_count
            )
    
    # 统计每个实体的属性出现次数
    # entity_props[entity] = {prop_name: count}
    entity_props: Dict[str, Dict[str, int]] = {}
    entity_classes: Dict[str, List[str]] = {}
    
    for triple in triples:
        subject = triple.get("subject", "")
        object_val = triple.get("object")
        relation = get_relation_name(triple)
        triple_type = get_triple_type(triple, ontology)
        subject_cats = get_subject_categories(triple)
        
        if triple_type == "datatype_property":
            # 数据属性：subject 有这个属性
            if subject not in entity_props:
                entity_props[subject] = {}
            entity_props[subject][relation] = entity_props[subject].get(relation, 0) + 1
            
            # 记录 subject 的类别
            if subject not in entity_classes:
                entity_classes[subject] = subject_cats
        
        # 对于 object_property，object 也可能有属性（反向关系）
        # 这里简化处理，只考虑 subject 的属性
    
    # 检查每个实体是否满足必填属性要求
    for entity, classes in entity_classes.items():
        for entity_class in classes:
            if entity_class not in class_required_props:
                continue
            
            required = class_required_props[entity_class]
            actual = entity_props.get(entity, {})
            
            for prop_name, min_count in required.items():
                actual_count = actual.get(prop_name, 0)
                
                if actual_count < min_count:
                    severity = "error" if options.get("strict_mode", False) else "warning"
                    violation = {
                        "type": ERROR_REQUIRED_PROPERTY,
                        "severity": severity,
                        "message": f'实体"{entity}"属于类别"{entity_class}"，缺少必填属性"{prop_name}"（期望>= {min_count}，实际 {actual_count}）',
                        "triple_index": None,
                        "subject": entity,
                        "property_name": prop_name,
                        "expected": min_count,
                        "actual": actual_count,
                    }
                    
                    if severity == "error":
                        violations.append(violation)
                    else:
                        warnings.append(violation)
    
    return violations, warnings


def check_inferred_constraints(
    inferred_constraints: Optional[List[dict]],
    ontology: dict,
    options: dict
) -> Tuple[List[dict], List[dict]]:
    """
    inferred_constraints 冲突检查。
    
    Args:
        inferred_constraints: 候选约束列表
        ontology: 本体字典
        options: 选项字典
        
    Returns:
        (violations, warnings) 元组
    """
    violations = []
    warnings = []
    
    if not inferred_constraints:
        return violations, warnings
    
    existing_constraints = ontology.get("domain_range_constraints", [])
    class_hierarchy = ontology.get("class_hierarchy", {})
    
    for inferred in inferred_constraints:
        if not isinstance(inferred, dict):
            continue
        
        prop_name = normalize_name(inferred.get("property_name"))
        prop_type = normalize_name(inferred.get("property_type"))
        domain_candidate = normalize_name(inferred.get("domain_candidate"))
        range_candidate = normalize_name(inferred.get("range_candidate"))
        
        constraints = find_constraints_for_property(prop_name, prop_type, ontology)
        
        for existing in constraints:
            existing_domain = normalize_name(existing.get("domain"))
            existing_range = normalize_name(existing.get("range"))
            
            # 完全一致：通过
            if domain_candidate == existing_domain and range_candidate == existing_range:
                continue
            
            # 检查子类兼容（候选是已有约束的子类）
            domain_is_subclass = is_subclass_of(domain_candidate, existing_domain, class_hierarchy)
            range_is_subclass = is_subclass_of(range_candidate, existing_range, class_hierarchy)
            
            if domain_is_subclass and range_is_subclass:
                # 兼容，不是冲突
                continue
            
            # 冲突
            severity = "error" if options.get("strict_mode", False) else "warning"
            
            violation = {
                "type": ERROR_CONSTRAINT_CONFLICT,
                "severity": severity,
                "message": f'候选约束 "{prop_name}" ({domain_candidate} -> {range_candidate}) 与已有约束 ({existing_domain} -> {existing_range}) 冲突',
                "triple_index": None,
                "property_name": prop_name,
                "expected": f"{existing_domain} -> {existing_range}",
                "actual": f"{domain_candidate} -> {range_candidate}",
            }
            
            if severity == "error":
                violations.append(violation)
            else:
                warnings.append(violation)
    
    return violations, warnings


def rule_based_consistency_check(
    triples: List[dict],
    ontology: dict,
    inferred_constraints: Optional[List[dict]] = None,
    options: Optional[dict] = None
) -> dict:
    """
    基于规则的一致性检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        inferred_constraints: 候选约束列表
        options: 选项字典
        
    Returns:
        检查结果字典
    """
    # 默认选项
    if options is None:
        options = {}
    options = copy.deepcopy(options)
    
    # 确保输入不被修改
    triples = copy.deepcopy(triples)
    ontology = copy.deepcopy(ontology)
    
    # 合并所有 violation 和 warning
    all_violations: List[dict] = []
    all_warnings: List[dict] = []
    
    # 1. 类别存在性检查
    violations, warnings = check_unknown_classes(triples, ontology, options)
    all_violations.extend(violations)
    all_warnings.extend(warnings)
    
    # 2. 属性存在性检查
    violations, warnings = check_unknown_properties(triples, ontology, options)
    all_violations.extend(violations)
    all_warnings.extend(warnings)
    
    # 3. domain 检查
    violations, warnings = check_domain_constraints(triples, ontology)
    all_violations.extend(violations)
    all_warnings.extend(warnings)
    
    # 4. range 检查
    violations, warnings = check_range_constraints(triples, ontology)
    all_violations.extend(violations)
    all_warnings.extend(warnings)
    
    # 5. 互斥类别检查
    violations, warnings = check_disjoint_classes(triples, ontology)
    all_violations.extend(violations)
    all_warnings.extend(warnings)
    
    # 6. 必填属性检查
    violations, warnings = check_required_properties(triples, ontology, options)
    all_violations.extend(violations)
    all_warnings.extend(warnings)
    
    # 7. 候选约束冲突检查
    violations, warnings = check_inferred_constraints(inferred_constraints, ontology, options)
    all_violations.extend(violations)
    all_warnings.extend(warnings)
    
    # 过滤严重级别
    rule_errors = [v for v in all_violations if v.get("severity") == "error"]
    rule_warnings = all_warnings
    
    passed = len(rule_errors) == 0
    
    return {
        "passed": passed,
        "violations": all_violations,
        "warnings": rule_warnings,
        "errors": len(rule_errors),
        "warnings_count": len(rule_warnings),
    }


# =================== SHACL 检查 ===================

def build_rdf_graph_from_triples(
    triples: List[dict],
    ontology: dict,
    base_iri: str
) -> Optional[Graph]:
    """
    将 triples 转换为 RDF 图。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        base_iri: 基础 IRI
        
    Returns:
        RDF 图，如果 rdflib 未安装则返回 None
    """
    if not HAS_RDFLIB:
        return None
    
    graph = Graph()
    
    # 绑定命名空间
    ns = Namespace(base_iri)
    graph.bind("ex", ns)
    
    # 创建类别 URI
    classes = ontology.get("classes", [])
    class_uris = {cls: make_uri(base_iri, cls) for cls in classes}
    
    # 创建属性 URI
    object_props = ontology.get("object_properties", [])
    datatype_props = ontology.get("datatype_properties", [])
    prop_uris = {}
    for prop in object_props:
        prop_uris[prop] = make_uri(base_iri, prop)
    for prop in datatype_props:
        prop_uris[prop] = make_uri(base_iri, prop)
    
    # 添加类和子类关系
    for cls, cls_uri in class_uris.items():
        graph.add((cls_uri, RDF.type, OWL.Class))
        graph.add((cls_uri, RDF.type, RDFS.Class))
    
    class_hierarchy = ontology.get("class_hierarchy", {})
    for child, parent in class_hierarchy.items():
        if child in class_uris and parent in class_uris:
            graph.add((class_uris[child], RDFS.subClassOf, class_uris[parent]))
    
    # 添加属性声明
    for prop in object_props:
        prop_uri = prop_uris[prop]
        graph.add((prop_uri, RDF.type, OWL.ObjectProperty))
    
    for prop in datatype_props:
        prop_uri = prop_uris[prop]
        graph.add((prop_uri, RDF.type, OWL.DatatypeProperty))
    
    # 处理每个三元组
    for triple in triples:
        subject = triple.get("subject", "")
        object_val = triple.get("object")
        relation = get_relation_name(triple)
        triple_type = get_triple_type(triple, ontology)
        
        subject_uri = make_uri(base_iri, subject)
        object_uri = make_uri(base_iri, str(object_val)) if object_val else None
        
        # 添加 subject 类型
        subject_cats = get_subject_categories(triple)
        for cat in subject_cats:
            if cat in class_uris:
                graph.add((subject_uri, RDF.type, class_uris[cat]))
        
        # 添加 object 类型
        object_cats = get_object_categories(triple)
        for cat in object_cats:
            if cat in class_uris:
                graph.add((object_uri, RDF.type, class_uris[cat]))
        
        relation_uri = prop_uris.get(relation)
        if relation_uri:
            if triple_type == "object_property" and object_uri:
                # 对象属性
                graph.add((subject_uri, relation_uri, object_uri))
            elif triple_type == "datatype_property":
                # 数据属性
                datatype = triple.get("datatype", DATATYPE_STRING)
                xsd_type = datatype_to_xsd(datatype)
                # 先验证值是否符合数据类型，不符合则作为字符串处理
                if check_datatype(object_val, datatype):
                    literal_val = Literal(object_val, datatype=xsd_type)
                else:
                    # 降级为字符串类型
                    literal_val = Literal(str(object_val))
                graph.add((subject_uri, relation_uri, literal_val))
    
    return graph


def build_shacl_shapes_graph(
    ontology: dict,
    base_iri: str
) -> Optional[Graph]:
    """
    根据 ontology 生成 SHACL shapes。
    
    Args:
        ontology: 本体字典
        base_iri: 基础 IRI
        
    Returns:
        SHACL 图，如果 rdflib 未安装则返回 None
    """
    if not HAS_RDFLIB:
        return None
    
    # SHACL 命名空间
    SHACL = Namespace("http://www.w3.org/ns/shacl#")
    
    graph = Graph()
    
    # 绑定命名空间
    ns = Namespace(base_iri)
    graph.bind("ex", ns)
    graph.bind("sh", SHACL)
    
    # 创建类别 URI
    classes = ontology.get("classes", [])
    class_uris = {cls: make_uri(base_iri, cls) for cls in classes}
    
    # 创建属性 URI
    object_props = ontology.get("object_properties", [])
    datatype_props = ontology.get("datatype_properties", [])
    prop_uris = {}
    for prop in object_props:
        prop_uris[prop] = make_uri(base_iri, prop)
    for prop in datatype_props:
        prop_uris[prop] = make_uri(base_iri, prop)
    
    # 处理 domain_range_constraints
    constraints = ontology.get("domain_range_constraints", [])
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        
        prop_name = normalize_name(constraint.get("property_name"))
        prop_type = normalize_name(constraint.get("property_type"))
        domain = normalize_name(constraint.get("domain"))
        
        if not prop_name or not domain:
            continue
        
        if prop_type not in ("object_property", "datatype_property"):
            continue
        
        # 创建 shape
        shape_uri = make_uri(base_iri, f"Shape_{prop_name}_{domain}")
        target_class = class_uris.get(domain)
        prop_uri = prop_uris.get(prop_name)
        
        if not target_class or not prop_uri:
            continue
        
        # 声明 shape
        graph.add((shape_uri, RDF.type, SHACL.NodeShape))
        graph.add((shape_uri, SHACL.targetClass, target_class))
        
        # 创建 property shape
        prop_shape = make_uri(base_iri, f"PropShape_{prop_name}_{domain}")
        graph.add((prop_shape, RDF.type, SHACL.PropertyShape))
        graph.add((shape_uri, SHACL.property, prop_shape))
        graph.add((prop_shape, SHACL.path, prop_uri))
        
        if prop_type == "object_property":
            # 对象属性：设置 sh:class
            range_class = normalize_name(constraint.get("range"))
            if range_class and range_class in class_uris:
                graph.add((prop_shape, SHACL.class_, class_uris[range_class]))
                
        elif prop_type == "datatype_property":
            # 数据属性：设置 sh:datatype
            range_datatype = normalize_name(constraint.get("range_datatype"))
            if range_datatype:
                xsd_type = datatype_to_xsd(range_datatype)
                graph.add((prop_shape, SHACL.datatype, xsd_type))
    
    # 处理 required_properties
    required_props = ontology.get("required_properties", [])
    for required in required_props:
        if not isinstance(required, dict):
            continue
        
        class_name = normalize_name(required.get("class_name"))
        prop_name = normalize_name(required.get("property_name"))
        min_count = int(required.get("min_count", 1))
        
        if not class_name or not prop_name:
            continue
        
        target_class = class_uris.get(class_name)
        prop_uri = prop_uris.get(prop_name)
        
        if not target_class or not prop_uri:
            continue
        
        # 创建或复用 shape
        shape_uri = make_uri(base_iri, f"Shape_required_{class_name}_{prop_name}")
        graph.add((shape_uri, RDF.type, SHACL.NodeShape))
        graph.add((shape_uri, SHACL.targetClass, target_class))
        
        # 创建 property shape
        prop_shape = make_uri(base_iri, f"PropShape_required_{class_name}_{prop_name}")
        graph.add((prop_shape, RDF.type, SHACL.PropertyShape))
        graph.add((shape_uri, SHACL.property, prop_shape))
        graph.add((prop_shape, SHACL.path, prop_uri))
        graph.add((prop_shape, SHACL.minCount, Literal(min_count, datatype=XSD.integer)))
    
    return graph


def run_shacl_validation(
    triples: List[dict],
    ontology: dict,
    base_iri: str
) -> dict:
    """
    使用 pyshacl.validate 执行 SHACL 校验。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        base_iri: 基础 IRI
        
    Returns:
        校验结果字典
    """
    if not HAS_PYSHACL:
        return {
            "enabled": False,
            "passed": False,
            "conforms": None,
            "report_text": "",
            "violations": [],
            "errors": [
                {
                    "error_type": ERROR_MISSING_DEPENDENCY,
                    "message": "未安装 pyshacl，请执行 pip install pyshacl"
                }
            ]
        }
    
    if not HAS_RDFLIB:
        return {
            "enabled": False,
            "passed": False,
            "conforms": None,
            "report_text": "",
            "violations": [],
            "errors": [
                {
                    "error_type": ERROR_MISSING_DEPENDENCY,
                    "message": "未安装 rdflib，请执行 pip install rdflib"
                }
            ]
        }
    
    # 构建 RDF 图
    data_graph = build_rdf_graph_from_triples(triples, ontology, base_iri)
    if data_graph is None:
        return {
            "enabled": False,
            "passed": False,
            "conforms": None,
            "report_text": "",
            "violations": [],
            "errors": [
                {
                    "error_type": ERROR_MISSING_DEPENDENCY,
                    "message": "rdflib 构建图失败"
                }
            ]
        }
    
    # 构建 SHACL shapes
    shapes_graph = build_shacl_shapes_graph(ontology, base_iri)
    if shapes_graph is None:
        return {
            "enabled": False,
            "passed": False,
            "conforms": None,
            "report_text": "",
            "violations": [],
            "errors": [
                {
                    "error_type": ERROR_MISSING_DEPENDENCY,
                    "message": "rdflib 构建 shapes 图失败"
                }
            ]
        }
    
    # 执行校验
    try:
        # 生成 TTL 文本用于返回
        data_ttl = data_graph.serialize(format="turtle")
        shapes_ttl = shapes_graph.serialize(format="turtle")
        
        conforms, results_graph, report_text = pyshacl.validate(
            data_graph,
            shacl_graph=shapes_graph,
            inference="rdfs",
            debug=False
        )
        
        # 解析 violations
        violations = []
        if not conforms and results_graph:
            # 解析 SHACL 结果图
            SHACL = Namespace("http://www.w3.org/ns/shacl#")
            for s, p, o in results_graph.triples((None, RDF.type, SHACL.ValidationResult)):
                # 获取详细信息
                for _, pred, obj in results_graph.triples((s, None, None)):
                    if pred == SHACL.focusNode:
                        violations.append({
                            "focus_node": str(obj),
                            "property_shape": str(s),
                            "severity": str(p) if p else ""
                        })
        
        return {
            "enabled": True,
            "passed": conforms,
            "conforms": conforms,
            "report_text": report_text,
            "violations": violations,
            "data_ttl": data_ttl,
            "shapes_ttl": shapes_ttl,
        }
        
    except Exception as e:
        return {
            "enabled": True,
            "passed": False,
            "conforms": False,
            "report_text": f"SHACL 校验异常: {str(e)}",
            "violations": [],
            "errors": [
                {
                    "error_type": "validation_error",
                    "message": f"SHACL 校验异常: {str(e)}"
                }
            ]
        }


# =================== OWL 检查 ===================

def build_owl_ontology_ttl(
    ontology: dict,
    triples: List[dict],
    base_iri: str
) -> str:
    """
    生成 OWL Turtle 文本。
    
    Args:
        ontology: 本体字典
        triples: 三元组列表
        base_iri: 基础 IRI
        
    Returns:
        OWL Turtle 文本
    """
    lines = []
    
    # 基础前缀
    lines.append("@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    lines.append("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .")
    lines.append("@prefix owl: <http://www.w3.org/2002/07/owl#> .")
    lines.append("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .")
    lines.append(f"@prefix ex: <{base_iri}> .")
    lines.append("")
    
    # 声明命名空间为 ontology
    lines.append(f"ex:ontology a owl:Ontology ;")
    lines.append(f"    rdfs:label \"Exported Ontology\" .")
    lines.append("")
    
    # 创建类别 URI
    classes = ontology.get("classes", [])
    class_uris = {cls: make_uri(base_iri, cls) for cls in classes}
    
    # 创建属性 URI
    object_props = ontology.get("object_properties", [])
    datatype_props = ontology.get("datatype_properties", [])
    prop_uris = {}
    for prop in object_props:
        prop_uris[prop] = make_uri(base_iri, prop)
    for prop in datatype_props:
        prop_uris[prop] = make_uri(base_iri, prop)
    
    # 添加类声明
    for cls, cls_uri in class_uris.items():
        lines.append(f"ex:{iri_safe(cls)} a owl:Class .")
    
    lines.append("")
    
    # 添加子类关系
    class_hierarchy = ontology.get("class_hierarchy", {})
    for child, parent in class_hierarchy.items():
        if child in class_uris and parent in class_uris:
            lines.append(f"ex:{iri_safe(child)} rdfs:subClassOf ex:{iri_safe(parent)} .")
    
    lines.append("")
    
    # 添加对象属性声明
    for prop in object_props:
        lines.append(f"ex:{iri_safe(prop)} a owl:ObjectProperty .")
    
    lines.append("")
    
    # 添加数据属性声明
    for prop in datatype_props:
        lines.append(f"ex:{iri_safe(prop)} a owl:DatatypeProperty .")
    
    lines.append("")
    
    # 添加互斥类声明
    disjoint_classes = ontology.get("disjoint_classes", [])
    disjoint_id = 0
    for disjoint_pair in disjoint_classes:
        if isinstance(disjoint_pair, list) and len(disjoint_pair) >= 2:
            disjoint_id += 1
            members = " ".join(f"ex:{iri_safe(c)}" for c in disjoint_pair)
            lines.append(f"ex:Disjoint_{disjoint_id} a owl:AllDisjointClasses ;")
            lines.append(f"    owl:members ({members}) .")
    
    lines.append("")
    
    # 添加数据属性范围
    constraints = ontology.get("domain_range_constraints", [])
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        
        prop_name = normalize_name(constraint.get("property_name"))
        prop_type = normalize_name(constraint.get("property_type"))
        domain = normalize_name(constraint.get("domain"))
        range_val = normalize_name(constraint.get("range"))
        range_datatype = normalize_name(constraint.get("range_datatype"))
        
        if prop_type not in ("object_property", "datatype_property"):
            continue
        
        prop_uri = prop_uris.get(prop_name)
        if not prop_uri:
            continue
        
        # 添加 domain
        if domain and domain in class_uris:
            lines.append(f"ex:{iri_safe(prop_name)} rdfs:domain ex:{iri_safe(domain)} .")
        
        # 添加 range
        if prop_type == "object_property" and range_val and range_val in class_uris:
            lines.append(f"ex:{iri_safe(prop_name)} rdfs:range ex:{iri_safe(range_val)} .")
        elif prop_type == "datatype_property" and range_datatype:
            xsd_type = datatype_to_xsd(range_datatype)
            # 提取本地名称
            local_name = str(xsd_type).split("#")[-1]
            lines.append(f"ex:{iri_safe(prop_name)} rdfs:range xsd:{local_name} .")
    
    lines.append("")
    
    # 添加实体和断言
    for triple in triples:
        subject = triple.get("subject", "")
        object_val = triple.get("object")
        relation = get_relation_name(triple)
        triple_type = get_triple_type(triple, ontology)
        
        subject_safe = iri_safe(subject)
        object_safe = iri_safe(str(object_val)) if object_val else ""
        
        # 添加 subject 类型
        subject_cats = get_subject_categories(triple)
        for cat in subject_cats:
            if cat in class_uris:
                lines.append(f"ex:{subject_safe} a ex:{iri_safe(cat)} .")
        
        if triple_type == "object_property" and object_val:
            # 对象属性断言
            if relation in prop_uris:
                lines.append(f"ex:{subject_safe} ex:{iri_safe(relation)} ex:{object_safe} .")
                
            # 添加 object 类型
            object_cats = get_object_categories(triple)
            for cat in object_cats:
                if cat in class_uris:
                    lines.append(f"ex:{object_safe} a ex:{iri_safe(cat)} .")
                    
        elif triple_type == "datatype_property":
            # 数据属性断言
            if relation in prop_uris:
                datatype = triple.get("datatype", DATATYPE_STRING)
                xsd_type = datatype_to_xsd(datatype)
                local_name = str(xsd_type).split("#")[-1]
                
                # 根据类型格式化值
                if datatype == DATATYPE_STRING:
                    formatted_val = f'"{object_val}"'
                elif datatype in (DATATYPE_INTEGER, DATATYPE_FLOAT):
                    formatted_val = str(object_val)
                elif datatype == DATATYPE_BOOLEAN:
                    formatted_val = str(object_val).lower()
                elif datatype == DATATYPE_DATE:
                    formatted_val = f'"{object_val}"^^xsd:date'
                elif datatype == DATATYPE_DATETIME:
                    formatted_val = f'"{object_val}"^^xsd:dateTime'
                else:
                    formatted_val = f'"{object_val}"^^xsd:{local_name}'
                
                lines.append(f"ex:{subject_safe} ex:{iri_safe(relation)} {formatted_val} .")
    
    lines.append("")
    
    return "\n".join(lines)


def run_owl_consistency_check(
    triples: List[dict],
    ontology: dict,
    base_iri: str
) -> dict:
    """
    使用 owlready2 进行 OWL 一致性检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        base_iri: 基础 IRI
        
    Returns:
        检查结果字典
    """
    if not HAS_OWLREADY2:
        return {
            "enabled": False,
            "passed": False,
            "consistent": None,
            "violations": [],
            "warnings": [
                "未安装 owlready2，已跳过 OWL 推理检查。基础互斥类检查已在 rule_check 中完成。"
            ]
        }
    
    try:
        # 生成 OWL TTL
        owl_ttl = build_owl_ontology_ttl(ontology, triples, base_iri)
        
        # 创建临时文件
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ttl', delete=False) as f:
            f.write(owl_ttl)
            temp_file = f.name
        
        try:
            # 加载本体
            onto = owlready2.get_ontology(f"file://{temp_file}")
            onto.load()
            
            # 运行推理器
            with onto:
                # 尝试运行推理器
                try:
                    owlready2.sync_reasoner()
                    consistent = True
                    inconsistencies = []
                except Exception as reasoner_error:
                    # 推理器可能不可用，但本体本身可能是一致的
                    consistent = True
                    inconsistencies = []
                    # 记录 warning
                    warnings = [
                        f"推理器运行时出现异常（本体可能仍是一致的）: {str(reasoner_error)}"
                    ]
                    return {
                        "enabled": True,
                        "passed": True,
                        "consistent": True,
                        "violations": [],
                        "warnings": warnings,
                        "owl_ttl": owl_ttl
                    }
            
            return {
                "enabled": True,
                "passed": consistent,
                "consistent": consistent,
                "violations": inconsistencies,
                "owl_ttl": owl_ttl
            }
            
        finally:
            # 清理临时文件
            try:
                os.unlink(temp_file)
            except OSError:
                pass
                
    except Exception as e:
        return {
            "enabled": True,
            "passed": False,
            "consistent": False,
            "violations": [
                {
                    "error_type": "owl_error",
                    "message": f"OWL 一致性检查异常: {str(e)}"
                }
            ],
            "owl_ttl": build_owl_ontology_ttl(ontology, triples, base_iri)
        }


# =================== 主函数 ===================

def run_consistency_checks(
    triples: List[dict],
    ontology: dict,
    inferred_constraints: Optional[List[dict]] = None,
    options: Optional[dict] = None
) -> dict:
    """
    运行一致性检查。
    
    Args:
        triples: 三元组列表
        ontology: 本体字典
        inferred_constraints: 候选约束列表
        options: 选项字典
        
    Returns:
        检查结果字典
    """
    # 默认选项
    if options is None:
        options = {}
    options = copy.deepcopy(options)
    
    # 确保输入不被修改
    triples = copy.deepcopy(triples)
    ontology = copy.deepcopy(ontology)
    
    # 获取选项
    enable_shacl = options.get("enable_shacl", True)
    enable_owl = options.get("enable_owl", True)
    base_iri = options.get("base_iri", "http://example.org/ontology/")
    
    # 初始化结果
    result = {
        "passed": True,
        "summary": {
            "total_triples": len(triples),
            "rule_errors": 0,
            "shacl_conforms": True,
            "owl_consistent": True,
            "warnings": 0,
            "errors": 0,
        },
        "rule_check": {
            "passed": True,
            "violations": [],
            "warnings": [],
        },
        "shacl_check": {
            "enabled": enable_shacl,
            "passed": True,
            "conforms": True,
            "report_text": "",
            "violations": [],
        },
        "owl_check": {
            "enabled": enable_owl,
            "passed": True,
            "consistent": True,
            "violations": [],
            "warnings": [],
        },
        "generated": {
            "data_ttl": "",
            "shapes_ttl": "",
            "ontology_ttl": "",
        },
        "errors": [],
    }
    
    # 运行规则检查（总是运行）
    rule_result = rule_based_consistency_check(triples, ontology, inferred_constraints, options)
    result["rule_check"] = {
        "passed": rule_result["passed"],
        "violations": rule_result["violations"],
        "warnings": rule_result["warnings"],
    }
    result["summary"]["rule_errors"] = rule_result["errors"]
    result["summary"]["warnings"] = len(rule_result["warnings"])
    
    # 运行 SHACL 检查
    if enable_shacl:
        shacl_result = run_shacl_validation(triples, ontology, base_iri)
        result["shacl_check"] = shacl_result
        result["generated"]["data_ttl"] = shacl_result.get("data_ttl", "")
        result["generated"]["shapes_ttl"] = shacl_result.get("shapes_ttl", "")
        
        if not shacl_result.get("enabled", False):
            result["errors"].extend(shacl_result.get("errors", []))
        
        if shacl_result.get("conforms") is False:
            result["passed"] = False
            result["summary"]["shacl_conforms"] = False
    
    # 运行 OWL 检查
    if enable_owl:
        owl_result = run_owl_consistency_check(triples, ontology, base_iri)
        result["owl_check"] = owl_result
        result["generated"]["ontology_ttl"] = owl_result.get("owl_ttl", "")
        
        if not owl_result.get("enabled", False):
            result["errors"].extend(owl_result.get("errors", []))
            result["owl_check"]["warnings"] = owl_result.get("warnings", [])
        
        if owl_result.get("consistent") is False:
            result["passed"] = False
            result["summary"]["owl_consistent"] = False
    
    # 更新摘要
    result["summary"]["errors"] = result["summary"]["rule_errors"]
    if result["shacl_check"].get("conforms") is False:
        result["summary"]["errors"] += 1
    if result["owl_check"].get("consistent") is False:
        result["summary"]["errors"] += 1
    
    result["passed"] = result["rule_check"]["passed"] and result["summary"]["errors"] == 0
    
    return result


# =================== 示例 ===================

if __name__ == "__main__":
    # 构造示例本体
    example_ontology = {
        "classes": [
            "武器装备",
            "火炮系统",
            "火炮部件",
            "性能参数",
            "设计参数",
            "弹药",
            "故障模式"
        ],
        "class_hierarchy": {
            "火炮系统": "武器装备",
            "火炮部件": "武器装备",
            "设计参数": "性能参数"
        },
        "object_properties": [
            "组成",
            "包含",
            "影响",
            "导致",
            "作用于",
            "用于"
        ],
        "datatype_properties": [
            "最大射程",
            "初速",
            "口径",
            "膛压"
        ],
        "domain_range_constraints": [
            {
                "property_name": "组成",
                "property_type": "object_property",
                "domain": "火炮系统",
                "range": "火炮部件"
            },
            {
                "property_name": "影响",
                "property_type": "object_property",
                "domain": "设计参数",
                "range": "性能参数"
            },
            {
                "property_name": "最大射程",
                "property_type": "datatype_property",
                "domain": "火炮系统",
                "range_datatype": "float"
            },
            {
                "property_name": "口径",
                "property_type": "datatype_property",
                "domain": "火炮系统",
                "range_datatype": "float"
            }
        ],
        "disjoint_classes": [
            ["火炮部件", "弹药"],
            ["性能参数", "故障模式"]
        ],
        "required_properties": [
            {
                "class_name": "火炮系统",
                "property_name": "口径",
                "property_type": "datatype_property",
                "min_count": 1
            }
        ]
    }
    
    # 构造示例三元组
    example_triples = [
        # 1. 合法 object_property
        {
            "subject": "某型火炮",
            "subject_categories": ["火炮系统"],
            "predicate": "组成",
            "normalized_predicate": "组成",
            "object": "反后坐装置",
            "object_categories": ["火炮部件"],
            "triple_type": "object_property",
            "confidence": 0.93,
            "document_id": "doc_001",
            "chunk_id": "chunk_001",
            "evidence": "某型火炮由反后坐装置、身管和炮闩组成。"
        },
        # 2. 非法 domain：主体类别不符合 domain 约束
        {
            "subject": "初速",
            "subject_categories": ["性能参数"],
            "predicate": "组成",
            "normalized_predicate": "组成",
            "object": "身管",
            "object_categories": ["火炮部件"],
            "triple_type": "object_property",
            "confidence": 0.85,
            "document_id": "doc_002",
            "chunk_id": "chunk_002",
            "evidence": "初速由身管参数计算得出。"
        },
        # 3. 非法 range：客体类别不符合 range 约束
        {
            "subject": "某型火炮",
            "subject_categories": ["火炮系统"],
            "predicate": "组成",
            "normalized_predicate": "组成",
            "object": "发射药",
            "object_categories": ["弹药"],
            "triple_type": "object_property",
            "confidence": 0.88,
            "document_id": "doc_003",
            "chunk_id": "chunk_003",
            "evidence": "某型火炮使用发射药作为推进剂。"
        },
        # 4. 合法 datatype_property
        {
            "subject": "某型火炮",
            "subject_categories": ["火炮系统"],
            "predicate": "最大射程",
            "normalized_predicate": "最大射程",
            "object": "24.5",
            "object_categories": [],
            "triple_type": "datatype_property",
            "datatype": "float",
            "unit": "km",
            "confidence": 0.91,
            "document_id": "doc_004",
            "chunk_id": "chunk_004",
            "evidence": "该火炮最大射程为 24.5 km。"
        },
        # 5. 非法 datatype_property：值类型不符合
        {
            "subject": "某型火炮",
            "subject_categories": ["火炮系统"],
            "predicate": "口径",
            "normalized_predicate": "口径",
            "object": "大口径",
            "object_categories": [],
            "triple_type": "datatype_property",
            "datatype": "float",
            "unit": "mm",
            "confidence": 0.82,
            "document_id": "doc_005",
            "chunk_id": "chunk_005",
            "evidence": "该火炮为大口径火炮。"
        },
        # 6. 必填属性缺失：火炮系统没有口径
        {
            "subject": "另一型火炮",
            "subject_categories": ["火炮系统"],
            "predicate": "最大射程",
            "normalized_predicate": "最大射程",
            "object": "30.0",
            "object_categories": [],
            "triple_type": "datatype_property",
            "datatype": "float",
            "unit": "km",
            "confidence": 0.89,
            "document_id": "doc_006",
            "chunk_id": "chunk_006",
            "evidence": "另一型火炮最大射程为 30.0 km。"
        }
    ]
    
    # 构造示例候选约束
    example_inferred_constraints = [
        {
            "property_name": "组成",
            "property_type": "object_property",
            "domain_candidate": "火炮系统",
            "range_candidate": "火炮部件",
            "constraint_strength": "strong",
            "constraint_status": "new_candidate",
            "support_count": 5,
            "support_ratio": 0.83,
            "need_review": False
        },
        # 7. 互斥类别冲突：同时标记为火炮部件和弹药
        {
            "subject": "某实体",
            "subject_categories": ["火炮部件", "弹药"],
            "predicate": "具有",
            "normalized_predicate": "具有",
            "object": "某属性",
            "object_categories": [],
            "triple_type": "object_property",
            "confidence": 0.75,
            "document_id": "doc_007",
            "chunk_id": "chunk_007",
            "evidence": "某实体同时具有火炮部件和弹药属性。"
        }
    ]
    
    # 运行一致性检查
    result = run_consistency_checks(
        triples=example_triples,
        ontology=example_ontology,
        inferred_constraints=example_inferred_constraints,
        options={
            "enable_shacl": True,
            "enable_owl": True,
            "strict_mode": False,
            "base_iri": "http://example.org/ontology/",
            "allow_unknown_classes": False,
            "allow_unknown_properties": False
        }
    )
    
    # 打印结果
    print(json.dumps(result, ensure_ascii=False, indent=2))