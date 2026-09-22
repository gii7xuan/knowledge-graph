import copy
import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ontologyBuild.review_module import apply_user_decision, review_candidates


ROOT = Path(__file__).resolve().parent
ONTOLOGY_BUILD_DIR = ROOT / "ontologyBuild"
ONTOLOGY_DIR = ROOT / "ontology"
MDFILES_DIR = ROOT / "mdfiles"
DEFAULT_CANDIDATES_PATH = ONTOLOGY_BUILD_DIR / "ontology_candidates.json"
DEFAULT_MODULES_PATH = ONTOLOGY_DIR / "ontology_modules.json"
DEFAULT_TOPIC_MAPPING_PATH = ONTOLOGY_DIR / "topic_schema_mapping.json"
DEFAULT_REVIEW_RESULT_PATH = ONTOLOGY_BUILD_DIR / "ontology_review_result.json"
DEFAULT_REVIEW_LOG_PATH = ONTOLOGY_BUILD_DIR / "ontology_review_log.json"

RESULT_FIELDS = (
    "candidate_classes",
    "candidate_object_properties",
    "candidate_datatype_properties",
    "candidate_constraints",
    "ontology_mapping",
    "uncertain_candidates",
)

SECTION_TYPE = {
    "candidate_classes": "class",
    "candidate_object_properties": "object_property",
    "candidate_datatype_properties": "datatype_property",
    "candidate_constraints": "constraint",
    "ontology_mapping": "mapping",
    "uncertain_candidates": "uncertain",
}


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json(path, default):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def list_markdown_files():
    if not MDFILES_DIR.exists():
        return []
    return sorted(str(path.relative_to(ROOT)).replace("\\", "/") for path in MDFILES_DIR.glob("*.md"))


def safe_root_path(value, default_path):
    if not value:
        return Path(default_path)
    path = (ROOT / value).resolve()
    if ROOT.resolve() not in [path, *path.parents]:
        raise ValueError("路径必须位于当前项目目录内")
    return path


def load_candidate_source(path):
    data = read_json(path, None)
    if data is None:
        raise FileNotFoundError(f"找不到候选本体文件: {path}")
    return data


def confidence_of(item):
    for key in ("confidence", "mapping_confidence"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                pass
    return 0.0


def display_name(item):
    if item.get("name"):
        return str(item["name"])
    if item.get("property_name"):
        name = str(item["property_name"])
        domain = item.get("domain_candidate")
        range_value = item.get("range_candidate") or item.get("range_datatype")
        if domain or range_value:
            return f"{name}: {domain or '?'} -> {range_value or '?'}"
        return name
    if item.get("text_mention"):
        return f"{item.get('text_mention')} -> {item.get('mapped_to')}"
    return str(item.get("candidate_id") or "未命名候选")


def flatten_candidates(data):
    if isinstance(data, list):
        result = []
        for index, item in enumerate(data):
            if isinstance(item, dict):
                if isinstance(item.get("candidate"), dict):
                    candidate = copy.deepcopy(item["candidate"])
                    candidate.setdefault("source_section", item.get("source_section"))
                    candidate.setdefault("review_type", item.get("review_type"))
                    candidate.setdefault("reason", item.get("reason"))
                else:
                    candidate = copy.deepcopy(item)
                candidate.setdefault("candidate_type", candidate.get("candidate_type") or "manual_review")
                candidate.setdefault("candidate_id", f"manual_{index + 1:06d}")
                result.append(candidate)
        return result

    if not isinstance(data, dict):
        raise ValueError("候选本体数据必须是 JSON 对象或数组")

    result = []
    for section in RESULT_FIELDS:
        values = data.get(section, [])
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            candidate = copy.deepcopy(item)
            candidate["source_section"] = section
            candidate.setdefault("candidate_type", SECTION_TYPE.get(section, section))
            candidate.setdefault("candidate_id", f"{section}_{index + 1:06d}")
            result.append(candidate)
    return result


def enrich_candidate_for_response(candidate):
    item = copy.deepcopy(candidate)
    item["display_name"] = display_name(item)
    item["confidence_display"] = f"{confidence_of(item):.4f}"
    source_module = item.get("source_module")
    if isinstance(source_module, dict):
        item["source_module_text"] = source_module.get("module_name") or ""
        item["directory_context_text"] = source_module.get("breadcrumb_path") or ""
        item["directory_role_text"] = source_module.get("directory_role") or ""
    else:
        item["source_module_text"] = ""
        item["directory_context_text"] = ""
        item["directory_role_text"] = ""
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        item["evidence_text"] = "\n\n".join(
            str(piece.get("text", "")) for piece in evidence if isinstance(piece, dict)
        )
    else:
        item["evidence_text"] = ""
    return item


def get_review_result():
    return read_json(DEFAULT_REVIEW_RESULT_PATH, {
        "passed": True,
        "review_mode": "manual",
        "accepted_candidates": [],
        "rejected_candidates": [],
        "pending_review_candidates": [],
        "deferred_candidates": [],
        "review_log": [],
        "summary": {
            "total": 0,
            "accepted": 0,
            "rejected": 0,
            "pending_review": 0,
            "deferred": 0,
        },
        "errors": [],
    })


def build_review_from_candidates(source_path, review_mode, reviewer):
    candidates = flatten_candidates(load_candidate_source(source_path))
    result = review_candidates(candidates, review_mode=review_mode, reviewer=reviewer)
    result["source_file"] = str(Path(source_path).relative_to(ROOT)).replace("\\", "/")
    result["generated_at"] = now_iso()
    write_json(DEFAULT_REVIEW_RESULT_PATH, result)
    return result


def run_extraction(markdown_path):
    env = os.environ.copy()
    env["ONTOLOGY_MARKDOWN_FILE"] = str(markdown_path)
    script_name = env.get("ONTOLOGY_EXTRACTION_SCRIPT", "directory_guided_builder.py")
    proc = subprocess.run(
        [sys.executable, str(ONTOLOGY_BUILD_DIR / script_name)],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "本体候选抽取失败").strip())
    normalize_stdout = ""
    normalize_stderr = ""
    if env.get("ONTOLOGY_RUN_NORMALIZATION", "1").strip().lower() not in {"0", "false", "no"}:
        normalize_proc = subprocess.run(
            [sys.executable, str(ONTOLOGY_BUILD_DIR / "ontology_normalizer.py")],
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
        )
        normalize_stdout = normalize_proc.stdout
        normalize_stderr = normalize_proc.stderr
        if normalize_proc.returncode != 0:
            raise RuntimeError(
                (normalize_proc.stderr or normalize_proc.stdout or "本体规范化导出失败").strip()
            )
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "normalization_stdout": normalize_stdout,
        "normalization_stderr": normalize_stderr,
    }


