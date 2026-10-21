import hashlib
import json
import os
import random
import re
import sys
import time

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from tqdm import tqdm

try:
    from ontologyBuild.ontology_candidate_schema import validate_format_data
    from ontologyBuild.business_validate import (
        validate_and_process_business_data,
        validate_business_data,
    )
except ImportError:
    from ontology_candidate_schema import validate_format_data
    from business_validate import validate_and_process_business_data, validate_business_data


# 将项目根目录加入 Python 路径；不要在导入模块时切换 cwd 或创建远程 client。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from document.hierarchy_index import (
    normalize_document_id,
    split_markdown_text as split_markdown_with_hierarchy,
)


# ================= 大模型配置 =================
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("QWEN_API_KEY", "sk-45jfj2wN89d0hwLFA18c71D7D3A3462b9eBe96F6Ea7d8cF5")
BASE_URL = os.environ.get("OPENAI_BASE_URL") or os.environ.get("QWEN_BASE_URL", "https://43.143.151.41:3000/v1")
MODEL = os.environ.get("ONTOLOGY_MODEL") or os.environ.get("OPENAI_MODEL") or os.environ.get("QWEN_MODEL", "Qwen")

MARKDOWN_FILE_PATH = os.environ.get(
    "ONTOLOGY_MARKDOWN_FILE",
    "mdfiles/火炮设计理论 (张相炎、郑建国、袁人枢).md",
)
OUTPUT_JSON_PATH = "ontologyBuild/ontology_candidates.json"
REVIEW_JSON_PATH = "ontologyBuild/ontology_candidates_review.json"
PROCESSING_LOG_PATH = "ontologyBuild/ontology_candidates_processing.json"

DEFAULT_LLM_TIMEOUT_SECONDS = 180
RETRY_BACKOFF_BASE_SECONDS = 10.0
RETRY_BACKOFF_MAX_SECONDS = 60.0
RETRY_BACKOFF_JITTER_RATIO = 0.2
DEFAULT_MAX_OUTPUT_TOKENS = 1500
DEFAULT_ADAPTIVE_RETRY_CHUNK_SIZE = 400
DEFAULT_ADAPTIVE_RETRY_CHUNK_OVERLAP = 40

# ==============================================


class SchemaValidationError(ValueError):
    """LLM 返回 JSON 可解析，但不符合候选本体 Schema。"""


class ChunkExtractionError(RuntimeError):
    """单个 chunk 在重试后仍无法完成抽取。"""

    def __init__(self, chunk_id, message, attempts=None):
        super().__init__(message)
        self.chunk_id = chunk_id
        self.attempts = attempts or []


def create_default_client():
    if not API_KEY:
        raise ValueError("缺少 OPENAI_API_KEY 或 QWEN_API_KEY，不能执行真实 LLM 本体抽取")
    if not BASE_URL:
        raise ValueError("缺少 OPENAI_BASE_URL 或 QWEN_BASE_URL，不能执行真实 LLM 本体抽取")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("缺少 openai 依赖，请先安装 openai 包") from exc
    try:
        import httpx
    except ImportError:
        return OpenAI(
            api_key=API_KEY,
            base_url=BASE_URL,
            timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )
    verify_value = os.environ.get("KG_LLM_VERIFY_SSL", "true").strip().lower()
    if verify_value in {"1", "true", "yes", "on"}:
        verify_ssl = True
    elif verify_value in {"0", "false", "no", "off"}:
        verify_ssl = False
    else:
        raise ValueError("KG_LLM_VERIFY_SSL 必须是 true/false")
    return OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
        max_retries=0,
        http_client=httpx.Client(
            timeout=DEFAULT_LLM_TIMEOUT_SECONDS,
            trust_env=False,
            verify=verify_ssl,
        ),
    )


def retry_backoff_seconds(
    failed_attempt,
    *,
    base_seconds=RETRY_BACKOFF_BASE_SECONDS,
    max_seconds=RETRY_BACKOFF_MAX_SECONDS,
    jitter_ratio=RETRY_BACKOFF_JITTER_RATIO,
):
    """Return capped exponential backoff with positive jitter for a failed attempt."""
    if failed_attempt < 1:
        raise ValueError("failed_attempt 必须大于等于 1")
    if base_seconds <= 0 or max_seconds <= 0:
        raise ValueError("退避时间必须大于 0")
    if jitter_ratio < 0:
        raise ValueError("jitter_ratio 不能小于 0")
    exponential = min(max_seconds, base_seconds * (2 ** (failed_attempt - 1)))
    jitter = random.uniform(0.0, exponential * jitter_ratio)
    return min(max_seconds, exponential + jitter)


