"""
关系两端类别统计模块。

本模块提供确定性的关系统计功能，用于：
1. 对每种关系统计主语类别和宾语类别分布
2. 根据统计结果归纳候选 domain/range 约束

本模块只做统计分析，不做语义推理，不调用大模型，不使用向量数据库。
"""

import copy
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple


# ================= 公共常量 =================

#: 错误类型常量
ERROR_INVALID_TRIPLES = "invalid_triples"
ERROR_INVALID_TRIPLE_ITEM = "invalid_triple_item"
ERROR_MISSING_RELATION = "missing_relation"
ERROR_INVALID_CONFIDENCE = "invalid_confidence"

#: 特殊类别常量
UNKNOWN_CATEGORY = "__UNKNOWN__"

#: 默认置信度
DEFAULT_CONFIDENCE = 1.0

# ===========================================


def as_list(value: Any) -> List[str]:
    """
    把字符串、列表、None 统一转成 list[str]。

    Args:
        value: 可能是字符串、列表或 None 的值

    Returns:
        字符串列表。如果输入是 None，返回空列表；如果是字符串，返回单元素列表。
    """
    if value is None:
        return []
    if isinstance(value, str):
        if value.strip():
            return [value.strip()]
        return []
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result
    return []


def get_relation_name(triple: dict) -> Optional[str]:
    """
    获取关系名称。

    优先取 normalized_predicate，否则取 predicate。

    Args:
        triple: 三元组字典

    Returns:
        关系名称字符串，如果两者都不存在或为空，返回 None
    """
    # 优先使用 normalized_predicate
    normalized = triple.get("normalized_predicate")
    if isinstance(normalized, str) and normalized.strip():
        return normalized.strip()

    # 否则使用 predicate
    predicate = triple.get("predicate")
    if isinstance(predicate, str) and predicate.strip():
        return predicate.strip()

    return None


def get_subject_categories(triple: dict) -> List[str]:
    """
    获取主语类别列表。

    优先使用 subject_categories，否则使用 subject_type，
    如果都没有，返回 ["__UNKNOWN__"]。

    Args:
        triple: 三元组字典

    Returns:
        类别字符串列表
    """
    # 优先使用 subject_categories
    categories = triple.get("subject_categories")
    categories_list = as_list(categories)
    if categories_list:
        return categories_list

    # 否则使用 subject_type
    subject_type = triple.get("subject_type")
    subject_type_list = as_list(subject_type)
    if subject_type_list:
        return subject_type_list

    # 都没有，返回特殊类别
    return [UNKNOWN_CATEGORY]


def get_object_categories(triple: dict) -> List[str]:
    """
    获取宾语类别列表。

    优先使用 object_categories，否则使用 object_type，
    如果都没有，返回 ["__UNKNOWN__"]。

    Args:
        triple: 三元组字典

    Returns:
        类别字符串列表
    """
    # 优先使用 object_categories
    categories = triple.get("object_categories")
    categories_list = as_list(categories)
    if categories_list:
        return categories_list

    # 否则使用 object_type
    object_type = triple.get("object_type")
    object_type_list = as_list(object_type)
    if object_type_list:
        return object_type_list

    # 都没有，返回特殊类别
    return [UNKNOWN_CATEGORY]


def get_confidence(triple: dict) -> float:
    """
    获取并规范化 confidence。

    处理逻辑：
    - 如果缺失，默认使用 1.0
    - 如果不能转为 float，使用 1.0
    - 如果 < 0，截断为 0
    - 如果 > 1，截断为 1

    Args:
        triple: 三元组字典

    Returns:
        规范化后的置信度（0.0 ~ 1.0）
    """
    confidence = triple.get("confidence")

    # 缺失时使用默认值
    if confidence is None:
        return DEFAULT_CONFIDENCE

    try:
        confidence = float(confidence)
    except (ValueError, TypeError):
        return DEFAULT_CONFIDENCE

    # 截断到 [0, 1] 范围
    if confidence < 0:
        confidence = 0.0
    if confidence > 1:
        confidence = 1.0

    return confidence


def make_dedup_key(triple: dict) -> Tuple:
    """
    生成去重 key。

    Key 由 (document_id, chunk_id, subject, relation, object) 组成。

    Args:
        triple: 三元组字典

    Returns:
        去重 key 元组
    """
    document_id = triple.get("document_id") or ""
    chunk_id = triple.get("chunk_id") or ""
    subject = triple.get("subject") or ""
    relation = get_relation_name(triple) or ""
    object_value = triple.get("object") or ""

    return (document_id, chunk_id, subject, relation, object_value)


