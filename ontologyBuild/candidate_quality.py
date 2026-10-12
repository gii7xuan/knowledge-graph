"""Conservative checks for ontology candidates and review triage."""

import re


FRAGMENT_WORDS = (
    "包括", "包含", "论述", "介绍", "可以", "能够", "并给出", "所示",
    "其中", "因此", "由于", "根据", "利用", "采用", "结合", "本书", "本章",
    "等内容", "为对象", "是由", "进行了", "研究了", "分析了",
)
DANGLING_ENDINGS = ("和", "与", "及", "的", "为", "由", "在", "等", "将", "把", "或")
GENERIC_NAMES = {
    "方法", "模型", "理论", "系统", "过程", "方程", "算法", "装置", "机构",
    "部件", "现象", "效应", "参数", "质量", "速度", "时间", "距离", "坐标", "系数",
}
CLAUSE_MARKERS = (
    "本书", "本章", "本节", "可以", "能够", "需要", "为了", "根据", "通过", "采用",
    "利用", "使用", "介绍", "论述", "研究了", "分析了", "给出", "提出", "表明",
    "该方程", "这种", "认为", "因而", "不论", "即假设", "带动", "终了时",
)
TERM_ENDINGS = (
    "理论", "方程", "模型", "方法", "算法", "系统", "装置", "机构", "部件",
    "现象", "过程", "效应", "阻力", "弹道", "火炮", "炮身", "炮架", "炮闩",
    "弹丸", "装药", "速度", "初速", "压力", "膛压", "温度", "质量", "长度",
    "角度", "射程", "密度", "加速度", "时间", "距离", "坐标", "系数", "口径",
    "弹量", "药量", "学",
)
DERIVED_CONSTRAINT_REASONS = (
    "由数据属性候选同步生成",
    "由单句关系归纳出的约束候选",
)


def is_fragment_name(name, from_body=False):
    raw = str(name or "").strip()
    text = re.sub(r"\s+", "", raw)
    if not text or any(word in text for word in FRAGMENT_WORDS):
        return True
    if re.search(r"^#{1,6}|\$|\\(?:frac|dot|mathrm|pmb|Delta|Omega)|[{}]", text):
        return True
    if re.match(r"^(?:第?[\d一二三四五六七八九十百]+(?:[.．、-][\d一二三四五六七八九十百]+)*)", text):
        return True
    if text in GENERIC_NAMES or any(marker in text for marker in CLAUSE_MARKERS):
        return True
    if text.startswith(("该", "此", "这种", "若干", "即", "故", "因而", "不论")):
        return True
    if text.count("(") != text.count(")") or text.count("（") != text.count("）"):
        return True
    if text.endswith(DANGLING_ENDINGS):
        return True
    if re.search(r"[^\d]\d{2,4}$", text):
        return True
    if from_body and len(text) > 16:
        return True
    return False


def candidate_name(item):
    return item.get("name") or item.get("property_name") or item.get("text_mention") or ""


def required_fields(item):
    """Fields needed before a new ontology element can be approved."""
    candidate_type = item.get("candidate_type")
    fields = []
    if candidate_type == "class":
        fields = [("name", "名称")]
        if item.get("status") != "existing":
            fields.append(("definition_candidate", "定义"))
    elif candidate_type == "object_property":
        fields = [("name", "关系名"), ("domain_candidate", "domain"), ("range_candidate", "range")]
        if item.get("status") != "existing":
            fields.append(("definition_candidate", "定义"))
    elif candidate_type == "datatype_property":
        fields = [("name", "属性名"), ("domain_candidate", "domain"), ("range_datatype", "数据类型")]
        if item.get("status") != "existing":
            fields.append(("definition_candidate", "定义"))
    elif candidate_type == "constraint":
        fields = [("property_name", "属性名"), ("property_type", "属性类型"), ("domain_candidate", "domain")]
        range_field = "range_datatype" if item.get("property_type") == "datatype_property" else "range_candidate"
        fields.append((range_field, "数据类型" if range_field == "range_datatype" else "range"))
    elif candidate_type == "mapping":
        fields = [("text_mention", "原文提及"), ("mapped_to", "映射目标"), ("mapped_type", "映射类型")]
    else:
        fields = [("name", "名称")]
    return [label for key, label in fields if not str(item.get(key) or "").strip()]


