"""
本体候选审核模块

自动接受 / 人工审核系统
"""

import json
import copy
from datetime import datetime
from typing import Any, Optional


def get_default_policy() -> dict:
    """返回默认审核策略"""
    return {
        "auto_accept_confidence": 0.85,
        "manual_review_confidence": 0.6,
        "auto_reject_confidence": 0.4,
        
        "auto_accept_constraint_strengths": ["strong"],
        "manual_review_constraint_strengths": ["medium", "weak"],
        
        "reject_on_format_failed": True,
        "reject_on_evidence_failed": True,
        
        "review_on_shacl_failed": True,
        "review_on_owl_failed": True,
        "review_on_conflict": True,
        
        "reject_on_low_confidence": True,
        "review_generalized_constraints": True,
        "review_uncertain_candidates": True,
        
        "allow_auto_accept_existing": True,
        "allow_auto_accept_new_candidate": True,
        "allow_auto_accept_compatible_existing": True,
        
        "strict_mode": False
    }


def merge_policy(user_policy: Optional[dict]) -> dict:
    """将用户策略与默认策略合并"""
    default_policy = get_default_policy()
    if user_policy is None:
        return default_policy
    
    result = copy.deepcopy(default_policy)
    for key, value in user_policy.items():
        if key in result:
            result[key] = value
    return result