def get_example(triple: dict) -> dict:
    """
    从三元组生成示例格式。

    Args:
        triple: 三元组字典

    Returns:
        示例字典
    """
    return {
        "subject": triple.get("subject"),
        "predicate": get_relation_name(triple),
        "object": triple.get("object"),
        "subject_categories": get_subject_categories(triple),
        "object_categories": get_object_categories(triple),
        "document_id": triple.get("document_id"),
        "chunk_id": triple.get("chunk_id"),
        "confidence": get_confidence(triple),
        "evidence": triple.get("evidence"),
    }


def collect_relation_endpoint_statistics(
    triples: List[dict],
    deduplicate: bool = True,
    use_confidence_weight: bool = True,
    max_examples_per_relation: int = 5,
) -> Dict[str, Any]:
    """
    收集关系两端类别的统计信息。

    对每种关系，统计：
    1. total_triples: 三元组总数
    2. weighted_total: 加权总数（使用 confidence）
    3. domain_counts: 主语类别出现次数
    4. range_counts: 宾语类别出现次数
    5. pair_counts: 主语-宾语类别对出现次数
    6. weighted_*_counts: 加权版本
    7. support_document_count: 支持的关系文档数
    8. support_chunk_count: 支持的关系分块数
    9. examples: 示例列表

    Args:
        triples: 三元组列表
        deduplicate: 是否去重
        use_confidence_weight: 是否使用置信度加权
        max_examples_per_relation: 每种关系保留的最大示例数

    Returns:
        统计结果字典，包含 relations 和 errors
    """
    # 初始化结果
    result: Dict[str, Any] = {
        "relations": {},
        "errors": [],
    }

    # 1. 验证 triples 类型
    if not isinstance(triples, list):
        result["errors"].append({
            "error_type": ERROR_INVALID_TRIPLES,
            "message": "triples 必须是 list",
        })
        return result

    # 用于去重的集合
    seen_keys = set() if deduplicate else None

    # 用于统计的字典：relation -> list of triples
    relation_triples: Dict[str, List[Tuple[dict, float]]] = defaultdict(list)

    # 2. 遍历 triples
    for index, triple in enumerate(triples):
        # 验证 triple 类型
        if not isinstance(triple, dict):
            result["errors"].append({
                "error_type": ERROR_INVALID_TRIPLE_ITEM,
                "message": f"triples[{index}] 不是对象，跳过",
            })
            continue

        # 3. 检查关系名称
        relation_name = get_relation_name(triple)
        if relation_name is None:
            result["errors"].append({
                "error_type": ERROR_MISSING_RELATION,
                "message": f"triples[{index}] 缺少 predicate 和 normalized_predicate，跳过",
            })
            continue

        # 4. 去重检查
        if deduplicate:
            key = make_dedup_key(triple)
            if key in seen_keys:
                continue
            seen_keys.add(key)

        # 5. 获取置信度
        confidence = get_confidence(triple)

        # 6. 获取类别列表
        subject_categories = get_subject_categories(triple)
        object_categories = get_object_categories(triple)

        # 7. 添加到统计
        relation_triples[relation_name].append((triple, confidence))

    # 8. 对每个关系进行统计
    for relation_name, triple_list in relation_triples.items():
        total_triples = len(triple_list)

        # 初始化统计
        domain_counter = Counter()
        range_counter = Counter()
        pair_counter = Counter()
        weighted_domain_counter = Counter()
        weighted_range_counter = Counter()
        weighted_pair_counter = Counter()
        document_ids = set()
        chunk_ids = set()
        examples = []
        weighted_total = 0.0

        for triple, confidence in triple_list:
            # 加权计数
            if use_confidence_weight:
                weight = confidence
            else:
                weight = 1.0
            weighted_total += weight

            # 获取类别列表
            subject_categories = get_subject_categories(triple)
            object_categories = get_object_categories(triple)

            # 统计 domain 和 range
            for subject_cat in subject_categories:
                domain_counter[subject_cat] += 1
                weighted_domain_counter[subject_cat] += weight

            for object_cat in object_categories:
                range_counter[object_cat] += 1
                weighted_range_counter[object_cat] += weight

            # 统计类别对（所有组合）
            for subject_cat in subject_categories:
                for object_cat in object_categories:
                    pair_counter[(subject_cat, object_cat)] += 1
                    weighted_pair_counter[(subject_cat, object_cat)] += weight

            # 统计文档和分块
            doc_id = triple.get("document_id")
            chunk_id = triple.get("chunk_id")
            if doc_id:
                document_ids.add(doc_id)
            if chunk_id:
                chunk_ids.add(chunk_id)

            # 保存示例
            if len(examples) < max_examples_per_relation:
                examples.append(get_example(triple))

        # 9. 构建统计结果
        relation_result = {
            "relation": relation_name,
            "total_triples": total_triples,
            "weighted_total": round(weighted_total, 2),
            "support_document_count": len(document_ids),
            "support_chunk_count": len(chunk_ids),
            "domain_counts": [
                {"name": name, "count": count}
                for name, count in domain_counter.most_common()
            ],
            "range_counts": [
                {"name": name, "count": count}
                for name, count in range_counter.most_common()
            ],
            "pair_counts": [
                {"domain": domain, "range": range_, "count": count}
                for (domain, range_), count in pair_counter.most_common()
            ],
            "weighted_domain_counts": [
                {"name": name, "count": round(count, 2)}
                for name, count in weighted_domain_counter.most_common()
            ],
            "weighted_range_counts": [
                {"name": name, "count": round(count, 2)}
                for name, count in weighted_range_counter.most_common()
            ],
            "weighted_pair_counts": [
                {"domain": domain, "range": range_, "count": round(count, 2)}
                for (domain, range_), count in weighted_pair_counter.most_common()
            ],
            "examples": examples,
        }

        result["relations"][relation_name] = relation_result

    return result