def wait_before_retry(chunk_id, failed_attempt):
    delay = retry_backoff_seconds(failed_attempt)
    print(f"片段 {chunk_id} 将在 {delay:.1f} 秒后进行第 {failed_attempt + 1} 次尝试。")
    time.sleep(delay)


def is_timeout_error(error):
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "504",
            "gateway time-out",
            "gateway timeout",
            "request timed out",
            "timeout",
            "timed out",
        )
    )


def split_chunk_for_timeout_retry(chunk, *, chunk_size, chunk_overlap):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", "、", ""],
    )
    return [part for part in splitter.split_text(str(chunk.get("content") or "")) if part.strip()]


def merge_extraction_results(results):
    merged = empty_extraction_result()
    seen = {field: set() for field in RESULT_FIELDS}
    for result in results:
        for field in RESULT_FIELDS:
            for item in result.get(field, []) or []:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if key in seen[field]:
                    continue
                seen[field].add(key)
                merged[field].append(item)
    return merged


def read_markdown(markdown_path=MARKDOWN_FILE_PATH):
    """读取并预处理 Markdown 文件。"""
    if not os.path.exists(markdown_path):
        raise FileNotFoundError(f"找不到文件: {markdown_path}")

    with open(markdown_path, "r", encoding="utf-8") as file:
        content = file.read()

    # 将 MinerU 特有标题转换为标准 Markdown 标题。
    content = content.replace("###HEADER#### ", "# ")
    content = content.replace("###HEADER####", "# ")

    content = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", content)
    content = re.sub(r"^[\s]*[-\*\+]\s+", "", content, flags=re.MULTILINE)
    content = re.sub(r"```[^\n]*\n", "", content)
    content = re.sub(r"```", "", content)
    content = re.sub(r"`([^`]+)`", r"\1", content)
    content = re.sub(r"\n\s*\n\s*\n+", "\n\n", content)

    return content.strip()


def build_breadcrumb_path(metadata):
    """根据标题元数据构建文档片段的面包屑路径。"""
    parts = []

    for key in ["Header-1", "Header-2", "Header-3"]:
        if key in metadata and metadata[key]:
            header_text = metadata[key].strip()
            header_text = re.sub(r"^#+\s*", "", header_text)
            parts.append(header_text)

    return " > ".join(parts) if parts else "正文"


def generate_chunk_id(breadcrumb_path, content):
    """根据标题路径和内容生成稳定的片段 ID。"""
    digest = hashlib.md5(
        f"{breadcrumb_path}\n{content}".encode("utf-8")
    ).hexdigest()[:10]
    return f"doc_chunk_{digest}"


def split_markdown_text(text):
    """
    先按 Markdown 标题层级切分，再递归切分长文本。

    返回包含 breadcrumb_path、content 和 metadata 的字典列表。
    """
    chunks = split_markdown_with_hierarchy(
        text,
        document_id=normalize_document_id(os.path.basename(MARKDOWN_FILE_PATH)),
    )
    print(f"统一层级切分后得到 {len(chunks)} 个最终片段")
    return chunks


def load_and_split_markdown(markdown_path=MARKDOWN_FILE_PATH):
    """读取、清洗并切分 Markdown 文档。"""
    return split_markdown_text(read_markdown(markdown_path))


RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)


def empty_extraction_result():
    """创建与提示词输出结构一致的空结果。"""
    return {field: [] for field in RESULT_FIELDS}


def normalize_extraction_result(parsed, chunk):
    """保留 LLM 原始结果，并在汇总前执行第一层格式校验。"""
    validation_report = validate_format_data(parsed)
    if not validation_report.valid:
        raise SchemaValidationError(
            "LLM 返回结果未通过第一层格式校验: "
            f"{validation_report.model_dump_json()}"
        )
    return parsed


