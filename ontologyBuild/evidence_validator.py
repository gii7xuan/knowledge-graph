"""
evidence 原文校验模块。

本模块提供确定性的证据校验功能，用于验证 LLM 抽取的候选元素中
evidence.text 是否真的出现在对应的 chunk_text 中。
"""

import copy
from typing import Any, Dict, List, Optional, Tuple, Union


# ================= 公共常量 =================

#: 归一化时需要忽略的字符
NORMALIZE_CHARS = {" ", "\t", "\r", "\n", "\u3000"}

#: 校验的顶层字段名
RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)

#: 错误类型枚举
ERROR_INVALID_SECTION_TYPE = "invalid_section_type"
ERROR_MISSING_EVIDENCE = "missing_evidence"
ERROR_EMPTY_EVIDENCE_TEXT = "empty_evidence_text"
ERROR_CHUNK_ID_MISMATCH = "chunk_id_mismatch"
ERROR_TOO_SHORT_EVIDENCE = "too_short_evidence"
ERROR_TOO_LONG_EVIDENCE = "too_long_evidence"
ERROR_EVIDENCE_NOT_FOUND = "evidence_not_found"

# ===========================================


def normalize_with_mapping(text: str) -> Tuple[str, List[int]]:
    """
    去除文本中的空格、换行、制表符、回车、中文全角空格，并返回归一化字符串和位置映射。

    Args:
        text: 原始文本

    Returns:
        tuple:
            - normalized_text: 归一化后的字符串
            - index_mapping: 列表，index_mapping[i] 表示 normalized_text[i]
              对应原始 text 中的字符下标
    """
    normalized_chars: List[str] = []
    index_mapping: List[int] = []

    for original_index, char in enumerate(text):
        if char not in NORMALIZE_CHARS:
            normalized_chars.append(char)
            index_mapping.append(original_index)

    return "".join(normalized_chars), index_mapping


def find_evidence_span(
    evidence_text: str,
    chunk_text: str,
) -> Tuple[bool, Optional[str], Optional[int], Optional[int]]:
    """
    查找 evidence_text 是否在 chunk_text 中。

    匹配顺序：
    1. 先执行严格匹配（exact_match）
    2. 如果严格匹配失败，执行归一化匹配（normalized_match）

    匹配规则：
    - 严格匹配：直接使用 str.find()
    - 归一化匹配：忽略空格、换行、制表符、回车、中文全角空格

    Args:
        evidence_text: 待查找的证据文本
        chunk_text: 原始文档片段

    Returns:
        tuple:
            - matched: 是否匹配成功
            - match_type: 匹配类型，"exact_match" 或 "normalized_match"
            - start: 匹配起始位置（包含），如果未匹配则为 None
            - end: 匹配结束位置（不包含），如果未匹配则为 None
    """
    evidence_text = evidence_text.strip()
    if not evidence_text:
        return False, None, None, None

    # 1. 严格匹配
    start = chunk_text.find(evidence_text)
    if start != -1:
        end = start + len(evidence_text)
        return True, "exact_match", start, end

    # 2. 归一化匹配
    # 归一化 chunk_text 并构建位置映射
    normalized_chunk, chunk_mapping = normalize_with_mapping(chunk_text)
    normalized_evidence, _ = normalize_with_mapping(evidence_text)

    # 在归一化的 chunk 中查找归一化的 evidence
    start_normalized = normalized_chunk.find(normalized_evidence)
    if start_normalized != -1:
        # 通过映射数组还原到原始 chunk_text 的位置
        start = chunk_mapping[start_normalized]
        # end 位置需要找到对应 normalized_evidence 最后一个字符在原始文本中的位置
        end_normalized = start_normalized + len(normalized_evidence) - 1
        end = chunk_mapping[end_normalized] + 1
        return True, "normalized_match", start, end

    return False, None, None, None


