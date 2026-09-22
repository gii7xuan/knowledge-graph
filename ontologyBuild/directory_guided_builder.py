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
    ("由...组成", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})由(?P<tail>[\u4e00-\u9fa5A-Za-z0-9、，和及与（）()]{2,40})组成")),
    ("包括", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})包括(?P<tail>[\u4e00-\u9fa5A-Za-z0-9、，和及与（）()]{2,40})")),
    ("影响", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})影响(?P<tail>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})")),
    ("决定", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})决定(?P<tail>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})")),
    ("导致", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})导致(?P<tail>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})")),
    ("产生", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})产生(?P<tail>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})")),
    ("作用于", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})作用于(?P<tail>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})")),
    ("用于", re.compile(r"(?P<head>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})用于(?P<tail>[\u4e00-\u9fa5A-Za-z0-9（）()]{2,20})")),
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
    value = re.sub(r"^[第\d一二三四五六七八九十百章节篇部.\s、（）()]+", "", str(value or "")).strip()
    value = re.sub(r"(?:[·.⋯…]+\s*\d{1,4}|\d{2,4})$", "", value).strip()
    value = re.sub(r"[，。；：、,.!?！？;:]+$", "", value).strip()
    value = re.sub(r"\s+", "", value)
    if len(value) < 2 or len(value) > 24:
        return ""
    if value in GENERIC_TITLES:
        return ""
    return value


def is_domain_term(value, suffixes):
    return bool(value and value.endswith(suffixes) and not is_fragment_name(value))


def infer_definition(name, text):
    match = re.search(rf"{re.escape(name)}(?:是|指|定义为)([^。；！？]{{4,120}})", text or "")
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
    if not name or is_fragment_name(name):
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
    if not is_domain_term(name, QUANTITY_SUFFIXES):
        return
    key = (name, clean_term(domain))
    if key in seen:
        return
    seen.add(key)
    item = {
        "name": name,
        "normalized_name": name,
        "definition_candidate": infer_definition(name, evidence_text),
        "status": "new_candidate",
        "matched_existing_property": None,
        "domain_candidate": clean_term(domain) or None,
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
    result["candidate_constraints"].append(
        {
            "property_name": prop,
            "property_type": "object_property",
            "domain_candidate": domain,
            "range_candidate": range_value,
            "range_datatype": None,
            "constraint_status": "weak_candidate",
            "matched_existing_constraint": None,
            "confidence": 0.62,
            "reason": "由单句关系归纳出的约束候选，需要人工确认后再写入正式本体。",
            **candidate_base(chunk, module_index, evidence_text),
        }
    )


def infer_unit(text):
    match = re.search(r"(?<![A-Za-z])(m/s|米/秒|MPa|Pa|kg|mm|cm|℃|度|g|m|s|K)(?![A-Za-z])", text or "")
    return match.group(1) if match else None


def infer_example_value(text):
    match = re.search(r"[-+]?\d+(?:\.\d+)?\s*(?:m/s|米/秒|MPa|Pa|kg|mm|cm|℃|度|g|m|s|K)(?![A-Za-z])", text or "")
    return match.group(0).strip() if match else None


def sentence_texts(chunk):
    sentences = [item.get("text", "") for item in chunk.get("sentences", []) if item.get("text")]
    if sentences:
        return sentences
    return [part.strip() for part in re.split(r"(?<=[。！？；;])", chunk["content"]) if part.strip()]


def title_candidates(chunk):
    parts = split_path(chunk.get("breadcrumb_path"))
    for part in parts:
        title = clean_term(part)
        if not title:
            continue
        if is_domain_term(title, CLASS_SUFFIXES):
            yield title, "目录标题中出现稳定领域概念，但目录父子关系不会直接作为本体继承关系。"


def phrase_candidates(text):
    for segment in re.split(r"[，。；：、,;:\n]|包括|包含|论述了|论述|介绍了|介绍|以及|和|是|指|定义为", text or ""):
        term = clean_term(segment)
        if is_domain_term(term, CLASS_SUFFIXES):
            yield term


def quantity_candidates(text):
    for segment in re.split(r"[，。；：、,;:\n]|包括|包含|以及|和|是|为|达到|等于", text or ""):
        term = clean_term(segment)
        if not term:
            continue
        domain = None
        if "的" in term:
            possible_domain, quantity = term.rsplit("的", 1)
            if is_domain_term(quantity, QUANTITY_SUFFIXES):
                term = quantity
                domain = clean_term(possible_domain)
                if not is_domain_term(domain, CLASS_SUFFIXES):
                    domain = None
        if is_domain_term(term, QUANTITY_SUFFIXES):
            yield term, domain


def nearest_domain(chunk):
    parts = [clean_term(part) for part in split_path(chunk.get("breadcrumb_path"))]
    parts = [part for part in parts if part and part not in GENERIC_TITLES]
    return parts[-1] if parts else None


def generate_candidates(chunks, module_index):
    result = {field: [] for field in RESULT_FIELDS}
    seen_classes = set()
    seen_datatype = set()
    seen_object = set()

    for chunk in chunks:
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

            for prop, pattern in RELATION_PATTERNS:
                for match in pattern.finditer(sentence):
                    head = match.group("head")
                    tail = re.split(r"[、，和及与]", match.group("tail"))[0]
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