def extract_candidates_from_chunk(
    chunk,
    retry_count=3,
    ontology_context=None,
    llm_client=None,
    model=None,
    allow_empty_on_failure=False,
    max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    adaptive_split_on_timeout=True,
    adaptive_retry_chunk_size=DEFAULT_ADAPTIVE_RETRY_CHUNK_SIZE,
    adaptive_retry_chunk_overlap=DEFAULT_ADAPTIVE_RETRY_CHUNK_OVERLAP,
):
    """调用 LLM，从一个文档片段中抽取候选类别、关系、domain 和 range。"""
    ontology_context = ontology_context or {}
    llm_client = llm_client or create_default_client()
    model = model or MODEL
    if not model:
        raise ValueError("缺少 ONTOLOGY_MODEL，不能执行真实 LLM 本体抽取")
    class_list = json.dumps(
        ontology_context.get("class_list", []), ensure_ascii=False, indent=2
    )
    object_property_list = json.dumps(
        ontology_context.get("object_property_list", []),
        ensure_ascii=False,
        indent=2,
    )
    datatype_property_list = json.dumps(
        ontology_context.get("datatype_property_list", []),
        ensure_ascii=False,
        indent=2,
    )
    domain_range_constraints = json.dumps(
        ontology_context.get("domain_range_constraints", []),
        ensure_ascii=False,
        indent=2,
    )
    class_hierarchy = json.dumps(
        ontology_context.get("class_hierarchy", []), ensure_ascii=False, indent=2
    )
    synonym_mapping = json.dumps(
        ontology_context.get("synonym_mapping", {}), ensure_ascii=False, indent=2
    )
    document_id = ontology_context.get(
        "document_id", os.path.basename(MARKDOWN_FILE_PATH)
    )
    chunk_id = chunk["chunk_id"]
    chunk_text = chunk["content"]

    system_prompt = """
你是一个“领域本体候选元素抽取器”，任务是从给定文档片段中抽取可用于构建或扩展知识图谱本体的候选元素。

你需要抽取以下内容：
1. 候选类别 candidate_classes
2. 候选对象属性 candidate_object_properties
3. 候选数据属性 candidate_datatype_properties
4. 候选 domain/range 约束 candidate_constraints
5. 与已有本体元素的匹配关系 ontology_mapping
6. 无法确认但可能有价值的低置信候选 uncertain_candidates

你必须严格遵守以下规则：

【证据规则】
1. 所有候选元素必须来自当前文档片段。
2. 不允许使用常识、外部知识或领域经验补全。
3. 每个候选类别、关系、属性、domain/range 约束都必须提供 evidence。
4. evidence 必须是文档中的原文句子或短语，不能改写。
5. 如果没有明确证据，不能抽取。
6. 如果 evidence 中不能支持该候选元素，不能输出该候选。
7. 如果当前片段没有任何可抽取内容，输出空数组。

【已有本体使用规则】
1. 已有本体类别、关系属性、数据属性和 domain/range 约束会在用户输入中提供。
2. 你应优先将文档中的概念匹配到已有本体元素。
3. 如果文档中的概念可以明确对应已有类别，status 标记为 existing。
4. 如果文档中出现已有本体未覆盖但有明确证据的新概念，status 标记为 new_candidate。
5. 不允许随意创造新类别、新关系或新属性。
6. 新候选必须是文档中具有稳定语义的领域概念，而不是普通描述性短语。
7. 如果无法判断是否应加入本体，放入 uncertain_candidates。

【类别抽取规则】
1. 类别应表示一类对象、系统、部件、过程、参数、方法、材料、故障、环境、场景等稳定概念。
2. 类别名称应尽量使用名词或名词短语。
3. 不要把具体数值、普通形容词、一次性描述抽为类别。
4. 不要把具体实例误当成类别。若无法判断是类别还是实例，应降低置信度。
5. 如果候选类别有可能属于已有上位类，需要给出 parent_candidate。

【对象属性抽取规则】
1. 对象属性表示两个概念或实体之间的语义关系。
2. 关系名称应使用简洁动词或动宾短语，例如：组成、包含、影响、导致、测量、作用于、用于、属于、产生、约束。
3. 不要生成过度细碎、只适用于单个句子的关系名称。
4. 如果文档表达的关系可以映射到已有关系属性，应优先使用已有关系。
5. 如果必须提出新关系，必须有明确证据。
6. 每个对象属性必须尽可能给出 domain_candidate 和 range_candidate。
7. 如果 domain 或 range 不确定，使用 null，不要猜测。

【数据属性抽取规则】
1. 数据属性表示某个类别拥有的数值、文本、日期、布尔值等属性。
2. 例如：最大射程、初速、膛压、质量、口径、长度、温度、试验时间。
3. 数据属性的 range_datatype 只能是以下之一：
   string, integer, float, boolean, date, datetime
4. 如果出现单位，需要填写 unit。
5. 如果无法确定数据类型，range_datatype 使用 null。

【domain/range 约束规则】
1. domain 表示关系或属性适用的主体类别。
2. range 表示关系或属性指向的客体类别或数据类型。
3. domain/range 必须来自：
   a. 已有本体类别
   b. 本次抽取出的候选类别
   c. 数据属性允许的数据类型
4. 不允许凭空构造 domain/range。
5. 如果文档只出现实例关系，而不能稳定归纳为本体约束，应标记为 weak_candidate。
6. 如果 domain/range 只是从单个句子推测出来，confidence 不得高于 0.75。
7. 如果文档明确说明某类对象具有某关系或属性，可以给出较高置信度。

【命名规则】
1. 类别名称使用名词或名词短语。
2. 对象属性名称使用动词或动宾结构。
3. 数据属性名称使用名词短语。
4. 不要使用“相关于”“有关系”“关联”等过泛关系，除非已有本体中明确存在。
5. 尽量避免同义重复，例如“包含”“包括”“由……组成”应尽量归一到已有关系。
6. 不要输出英文名称，除非原文就是英文术语或已有本体使用英文。

【置信度规则】
confidence 取值范围为 0 到 1。
0.90 - 1.00：文档中有明确直接表述，且与已有本体高度一致。
0.75 - 0.89：文档中有较明确证据，但可能需要少量归纳。
0.60 - 0.74：有证据，但 domain/range 或上位类不够确定。
0.40 - 0.59：可能有价值，但不适合自动入库，应放入 uncertain_candidates。
低于 0.40：不要输出。

【输出规则】
1. 只能输出 JSON。
2. 不要输出解释性文字。
3. 不要输出 Markdown。
4. 不要在 JSON 外添加任何内容。
5. 所有字段必须完整出现。
6. 如果某字段没有结果，输出空数组 []。
7. 如果某个值不确定，使用 null。
8. evidence 必须包含 chunk_id 和 text。
9. text 必须是原文片段，不得改写。
10. JSON 必须能被 Python json.loads() 正确解析。

输出 JSON 结构必须严格如下：
{
  "candidate_classes": [],
  "candidate_object_properties": [],
  "candidate_datatype_properties": [],
  "candidate_constraints": [],
  "ontology_mapping": [],
  "uncertain_candidates": []
}
""".strip()

    user_prompt = f"""
下面是已有本体信息和待抽取的文档片段。

请你根据 System Prompt 的规则，从文档片段中抽取候选本体元素。

【抽取模式】
mode = open_candidate_extraction

说明：
1. 优先匹配已有本体。
2. 允许提出新增候选类别、对象属性、数据属性和 domain/range 约束。
3. 新增候选必须有文档原文证据。
4. 没有证据时不要抽取。
5. 不确定的内容放入 uncertain_candidates。
6. 只输出 JSON，不要输出任何解释。

【已有类别列表】
{class_list}

【已有对象属性列表】
{object_property_list}

【已有数据属性列表】
{datatype_property_list}

【已有 domain/range 约束】
{domain_range_constraints}

【已有类别层级结构】
{class_hierarchy}

【已有同义词或术语映射】
{synonym_mapping}

【文档信息】
document_id: {document_id}
chunk_id: {chunk_id}
chunk_text:
{chunk_text}

【额外限制】
1. 如果候选名称没有直接出现在原文中，必须在 reason 中说明它是如何由原文归纳得到的。
2. 如果候选名称没有直接出现在原文中，confidence 不能超过 0.75。
3. 如果 domain_candidate 或 range_candidate 没有直接出现在原文中，confidence 不能超过 0.70。
4. 如果只是根据一个实例三元组推测 domain/range，constraint_status 必须是 weak_candidate。
5. 如果无法判断某个术语是类别还是实例，不要放入 candidate_classes，应放入 uncertain_candidates。
6. 不允许输出泛化过度的类别，例如“系统”“部件”“参数”“过程”，除非它们是已有本体类别或原文明确作为领域类别使用。
7. 不允许输出泛化过度的关系，例如“相关”“关联”“涉及”“具有关系”。
8. 不允许把章节标题中的词机械地全部抽为类别，除非正文中有定义或稳定用法支持。

请严格按照以下 JSON 结构输出：

{{
  "candidate_classes": [
    {{
      "name": "",
      "normalized_name": "",
      "parent_candidate": "",
      "definition_candidate": "",
      "status": "existing | new_candidate",
      "matched_existing_class": "",
      "evidence": [
        {{
          "chunk_id": "{chunk_id}",
          "text": ""
        }}
      ],
      "confidence": 0.0,
      "reason": ""
    }}
  ],
  "candidate_object_properties": [
    {{
      "name": "",
      "normalized_name": "",
      "definition_candidate": "",
      "status": "existing | new_candidate",
      "matched_existing_property": "",
      "domain_candidate": "",
      "range_candidate": "",
      "evidence": [
        {{
          "chunk_id": "{chunk_id}",
          "text": ""
        }}
      ],
      "confidence": 0.0,
      "reason": ""
    }}
  ],
  "candidate_datatype_properties": [
    {{
      "name": "",
      "normalized_name": "",
      "definition_candidate": "",
      "status": "existing | new_candidate",
      "matched_existing_property": "",
      "domain_candidate": "",
      "range_datatype": "string | integer | float | boolean | date | datetime | null",
      "unit": "",
      "example_value": "",
      "evidence": [
        {{
          "chunk_id": "{chunk_id}",
          "text": ""
        }}
      ],
      "confidence": 0.0,
      "reason": ""
    }}
  ],
  "candidate_constraints": [
    {{
      "property_name": "",
      "property_type": "object_property | datatype_property",
      "domain_candidate": "",
      "range_candidate": "",
      "range_datatype": "",
      "constraint_status": "existing | new_candidate | weak_candidate",
      "matched_existing_constraint": "",
      "evidence": [
        {{
          "chunk_id": "{chunk_id}",
          "text": ""
        }}
      ],
      "confidence": 0.0,
      "reason": ""
    }}
  ],
  "ontology_mapping": [
    {{
      "text_mention": "",
      "mapped_to": "",
      "mapped_type": "class | object_property | datatype_property",
      "mapping_confidence": 0.0,
      "evidence": [
        {{
          "chunk_id": "{chunk_id}",
          "text": ""
        }}
      ]
    }}
  ],
  "uncertain_candidates": [
    {{
      "name": "",
      "candidate_type": "class | object_property | datatype_property | constraint",
      "why_uncertain": "",
      "evidence": [
        {{
          "chunk_id": "{chunk_id}",
          "text": ""
        }}
      ],
      "confidence": 0.0
    }}
  ]
}}
""".strip()

    retry_feedback = ""
    attempts = []
    for attempt in range(1, retry_count + 1):
        try:
            current_user_prompt = user_prompt
            if retry_feedback:
                current_user_prompt += (
                    "\n\n【上一次输出错误，请修正后重新输出】\n"
                    f"{retry_feedback}"
                )
            response = llm_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": current_user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=max_output_tokens,
            )
            parsed = json.loads(response.choices[0].message.content)
            return normalize_extraction_result(parsed, chunk)
        except json.JSONDecodeError as error:
            attempts.append({"attempt": attempt, "error_type": "json_decode", "message": str(error)})
            print(
                f"片段 {chunk['chunk_id']} 第 {attempt}/{retry_count} 次 JSON 解析失败，"
                f"要求 LLM 重新输出: {error}"
            )
            retry_feedback = f"上一次输出不是合法 JSON：{error}"
            if attempt < retry_count:
                wait_before_retry(chunk["chunk_id"], attempt)
        except SchemaValidationError as error:
            attempts.append({"attempt": attempt, "error_type": "schema_validation", "message": str(error)})
            print(
                f"片段 {chunk['chunk_id']} 第 {attempt}/{retry_count} 次 Schema 校验失败，"
                f"要求 LLM 重新输出: {error}"
            )
            retry_feedback = f"上一次输出不符合 Schema：{error}"
            if attempt < retry_count:
                wait_before_retry(chunk["chunk_id"], attempt)
        except Exception as error:
            attempts.append({"attempt": attempt, "error_type": type(error).__name__, "message": str(error)})
            print(
                f"片段 {chunk['chunk_id']} 第 {attempt}/{retry_count} 次抽取失败: {error}"
            )
            if (
                attempt == 1
                and adaptive_split_on_timeout
                and is_timeout_error(error)
                and len(str(chunk.get("content") or "")) > adaptive_retry_chunk_size
            ):
                wait_before_retry(chunk["chunk_id"], attempt)
                parts = split_chunk_for_timeout_retry(
                    chunk,
                    chunk_size=adaptive_retry_chunk_size,
                    chunk_overlap=adaptive_retry_chunk_overlap,
                )
                print(
                    f"片段 {chunk['chunk_id']} 首次超时，拆分为 {len(parts)} 个子片段后重试。"
                )
                part_results = []
                for part in parts:
                    part_chunk = dict(chunk)
                    part_chunk["content"] = part
                    part_results.append(
                        extract_candidates_from_chunk(
                            part_chunk,
                            retry_count=retry_count,
                            ontology_context=ontology_context,
                            llm_client=llm_client,
                            model=model,
                            allow_empty_on_failure=allow_empty_on_failure,
                            max_output_tokens=max_output_tokens,
                            adaptive_split_on_timeout=False,
                            adaptive_retry_chunk_size=adaptive_retry_chunk_size,
                            adaptive_retry_chunk_overlap=adaptive_retry_chunk_overlap,
                        )
                    )
                return merge_extraction_results(part_results)
            if attempt < retry_count:
                wait_before_retry(chunk["chunk_id"], attempt)

    message = f"片段 {chunk['chunk_id']} 多次输出不合格，已停止该 chunk。"
    print(message)
    if allow_empty_on_failure:
        return empty_extraction_result()
    raise ChunkExtractionError(chunk["chunk_id"], message, attempts)


