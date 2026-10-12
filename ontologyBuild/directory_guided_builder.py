import hashlib
import json
import os
import re
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from document.hierarchy_index import (  # noqa: E402
    normalize_document_id,
    split_markdown_text as split_markdown_with_hierarchy,
)
from ontologyBuild.candidate_quality import is_fragment_name  # noqa: E402


MARKDOWN_FILE_PATH = os.environ.get(
    "ONTOLOGY_MARKDOWN_FILE",
    str(PROJECT_ROOT / "mdfiles" / "火炮设计理论 (张相炎、郑建国、袁人枢).md"),
)
OUTPUT_JSON_PATH = PROJECT_ROOT / "ontologyBuild" / "ontology_candidates.json"
MODULES_JSON_PATH = PROJECT_ROOT / "ontologyBuild" / "ontology_modules.json"

RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)

MODULE_RULES = (
    ("外弹道子本体", ("外弹道", "空气阻力", "质点弹道", "弹丸飞行", "射表", "弹道方程")),
    ("内弹道子本体", ("内弹道", "膛压", "装药", "发射药", "燃烧", "膛内", "初速")),
    ("火炮结构子本体", ("结构", "构造", "炮身", "炮闩", "炮架", "反后坐", "复进", "自动机", "装置", "机构")),
    ("火炮设计子本体", ("设计", "理论", "优化", "计算", "校核", "仿真", "模型", "方法")),
)

GENERIC_TITLES = {
    "绪论",
    "概述",
    "总论",
    "基本概念",
    "基本知识",
    "本章小结",
    "习题",
    "参考文献",
}

CLASS_SUFFIXES = (
    "学",
    "理论",
    "方程",
    "模型",
    "方法",
    "算法",
    "系统",
    "装置",
    "机构",
    "部件",
    "现象",
    "过程",
    "效应",
    "阻力",
    "弹道",
    "火炮",
    "炮身",
    "炮架",
    "炮闩",
    "弹丸",
    "装药",
    "武器",
    "枪械",
    "传感器",
    "结构",
    "特性",
    "参数",
    "变量",
    "自由度",
    "载荷",
    "振动",
    "变形",
    "精度",
    "可靠性",
    "稳定性",
    "密集度",
    "散布",
    "坐标",
    "黏性",
)

QUANTITY_SUFFIXES = (
    "速度",
    "初速",
    "压力",
    "膛压",
    "温度",
    "质量",
    "长度",
    "角度",
    "射程",
    "密度",
    "阻力",
    "加速度",
    "时间",
    "距离",
    "坐标",
    "系数",
    "口径",
    "弹量",
    "药量",
)

RELATION_PATTERNS = (
    ("影响", re.compile(r"影响")),
    ("决定", re.compile(r"决定")),
    ("导致", re.compile(r"导致")),
    ("产生", re.compile(r"产生")),
    ("作用于", re.compile(r"作用于")),
    ("用于", re.compile(r"用于")),
)

GENERIC_TERMS = {
    "方法", "模型", "理论", "系统", "过程", "方程", "算法", "装置", "机构",
    "部件", "现象", "效应", "参数", "质量", "速度", "时间", "距离", "坐标", "系数",
}

TERM_BAD_PREFIXES = (
    "本书", "本章", "本节", "为了", "为了解决", "根据", "通过", "采用", "利用", "使用",
    "介绍", "论述", "研究", "分析", "给出", "提出", "实现", "形成", "有必要", "可以",
    "能够", "需要", "应当", "并按照", "分别", "式中", "其中", "因此", "所以", "但是",
    "都要", "使", "非常", "人们", "已知", "对于", "已经", "直接", "有助于", "能动地",
    "不受", "受到", "则称", "如果", "可得", "这个", "则这个", "求解", "其", "式(",
    "式（", "得到", "用", "从", "将", "把", "为多个", "依照", "这里", "某", "另一类",
    "该", "此", "这种", "若干", "即", "故", "因而", "不论", "是最快", "不设置",
)

TERM_CLAUSE_MARKERS = (
    "提供了", "称为", "视为", "存在着", "要经过", "控制", "获得", "完成", "可用", "只需要",
    "如图", "所示", "为例", "进行", "成为", "归结为", "简化为", "选择", "设定", "按照",
    "认为",
)

