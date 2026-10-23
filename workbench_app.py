"""Single-process API and static host for the Vue review workbench."""

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
from uuid import uuid4
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import ontology_build_app as ontology
import review_triplets_app as triplets
from ontologyBuild.candidate_quality import approval_errors, describe_quality
from ontologyBuild.config import OntologyPipelineConfig, configured_base_url
from review_agent.repository import (
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from review_agent.service import ServiceUnavailableError, get_default_service
from scripts.freeze_ontology import freeze_ontology


ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend" / "workbench" / "dist"
MDFILES_DIR = ROOT / "mdfiles"
WORKBENCH_RUN_DIR = ROOT / "runs" / "workbench"
MODEL_CONFIG_PATH = ROOT / "config" / "workbench_models.json"
HOST = "127.0.0.1"
PORT = int(os.environ.get("WORKBENCH_PORT", "8770"))
PROXY_ENV_NAMES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)

triplet_lock = threading.Lock()
triplet_job = None
ontology_lock = threading.Lock()
ontology_job = {"exists": False, "running": False}
ontology_data_lock = threading.RLock()
model_config_lock = threading.RLock()
ontology_cache_key = None
ontology_cache_result = None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def rel_path(path):
    return str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")


def safe_markdown_path(value):
    if not value:
        raise ValueError("请选择 Markdown 文档")
    path = (ROOT / value).resolve()
    if MDFILES_DIR.resolve() not in path.parents or not path.is_file() or path.suffix.lower() != ".md":
        raise ValueError("请选择 mdfiles 目录内存在的 Markdown 文档")
    return path


def list_markdown_files():
    if not MDFILES_DIR.exists():
        return []
    return sorted(rel_path(path) for path in MDFILES_DIR.rglob("*.md"))


def sanitize_stem(value):
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip()).strip("._")
    return value or "triplets"


def default_stem(path):
    stem = Path(path).stem
    if stem.startswith("MinerU_markdown_"):
        stem = stem[len("MinerU_markdown_"):]
    return sanitize_stem(stem)


def json_count(path):
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return sum(len(value) for value in data.values() if isinstance(value, list))
    return 0


def read_log_tail(path, limit=30000):
    if not path.exists():
        return ""
    with path.open("rb") as file:
        file.seek(0, os.SEEK_END)
        file.seek(max(0, file.tell() - limit))
        return file.read().decode("utf-8", errors="replace")


def write_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def read_model_store():
    if not MODEL_CONFIG_PATH.exists():
        return []
    try:
        with MODEL_CONFIG_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("models", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def normalize_model_record(record):
    record_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(record.get("id") or "")).strip("._")
    return {
        "id": record_id or f"model_{uuid4().hex[:12]}",
        "name": str(record.get("name") or record.get("model") or "未命名模型").strip(),
        "base_url": str(record.get("base_url") or "").strip(),
        "api_key": str(record.get("api_key") or "").strip(),
        "model": str(record.get("model") or "").strip(),
        "created_at": str(record.get("created_at") or now_iso()),
    }


def default_model_record():
    config = OntologyPipelineConfig.from_env()
    return {
        "id": "default",
        "name": "当前默认配置",
        "base_url": configured_base_url(config),
        "api_key": config.api_key,
        "model": config.model,
        "created_at": "",
        "source": "default",
    }


def sanitize_model_record(record):
    ready = bool(record.get("api_key") and record.get("base_url") and record.get("model"))
    return {
        "id": record.get("id", ""),
        "name": record.get("name", ""),
        "base_url": record.get("base_url", ""),
        "model": record.get("model", ""),
        "source": record.get("source", "saved"),
        "created_at": record.get("created_at", ""),
        "has_api_key": bool(record.get("api_key")),
        "ready": ready,
    }


def list_model_configs():
    with model_config_lock:
        saved = [normalize_model_record(item) for item in read_model_store()]
    models = [sanitize_model_record(default_model_record())]
    models.extend(sanitize_model_record(item) for item in saved)
    return {"models": models}