def extract_ontology_candidates(chunks, ontology_context=None, llm_client=None, model=None):
    """逐片段调用 LLM，汇总本体候选关系。"""
    candidates = empty_extraction_result()
    for chunk in tqdm(chunks, desc="抽取本体候选"):
        chunk_result = extract_candidates_from_chunk(
            chunk, ontology_context=ontology_context, llm_client=llm_client, model=model
        )
        for field in RESULT_FIELDS:
            candidates[field].extend(chunk_result[field])
    return candidates


def write_json(data, output_path):
    """写入 JSON 文件。"""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def save_candidates(
    candidates,
    chunks,
    output_path=OUTPUT_JSON_PATH,
    review_path=REVIEW_JSON_PATH,
    processing_log_path=PROCESSING_LOG_PATH,
):
    """执行两层校验与处理策略后保存候选、审核项和处理日志。"""
    validation_report = validate_format_data(candidates)
    if not validation_report.valid:
        print("候选本体结果未通过第一层格式校验：")
        print(validation_report.model_dump_json(indent=2))
        raise ValueError("候选本体结果格式校验失败，已停止写入文件")

    processing_result = validate_and_process_business_data(candidates, chunks)
    processed_candidates = processing_result.candidates

    processed_format_report = validate_format_data(processed_candidates)
    if not processed_format_report.valid:
        print("处理后的候选结果未通过第一层格式校验：")
        print(processed_format_report.model_dump_json(indent=2))
        raise ValueError("处理后的候选结果格式校验失败，已停止写入文件")

    remaining_business_report = validate_business_data(processed_candidates, chunks)
    if not remaining_business_report.valid:
        print("处理后的候选结果仍存在业务校验错误：")
        print(remaining_business_report.model_dump_json(indent=2))
        raise ValueError("处理后的候选结果业务校验失败，已停止写入文件")

    write_json(processed_candidates, output_path)
    write_json(processing_result.manual_review, review_path)
    write_json(
        {
            "actions": processing_result.actions,
            "discarded": processing_result.discarded,
        },
        processing_log_path,
    )
    return processed_candidates


if __name__ == "__main__":
    chunks = load_and_split_markdown()
    print(f"文档切分完成，共得到 {len(chunks)} 个片段。")
    candidates = extract_ontology_candidates(chunks)
    processed_candidates = save_candidates(candidates, chunks)
    candidate_count = sum(len(processed_candidates[field]) for field in RESULT_FIELDS)
    print(f"候选元素抽取完成，共得到 {candidate_count} 条，已保存至 {OUTPUT_JSON_PATH}。")