FRONTEND_DIR = ROOT / "frontend" / "ontology_build"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def content_type_for(path):
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def frontend_file_for(request_path):
    if request_path == "/":
        relative = Path("index.html")
    elif request_path.startswith("/static/"):
        relative = Path(request_path[len("/static/"):])
    else:
        return None
    path = (FRONTEND_DIR / relative).resolve()
    if FRONTEND_DIR.resolve() not in [path, *path.parents] or not path.is_file():
        return None
    return path



class OntologyBuildHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        body = Path(path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type_for(Path(path)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self):
        self.send_file(FRONTEND_DIR / "index.html")

    def read_payload(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_html()
            elif parsed.path.startswith("/static/"):
                frontend_path = frontend_file_for(parsed.path)
                if frontend_path is None:
                    self.send_json({"error": "not found"}, 404)
                else:
                    self.send_file(frontend_path)
            elif parsed.path == "/api/init":
                self.send_json({
                    "markdown_files": list_markdown_files(),
                    "module_index": read_json(DEFAULT_MODULES_PATH, {}),
                    "topic_schema_mapping": read_json(DEFAULT_TOPIC_MAPPING_PATH, {}),
                })
            elif parsed.path == "/api/review-result":
                result = get_review_result()
                self.send_json(enrich_review_result(result))
            elif parsed.path == "/api/module-index":
                self.send_json(read_json(DEFAULT_MODULES_PATH, {}))
            elif parsed.path == "/api/topic-schema-mapping":
                self.send_json(read_json(DEFAULT_TOPIC_MAPPING_PATH, {}))
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            payload = self.read_payload()
            if parsed.path == "/api/review-run":
                self.handle_review_run(payload)
            elif parsed.path == "/api/build-run":
                self.handle_build_run(payload)
            elif parsed.path == "/api/review-decision":
                self.handle_review_decision(payload)
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_review_run(self, payload):
        source_path = safe_root_path(payload.get("source_path"), DEFAULT_CANDIDATES_PATH)
        review_mode = payload.get("review_mode", "manual")
        reviewer = payload.get("reviewer", "human")
        if review_mode not in {"auto", "manual", "hybrid"}:
            raise ValueError("review_mode 必须是 auto/manual/hybrid")
        result = build_review_from_candidates(source_path, review_mode, reviewer)
        self.send_json(enrich_review_result(result))

    def handle_build_run(self, payload):
        markdown_path = safe_root_path(payload.get("markdown_path"), "")
        if not markdown_path.exists() or markdown_path.suffix.lower() != ".md":
            raise ValueError("请选择项目内存在的 Markdown 文档")
        review_mode = payload.get("review_mode", "manual")
        reviewer = payload.get("reviewer", "human")
        run_extraction(markdown_path)
        result = build_review_from_candidates(DEFAULT_CANDIDATES_PATH, review_mode, reviewer)
        self.send_json(enrich_review_result(result))

    def handle_review_decision(self, payload):
        result = get_review_result()
        updated = apply_user_decision(
            result,
            candidate_id=payload.get("candidate_id", ""),
            user_decision=payload.get("decision", ""),
            reviewer=payload.get("reviewer", "human"),
            review_comment=payload.get("comment"),
            modifications=payload.get("modifications"),
        )
        write_json(DEFAULT_REVIEW_RESULT_PATH, updated)
        write_json(DEFAULT_REVIEW_LOG_PATH, updated.get("review_log", []))
        self.send_json(enrich_review_result(updated))

    def log_message(self, fmt, *args):
        return


def enrich_review_result(result):
    enriched = copy.deepcopy(result)
    for list_name in (
        "accepted_candidates",
        "rejected_candidates",
        "pending_review_candidates",
        "deferred_candidates",
    ):
        enriched[list_name] = [
            enrich_candidate_for_response(item)
            for item in enriched.get(list_name, [])
            if isinstance(item, dict)
        ]
    return enriched


def main():
    host = "127.0.0.1"
    port = int(os.environ.get("ONTOLOGY_BUILD_PORT", "8766"))
    server = ThreadingHTTPServer((host, port), OntologyBuildHandler)
    print(f"本体构建页面已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止服务")
    server.serve_forever()


if __name__ == "__main__":
    main()