def infer_domain_range_candidates(
    relation_stats: Dict[str, Any],
    min_pair_count: int = 3,
    min_pair_ratio: float = 0.6,
    min_support_chunks: int = 2,
    allow_unknown: bool = False,
) -> List[Dict[str, Any]]:
    """
    根据关系两端类别统计结果，生成候选 domain/range 约束。

    规则：
    1. 遍历每个关系的 pair_counts
    2. 计算 support_ratio = pair_count / total_triples
    3. 如果满足条件，标记为 "strong_candidate"
    4. 否则如果 pair_count >= 1，标记为 "weak_candidate"

    Args:
        relation_stats: collect_relation_endpoint_statistics 的返回结果
        min_pair_count: 最小类别对计数
        min_pair_ratio: 最小类别对比例
        min_support_chunks: 最小支持分块数
        allow_unknown: 是否允许 "__UNKNOWN__" 类别

    Returns:
        候选约束列表
    """
    candidates: List[Dict[str, Any]] = []

    # 验证输入
    if not isinstance(relation_stats, dict):
        return candidates

    relations = relation_stats.get("relations", {})

    if not isinstance(relations, dict):
        return candidates

    # 遍历每个关系
    for relation_name, relation_data in relations.items():
        if not isinstance(relation_data, dict):
            continue

        total_triples = relation_data.get("total_triples", 0)
        support_chunks = relation_data.get("support_chunk_count", 0)
        pair_counts = relation_data.get("pair_counts", [])

        if not isinstance(pair_counts, list):
            continue

        # 遍历每个类别对
        for pair in pair_counts:
            if not isinstance(pair, dict):
                continue

            domain = pair.get("domain")
            range_ = pair.get("range")
            pair_count = pair.get("count", 0)

            # 检查是否允许 UNKNOWN
            if not allow_unknown:
                if domain == UNKNOWN_CATEGORY or range_ == UNKNOWN_CATEGORY:
                    continue

            # 跳过空类别
            if not domain or not range_:
                continue

            # 计算支持比例
            if total_triples > 0:
                support_ratio = pair_count / total_triples
            else:
                support_ratio = 0.0

            # 判断约束状态
            if (pair_count >= min_pair_count
                    and support_ratio >= min_pair_ratio
                    and support_chunks >= min_support_chunks):
                constraint_status = "strong_candidate"
                need_review = False
            elif pair_count >= 1:
                constraint_status = "weak_candidate"
                need_review = True
            else:
                continue

            # 构建候选结果
            candidate = {
                "property_name": relation_name,
                "property_type": "object_property",
                "domain_candidate": domain,
                "range_candidate": range_,
                "support_count": pair_count,
                "total_relation_triples": total_triples,
                "support_ratio": round(support_ratio, 4),
                "support_chunk_count": support_chunks,
                "support_document_count": relation_data.get("support_document_count", 0),
                "constraint_status": constraint_status,
                "need_review": need_review,
                "reason": (
                    f"关系\"{relation_name}\"中，{domain} -> {range_} "
                    f"出现 {pair_count}/{total_triples} 次，"
                    f"占比 {support_ratio * 100:.2f}%"
                ),
            }

            candidates.append(candidate)

    return candidates


# ===========================================
