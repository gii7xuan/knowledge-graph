"""
已有本体匹配 / 新候选标记模块

本模块提供确定性的本体匹配功能，用于将 LLM 抽取的候选类别、对象属性、数据属性
与已有本体元素进行匹配：
- 能匹配的标记为 existing
- 不能匹配但有证据的标记为 new_candidate
- 相似但不确定的标记为 uncertain
"""

import copy
import difflib
from typing import Any, Dict, List, Optional, Tuple

try:
    from ontologyBuild.ontology_adapter import to_legacy_ontology
except ImportError:
    from ontology_adapter import to_legacy_ontology


# ================= 公共常量 =================

#: 候选类型常量
CANDIDATE_CLASSES = "candidate_classes"
CANDIDATE_OBJECT_PROPERTIES = "candidate_object_properties"
CANDIDATE_DATATYPE_PROPERTIES = "candidate_datatype_properties"

#: 匹配方法
MATCH_EXACT = "exact"
MATCH_SYNONYM = "synonym"
MATCH_SIMILARITY = "similarity"
MATCH_NO_MATCH = "no_match"

#: 状态常量
STATUS_EXISTING = "existing"
STATUS_NEW_CANDIDATE = "new_candidate"
STATUS_UNCERTAIN = "uncertain"

#: 错误类型
ERROR_INVALID_EXTRACTION_RESULT = "invalid_extraction_result"
ERROR_INVALID_ONTOLOGY = "invalid_ontology"
ERROR_INVALID_SECTION_TYPE = "invalid_section_type"
ERROR_INVALID_CANDIDATE_ITEM = "invalid_candidate_item"
ERROR_MISSING_CANDIDATE_NAME = "missing_candidate_name"

#: 归一化时需要处理的字符映射
_NORMALIZE_TABLE = str.maketrans({
    '：': ':',
    '（': '(',
    '）': ')',
    '…': '.',
})


# ===========================================


def normalize_name(name: Optional[str]) -> str:
    """
    归一化名称，用于标准化候选名称和本体元素名称。
    
    处理内容：
    - 去掉首尾空格
    - 去掉中间多余空格
    - 中文括号转英文括号
    - 全角冒号转半角冒号
    - 中文省略号"……"转成"..."
    - 去除不可见字符
    - 英文转小写
    
    Args:
        name: 原始名称
        
    Returns:
        归一化后的名称字符串
    """
    if name is None:
        return ""
    
    # 去掉首尾空格
    result = name.strip()
    
    # 去掉中间多余空格（将连续空格合并为一个）
    result = ' '.join(result.split())
    
    # 中文括号转英文括号
    result = result.translate(_NORMALIZE_TABLE)
    
    # 中文省略号"……"转成"..."（处理多个连续的省略号）
    result = result.replace('……', '...')
    
    # 去除不可见字符（除了空格）
    result = ''.join(ch for ch in result if ch.isprintable() or ch == ' ')
    
    # 英文转小写
    result = result.lower()
    
    return result


def build_normalized_index(items: List[str]) -> Dict[str, str]:
    """
    将已有本体列表构建成归一化索引。
    
    Args:
        items: 原始本体元素列表
        
    Returns:
        {
            "归一化名称": "原始名称"
        }
    """
    index: Dict[str, str] = {}
    for item in items:
        if isinstance(item, str):
            normalized = normalize_name(item)
            # 如果有重复的归一化名称，保留第一个
            if normalized not in index:
                index[normalized] = item
    return index


def normalize_synonym_mapping(synonym_mapping: Optional[Dict[str, str]]) -> Dict[str, str]:
    """
    归一化同义词映射表。
    
    Args:
        synonym_mapping: 原始同义词映射表
        
    Returns:
        归一化后的同义词映射表
    """
    if synonym_mapping is None:
        return {}
    
    result: Dict[str, str] = {}
    for key, value in synonym_mapping.items():
        normalized_key = normalize_name(key)
        normalized_value = normalize_name(value)
        if normalized_key and normalized_value:
            result[normalized_key] = normalized_value
    
    return result