def create_model_config(payload):
    name = str(payload.get("name") or payload.get("model") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()
    missing = []
    if not base_url:
        missing.append("url")
    if not api_key:
        missing.append("key")
    if not model:
        missing.append("模型名称")
    if missing:
        raise ValueError("模型配置缺少：" + "、".join(missing))
    if not re.match(r"^https?://", base_url, flags=re.I):
        raise ValueError("模型 URL 必须以 http:// 或 https:// 开头")
    record = normalize_model_record({
        "id": f"model_{uuid4().hex[:12]}",
        "name": name or model,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "created_at": now_iso(),
    })
    with model_config_lock:
        saved = [normalize_model_record(item) for item in read_model_store()]
        saved.append(record)
        write_json_atomic(MODEL_CONFIG_PATH, {"models": saved})
    return {"model": sanitize_model_record(record), "models": list_model_configs()["models"]}


def resolve_model_config(model_id):
    model_id = str(model_id or "default").strip() or "default"
    if model_id == "default":
        record = default_model_record()
    else:
        with model_config_lock:
            record = next(
                (item for item in (normalize_model_record(value) for value in read_model_store()) if item["id"] == model_id),
                None,
            )
        if record is None:
            raise ValueError("未找到选择的大模型配置")
    missing = []
    if not record.get("base_url"):
        missing.append("url")
    if not record.get("api_key"):
        missing.append("key")
    if not record.get("model"):
        missing.append("模型名称")
    if missing:
        raise ValueError("选择的大模型配置不完整：" + "、".join(missing))
    return record


def apply_model_env(env, model_config):
    for name in PROXY_ENV_NAMES:
        env.pop(name, None)
    env.update({
        "OPENAI_API_KEY": model_config["api_key"],
        "QWEN_API_KEY": model_config["api_key"],
        "OPENAI_BASE_URL": model_config["base_url"],
        "QWEN_BASE_URL": model_config["base_url"],
        "OPENAI_MODEL": model_config["model"],
        "QWEN_MODEL": model_config["model"],
        "ONTOLOGY_MODEL": model_config["model"],
        "TRIPLET_MODEL": model_config["model"],
    })


def triplet_status():
    with triplet_lock:
        job = triplet_job
    if job is None:
        return {"exists": False, "running": False}
    returncode = job["process"].poll()
    return {
        "exists": True,
        "running": returncode is None,
        "pid": job["process"].pid,
        "returncode": returncode,
        "started_at": job["started_at"],
        "markdown_path": rel_path(job["markdown_path"]),
        "output_stem": job["output_stem"],
        "run_id": job.get("run_id"),
        "audit_stage": "candidate_persistence" if returncode is None else "manual_review",
        "outputs": {key: rel_path(path) for key, path in job["outputs"].items()},
        "model": job.get("model", {}),
        "counts": {
            "accepted": json_count(job["outputs"]["accepted"]),
            "rejected": json_count(job["outputs"]["rejected"]),
        },
        "log_tail": read_log_tail(job["outputs"]["log"]),
    }


def start_triplet_extraction(payload):
    global triplet_job
    markdown_path = safe_markdown_path(payload.get("markdown_path"))
    output_stem = sanitize_stem(payload.get("output_stem") or default_stem(markdown_path))
    model_config = resolve_model_config(payload.get("model_id"))
    run_id = "run_" + now_stamp() + "_" + uuid4().hex[:8]
    outputs = {
        "accepted": ROOT / "triplets" / f"{output_stem}.json",
        "rejected": ROOT / "rejected_triplets" / f"{output_stem}_rejected_triplets.json",
        "evidence": ROOT / "evidence" / f"{output_stem}_evidence.md",
        "log": WORKBENCH_RUN_DIR / f"triplet_extract_{output_stem}_{now_stamp()}.log",
    }
    with triplet_lock:
        if triplet_job and triplet_job["process"].poll() is None:
            raise ValueError("三元组抽取任务正在运行")
        for path in outputs.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({
            "TRIPLET_MARKDOWN_FILE": str(markdown_path),
            "TRIPLET_OUTPUT_JSON": str(outputs["accepted"]),
            "TRIPLET_REJECTED_JSON": str(outputs["rejected"]),
            "TRIPLET_EVIDENCE_MD": str(outputs["evidence"]),
            "TRIPLET_BOOK_NAME": output_stem,
            "KG_RUN_ID": run_id,
            "KG_REVIEW_MODE": str(payload.get("review_mode") or "shadow"),
        })
        apply_model_env(env, model_config)
        with outputs["log"].open("w", encoding="utf-8", errors="replace") as log_file:
            process = subprocess.Popen(
                [sys.executable, "-u", "-m", "extract_triplets.extrct", "--review-mode",
                 str(payload.get("review_mode") or "shadow")],
                cwd=ROOT,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        triplet_job = {
            "process": process,
            "started_at": now_iso(),
            "markdown_path": markdown_path,
            "output_stem": output_stem,
            "run_id": run_id,
            "model": sanitize_model_record(model_config),
            "outputs": outputs,
        }
    return triplet_status()


def ontology_build_status():
    with ontology_lock:
        return ontology_job.copy()


def run_ontology_build(markdown_path, review_mode, reviewer, base_policy, model_config):
    global ontology_job
    try:
        manifest = ontology.run_extraction(
            markdown_path,
            review_mode=review_mode,
            base_policy=base_policy,
            model_config=model_config,
        )
        with ontology_data_lock:
            result = ontology.build_review_from_candidates(
                ontology.DEFAULT_CANDIDATES_PATH, review_mode, reviewer
            )
            result["run_id"] = manifest.get("run_id")
            result["run_status"] = manifest.get("status")
            result["run_dir"] = manifest.get("outputs", {}).get("run_dir")
            result["base_policy"] = manifest.get("config", {}).get("base_policy")
            result["model"] = sanitize_model_record(model_config)
            ontology.write_json(ontology.DEFAULT_REVIEW_RESULT_PATH, result)
            cache_ontology_result(result)
    except Exception as exc:
        with ontology_lock:
            ontology_job = {**ontology_job, "running": False, "error": str(exc), "finished_at": now_iso()}
    else:
        with ontology_lock:
            ontology_job = {
                **ontology_job,
                "running": False,
                "error": "",
                "finished_at": now_iso(),
                "run_id": manifest.get("run_id"),
                "run_status": manifest.get("status"),
                "run_dir": manifest.get("outputs", {}).get("run_dir"),
                "base_policy": manifest.get("config", {}).get("base_policy"),
                "model": sanitize_model_record(model_config),
            }


def start_ontology_build(payload):
    global ontology_job
    markdown_path = safe_markdown_path(payload.get("markdown_path"))
    review_mode = payload.get("review_mode", "manual")
    if review_mode not in {"manual", "hybrid", "auto"}:
        raise ValueError("review_mode 必须是 manual/hybrid/auto")
    base_policy = ontology.normalize_base_policy(payload.get("base_policy") or "draft")
    reviewer = str(payload.get("reviewer") or "human")
    model_config = resolve_model_config(payload.get("model_id"))
    with ontology_lock:
        if ontology_job.get("running"):
            raise ValueError("本体抽取任务正在运行")
        ontology_job = {
            "exists": True,
            "running": True,
            "error": "",
            "started_at": now_iso(),
            "markdown_path": rel_path(markdown_path),
            "review_mode": review_mode,
            "base_policy": base_policy,
            "model": sanitize_model_record(model_config),
        }
        threading.Thread(
            target=run_ontology_build,
            args=(markdown_path, review_mode, reviewer, base_policy, model_config),
            daemon=True,
            name="ontology-build",
        ).start()
        return ontology_job.copy()


def triplet_items_page(query):
    review_file = query.get("file", [""])[0]
    if review_file not in triplets.list_review_files():
        raise ValueError("非法或不存在的待审文件")
    relation = query.get("relation", [""])[0]
    min_confidence = triplets.parse_float_arg(query, "min_confidence")
    max_confidence = triplets.parse_float_arg(query, "max_confidence")
    include_done = query.get("include_done", ["0"])[0] == "1"
    page = max(1, int(query.get("page", ["1"])[0]))
    page_size = min(100, max(1, int(query.get("page_size", ["20"])[0])))
    filtered = []
    for item in triplets.load_review_items(review_file):
        if not isinstance(item, dict):
            continue
        if not include_done and triplets.review_status(item) in {"accepted", "rejected"}:
            continue
        confidence = triplets.confidence_value(item)
        if min_confidence is not None and confidence < min_confidence:
            continue
        if max_confidence is not None and confidence > max_confidence:
            continue
        if relation and triplets.item_relation(item) != relation:
            continue
        filtered.append(item)
    filtered.sort(key=lambda item: (triplets.item_relation(item), triplets.confidence_value(item)))
    start = (page - 1) * page_size
    return {
        "items": [triplets.item_for_response(review_file, item) for item in filtered[start:start + page_size]],
        "total": len(filtered),
        "page": page,
        "page_size": page_size,
    }


def cache_ontology_result(result):
    global ontology_cache_key, ontology_cache_result
    path = ontology.DEFAULT_REVIEW_RESULT_PATH
    stat = path.stat() if path.exists() else None
    ontology_cache_key = (stat.st_mtime_ns, stat.st_size) if stat else None
    ontology_cache_result = result


def cached_ontology_result():
    global ontology_cache_key, ontology_cache_result
    with ontology_data_lock:
        path = ontology.DEFAULT_REVIEW_RESULT_PATH
        stat = path.stat() if path.exists() else None
        key = (stat.st_mtime_ns, stat.st_size) if stat else None
        if ontology_cache_result is None or key != ontology_cache_key:
            ontology_cache_result = ontology.get_review_result()
            ontology_cache_key = key
        return ontology_cache_result


def ontology_overview(result=None):
    result = result if result is not None else cached_ontology_result()
    pending = result.get("pending_review_candidates", [])
    cleanup_count = sum(ontology_review_quality(item)["needs_cleanup"] for item in pending)
    summary = dict(result.get("summary", {}))
    summary["actionable_pending"] = len(pending) - cleanup_count
    summary["cleanup_pending"] = cleanup_count
    return {
        "summary": summary,
        "source_file": result.get("source_file", ""),
        "review_mode": result.get("review_mode", ""),
        "generated_at": result.get("generated_at", ""),
        "run_id": result.get("run_id", ""),
        "run_status": result.get("run_status", ""),
        "run_dir": result.get("run_dir", ""),
        "base_policy": result.get("base_policy", ""),
        "model": result.get("model", {}),
        "errors": result.get("errors", []),
    }


def ontology_items_page(query):
    status = query.get("status", ["pending_review_candidates"])[0]
    if status not in {
        "pending_review_candidates", "accepted_candidates",
        "rejected_candidates", "deferred_candidates", "cleanup_candidates",
    }:
        raise ValueError("无效的审核状态")
    keyword = query.get("keyword", [""])[0].strip().lower()
    page = max(1, int(query.get("page", ["1"])[0]))
    page_size = min(100, max(1, int(query.get("page_size", ["20"])[0])))
    result = cached_ontology_result()
    if status in {"pending_review_candidates", "cleanup_candidates"}:
        needs_cleanup = status == "cleanup_candidates"
        items = [
            item for item in result.get("pending_review_candidates", [])
            if ontology_review_quality(item)["needs_cleanup"] == needs_cleanup
        ]
    else:
        items = result.get(status, [])
    if keyword:
        items = [item for item in items if keyword in json.dumps(item, ensure_ascii=False).lower()]
    start = (page - 1) * page_size
    return {
        "items": [enrich_ontology_item(item) for item in items[start:start + page_size]],
        "total": len(items),
        "page": page,
        "page_size": page_size,
    }


def ontology_review_quality(item):
    quality = describe_quality(item)
    quality["approval_errors"] = approval_errors(item)
    quality["needs_cleanup"] = quality["needs_cleanup"] or bool(quality["approval_errors"])
    return quality


def enrich_ontology_item(item):
    enriched = ontology.enrich_candidate_for_response(item)
    enriched["review_quality"] = ontology_review_quality(item)
    return enriched


def run_ontology_review(payload):
    source_path = ontology.safe_root_path(payload.get("source_path"), ontology.DEFAULT_CANDIDATES_PATH)
    review_mode = payload.get("review_mode", "manual")
    if review_mode not in {"manual", "hybrid", "auto"}:
        raise ValueError("review_mode 必须是 manual/hybrid/auto")
    with ontology_data_lock:
        result = ontology.build_review_from_candidates(
            source_path, review_mode, payload.get("reviewer", "human")
        )
        cache_ontology_result(result)
        return ontology_overview(result)


def decide_ontology(payload):
    with ontology_data_lock:
        current = cached_ontology_result()
        candidate_id = payload.get("candidate_id", "")
        decision = payload.get("decision", "")
        if decision not in {"accept", "modify", "reject", "defer"}:
            raise ValueError("无效的审核决定")
        candidate = next(
            (item for key in (
                "pending_review_candidates", "accepted_candidates",
                "rejected_candidates", "deferred_candidates",
            ) for item in current.get(key, []) if item.get("candidate_id") == candidate_id),
            None,
        )
        if candidate is None:
            raise ValueError("未找到待审核候选项")
        if decision in {"accept", "modify"}:
            modifications = payload.get("modifications")
            if decision == "modify" and (not isinstance(modifications, dict) or not modifications):
                raise ValueError("修改后通过需要提交字段修改")
            effective = {**candidate, **(modifications or {})} if decision == "modify" else candidate
            errors = approval_errors(effective)
            if errors:
                raise ValueError("候选字段不完整：" + "；".join(errors))
        updated = ontology.apply_user_decision(
            current,
            candidate_id=candidate_id,
            user_decision=decision,
            reviewer=payload.get("reviewer", "human"),
            review_comment=payload.get("comment"),
            modifications=payload.get("modifications"),
        )
        if len(updated.get("errors", [])) > len(current.get("errors", [])):
            raise ValueError(updated["errors"][-1].get("message", "审核失败"))
        ontology.write_json(ontology.DEFAULT_REVIEW_RESULT_PATH, updated)
        ontology.write_json(ontology.DEFAULT_REVIEW_LOG_PATH, updated.get("review_log", []))
        cache_ontology_result(updated)
        return {"ok": True}


def frontend_file_for(request_path):
    if request_path in {"/", "/ontology", "/triplet-extract", "/triplet-review"}:
        path = FRONTEND_DIR / "index.html"
    elif request_path.startswith("/assets/"):
        path = FRONTEND_DIR / unquote(request_path).lstrip("/")
    else:
        return None
    path = path.resolve()
    if FRONTEND_DIR.resolve() not in [path, *path.parents] or not path.is_file():
        return None
    return path


class WorkbenchHandler(triplets.ReviewHandler, ontology.OntologyBuildHandler):
    """Serve both existing business APIs under one origin."""

    def send_file(self, path):
        path = Path(path)
        body = path.read_bytes()
        content_type = {
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".woff": "font/woff",
            ".woff2": "font/woff2",
            ".ttf": "font/ttf",
        }.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        try:
            if path == "/api/triplets/runs":
                self.send_json(get_default_service().runs(
                    int(query.get("page", ["1"])[0]), int(query.get("page_size", ["20"])[0])
                ))
            elif path == "/api/triplets/config-status":
                self.send_json(get_default_service().config_status())
            elif path == "/api/triplets/items" and query.get("run_id", [""])[0]:
                self.send_json(get_default_service().items(
                    query["run_id"][0], status=query.get("status", [None])[0],
                    relation_id=query.get("relation_id", [None])[0],
                    page=int(query.get("page", ["1"])[0]),
                    page_size=int(query.get("page_size", ["20"])[0]),
                ))
            elif path == "/api/triplets/summary" and query.get("run_id", [""])[0]:
                self.send_json(get_default_service().summary(query["run_id"][0]))
            elif re.fullmatch(r"/api/triplets/audit-jobs/[^/]+", path):
                self.send_json(get_default_service().job(path.rsplit("/", 1)[-1]))
            elif re.fullmatch(r"/api/triplets/candidates/[^/]+/audit", path):
                candidate_id = path.split("/")[-2]
                self.send_json(get_default_service().audit(candidate_id, query.get("audit_id", [None])[0]))
            elif re.fullmatch(r"/api/triplets/candidates/[^/]+", path):
                candidate_id = path.rsplit("/", 1)[-1]
                revision = query.get("revision", [None])[0]
                self.send_json(get_default_service().candidate(
                    candidate_id, int(revision) if revision is not None else None
                ))
            elif re.fullmatch(r"/api/triplets/evidence/[^/]+/context", path):
                evidence_id = path.split("/")[-2]
                self.send_json(get_default_service().evidence_context(
                    evidence_id, before=int(query.get("before", ["1"])[0]),
                    after=int(query.get("after", ["1"])[0]),
                    audit_id=query.get("audit_id", [None])[0],
                    round_id=int(query["round_id"][0]) if query.get("round_id") else None,
                ))
            elif path == "/api/triplets/items":
                self.send_json(triplet_items_page(query))
            elif path == "/api/ontology/overview":
                self.send_json(ontology_overview())
            elif path == "/api/ontology/items":
                self.send_json(ontology_items_page(parse_qs(urlparse(self.path).query)))
            elif path.startswith("/api/triplets/"):
                self.dispatch_legacy(triplets.ReviewHandler.do_GET, "/api/triplets")
            elif path.startswith("/api/ontology/") and path != "/api/ontology/build-status":
                self.dispatch_legacy(ontology.OntologyBuildHandler.do_GET, "/api/ontology")
            elif path == "/api/init":
                self.send_json({"markdown_files": list_markdown_files(), "model_configs": list_model_configs()["models"]})
            elif path == "/api/models":
                self.send_json(list_model_configs())
            elif path == "/api/triplet/status":
                self.send_json(triplet_status())
            elif path == "/api/ontology/build-status":
                self.send_json(ontology_build_status())
            else:
                frontend_path = frontend_file_for(path)
                if frontend_path:
                    self.send_file(frontend_path)
                elif path == "/" and not FRONTEND_DIR.exists():
                    self.send_json({"error": "前端未构建，请在 frontend/workbench 中运行 npm install 和 npm run build"}, 503)
                else:
                    self.send_json({"error": "not found"}, 404)
        except NotFoundError as exc:
            self.send_json({"error": str(exc)}, 404)
        except ConflictError as exc:
            self.send_json({"error": str(exc), "reason_codes": ["VERSION_CONFLICT"]}, 409)
        except ServiceUnavailableError as exc:
            self.send_json({"error": str(exc), "reason_codes": ["MODEL_MODE_UNAVAILABLE"]}, 503)
        except (ValueError, ValidationError, FileNotFoundError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/triplets/audit-jobs":
                self.send_json(get_default_service().create_audit_job(self.read_payload()), 202)
            elif re.fullmatch(r"/api/triplets/audit-jobs/[^/]+/cancel", path):
                self.send_json(get_default_service().cancel_job(path.split("/")[-2]))
            elif re.fullmatch(r"/api/triplets/candidates/[^/]+/reaudit", path):
                payload = self.read_payload()
                self.send_json(get_default_service().create_reaudit_job(
                    path.split("/")[-2], payload
                ), 202)
            elif path == "/api/triplets/decision":
                payload = self.read_payload()
                if payload.get("file"):
                    original_path = self.path
                    try:
                        self.path = "/api/decision"
                        self.handle_decision(payload)
                    finally:
                        self.path = original_path
                else:
                    self.send_json(get_default_service().decide(payload))
            elif path.startswith("/api/triplets/"):
                self.dispatch_legacy(triplets.ReviewHandler.do_POST, "/api/triplets")
            elif path == "/api/ontology/review-run":
                self.send_json(run_ontology_review(self.read_payload()))
            elif path == "/api/ontology/freeze":
                payload = self.read_payload()
                ontology_path = ontology.safe_root_path(
                    payload.get("ontology_path"), ROOT / "ontology" / "ontology.json"
                )
                result = freeze_ontology(
                    ontology_path, payload.get("output_root"),
                    approved_event_id=payload.get("approved_event_id"),
                )
                self.send_json(result, 201 if result.get("ok") else 422)
            elif path == "/api/ontology/review-decision":
                self.send_json(decide_ontology(self.read_payload()))
            elif path.startswith("/api/ontology/") and path != "/api/ontology/build-run":
                self.dispatch_legacy(ontology.OntologyBuildHandler.do_POST, "/api/ontology")
            elif path == "/api/triplet/start":
                self.send_json(start_triplet_extraction(self.read_payload()))
            elif path == "/api/ontology/build-run":
                self.send_json(start_ontology_build(self.read_payload()), 202)
            elif path == "/api/models":
                self.send_json(create_model_config(self.read_payload()), 201)
            else:
                self.send_json({"error": "not found"}, 404)
        except NotFoundError as exc:
            self.send_json({"error": str(exc)}, 404)
        except ConflictError as exc:
            self.send_json({"error": str(exc), "reason_codes": ["VERSION_CONFLICT"]}, 409)
        except ServiceUnavailableError as exc:
            self.send_json({"error": str(exc), "reason_codes": ["MODEL_MODE_UNAVAILABLE"]}, 503)
        except UnprocessableEntityError as exc:
            self.send_json({"error": str(exc), "reason_codes": ["INVALID_FEEDBACK"]}, 422)
        except (ValueError, ValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def dispatch_legacy(self, handler, prefix):
        original_path = self.path
        try:
            self.path = "/api" + original_path[len(prefix):]
            handler(self)
        finally:
            self.path = original_path


def main():
    server = ThreadingHTTPServer((HOST, PORT), WorkbenchHandler)
    server.daemon_threads = True
    print(f"知识图谱审核工作台: http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