def validate_evidence_for_item(
    item: Dict[str, Any],
    section_name: str,
    item_index: int,
    chunk_id: str,
    chunk_text: str,
    min_evidence_len: int = 2,
    max_evidence_len: int = 300,
) -> List[Dict[str, Any]]:
    """
    校验单个候选项的 evidence。

    Args:
        item: 候选项字典
        section_name: 所在的顶层字段名
        item_index: 候选项在该字段 list 中的下标
        chunk_id: 当前文档片段 ID
        chunk_text: 当前文档片段原文
        min_evidence_len: 最小证据长度（默认 2）
        max_evidence_len: 最大证据长度（默认 300）

    Returns:
        list: 错误列表。如果为空列表，表示校验通过。
    """
    errors: List[Dict[str, Any]] = []

    # 1. 检查 evidence 字段是否存在
    if "evidence" not in item:
        errors.append({
            "section": section_name,
            "index": item_index,
            "evidence_index": 0,
            "error_type": ERROR_MISSING_EVIDENCE,
            "message": f"{section_name}[{item_index}] 缺少 evidence 字段",
        })
        return errors

    evidence_list = item["evidence"]

    # 2. 检查 evidence 是否是 list
    if not isinstance(evidence_list, list):
        errors.append({
            "section": section_name,
            "index": item_index,
            "evidence_index": 0,
            "error_type": ERROR_MISSING_EVIDENCE,
            "message": f"{section_name}[{item_index}].evidence 不是数组",
        })
        return errors

    # 3. 检查 evidence 是否为空
    if len(evidence_list) == 0:
        errors.append({
            "section": section_name,
            "index": item_index,
            "evidence_index": 0,
            "error_type": ERROR_MISSING_EVIDENCE,
            "message": f"{section_name}[{item_index}].evidence 为空数组",
        })
        return errors

    # 4. 逐条校验 evidence
    for ev_index, evidence in enumerate(evidence_list):
        # 4.1 检查 evidence 是否是 dict
        if not isinstance(evidence, dict):
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_MISSING_EVIDENCE,
                "message": f"{section_name}[{item_index}].evidence[{ev_index}] 不是对象",
            })
            continue

        # 4.2 检查 chunk_id 是否一致
        evidence_chunk_id = evidence.get("chunk_id")
        if evidence_chunk_id != chunk_id:
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_CHUNK_ID_MISMATCH,
                "message": (
                    f"{section_name}[{item_index}].evidence[{ev_index}].chunk_id "
                    f"不匹配：期望 '{chunk_id}'，实际 '{evidence_chunk_id}'"
                ),
            })
            continue

        # 4.3 检查 text 是否存在且有效
        evidence_text = evidence.get("text")
        if evidence_text is None:
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_EMPTY_EVIDENCE_TEXT,
                "message": f"{section_name}[{item_index}].evidence[{ev_index}].text 不存在",
            })
            continue

        if not isinstance(evidence_text, str):
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_EMPTY_EVIDENCE_TEXT,
                "message": f"{section_name}[{item_index}].evidence[{ev_index}].text 不是字符串",
            })
            continue

        evidence_text_stripped = evidence_text.strip()
        if not evidence_text_stripped:
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_EMPTY_EVIDENCE_TEXT,
                "message": f"{section_name}[{item_index}].evidence[{ev_index}].text 是空字符串",
            })
            continue

        # 4.4 检查 evidence 文本长度
        text_len = len(evidence_text_stripped)
        if text_len < min_evidence_len:
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_TOO_SHORT_EVIDENCE,
                "message": (
                    f"{section_name}[{item_index}].evidence[{ev_index}].text 长度太短："
                    f"{text_len} < {min_evidence_len}"
                ),
            })
            continue

        if text_len > max_evidence_len:
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_TOO_LONG_EVIDENCE,
                "message": (
                    f"{section_name}[{item_index}].evidence[{ev_index}].text 长度太长："
                    f"{text_len} > {max_evidence_len}"
                ),
            })
            continue

        # 4.5 检查 evidence.text 是否出现在 chunk_text 中
        matched, match_type, start, end = find_evidence_span(
            evidence_text_stripped, chunk_text
        )

        if not matched:
            errors.append({
                "section": section_name,
                "index": item_index,
                "evidence_index": ev_index,
                "error_type": ERROR_EVIDENCE_NOT_FOUND,
                "message": (
                    f"{section_name}[{item_index}].evidence[{ev_index}].text "
                    "未在原始 chunk_text 中找到，可能是 LLM 改写或幻觉"
                ),
            })
            continue

        # 匹配成功，添加匹配信息（注意：这里修改的是原始 item，后续需要深拷贝）
        evidence["match_type"] = match_type
        evidence["start"] = start
        evidence["end"] = end

    return errors


def validate_all_evidence(
    extraction_result: Dict[str, Any],
    chunk_id: str,
    chunk_text: str,
    min_evidence_len: int = 2,
    max_evidence_len: int = 300,
) -> Dict[str, Any]:
    """
    遍历整个抽取结果，校验所有 evidence。

    Args:
        extraction_result: LLM 输出并经过 json.loads() 解析后的 dict
        chunk_id: 当前正在校验的文档片段 ID
        chunk_text: 当前文档片段原文
        min_evidence_len: 最小证据长度（默认 2）
        max_evidence_len: 最大证据长度（默认 300）

    Returns:
        dict:
            - passed: bool，如果没有任何错误，则为 True
            - errors: list，错误列表
            - result_with_offsets: dict，原始 extraction_result 的深拷贝版本，
              匹配成功的 evidence 会添加 match_type、start、end 字段
    """
    # 深拷贝原始结果，避免修改输入
    result_with_offsets = copy.deepcopy(extraction_result)

    all_errors: List[Dict[str, Any]] = []

    # 遍历所有 6 个字段
    for section_name in RESULT_FIELDS:
        # 检查 section 是否存在
        if section_name not in result_with_offsets:
            all_errors.append({
                "section": section_name,
                "error_type": ERROR_INVALID_SECTION_TYPE,
                "message": f"结果中缺少 {section_name} 字段",
            })
            continue

        section_data = result_with_offsets[section_name]

        # 检查 section 是否是 list
        if not isinstance(section_data, list):
            all_errors.append({
                "section": section_name,
                "error_type": ERROR_INVALID_SECTION_TYPE,
                "message": f"{section_name} 应该是数组",
            })
            continue

        # 遍历该 section 中的所有候选项
        for item_index, item in enumerate(section_data):
            if not isinstance(item, dict):
                all_errors.append({
                    "section": section_name,
                    "index": item_index,
                    "evidence_index": 0,
                    "error_type": ERROR_MISSING_EVIDENCE,
                    "message": f"{section_name}[{item_index}] 不是对象",
                })
                continue

            # 校验单个候选项的 evidence
            errors = validate_evidence_for_item(
                item=item,
                section_name=section_name,
                item_index=item_index,
                chunk_id=chunk_id,
                chunk_text=chunk_text,
                min_evidence_len=min_evidence_len,
                max_evidence_len=max_evidence_len,
            )
            all_errors.extend(errors)

    return {
        "passed": len(all_errors) == 0,
        "errors": all_errors,
        "result_with_offsets": result_with_offsets,
    }