ENDPOINT_FORBIDDEN = (
    "本书", "本章", "本节", "可以", "能够", "需要", "为了", "根据", "通过", "采用",
    "利用", "使用", "介绍", "论述", "讨论", "研究", "分析", "给出", "提出", "找出",
    "表示", "借助", "如果", "虽然", "因为", "是否", "应从", "使得", "从而", "进而", "考虑",
    "测量", "影响", "选择", "建立", "完成", "由", "称为", "归结为", "简化为", "作用在",
    "带动", "终了时",
)

ENDPOINT_SUFFIXES = CLASS_SUFFIXES + QUANTITY_SUFFIXES + (
    "特性", "精度", "响应", "载荷", "振动", "变形", "因素", "气体", "力矩", "动作",
    "位置", "结构", "参数", "变量", "刚度", "位移", "频率", "可靠性", "稳定性", "密集度",
    "散布", "能量", "功", "力", "角", "点", "源", "值", "规律", "性能", "模态", "自由度",
    "身管", "枪管", "机枪", "武器", "构件", "零件", "元件", "材料", "弹簧", "车体", "枪架",
    "机匣", "摇架", "立轴", "炮", "枪", "器", "机", "片",
)

NON_OBJECT_GROUP_SUFFIXES = (
    "质量", "参数", "特性", "载荷", "变量", "速度", "加速度", "压力", "温度", "系数",
)