def compute_similarity(a: str, b: str) -> float:
    """
    使用 difflib.SequenceMatcher 计算两个字符串的相似度。
    
    Args:
        a: 第一个字符串
        b: 第二个字符串
        
    Returns:
        相似度，范围 0 到 1
    """
    return difflib.SequenceMatcher(None, a, b).ratio()


def find_best_similarity_match(
    name: str,
    known_items: List[str]
) -> Tuple[Optional[str], float]:
    """
    查找最佳相似匹配。
    
    Args:
        name: 候选名称
        known_items: 已有本体元素列表
        
    Returns:
        (最佳匹配项, 最佳相似度)
    """
    if not known_items:
        return None, 0.0
    
    best_match = None
    best_score = 0.0
    
    for item in known_items:
        if not isinstance(item, str):
            continue
        score = compute_similarity(name, normalize_name(item))
        if score > best_score:
            best_score = score
            best_match = item
    
    return best_match, best_score


def match_one_candidate(
    name: str,
    known_items: List[str],
    synonym_mapping: Dict[str, str],
    high_threshold: float,
    low_threshold: float
) -> Dict[str, Any]:
    """
    匹配单个候选元素。
    
    匹配顺序：
    1. 精确匹配（归一化后完全一致）
    2. 同义词匹配
    3. 相似度匹配
    
    Args:
        name: 候选名称
        known_items: 已有本体元素列表
        synonym_mapping: 同义词映射表
        high_threshold: 高相似度阈值
        low_threshold: 低相似度阈值
        
    Returns:
        匹配结果字典
    """
    normalized_name = normalize_name(name)
    
    # 1. 精确匹配
    normalized_index = build_normalized_index(known_items)
    if normalized_name in normalized_index:
        return {
            "normalized_name": normalized_name,
            "status": STATUS_EXISTING,
            "matched_existing": normalized_index[normalized_name],
            "match_method": MATCH_EXACT,
            "match_confidence": 1.0,
            "need_review": False
        }
    
    # 2. 同义词匹配
    if normalized_name in synonym_mapping:
        mapped_value = synonym_mapping[normalized_name]
        if mapped_value in normalized_index:
            return {
                "normalized_name": normalized_name,
                "status": STATUS_EXISTING,
                "matched_existing": normalized_index[mapped_value],
                "match_method": MATCH_SYNONYM,
                "match_confidence": 0.95,
                "need_review": False
            }
    
    # 3. 相似度匹配
    best_match, best_score = find_best_similarity_match(normalized_name, known_items)
    
    if best_match is None:
        return {
            "normalized_name": normalized_name,
            "status": STATUS_NEW_CANDIDATE,
            "matched_existing": None,
            "match_method": MATCH_NO_MATCH,
            "match_confidence": 0.0,
            "need_review": True
        }
    
    if best_score >= high_threshold:
        return {
            "normalized_name": normalized_name,
            "status": STATUS_EXISTING,
            "matched_existing": best_match,
            "match_method": MATCH_SIMILARITY,
            "match_confidence": best_score,
            "need_review": False
        }
    elif best_score >= low_threshold:
        return {
            "normalized_name": normalized_name,
            "status": STATUS_UNCERTAIN,
            "matched_existing": best_match,
            "match_method": MATCH_SIMILARITY,
            "match_confidence": best_score,
            "need_review": True
        }
    else:
        return {
            "normalized_name": normalized_name,
            "status": STATUS_NEW_CANDIDATE,
            "matched_existing": None,
            "match_method": MATCH_NO_MATCH,
            "match_confidence": best_score,
            "need_review": True
        }


