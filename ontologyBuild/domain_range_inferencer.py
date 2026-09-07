"""
domain/range 归纳模块。

本模块提供基于关系统计数据的 domain/range 约束归纳功能。

本模块只做统计分析，不做语义推理，不调用大模型，不使用向量数据库。
"""

import copy
from typing import Any, Dict, List, Optional, Tuple

# ================= 公共常量 =================

#: 错误类型常量
ERROR_INVALID_RELATION_STATS = "invalid_relation_stats"
ERROR_MISSING_RELATIONS = "missing_relations"
ERROR_INVALID_RELATION_STAT = "invalid_relation_stat"
ERROR_INVALID_PAIR_COUNTS = "invalid_pair_counts"
ERROR_INVALID_PAIR_ITEM = "invalid_pair_item"

#: 特殊类别常量
UNKNOWN_CATEGORY = "__UNKNOWN__"

#: 约束强度常量
CONSTRAINT_STRENGTH_STRONG = "strong"
CONSTRAINT_STRENGTH_MEDIUM = "medium"
CONSTRAINT_STRENGTH_WEAK = "weak"

#: 约束状态常量
CONSTRAINT_STATUS_EXISTING = "existing"
CONSTRAINT_STATUS_COMPATIBLE_WITH_EXISTING = "compatible_with_existing"
CONSTRAINT_STATUS_CONFLICT_CANDIDATE = "conflict_candidate"
CONSTRAINT_STATUS_NEW_CANDIDATE = "new_candidate"

# ===========================================


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    安全转换为 float。

    Args:
        value: 可能是数字的值
        default: 转换失败时的默认值

    Returns:
        float 值
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def normalize_class_name(name: Optional[str]) -> str:
    """
    标准化类别名称。

    Args:
        name: 原始名称

    Returns:
        标准化后的名称，如果为 None 或空字符串，返回 "__UNKNOWN__"
    """
    if name is None:
        return UNKNOWN_CATEGORY
    name = str(name).strip()
    if not name:
        return UNKNOWN_CATEGORY
    return name


def normalize_property_name(name: Optional[str]) -> str:
    """
    标准化关系名称。

    Args:
        name: 原始名称

    Returns:
        标准化后的名称，如果为 None 或空字符串，返回空字符串
    """
    if name is None:
        return ""
    return str(name).strip()


def get_weighted_pair_count(
    weighted_pair_counts: List[Dict[str, Any]], domain: str, range_: str, default: float
) -> float:
    """
    从 weighted_pair_counts 中找到对应 pair 的加权 count。

    Args:
        weighted_pair_counts: 加权 pair 计数列表
        domain: domain 类别名称
        range_: range 类别名称
        default: 如果找不到，默认返回值

    Returns:
        加权 count 值
    """
    if not isinstance(weighted_pair_counts, list):
        return default

    for item in weighted_pair_counts:
        if isinstance(item, dict):
            item_domain = normalize_class_name(item.get("domain"))
            item_range = normalize_class_name(item.get("range"))
            if item_domain == domain and item_range == range_:
                return safe_float(item.get("count"), default)

    return default


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