def read_markdown(markdown_path):
    path = Path(markdown_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到 Markdown 文件: {path}")
    content = path.read_text(encoding="utf-8")
    content = content.replace("###HEADER#### ", "# ").replace("###HEADER####", "# ")
    content = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", content)
    content = re.sub(r"```[^\n]*\n|```", "", content)
    content = re.sub(r"`([^`]+)`", r"\1", content)
    content = re.sub(r"\n\s*\n\s*\n+", "\n\n", content)
    return content.strip()


def stable_id(prefix, *parts):
    digest = hashlib.sha1("\n".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def split_path(breadcrumb_path):
    return [part.strip() for part in str(breadcrumb_path or "").split(">") if part.strip()]


def clean_term(value):
    value = str(value or "").strip()
    value = re.sub(r"^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"^\s*第[\d一二三四五六七八九十百]+[章节篇部]\s*", "", value)
    value = re.sub(r"^\s*\d+(?:[.．]\d+){1,5}\s*", "", value)
    value = re.sub(r"^\s*(?:[-*+]\s+|[（(]?\d+[）).、]\s*)", "", value)
    value = re.sub(r"(?:[·.⋯…]+\s*)?\s+\d{1,4}$", "", value).strip()
    value = re.sub(r"[*_`]+", "", value)
    value = value.strip(" 《》“”\"'（）()[]【】")
    value = re.sub(r"[，。；：、,.!?！？;:]+$", "", value).strip()
    value = re.sub(r"\s+", "", value)
    if len(value) < 2 or len(value) > 24:
        return ""
    if value in GENERIC_TITLES:
        return ""
    return value


def is_domain_term(value, suffixes):
    return bool(value and value.endswith(suffixes) and not is_fragment_name(value))


def is_quality_class_term(value, from_body=False):
    value = clean_term(value)
    if not value or value in GENERIC_TERMS or is_fragment_name(value, from_body):
        return False
    if from_body and len(value) > 16:
        return False
    if from_body and any(value.startswith(prefix) for prefix in TERM_BAD_PREFIXES):
        return False
    if from_body and any(marker in value for marker in TERM_CLAUSE_MARKERS):
        return False
    if re.search(r"[$\\={}<>]|(?:^|[^A-Za-z])(图|表|式)\d", value):
        return False
    return is_domain_term(value, CLASS_SUFFIXES)


def clean_endpoint(value):
    value = clean_term(value)
    if not value:
        return ""
    value = re.sub(r"^(?:了|着|该|其|此|上述|这些|那些|它们|因此|所以|但|而|并|则|故|即|如)+", "", value)
    value = re.sub(r"(?:直接|主要|显著|较大|很大|会|将|也|又|既|不|所|就)+$", "", value)
    value = clean_term(value)
    if (
        not value
        or len(value) > 16
        or value.startswith(("使", "让"))
        or any(token in value for token in ("使", "为"))
        or any(word in value for word in ENDPOINT_FORBIDDEN)
        or value == "试系统"
    ):
        return ""
    if re.search(r"[$\\={}<>]", value) or is_fragment_name(value, from_body=True):
        return ""
    if not value.endswith(ENDPOINT_SUFFIXES):
        return ""
    return value


def infer_definition(name, text):
    match = re.search(rf"{re.escape(name)}(?:是指|是|指|称为|定义为)([^。；！？]{{4,120}})", text or "")
    return match.group(0).strip() if match else None


def infer_module_name(breadcrumb_path):
    text = breadcrumb_path or ""
    for module_name, keywords in MODULE_RULES:
        if any(keyword in text for keyword in keywords):
            return module_name
    parts = split_path(text)
    if len(parts) > 1:
        title = clean_term(parts[1])
        if title:
            return f"{title}子本体"
    return "通用火炮知识子本体"


def first_evidence(item):
    evidence = item.get("evidence") or []
    if not evidence:
        return None
    return evidence[0].get("chunk_id")


def module_ref(module, chunk):
    return {
        "ontology_name": "火炮领域本体",
        "module_id": module["module_id"],
        "module_name": module["name"],
        "directory_role": "module_partition",
        "note": "目录树仅用于划分子本体模块，不自动生成 subClassOf 关系。",
        "breadcrumb_path": chunk.get("breadcrumb_path"),
        "section_id": chunk.get("section_id"),
        "parent_section_id": chunk.get("parent_section_id"),
    }


def build_module_index(chunks):
    modules = {}
    chunk_modules = {}
    for chunk in chunks:
        breadcrumb_path = chunk.get("breadcrumb_path") or "正文"
        module_name = infer_module_name(breadcrumb_path)
        module_id = stable_id("module", module_name)
        module = modules.setdefault(
            module_id,
            {
                "module_id": module_id,
                "name": module_name,
                "ontology_name": "火炮领域本体",
                "directory_role": "module_partition",
                "source_paths": [],
                "section_ids": [],
                "chunk_ids": [],
            },
        )
        if breadcrumb_path not in module["source_paths"]:
            module["source_paths"].append(breadcrumb_path)
        if chunk.get("section_id") not in module["section_ids"]:
            module["section_ids"].append(chunk.get("section_id"))
        module["chunk_ids"].append(chunk["chunk_id"])
        chunk_modules[chunk["chunk_id"]] = module_ref(module, chunk)

    return {
        "ontology_name": "火炮领域本体",
        "principle": "目录树用于划分子本体模块；标题父子关系不是本体类继承关系。",
        "modules": sorted(modules.values(), key=lambda item: item["name"]),
        "chunks": chunk_modules,
    }


def evidence(chunk, text):
    text = str(text or "").strip()
    if text and text not in chunk["content"]:
        text = chunk["content"][:180].strip()
    return [{"chunk_id": chunk["chunk_id"], "text": text or chunk["content"][:180].strip()}]


def candidate_base(chunk, module_index, text):
    return {
        "evidence": evidence(chunk, text),
        "source_module": module_index["chunks"].get(chunk["chunk_id"]),
    }


def add_class(result, seen, name, chunk, module_index, reason, confidence=0.72, parent=None, evidence_text=None):
    name = clean_term(name)
    from_body = reason == "正文中出现稳定领域概念。"
    from_relation = reason.startswith("关系句中的")
    if from_relation:
        name = clean_endpoint(name)
    if not name or (not from_relation and not is_quality_class_term(name, from_body=from_body)):
        return
    key = name
    if key in seen:
        return
    seen.add(key)
    item = {
        "name": name,
        "normalized_name": name,
        "parent_candidate": clean_term(parent) or None,
        "definition_candidate": infer_definition(name, evidence_text),
        "status": "new_candidate",
        "matched_existing_class": None,
        "confidence": confidence,
        "reason": reason,
    }
    item.update(candidate_base(chunk, module_index, evidence_text or name))
    result["candidate_classes"].append(item)


def add_datatype_property(result, seen, name, domain, chunk, module_index, evidence_text):
    name = clean_term(name)
    domain = clean_endpoint(domain)
    if (
        not is_domain_term(name, QUANTITY_SUFFIXES)
        or not domain
        or len(domain) > 10
        or domain.endswith(QUANTITY_SUFFIXES)
        or any(word in domain for word in ("提高", "得到", "求出", "缩短", "相对", "作用时期"))
    ):
        return
    key = (name, domain)
    if key in seen:
        return
    seen.add(key)
    item = {
        "name": name,
        "normalized_name": name,
        "definition_candidate": infer_definition(name, evidence_text),
        "status": "new_candidate",
        "matched_existing_property": None,
        "domain_candidate": domain,
        "range_datatype": "float",
        "unit": infer_unit(evidence_text),
        "example_value": infer_example_value(evidence_text),
        "confidence": 0.7,
        "reason": "正文中出现可度量的物理量或参数。",
    }
    item.update(candidate_base(chunk, module_index, evidence_text))
    result["candidate_datatype_properties"].append(item)


def add_object_property(result, seen, prop, domain, range_value, chunk, module_index, evidence_text):
    domain = clean_term(domain)
    range_value = clean_term(range_value)
    if not prop or not domain or not range_value or is_fragment_name(domain) or is_fragment_name(range_value):
        return
    key = (prop, domain, range_value)
    if key in seen:
        return
    seen.add(key)
    item = {
        "name": prop,
        "normalized_name": prop,
        "definition_candidate": infer_definition(prop, evidence_text),
        "status": "new_candidate",
        "matched_existing_property": None,
        "domain_candidate": domain,
        "range_candidate": range_value,
        "confidence": 0.68,
        "reason": "正文中出现稳定的概念间语义关系。",
    }
    item.update(candidate_base(chunk, module_index, evidence_text))
    result["candidate_object_properties"].append(item)


def infer_unit(text):
    match = re.search(r"(?<![A-Za-z])(m/s|米/秒|MPa|Pa|kg|mm|cm|℃|度|g|m|s|K)(?![A-Za-z])", text or "")
    return match.group(1) if match else None


def infer_example_value(text):
    match = re.search(r"[-+]?\d+(?:\.\d+)?\s*(?:m/s|米/秒|MPa|Pa|kg|mm|cm|℃|度|g|m|s|K)(?![A-Za-z])", text or "")
    return match.group(0).strip() if match else None


def sentence_texts(chunk):
    sentences = [item.get("text", "") for item in chunk.get("sentences", []) if item.get("text")]
    if sentences:
        return [sentence for sentence in sentences if not re.match(r"^\s*#{1,6}\s+", sentence)]
    return [
        part.strip() for part in re.split(r"(?<=[。！？；;])", chunk["content"])
        if part.strip() and not re.match(r"^\s*#{1,6}\s+", part)
    ]


def title_candidates(chunk):
    parts = split_path(chunk.get("breadcrumb_path"))
    for part in parts:
        title = clean_term(part)
        if not title:
            continue
        if is_quality_class_term(title):
            yield title, "目录标题中出现稳定领域概念，但目录父子关系不会直接作为本体继承关系。"


def phrase_candidates(text):
    text = str(text or "").strip()
    values = []

    for match in re.finditer(r"(?:^|[，；;])(?P<term>[^，；;。]{2,24}?)(?:是指|是|指|定义为)", text):
        values.append(match.group("term"))
    for match in re.finditer(r"(?:称为|叫作|叫做|定义为|就是)(?P<term>[^，；;。]{2,20})", text):
        values.append(match.group("term"))
    for match in re.finditer(r"[“\"](?P<term>[^”\"]{2,20})[”\"]", text):
        values.append(match.group("term"))

    for marker in ("主要有", "包括", "包含", "分为", "可分为"):
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1]
        tail = re.split(r"[。；;]|等(?:内容|方法|类型|部分|参数|因素|载荷)?", tail, maxsplit=1)[0]
        values.extend(re.split(r"[、，,]|以及|和|及", tail))

    seen = set()
    for value in values:
        term = clean_term(value)
        if term in seen:
            continue
        if is_quality_class_term(term, from_body=True):
            seen.add(term)
            yield term


def quantity_candidates(text):
    for segment in re.split(
        r"[，。；：、,;:\n]|包括|包含|以及|和|是|为|达到|等于|影响|决定|导致|产生|作用于|用于",
        text or "",
    ):
        term = clean_term(segment)
        if not term:
            continue
        domain = None
        if "的" in term:
            possible_domain, quantity = term.rsplit("的", 1)
            if is_domain_term(quantity, QUANTITY_SUFFIXES):
                term = quantity
                domain = clean_endpoint(possible_domain)
        if domain and is_domain_term(term, QUANTITY_SUFFIXES):
            yield term, domain


def relation_candidates(text):
    clauses = [part.strip() for part in re.split(r"[。！？；;\n]", text or "") if part.strip()]
    for clause in clauses:
        composition = re.search(r"(?P<head>[^，,:：]{2,30})由(?P<tail>[^，。；;]{2,80})组成", clause)
        if composition:
            domain = clean_endpoint(composition.group("head"))
            for raw_range in re.split(r"[、，和及与]", composition.group("tail")):
                range_value = clean_endpoint(raw_range)
                if domain and range_value:
                    yield "由...组成", domain, range_value

        inclusion = re.search(r"(?P<head>[^，,:：]{2,30})(?:包括|包含)(?P<tail>[^。；;]{2,80})", clause)
        if inclusion:
            domain = clean_endpoint(inclusion.group("head"))
            tail = re.split(r"等(?:内容|方法|类型|部分|参数|因素|载荷)?", inclusion.group("tail"), maxsplit=1)[0]
            for raw_range in re.split(r"[、，和及与]", tail):
                range_value = clean_endpoint(raw_range)
                if domain and range_value and not domain.endswith(NON_OBJECT_GROUP_SUFFIXES):
                    yield "包括", domain, range_value

        for prop, pattern in RELATION_PATTERNS:
            for match in pattern.finditer(clause):
                before = re.split(r"[，,:：]", clause[:match.start()])[-1]
                after = re.split(r"[，,:：]", clause[match.end():])[0]
                domain = clean_endpoint(before)
                range_value = clean_endpoint(after)
                if domain and range_value:
                    yield prop, domain, range_value


def nearest_domain(chunk):
    parts = [clean_term(part) for part in split_path(chunk.get("breadcrumb_path"))]
    parts = [part for part in parts if part and part not in GENERIC_TITLES]
    return parts[-1] if parts else None


def content_chunks(chunks):
    """Exclude book front matter, table of contents, references, and index."""
    if not chunks:
        return []
    chapter_one = []
    for index, chunk in enumerate(chunks):
        root = split_path(chunk.get("breadcrumb_path"))[0] if split_path(chunk.get("breadcrumb_path")) else ""
        if re.match(r"^第(?:1|一)章", root):
            chapter_one.append(index)
    start = chapter_one[-1] if chapter_one else 0

    end = len(chunks)
    for index in range(start + 1, len(chunks)):
        parts = split_path(chunks[index].get("breadcrumb_path"))
        root = parts[0].strip() if parts else ""
        if re.match(r"^(?:参考文献|索引)(?:\s|·|$)", root):
            end = index
            break
    return chunks[start:end]


def generate_candidates(chunks, module_index):
    result = {field: [] for field in RESULT_FIELDS}
    seen_classes = set()
    seen_datatype = set()
    seen_object = set()

    for chunk in content_chunks(chunks):
        for title, reason in title_candidates(chunk):
            add_class(result, seen_classes, title, chunk, module_index, reason, confidence=0.74, parent=None, evidence_text=title)

        for sentence in sentence_texts(chunk):
            for phrase in phrase_candidates(sentence):
                add_class(
                    result,
                    seen_classes,
                    phrase,
                    chunk,
                    module_index,
                    "正文中出现稳定领域概念。",
                    confidence=0.7,
                    parent=None,
                    evidence_text=sentence,
                )

            for quantity, domain in quantity_candidates(sentence):
                add_datatype_property(result, seen_datatype, quantity, domain, chunk, module_index, sentence)

            for prop, head, tail in relation_candidates(sentence):
                add_class(result, seen_classes, head, chunk, module_index, "关系句中的 domain 候选。", 0.64, evidence_text=sentence)
                add_class(result, seen_classes, tail, chunk, module_index, "关系句中的 range 候选。", 0.64, evidence_text=sentence)
                add_object_property(result, seen_object, prop, head, tail, chunk, module_index, sentence)

    return result


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def main():
    markdown_path = Path(MARKDOWN_FILE_PATH)
    text = read_markdown(markdown_path)
    document_id = normalize_document_id(markdown_path.name)
    chunks = split_markdown_with_hierarchy(text, document_id=document_id)
    module_index = build_module_index(chunks)
    candidates = generate_candidates(chunks, module_index)
    write_json(MODULES_JSON_PATH, module_index)
    write_json(OUTPUT_JSON_PATH, candidates)
    candidate_count = sum(len(candidates[field]) for field in RESULT_FIELDS)
    print(f"目录引导候选生成完成：{len(module_index['modules'])} 个子本体模块，{candidate_count} 个候选项。")
    print(f"模块索引: {MODULES_JSON_PATH}")
    print(f"候选结果: {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