def process_candidate_section(
    result: Dict[str, Any],
    section_name: str,
    known_items: List[str],
    matched_field_name: str,
    synonym_mapping: Dict[str, str],
    high_threshold: float,
    low_threshold: float,
    errors: List[Dict[str, Any]]
) -> Dict[str, int]:
    """
    处理某个候选 section。
    
    Args:
        result: extraction_result 的深拷贝
        section_name: section 名称
        known_items: 已有本体元素列表
        matched_field_name: 匹配结果存储的字段名
        synonym_mapping: 同义词映射表
        high_threshold: 高相似度阈值
        low_threshold: 低相似度阈值
        errors: 错误列表
        
    Returns:
        状态统计字典
    """
    status_counts = {
        STATUS_EXISTING: 0,
        STATUS_NEW_CANDIDATE: 0,
        STATUS_UNCERTAIN: 0
    }
    
    # 检查 section 是否存在
    if section_name not in result:
        errors.append({
            "error_type": ERROR_INVALID_SECTION_TYPE,
            "message": f"结果中缺少 {section_name} 字段"
        })
        return status_counts
    
    section_data = result[section_name]
    
    # 检查 section 是否是 list
    if not isinstance(section_data, list):
        errors.append({
            "error_type": ERROR_INVALID_SECTION_TYPE,
            "message": f"{section_name} 应该是数组"
        })
        return status_counts
    
    # 遍历所有候选项
    for index, item in enumerate(section_data):
        # 检查候选项是否是 dict
        if not isinstance(item, dict):
            errors.append({
                "error_type": ERROR_INVALID_CANDIDATE_ITEM,
                "message": f"{section_name}[{index}] 不是对象"
            })
            continue
        
        # 检查 name 字段
        name = item.get("name")
        if name is None or (isinstance(name, str) and not name.strip()):
            errors.append({
                "error_type": ERROR_MISSING_CANDIDATE_NAME,
                "message": f"{section_name}[{index}] 缺少有效的 name 字段"
            })
            continue
        
        # 执行匹配
        match_result = match_one_candidate(
            name=name,
            known_items=known_items,
            synonym_mapping=synonym_mapping,
            high_threshold=high_threshold,
            low_threshold=low_threshold
        )
        
        # 更新候选项
        item["normalized_name"] = match_result["normalized_name"]
        item["status"] = match_result["status"]
        item["match_method"] = match_result["match_method"]
        item["match_confidence"] = match_result["match_confidence"]
        item["need_review"] = match_result["need_review"]
        
        # 存储匹配结果
        if matched_field_name == "matched_existing_class":
            item["matched_existing_class"] = match_result["matched_existing"]
            item["matched_existing_property"] = None
        else:
            item["matched_existing_class"] = None
            item["matched_existing_property"] = match_result["matched_existing"]
        
        # 更新统计
        status_counts[match_result["status"]] += 1
    
    return status_counts