def match_existing_constraint(
    property_name: str,
    property_type: str,
    domain_candidate: str,
    range_candidate: str,
    existing_constraints: List[Dict[str, Any]],
    class_hierarchy: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    匹配已有约束。

    Args:
        property_name: 属性名称
        property_type: 属性类型
        domain_candidate: 候选 domain
        range_candidate: 候选 range
        existing_constraints: 已有约束列表
        class_hierarchy: 类别层级字典

    Returns:
        匹配结果：
        {
            "constraint_status": "existing | compatible_with_existing | conflict_candidate | new_candidate",
            "matched_existing_constraint": dict | None,
            "conflict_with_existing": bool
        }
    """
    if not isinstance(existing_constraints, list):
        return {
            "constraint_status": CONSTRAINT_STATUS_NEW_CANDIDATE,
            "matched_existing_constraint": None,
            "conflict_with_existing": False,
        }

    for existing in existing_constraints:
        if not isinstance(existing, dict):
            continue

        # 检查 property_name 和 property_type
        existing_property_name = normalize_property_name(existing.get("property_name"))
        existing_property_type = normalize_property_name(existing.get("property_type"))

        if existing_property_name != property_name or existing_property_type != property_type:
            continue

        existing_domain = normalize_class_name(existing.get("domain"))
        existing_range = normalize_class_name(existing.get("range"))

        # 完全一致
        if domain_candidate == existing_domain and range_candidate == existing_range:
            return {
                "constraint_status": CONSTRAINT_STATUS_EXISTING,
                "matched_existing_constraint": existing,
                "conflict_with_existing": False,
            }

        # 检查子类兼容（候选是已有约束的子类）
        if class_hierarchy:
            domain_is_subclass = is_subclass_of(domain_candidate, existing_domain, class_hierarchy)
            range_is_subclass = is_subclass_of(range_candidate, existing_range, class_hierarchy)

            if domain_is_subclass and range_is_subclass:
                return {
                    "constraint_status": CONSTRAINT_STATUS_COMPATIBLE_WITH_EXISTING,
                    "matched_existing_constraint": existing,
                    "conflict_with_existing": False,
                }

        # 冲突：同一 property_name，但 domain/range 不一致且无法通过子类关系解释
        return {
            "constraint_status": CONSTRAINT_STATUS_CONFLICT_CANDIDATE,
            "matched_existing_constraint": existing,
            "conflict_with_existing": True,
        }

    return {
        "constraint_status": CONSTRAINT_STATUS_NEW_CANDIDATE,
        "matched_existing_constraint": None,
        "conflict_with_existing": False,
    }


def determine_constraint_strength(
    support_count: float,
    support_ratio: float,
    support_chunk_count: int,
    support_document_count: int,
    min_pair_count: int,
    strong_threshold: float,
    medium_threshold: float,
    min_support_chunks: int,
    min_support_documents: int,
) -> Tuple[str, bool]:
    """
    确定约束强度。

    Args:
        support_count: 支持计数
        support_ratio: 支持比例
        support_chunk_count: 支持分块数
        support_document_count: 支持文档数
        min_pair_count: 最小 pair 计数
        strong_threshold: strong 阈值
        medium_threshold: medium 阈值
        min_support_chunks: 最小支持分块数
        min_support_documents: 最小支持文档数

    Returns:
        (constraint_strength, need_review)
    """
    # 检查是否满足 strong 条件
    is_strong = (
        support_count >= min_pair_count
        and support_ratio >= strong_threshold
        and support_chunk_count >= min_support_chunks
        and support_document_count >= min_support_documents
    )

    if is_strong:
        return CONSTRAINT_STRENGTH_STRONG, False

    # 检查是否满足 medium 条件
    is_medium = support_count >= min_pair_count and support_ratio >= medium_threshold

    if is_medium:
        return CONSTRAINT_STRENGTH_MEDIUM, True

    # 否则为 weak
    return CONSTRAINT_STRENGTH_WEAK, True


def build_constraint_candidate(
    relation_name: str,
    domain: str,
    range_: str,
    pair_count: float,
    weighted_pair_count: float,
    total_triples: int,
    weighted_total: float,
    support_chunk_count: int,
    support_document_count: int,
    min_pair_count: int,
    min_support_chunks: int,
    min_support_documents: int,
    strong_threshold: float,
    medium_threshold: float,
    allow_unknown: bool,
) -> Optional[Dict[str, Any]]:
    """
    构造单条候选约束。

    Args:
        relation_name: 关系名称
        domain: domain 类别
        range_: range 类别
        pair_count: 支持计数
        weighted_pair_count: 加权支持计数
        total_triples: 总三元组数
        weighted_total: 加权总数
        support_chunk_count: 支持分块数
        support_document_count: 支持文档数
        min_pair_count: 最小 pair 计数
        min_support_chunks: 最小支持分块数
        min_support_documents: 最小支持文档数
        strong_threshold: strong 阈值
        medium_threshold: medium 阈值
        allow_unknown: 是否允许 UNKNOWN

    Returns:
        候选约束字典，如果不满足基本条件返回 None
    """
    # 检查基本条件
    if pair_count < 1:
        return None

    # 检查 UNKNOWN 处理
    if not allow_unknown:
        if domain == UNKNOWN_CATEGORY or range_ == UNKNOWN_CATEGORY:
            return None

    # 空类别检查
    if not domain or not range_:
        return None

    # 计算支持比例
    support_ratio = safe_float(pair_count) / total_triples if total_triples > 0 else 0.0

    # 计算加权支持比例
    if weighted_total > 0:
        weighted_support_ratio = weighted_pair_count / weighted_total
    else:
        weighted_support_ratio = support_ratio

    # 确定约束强度
    constraint_strength, need_review = determine_constraint_strength(
        support_count=pair_count,
        support_ratio=support_ratio,
        support_chunk_count=support_chunk_count,
        support_document_count=support_document_count,
        min_pair_count=min_pair_count,
        strong_threshold=strong_threshold,
        medium_threshold=medium_threshold,
        min_support_chunks=min_support_chunks,
        min_support_documents=min_support_documents,
    )

    # 如果未达到 minimum 要求，需要审核
    if support_chunk_count < min_support_chunks or support_document_count < min_support_documents:
        need_review = True

    return {
        "property_name": relation_name,
        "property_type": "object_property",
        "domain_candidate": domain,
        "range_candidate": range_,
        "support_count": int(pair_count),
        "weighted_support_count": round(weighted_pair_count, 2),
        "total_relation_triples": total_triples,
        "support_ratio": round(support_ratio, 4),
        "weighted_support_ratio": round(weighted_support_ratio, 4),
        "support_chunk_count": support_chunk_count,
        "support_document_count": support_document_count,
        "constraint_strength": constraint_strength,
        "need_review": need_review,
        "reason": f"关系\"{relation_name}\"中，{domain} -> {range_} 出现 {int(pair_count)}/{total_triples} 次，占比 {support_ratio * 100:.2f}%，支持 chunk 数为 {support_chunk_count}，支持文档数为 {support_document_count}。",
    }


def generalize_pair(
    domain: str, range_: str, class_hierarchy: Dict[str, str]
) -> List[Tuple[str, str]]:
    """
    对一个 pair 生成可能的上位类 pair。

    例如：
    domain = "火炮系统"
    range = "动作机构"
    class_hierarchy = {"火炮系统": "武器装备", "动作机构": "火炮部件", "火炮部件": "武器装备"}

    可能生成：
    - 火炮系统 -> 火炮部件
    - 武器装备 -> 动作机构
    - 武器装备 -> 火炮部件

    Args:
        domain: domain 类别
        range_: range 类别
        class_hierarchy: 类别层级字典

    Returns:
        泛化后的 (domain, range) 对列表
    """
    if not class_hierarchy:
        return []

    result = []

    # 获取所有祖先
    domain_ancestors = get_ancestors(domain, class_hierarchy)
    range_ancestors = get_ancestors(range_, class_hierarchy)

    # 添加 domain 本身（如果祖先中有 range 或 range 的祖先）
    # 这里生成组合
    for d_ancestor in [domain] + domain_ancestors:
        for r_ancestor in [range_] + range_ancestors:
            # 跳过原始 pair
            if d_ancestor == domain and r_ancestor == range_:
                continue
            result.append((d_ancestor, r_ancestor))

    return result


def infer_generalized_constraints(
    relations: Dict[str, Any],
    existing_constraints: List[Dict[str, Any]],
    class_hierarchy: Optional[Dict[str, str]],
    min_pair_count: int,
    min_pair_ratio: float,
    strong_threshold: float,
    medium_threshold: float,
    min_support_chunks: int,
    min_support_documents: int,
) -> List[Dict[str, Any]]:
    """
    推断泛化的约束（通过类别层级泛化）。

    Args:
        relations: 关系字典
        existing_constraints: 已有约束列表
        class_hierarchy: 类别层级字典
        min_pair_count: 最小 pair 计数
        min_pair_ratio: 最小 pair 比例
        strong_threshold: strong 阈值
        medium_threshold: medium 阈值
        min_support_chunks: 最小支持分块数
        min_support_documents: 最小支持文档数

    Returns:
        泛化约束列表
    """
    if not class_hierarchy:
        return []

    # 统计泛化 pair 的支持度
    # generalized_pairs[(generalized_domain, generalized_range)] = {
    #     "count": total_count,
    #     "source_pairs": [(domain, range_, count), ...]
    # }
    generalized_pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for relation_name, relation_data in relations.items():
        if not isinstance(relation_data, dict):
            continue

        pair_counts = relation_data.get("pair_counts", [])
        if not isinstance(pair_counts, list):
            continue

        for pair in pair_counts:
            if not isinstance(pair, dict):
                continue

            domain = normalize_class_name(pair.get("domain"))
            range_ = normalize_class_name(pair.get("range"))
            count = safe_float(pair.get("count"), 0)

            # 跳过 UNKNOWN
            if domain == UNKNOWN_CATEGORY or range_ == UNKNOWN_CATEGORY:
                continue

            # 生成泛化 pair
            gen_pairs = generalize_pair(domain, range_, class_hierarchy)

            for gen_domain, gen_range in gen_pairs:
                key = (gen_domain, gen_range)

                if key not in generalized_pairs:
                    generalized_pairs[key] = {
                        "count": 0,
                        "source_pairs": [],
                    }

                generalized_pairs[key]["count"] += count
                generalized_pairs[key]["source_pairs"].append(
                    {"domain": domain, "range": range_, "count": count}
                )

    # 构建泛化约束
    result = []

    for (gen_domain, gen_range), data in generalized_pairs.items():
        count = data["count"]
        source_pairs = data["source_pairs"]

        # 计算总 triples 数（用于支持比例）
        total_triples = 0
        for relation_data in relations.values():
            if isinstance(relation_data, dict):
                total_triples += safe_float(relation_data.get("total_triples", 0))

        support_ratio = count / total_triples if total_triples > 0 else 0.0

        # 计算支持分块和文档数（取最大值）
        support_chunk_count = 0
        support_document_count = 0
        for relation_data in relations.values():
            if isinstance(relation_data, dict):
                support_chunk_count = max(
                    support_chunk_count, safe_float(relation_data.get("support_chunk_count", 0))
                )
                support_document_count = max(
                    support_document_count, safe_float(relation_data.get("support_document_count", 0))
                )

        # 检查最小要求
        if count < min_pair_count or support_ratio < min_pair_ratio:
            continue

        # 确定约束强度
        constraint_strength, need_review = determine_constraint_strength(
            support_count=count,
            support_ratio=support_ratio,
            support_chunk_count=int(support_chunk_count),
            support_document_count=int(support_document_count),
            min_pair_count=min_pair_count,
            strong_threshold=strong_threshold,
            medium_threshold=medium_threshold,
            min_support_chunks=min_support_chunks,
            min_support_documents=min_support_documents,
        )

        # 泛化约束默认需要审核
        need_review = True

        result.append(
            {
                "property_name": "generic",  # 泛化约束使用 generic 作为 property_name
                "property_type": "object_property",
                "domain_candidate": gen_domain,
                "range_candidate": gen_range,
                "source_pairs": source_pairs,
                "support_count": int(count),
                "support_ratio": round(support_ratio, 4),
                "constraint_strength": constraint_strength,
                "need_review": need_review,
                "reason": f"多个子类类别对可归并到上位类约束：{gen_domain} -> {gen_range}。",
            }
        )

    return result


def summarize_constraints(
    constraints: List[Dict[str, Any]], generalized_constraints: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    生成摘要统计。

    Args:
        constraints: 候选约束列表
        generalized_constraints: 泛化约束列表

    Returns:
        摘要字典
    """
    summary = {
        "total_relations": 0,
        "total_constraints": 0,
        "strong": 0,
        "medium": 0,
        "weak": 0,
        "existing": 0,
        "new_candidate": 0,
        "conflict": 0,
        "need_review": 0,
    }

    for c in constraints:
        summary["total_constraints"] += 1

        strength = c.get("constraint_strength", CONSTRAINT_STRENGTH_WEAK)
        if strength == CONSTRAINT_STRENGTH_STRONG:
            summary["strong"] += 1
        elif strength == CONSTRAINT_STRENGTH_MEDIUM:
            summary["medium"] += 1
        else:
            summary["weak"] += 1

        status = c.get("constraint_status", CONSTRAINT_STATUS_NEW_CANDIDATE)
        if status == CONSTRAINT_STATUS_EXISTING:
            summary["existing"] += 1
        elif status == CONSTRAINT_STATUS_NEW_CANDIDATE:
            summary["new_candidate"] += 1
        elif status == CONSTRAINT_STATUS_CONFLICT_CANDIDATE:
            summary["conflict"] += 1

        if c.get("need_review", False):
            summary["need_review"] += 1

    for gc in generalized_constraints:
        summary["total_constraints"] += 1

        strength = gc.get("constraint_strength", CONSTRAINT_STRENGTH_WEAK)
        if strength == CONSTRAINT_STRENGTH_STRONG:
            summary["strong"] += 1
        elif strength == CONSTRAINT_STRENGTH_MEDIUM:
            summary["medium"] += 1
        else:
            summary["weak"] += 1

        # 泛化约束默认为 new_candidate
        summary["new_candidate"] += 1

        if gc.get("need_review", False):
            summary["need_review"] += 1

    return summary


def infer_domain_range_constraints(
    relation_stats: Dict[str, Any],
    ontology: Optional[Dict[str, Any]] = None,
    existing_constraints: Optional[List[Dict[str, Any]]] = None,
    min_pair_count: int = 3,
    min_pair_ratio: float = 0.6,
    min_support_chunks: int = 2,
    min_support_documents: int = 1,
    strong_threshold: float = 0.75,
    medium_threshold: float = 0.5,
    allow_unknown: bool = False,
    generalize_with_hierarchy: bool = True,
) -> Dict[str, Any]:
    """
    根据关系两端类别统计结果，归纳候选 domain/range 约束。

    Args:
        relation_stats: 关系统计结果
        ontology: 本体数据，包含 classes、class_hierarchy 等
        existing_constraints: 已有约束列表
        min_pair_count: 最小 pair 计数
        min_pair_ratio: 最小 pair 比例
        min_support_chunks: 最小支持分块数
        min_support_documents: 最小支持文档数
        strong_threshold: strong 阈值
        medium_threshold: medium 阈值
        allow_unknown: 是否允许 UNKNOWN 类别
        generalize_with_hierarchy: 是否使用类别层级泛化

    Returns:
        归纳结果：
        {
            "passed": bool,
            "constraints": List[Dict],
            "generalized_constraints": List[Dict],
            "summary": Dict,
            "errors": List[Dict]
        }
    """
    # 初始化结果
    result = {
        "passed": True,
        "constraints": [],
        "generalized_constraints": [],
        "summary": {},
        "errors": [],
    }

    # 1. 验证 relation_stats 类型
    if not isinstance(relation_stats, dict):
        result["passed"] = False
        result["errors"].append({
            "error_type": ERROR_INVALID_RELATION_STATS,
            "message": "relation_stats 必须是 dict",
        })
        return result

    # 2. 获取 relations
    relations = relation_stats.get("relations", {})

    if not isinstance(relations, dict):
        result["passed"] = False
        result["errors"].append({
            "error_type": ERROR_MISSING_RELATIONS,
            "message": "relation_stats 缺少 relations 字典",
        })
        return result

    # 3. 提取 ontology 信息
    class_hierarchy = {}
    if isinstance(ontology, dict):
        hierarchy = ontology.get("class_hierarchy", {})
        if isinstance(hierarchy, dict):
            class_hierarchy = hierarchy

    # 4. 处理已有约束
    processed_constraints = []
    errors = []

    for relation_name, relation_data in relations.items():
        if not isinstance(relation_data, dict):
            errors.append({
                "error_type": ERROR_INVALID_RELATION_STAT,
                "message": f"关系\"{relation_name}\"的统计数据不是对象，跳过",
            })
            continue

        total_triples = safe_float(relation_data.get("total_triples", 0))
        weighted_total = safe_float(relation_data.get("weighted_total", 0))
        support_chunk_count = int(relation_data.get("support_chunk_count", 0))
        support_document_count = int(relation_data.get("support_document_count", 0))

        pair_counts = relation_data.get("pair_counts", [])
        weighted_pair_counts = relation_data.get("weighted_pair_counts", [])

        if not isinstance(pair_counts, list):
            errors.append({
                "error_type": ERROR_INVALID_PAIR_COUNTS,
                "message": f"关系\"{relation_name}\"的 pair_counts 不是列表，跳过",
            })
            continue

        # 遍历每个 pair
        for pair in pair_counts:
            if not isinstance(pair, dict):
                errors.append({
                    "error_type": ERROR_INVALID_PAIR_ITEM,
                    "message": f"关系\"{relation_name}\"的 pair_counts 中包含非对象元素，跳过",
                })
                continue

            domain = normalize_class_name(pair.get("domain"))
            range_ = normalize_class_name(pair.get("range"))
            pair_count = safe_float(pair.get("count"), 0)

            # 跳过 UNKNOWN 处理
            if not allow_unknown:
                if domain == UNKNOWN_CATEGORY or range_ == UNKNOWN_CATEGORY:
                    continue

            # 空类别检查
            if not domain or not range_:
                continue

            # 获取加权 pair count
            weighted_pair_count = get_weighted_pair_count(
                weighted_pair_counts if isinstance(weighted_pair_counts, list) else [],
                domain,
                range_,
                pair_count,
            )

            # 构建候选约束
            candidate = build_constraint_candidate(
                relation_name=relation_name,
                domain=domain,
                range_=range_,
                pair_count=pair_count,
                weighted_pair_count=weighted_pair_count,
                total_triples=int(total_triples),
                weighted_total=weighted_total,
                support_chunk_count=support_chunk_count,
                support_document_count=support_document_count,
                min_pair_count=min_pair_count,
                min_support_chunks=min_support_chunks,
                min_support_documents=min_support_documents,
                strong_threshold=strong_threshold,
                medium_threshold=medium_threshold,
                allow_unknown=allow_unknown,
            )

            if candidate is None:
                continue

            # 匹配已有约束
            match_result = match_existing_constraint(
                property_name=relation_name,
                property_type="object_property",
                domain_candidate=domain,
                range_candidate=range_,
                existing_constraints=existing_constraints or [],
                class_hierarchy=class_hierarchy if generalize_with_hierarchy else None,
            )

            candidate["matched_existing_constraint"] = match_result["matched_existing_constraint"]
            candidate["conflict_with_existing"] = match_result["conflict_with_existing"]
            candidate["constraint_status"] = match_result["constraint_status"]

            # 如果与已有约束冲突，设置 need_review 为 true
            if match_result["conflict_with_existing"]:
                candidate["need_review"] = True

            processed_constraints.append(candidate)

    # 5. 推断泛化约束
    generalized_constraints = []
    if generalize_with_hierarchy and class_hierarchy:
        generalized_constraints = infer_generalized_constraints(
            relations=relations,
            existing_constraints=existing_constraints or [],
            class_hierarchy=class_hierarchy,
            min_pair_count=min_pair_count,
            min_pair_ratio=min_pair_ratio,
            strong_threshold=strong_threshold,
            medium_threshold=medium_threshold,
            min_support_chunks=min_support_chunks,
            min_support_documents=min_support_documents,
        )

        # 匹配已有约束
        for gc in generalized_constraints:
            match_result = match_existing_constraint(
                property_name=gc.get("property_name", ""),
                property_type=gc.get("property_type", "object_property"),
                domain_candidate=gc.get("domain_candidate", ""),
                range_candidate=gc.get("range_candidate", ""),
                existing_constraints=existing_constraints or [],
                class_hierarchy=class_hierarchy,
            )

            gc["matched_existing_constraint"] = match_result["matched_existing_constraint"]
            gc["conflict_with_existing"] = match_result["conflict_with_existing"]

            # 泛化约束不能是 existing，因为泛化约束是新生成的
            if match_result["constraint_status"] == CONSTRAINT_STATUS_EXISTING:
                gc["constraint_status"] = CONSTRAINT_STATUS_COMPATIBLE_WITH_EXISTING
            else:
                gc["constraint_status"] = CONSTRAINT_STATUS_NEW_CANDIDATE

            # 泛化约束默认需要审核
            gc["need_review"] = True

    # 6. 排序规则
    def constraint_sort_key(c: Dict[str, Any]) -> Tuple:
        strength_order = {
            CONSTRAINT_STRENGTH_STRONG: 0,
            CONSTRAINT_STRENGTH_MEDIUM: 1,
            CONSTRAINT_STRENGTH_WEAK: 2,
        }
        return (
            c.get("property_name", ""),
            strength_order.get(c.get("constraint_strength", CONSTRAINT_STRENGTH_WEAK), 3),
            -c.get("support_ratio", 0),
            -c.get("support_count", 0),
        )

    processed_constraints.sort(key=constraint_sort_key)
    generalized_constraints.sort(key=constraint_sort_key)

    # 7. 生成摘要
    summary = summarize_constraints(processed_constraints, generalized_constraints)

    # 更新结果
    result["constraints"] = processed_constraints
    result["generalized_constraints"] = generalized_constraints
    result["summary"] = summary
    result["errors"] = errors

    return result


# ===========================================