def quality_issues(item):
    issues = []
    candidate_type = item.get("candidate_type")
    from_body = "正文" in str(item.get("reason") or "")
    name = candidate_name(item)
    if candidate_type in {"class", "datatype_property"} and is_fragment_name(name, from_body):
        issues.append({"code": "sentence_fragment", "message": "名称像句子残片或被截断", "cleanup": True})
    if candidate_type == "class" and item.get("parent_candidate"):
        module = item.get("source_module") or {}
        path = str(module.get("breadcrumb_path") or "")
        parent = str(item["parent_candidate"])
        if parent == "正文" or parent in [part.strip() for part in path.split(">")]:
            issues.append({"code": "directory_parent", "message": "上位类来自目录位置，须按语义核实", "cleanup": False})
    if candidate_type in {"object_property", "constraint"}:
        for field in ("domain_candidate", "range_candidate"):
            value = item.get(field)
            if value and is_fragment_name(value):
                issues.append({"code": "fragment_endpoint", "message": f"{field} 像句子残片", "cleanup": True})
    if candidate_type == "datatype_property":
        domain = item.get("domain_candidate")
        if domain and is_fragment_name(domain, from_body=True):
            issues.append({"code": "fragment_endpoint", "message": "domain_candidate 像句子残片", "cleanup": True})
    if candidate_type == "constraint" and str(item.get("reason") or "").startswith(DERIVED_CONSTRAINT_REASONS):
        issues.append({"code": "derived_constraint", "message": "自动派生约束，需先确认属性及 domain/range", "cleanup": True})
    if candidate_type in {"object_property", "datatype_property", "constraint"}:
        missing = required_fields(item)
        if any(field in missing for field in ("domain", "range", "数据类型")):
            issues.append({"code": "missing_endpoint", "message": "domain/range 尚未补全", "cleanup": True})
    return issues


def describe_quality(item):
    issues = quality_issues(item)
    return {
        "needs_cleanup": any(issue["cleanup"] for issue in issues),
        "issues": issues,
        "missing_fields": required_fields(item),
    }


def approval_errors(item):
    errors = [f"缺少{label}" for label in required_fields(item)]
    validation_status = item.get("validation_status")
    if validation_status and validation_status != "passed":
        labels = {
            "legacy_unverified": "候选未经过新流水线校验",
            "dependency_pending": "候选依赖尚未通过校验",
            "failed": "候选校验失败",
            "not_run": "候选校验尚未执行",
        }
        errors.append(labels.get(validation_status, f"候选校验状态为 {validation_status}"))
    for field, label in (
        ("format_passed", "格式校验"),
        ("evidence_passed", "证据校验"),
        ("business_passed", "业务校验"),
    ):
        if field in item and item.get(field) is not True:
            errors.append(f"{label}未通过")
    from_body = "正文" in str(item.get("reason") or "")
    if item.get("candidate_type") in {"class", "datatype_property"} and is_fragment_name(candidate_name(item), from_body):
        errors.append("名称像句子残片，请修改后再通过")
    if item.get("candidate_type") in {"object_property", "constraint"}:
        for field in ("domain_candidate", "range_candidate"):
            if item.get(field) and is_fragment_name(item[field]):
                errors.append(f"{field} 像句子残片")
    if item.get("candidate_type") == "datatype_property":
        domain = item.get("domain_candidate")
        if domain and is_fragment_name(domain, from_body=True):
            errors.append("domain_candidate 像句子残片")
    return errors