def safe_float(value: Any, default: float = 0.0) -> float:
    """安全转换置信度"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    return default


def is_false(value: Any) -> bool:
    """只有 value is False 或字符串 'false' 时返回 True"""
    return value is False or value == "false"


def ensure_candidate_id(candidate: dict, index: int) -> dict:
    """补充 candidate_id"""
    result = copy.deepcopy(candidate)
    if "candidate_id" not in result or not result["candidate_id"]:
        result["candidate_id"] = f"cand_{index:06d}"
    return result


def get_candidate_display_name(candidate: dict) -> str:
    """获取候选显示名称"""
    # 优先级 1: name 字段
    if "name" in candidate and candidate["name"]:
        return str(candidate["name"])
    
    # 优先级 2: property_name 字段
    if "property_name" in candidate and candidate["property_name"]:
        prop_name = str(candidate["property_name"])
        
        # 优先级 3: property_name: domain -> range
        if "domain_candidate" in candidate and candidate["domain_candidate"]:
            domain = str(candidate["domain_candidate"])
            if "range_candidate" in candidate and candidate["range_candidate"]:
                range_val = str(candidate["range_candidate"])
                return f"{prop_name}: {domain} -> {range_val}"
        
        return prop_name
    
    # 优先级 4: subject --predicate--> object
    if "subject" in candidate and candidate["subject"]:
        subject = str(candidate["subject"])
        if "predicate" in candidate and candidate["predicate"]:
            predicate = str(candidate["predicate"])
            if "object" in candidate and candidate["object"]:
                obj = str(candidate["object"])
                return f"{subject} --{predicate}--> {obj}"
    
    # 优先级 5: candidate_id
    if "candidate_id" in candidate and candidate["candidate_id"]:
        return str(candidate["candidate_id"])
    
    return "unknown"


def utc_now() -> str:
    """返回 UTC ISO 时间"""
    return datetime.utcnow().isoformat() + "Z"


def recommend_decision(candidate: dict, policy: dict) -> dict:
    """
    生成系统推荐
    
    返回:
        {
            "recommendation": "accept | review | reject",
            "reasons": []
        }
    """
    result = {"recommendation": "reject", "reasons": []}
    
    confidence = safe_float(candidate.get("confidence"), 0.0)
    candidate_type = candidate.get("candidate_type", "")
    format_passed = candidate.get("format_passed")
    evidence_passed = candidate.get("evidence_passed")
    shacl_passed = candidate.get("shacl_passed")
    owl_passed = candidate.get("owl_passed")
    conflict_with_existing = candidate.get("conflict_with_existing")
    need_review = candidate.get("need_review")
    is_generalized = candidate.get("is_generalized", False)
    status = candidate.get("status")
    ontology_match_status = candidate.get("ontology_match_status")
    constraint_strength = candidate.get("constraint_strength")
    
    # 规则 1: 格式校验失败
    if is_false(format_passed) and policy.get("reject_on_format_failed", True):
        result["recommendation"] = "reject"
        result["reasons"].append("格式校验失败")
        return result
    
    # 规则 2: evidence 校验失败
    if is_false(evidence_passed) and policy.get("reject_on_evidence_failed", True):
        result["recommendation"] = "reject"
        result["reasons"].append("evidence 原文校验失败")
        return result
    
    # 规则 3: 低置信度
    if (confidence < policy.get("auto_reject_confidence", 0.4) and 
        policy.get("reject_on_low_confidence", True)):
        result["recommendation"] = "reject"
        result["reasons"].append("置信度低于自动拒绝阈值")
        return result
    
    # 规则 4: SHACL 检查失败
    if is_false(shacl_passed) and policy.get("review_on_shacl_failed", True):
        if policy.get("strict_mode", False):
            result["recommendation"] = "reject"
            result["reasons"].append("SHACL 一致性检查未通过")
            return result
        else:
            result["recommendation"] = "review"
            result["reasons"].append("SHACL 一致性检查未通过")
            return result
    
    # 规则 5: OWL 检查失败
    if is_false(owl_passed) and policy.get("review_on_owl_failed", True):
        if policy.get("strict_mode", False):
            result["recommendation"] = "reject"
            result["reasons"].append("OWL 一致性检查未通过")
            return result
        else:
            result["recommendation"] = "review"
            result["reasons"].append("OWL 一致性检查未通过")
            return result
    
    # 规则 6: 与已有本体冲突
    if conflict_with_existing is True and policy.get("review_on_conflict", True):
        if policy.get("strict_mode", False):
            result["recommendation"] = "reject"
            result["reasons"].append("与已有本体约束冲突")
            return result
        else:
            result["recommendation"] = "review"
            result["reasons"].append("与已有本体约束冲突")
            return result
    
    # 规则 7: 泛化约束
    if is_generalized is True or candidate_type == "generalized_constraint":
        if policy.get("review_generalized_constraints", True):
            result["recommendation"] = "review"
            result["reasons"].append("泛化约束需要人工审核，防止过度泛化")
            return result
    
    # 规则 8: uncertain 候选
    if status == "uncertain" or ontology_match_status == "uncertain":
        if policy.get("review_uncertain_candidates", True):
            result["recommendation"] = "review"
            result["reasons"].append("候选与已有本体匹配不确定")
            return result
    
    # 规则 9: constraint_strength 判断
    is_constraint_type = candidate_type in ["constraint", "generalized_constraint"] or constraint_strength is not None
    
    if is_constraint_type and constraint_strength is not None:
        if (constraint_strength in policy.get("auto_accept_constraint_strengths", []) and
            confidence >= policy.get("auto_accept_confidence", 0.85) and
            not is_false(evidence_passed) and
            not is_false(format_passed) and
            not is_false(shacl_passed) and
            not is_false(owl_passed) and
            not conflict_with_existing and
            need_review is not True):
            result["recommendation"] = "accept"
            result["reasons"].append("强约束候选满足自动接受条件")
            return result
        
        if constraint_strength in policy.get("manual_review_constraint_strengths", ["medium", "weak"]):
            result["recommendation"] = "review"
            result["reasons"].append("中弱约束候选需要人工审核")
            return result
    
    # 规则 10: 普通候选自动接受
    if not is_constraint_type:
        if (confidence >= policy.get("auto_accept_confidence", 0.85) and
            not is_false(format_passed) and
            not is_false(evidence_passed) and
            not is_false(shacl_passed) and
            not is_false(owl_passed) and
            not conflict_with_existing and
            need_review is not True and
            status != "uncertain"):
            result["recommendation"] = "accept"
            result["reasons"].append("候选满足自动接受条件")
            return result
    
    # 规则 11: 人工审核
    if confidence >= policy.get("manual_review_confidence", 0.6):
        result["recommendation"] = "review"
        result["reasons"].append("置信度达到人工审核阈值，但未满足自动接受条件")
        return result
    
    # 规则 12: 默认拒绝
    result["recommendation"] = "reject"
    result["reasons"].append("候选未达到人工审核阈值")
    return result


def apply_review_mode(
    candidate: dict,
    recommendation: str,
    review_mode: str,
    reviewer: str
) -> dict:
    """
    根据审核模式设置 final_decision
    
    返回:
        {
            "final_decision": "accepted | rejected | pending_review | deferred",
            "decision_source": "system | user",
            "review_mode": review_mode,
            "reviewer": reviewer,
            "review_comment": None,
            "review_time": "ISO timestamp"
        }
    """
    timestamp = utc_now()
    
    result = {
        "final_decision": None,
        "decision_source": None,
        "review_mode": review_mode,
        "reviewer": reviewer,
        "review_comment": None,
        "review_time": timestamp
    }
    
    if review_mode == "manual":
        # 所有候选都进入人工审核
        result["final_decision"] = "pending_review"
        result["decision_source"] = "system"
        return result
    
    # auto 和 hybrid 模式
    if recommendation == "accept":
        result["final_decision"] = "accepted"
        result["decision_source"] = "system"
    elif recommendation == "reject":
        result["final_decision"] = "rejected"
        result["decision_source"] = "system"
    else:  # review
        result["final_decision"] = "pending_review"
        result["decision_source"] = "system"
    
    return result


def create_review_log(
    candidate: dict,
    reasons: list,
    reviewer: str,
    user_decision: Optional[str] = None
) -> dict:
    """生成审核日志"""
    log_id = f"log_{int(datetime.utcnow().timestamp() * 1000) % 1000000:06d}"
    
    base_log = {
        "log_id": log_id,
        "candidate_id": candidate.get("candidate_id"),
        "candidate_type": candidate.get("candidate_type"),
        "name": get_candidate_display_name(candidate),
        "system_recommendation": candidate.get("system_recommendation"),
        "final_decision": candidate.get("final_decision"),
        "decision_source": candidate.get("decision_source"),
        "reviewer": reviewer,
        "review_comment": candidate.get("review_comment"),
        "reasons": reasons.copy(),
        "timestamp": candidate.get("review_time")
    }
    
    if user_decision:
        base_log["user_decision"] = user_decision
    
    return base_log


def find_candidate_by_id(result: dict, candidate_id: str) -> tuple:
    """
    在 accepted/rejected/pending/deferred 中查找候选
    
    返回:
        (list_name, index, candidate_dict)
    """
    lists_to_check = [
        ("accepted_candidates", result.get("accepted_candidates", [])),
        ("rejected_candidates", result.get("rejected_candidates", [])),
        ("pending_review_candidates", result.get("pending_review_candidates", [])),
        ("deferred_candidates", result.get("deferred_candidates", []))
    ]
    
    for list_name, candidates in lists_to_check:
        for i, candidate in enumerate(candidates):
            if candidate.get("candidate_id") == candidate_id:
                return (list_name, i, candidate)
    
    return (None, None, None)


def summarize_review_result(result: dict) -> dict:
    """生成 summary"""
    summary = {
        "total": 0,
        "accepted": 0,
        "rejected": 0,
        "pending_review": 0,
        "deferred": 0,
        "system_accept": 0,
        "system_reject": 0,
        "system_review": 0,
        "user_accept": 0,
        "user_reject": 0,
        "user_modify": 0,
        "user_defer": 0
    }
    
    all_candidates = (
        result.get("accepted_candidates", []) +
        result.get("rejected_candidates", []) +
        result.get("pending_review_candidates", []) +
        result.get("deferred_candidates", [])
    )
    
    summary["total"] = len(all_candidates)
    
    for candidate in result.get("accepted_candidates", []):
        summary["accepted"] += 1
        if candidate.get("decision_source") == "system":
            summary["system_accept"] += 1
        else:
            if candidate.get("final_decision") == "accepted_after_modify":
                summary["user_modify"] += 1
            else:
                summary["user_accept"] += 1
    
    for candidate in result.get("rejected_candidates", []):
        summary["rejected"] += 1
        if candidate.get("decision_source") == "system":
            summary["system_reject"] += 1
        else:
            summary["user_reject"] += 1
    
    for candidate in result.get("pending_review_candidates", []):
        summary["pending_review"] += 1
        if candidate.get("decision_source") == "system":
            summary["system_review"] += 1
    
    for candidate in result.get("deferred_candidates", []):
        summary["deferred"] += 1
        if candidate.get("decision_source") == "user":
            summary["user_defer"] += 1
    
    return summary


def review_candidates(
    candidates: list,
    review_mode: str = "hybrid",
    policy: Optional[dict] = None,
    reviewer: str = "system"
) -> dict:
    """
    主审核函数
    
    参数:
        candidates: 候选列表
        review_mode: 审核模式 ("auto" | "manual" | "hybrid")
        policy: 审核策略配置
        reviewer: 审核者名称
    
    返回:
        审核结果字典
    """
    # 初始化结果
    result = {
        "passed": True,
        "review_mode": review_mode,
        "accepted_candidates": [],
        "rejected_candidates": [],
        "pending_review_candidates": [],
        "deferred_candidates": [],
        "review_log": [],
        "summary": {},
        "errors": []
    }
    
    # 验证 candidates 类型
    if not isinstance(candidates, list):
        result["passed"] = False
        result["errors"].append({
            "error_type": "invalid_candidates",
            "message": "candidates 必须是 list"
        })
        return result
    
    # 验证 review_mode
    valid_modes = ["auto", "manual", "hybrid"]
    if review_mode not in valid_modes:
        result["passed"] = False
        result["errors"].append({
            "error_type": "invalid_review_mode",
            "message": f"review_mode 必须是 {valid_modes} 之一"
        })
        return result
    
    # 合并策略
    merged_policy = merge_policy(policy)
    
    # 处理每个候选
    for index, candidate in enumerate(candidates):
        # 跳过非 dict 候选
        if not isinstance(candidate, dict):
            result["errors"].append({
                "error_type": "invalid_candidate_item",
                "message": f"索引 {index} 的候选不是 dict 类型，已跳过"
            })
            continue
        
        # 确保 candidate_id
        candidate = ensure_candidate_id(candidate, index + 1)
        
        # 存储原始候选（用于后续修改参考）
        original_candidate = copy.deepcopy(candidate)
        
        # 系统推荐
        recommendation_result = recommend_decision(candidate, merged_policy)
        candidate["system_recommendation"] = recommendation_result["recommendation"]
        candidate["recommendation_reasons"] = recommendation_result["reasons"]
        
        # 应用审核模式
        mode_result = apply_review_mode(
            candidate,
            recommendation_result["recommendation"],
            review_mode,
            reviewer
        )
        candidate["final_decision"] = mode_result["final_decision"]
        candidate["decision_source"] = mode_result["decision_source"]
        candidate["review_mode"] = mode_result["review_mode"]
        candidate["reviewer"] = mode_result["reviewer"]
        candidate["review_comment"] = mode_result["review_comment"]
        candidate["review_time"] = mode_result["review_time"]
        
        # 创建审核日志
        log = create_review_log(candidate, recommendation_result["reasons"], reviewer)
        result["review_log"].append(log)
        
        # 根据最终决策分类
        final_decision = candidate["final_decision"]
        
        if final_decision == "accepted":
            result["accepted_candidates"].append(candidate)
        elif final_decision == "rejected":
            result["rejected_candidates"].append(candidate)
        elif final_decision == "pending_review":
            result["pending_review_candidates"].append(candidate)
        elif final_decision == "deferred":
            result["deferred_candidates"].append(candidate)
        
        # 如果有 original_candidate 字段，添加到结果中
        if original_candidate:
            candidate["original_candidate"] = original_candidate
    
    # 生成 summary
    result["summary"] = summarize_review_result(result)
    
    # 如果有错误，passed 设为 False
    if result["errors"]:
        result["passed"] = False
    
    return result


def apply_user_decision(
    reviewed_result: dict,
    candidate_id: str,
    user_decision: str,
    reviewer: str,
    review_comment: Optional[str] = None,
    modifications: Optional[dict] = None
) -> dict:
    """
    人工审核操作函数
    
    参数:
        reviewed_result: review_candidates 的返回结果
        candidate_id: 候选 ID
        user_decision: "accept" | "reject" | "modify" | "defer"
        reviewer: 审核者名称
        review_comment: 审核评论
        modifications: 修改内容（modify 时必需）
    
    返回:
        更新后的审核结果
    """
    # 深拷贝，不修改原对象
    result = copy.deepcopy(reviewed_result)
    
    # 验证 user_decision
    valid_decisions = ["accept", "reject", "modify", "defer"]
    if user_decision not in valid_decisions:
        result["errors"].append({
            "error_type": "invalid_user_decision",
            "message": f"user_decision 必须是 {valid_decisions} 之一"
        })
        result["passed"] = False
        return result
    
    # 查找候选
    list_name, index, candidate = find_candidate_by_id(result, candidate_id)
    
    if candidate is None:
        result["errors"].append({
            "error_type": "candidate_not_found",
            "message": f"未找到 candidate_id = {candidate_id} 的候选"
        })
        result["passed"] = False
        return result
    
    # 移除 original_candidate（防止重复）
    candidate.pop("original_candidate", None)
    
    # 记录原状态
    original_decision = candidate["final_decision"]
    original_list = list_name
    
    # 应用用户决策
    timestamp = utc_now()
    
    if user_decision == "accept":
        # 从原列表移除
        if list_name and index is not None:
            if list_name == "accepted_candidates":
                result["accepted_candidates"].pop(index)
            elif list_name == "rejected_candidates":
                result["rejected_candidates"].pop(index)
            elif list_name == "pending_review_candidates":
                result["pending_review_candidates"].pop(index)
            elif list_name == "deferred_candidates":
                result["deferred_candidates"].pop(index)
        
        # 更新候选
        candidate["final_decision"] = "accepted"
        candidate["decision_source"] = "user"
        candidate["review_comment"] = review_comment
        candidate["review_time"] = timestamp
        candidate["reviewer"] = reviewer
        
        # 添加到 accepted
        result["accepted_candidates"].append(candidate)
        
        # 创建日志
        log = create_review_log(
            candidate,
            ["用户人工接受"],
            reviewer,
            user_decision="accept"
        )
        result["review_log"].append(log)
        
    elif user_decision == "reject":
        # 从原列表移除
        if list_name and index is not None:
            if list_name == "accepted_candidates":
                result["accepted_candidates"].pop(index)
            elif list_name == "rejected_candidates":
                result["rejected_candidates"].pop(index)
            elif list_name == "pending_review_candidates":
                result["pending_review_candidates"].pop(index)
            elif list_name == "deferred_candidates":
                result["deferred_candidates"].pop(index)
        
        # 更新候选
        candidate["final_decision"] = "rejected"
        candidate["decision_source"] = "user"
        candidate["review_comment"] = review_comment
        candidate["review_time"] = timestamp
        candidate["reviewer"] = reviewer
        
        # 添加到 rejected
        result["rejected_candidates"].append(candidate)
        
        # 创建日志
        log = create_review_log(
            candidate,
            ["用户人工拒绝"],
            reviewer,
            user_decision="reject"
        )
        result["review_log"].append(log)
        
    elif user_decision == "modify":
        # 验证 modifications
        if modifications is None or not isinstance(modifications, dict) or len(modifications) == 0:
            result["errors"].append({
                "error_type": "missing_modifications",
                "message": "modify 决策需要提供 modifications 参数"
            })
            result["passed"] = False
            return result
        
        # 从原列表移除
        if list_name and index is not None:
            if list_name == "accepted_candidates":
                result["accepted_candidates"].pop(index)
            elif list_name == "rejected_candidates":
                result["rejected_candidates"].pop(index)
            elif list_name == "pending_review_candidates":
                result["pending_review_candidates"].pop(index)
            elif list_name == "deferred_candidates":
                result["deferred_candidates"].pop(index)
        
        # 保存原始候选
        if "original_candidate" not in candidate:
            candidate["original_candidate"] = copy.deepcopy({
                k: v for k, v in candidate.items() 
                if k not in ["final_decision", "decision_source", "review_comment", "review_time", "reviewer"]
            })
        
        # 合并修改
        for key, value in modifications.items():
            candidate[key] = value
        
        # 更新候选
        candidate["final_decision"] = "accepted_after_modify"
        candidate["decision_source"] = "user"
        candidate["review_comment"] = review_comment
        candidate["review_time"] = timestamp
        candidate["reviewer"] = reviewer
        
        # 添加到 accepted
        result["accepted_candidates"].append(candidate)
        
        # 创建日志
        log = create_review_log(
            candidate,
            ["用户人工修改后接受"],
            reviewer,
            user_decision="modify"
        )
        result["review_log"].append(log)
        
    elif user_decision == "defer":
        # 从原列表移除
        if list_name and index is not None:
            if list_name == "accepted_candidates":
                result["accepted_candidates"].pop(index)
            elif list_name == "rejected_candidates":
                result["rejected_candidates"].pop(index)
            elif list_name == "pending_review_candidates":
                result["pending_review_candidates"].pop(index)
            elif list_name == "deferred_candidates":
                result["deferred_candidates"].pop(index)
        
        # 更新候选
        candidate["final_decision"] = "deferred"
        candidate["decision_source"] = "user"
        candidate["review_comment"] = review_comment
        candidate["review_time"] = timestamp
        candidate["reviewer"] = reviewer
        
        # 添加到 deferred
        result["deferred_candidates"].append(candidate)
        
        # 创建日志
        log = create_review_log(
            candidate,
            ["用户暂缓处理"],
            reviewer,
            user_decision="defer"
        )
        result["review_log"].append(log)
    
    # 更新 summary
    result["summary"] = summarize_review_result(result)
    
    return result