def match_ontology_candidates(
    extraction_result: Dict[str, Any],
    ontology: Dict[str, Any],
    synonym_mapping: Optional[Dict[str, str]] = None,
    high_threshold: float = 0.9,
    low_threshold: float = 0.75
) -> Dict[str, Any]:
    """
    主函数：匹配候选元素与已有本体。
    
    Args:
        extraction_result: LLM 抽取结果
        ontology: 已有本体信息
        synonym_mapping: 同义词映射表（可选）
        high_threshold: 高相似度阈值（默认 0.9）
        low_threshold: 低相似度阈值（默认 0.75）
        
    Returns:
        匹配结果字典
    """
    # 初始化返回结果
    result_dict: Dict[str, Any] = {
        "passed": True,
        "result": None,
        "summary": {
            CANDIDATE_CLASSES: {
                STATUS_EXISTING: 0,
                STATUS_NEW_CANDIDATE: 0,
                STATUS_UNCERTAIN: 0
            },
            CANDIDATE_OBJECT_PROPERTIES: {
                STATUS_EXISTING: 0,
                STATUS_NEW_CANDIDATE: 0,
                STATUS_UNCERTAIN: 0
            },
            CANDIDATE_DATATYPE_PROPERTIES: {
                STATUS_EXISTING: 0,
                STATUS_NEW_CANDIDATE: 0,
                STATUS_UNCERTAIN: 0
            }
        },
        "errors": []
    }
    
    # 1. 验证 extraction_result
    if not isinstance(extraction_result, dict):
        result_dict["passed"] = False
        result_dict["errors"].append({
            "error_type": ERROR_INVALID_EXTRACTION_RESULT,
            "message": "extraction_result 必须是 dict"
        })
        return result_dict
    
    # 2. 验证 ontology
    if not isinstance(ontology, dict):
        result_dict["passed"] = False
        result_dict["errors"].append({
            "error_type": ERROR_INVALID_ONTOLOGY,
            "message": "ontology 必须是 dict"
        })
        return result_dict
    
    # 3. 提取本体元素列表
    ontology = to_legacy_ontology(ontology)
    known_classes = ontology.get("classes", [])
    known_object_properties = ontology.get("object_properties", [])
    known_datatype_properties = ontology.get("datatype_properties", [])
    
    # 记录缺少的字段警告
    if "classes" not in ontology:
        result_dict["errors"].append({
            "error_type": "warning",
            "message": "ontology 缺少 classes 字段，当作空数组处理"
        })
    if "object_properties" not in ontology:
        result_dict["errors"].append({
            "error_type": "warning",
            "message": "ontology 缺少 object_properties 字段，当作空数组处理"
        })
    if "datatype_properties" not in ontology:
        result_dict["errors"].append({
            "error_type": "warning",
            "message": "ontology 缺少 datatype_properties 字段，当作空数组处理"
        })
    
    # 确保是列表类型
    if not isinstance(known_classes, list):
        known_classes = []
    if not isinstance(known_object_properties, list):
        known_object_properties = []
    if not isinstance(known_datatype_properties, list):
        known_datatype_properties = []
    
    # 4. 归一化同义词映射
    normalized_synonym_mapping = normalize_synonym_mapping(synonym_mapping)
    
    # 5. 深拷贝输入，避免修改原始对象
    result_copy = copy.deepcopy(extraction_result)
    
    # 6. 处理各个 section
    # 处理 candidate_classes
    class_status = process_candidate_section(
        result=result_copy,
        section_name=CANDIDATE_CLASSES,
        known_items=known_classes,
        matched_field_name="matched_existing_class",
        synonym_mapping=normalized_synonym_mapping,
        high_threshold=high_threshold,
        low_threshold=low_threshold,
        errors=result_dict["errors"]
    )
    result_dict["summary"][CANDIDATE_CLASSES] = class_status
    
    # 处理 candidate_object_properties
    object_prop_status = process_candidate_section(
        result=result_copy,
        section_name=CANDIDATE_OBJECT_PROPERTIES,
        known_items=known_object_properties,
        matched_field_name="matched_existing_property",
        synonym_mapping=normalized_synonym_mapping,
        high_threshold=high_threshold,
        low_threshold=low_threshold,
        errors=result_dict["errors"]
    )
    result_dict["summary"][CANDIDATE_OBJECT_PROPERTIES] = object_prop_status
    
    # 处理 candidate_datatype_properties
    datatype_prop_status = process_candidate_section(
        result=result_copy,
        section_name=CANDIDATE_DATATYPE_PROPERTIES,
        known_items=known_datatype_properties,
        matched_field_name="matched_existing_property",
        synonym_mapping=normalized_synonym_mapping,
        high_threshold=high_threshold,
        low_threshold=low_threshold,
        errors=result_dict["errors"]
    )
    result_dict["summary"][CANDIDATE_DATATYPE_PROPERTIES] = datatype_prop_status
    
    # 7. 设置结果
    result_dict["result"] = result_copy
    
    # 8. 如果有错误，设置 passed 为 False
    if result_dict["errors"]:
        result_dict["passed"] = False
    
    return result_dict
