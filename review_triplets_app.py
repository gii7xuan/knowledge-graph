import copy
import json
import os
import re
import hashlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from review_agent.contracts import candidate_id_for, fact_key_for
from review_agent.feedback import FeedbackService
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.repository import (
    ConflictError,
    NotFoundError,
    ReviewRepository,
    UnprocessableEntityError,
    ValidationError,
)
from review_agent.service import ServiceUnavailableError, get_default_service


ROOT = Path(__file__).resolve().parent
REJECTED_DIR = ROOT / "rejected_triplets"
TRIPLETS_DIR = ROOT / "triplets"
REVIEW_LOG_DIR = ROOT / "review_logs"
EVIDENCE_DIR = ROOT / "evidence"
RUNS_V2_ACCEPTED_DIR = ROOT / "runs" / "v2" / "accepted"
V3_DATABASE_PATH = ROOT / "runs" / "v3" / "review.sqlite3"
EVIDENCE_CACHE = {}


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_json(path, default):
    if not path.exists():
        return copy.deepcopy(default)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def list_review_files():
    if not REJECTED_DIR.exists():
        return []
    return sorted(p.name for p in REJECTED_DIR.glob("*.json"))


def target_triplets_path(review_file):
    name = review_file
    suffix = "_rejected_triplets.json"
    if name.endswith(suffix):
        return TRIPLETS_DIR / f"{name[:-len(suffix)]}.json"
    return TRIPLETS_DIR / name


def evidence_path(review_file):
    name = review_file
    suffix = "_rejected_triplets.json"
    if name.endswith(suffix):
        return EVIDENCE_DIR / f"{name[:-len(suffix)]}_evidence.md"
    return EVIDENCE_DIR / f"{Path(name).stem}_evidence.md"


def parse_evidence_chunks(path):
    if not path.exists():
        return {}
    cache_key = (str(path), path.stat().st_mtime)
    if cache_key in EVIDENCE_CACHE:
        return EVIDENCE_CACHE[cache_key]

    text = path.read_text(encoding="utf-8")
    chunks = {}
    pattern = re.compile(r"^## ID:\s*(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for index, match in enumerate(matches):
        source_id = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content.endswith("---"):
            content = content[:-3].strip()
        chunks[source_id] = content

    EVIDENCE_CACHE.clear()
    EVIDENCE_CACHE[cache_key] = chunks
    return chunks


def evidence_chunk_for(review_file, source_id):
    if not source_id:
        return ""
    chunks = parse_evidence_chunks(evidence_path(review_file))
    return chunks.get(source_id, "")


def item_id(item, index):
    return item.get("review_id") or f"{item.get('source_id', 'source')}::{index}"


def confidence_value(item):
    value = item.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        matched = re.search(r"-?\d+(?:\.\d+)?", value)
        if matched:
            return float(matched.group(0))
    return 0.0


def item_for_response(review_file, item):
    result = copy.deepcopy(item)
    confidence = confidence_value(item)
    result["_confidence"] = confidence
    result["confidence_display"] = f"{confidence:.4f}"
    result["evidence_chunk"] = evidence_chunk_for(review_file, item.get("source_id", ""))
    return result


def item_relation(item):
    return item.get("corrected_relation") or item.get("relation") or "未命名关系"


def review_status(item):
    return item.get("review_status", "pending")


def load_review_items(review_file):
    path = REJECTED_DIR / review_file
    data = read_json(path, [])
    if not isinstance(data, list):
        raise ValueError("待审文件必须是 JSON 数组")
    for index, item in enumerate(data):
        if isinstance(item, dict) and not item.get("review_id"):
            item["review_id"] = item_id(item, index)
    return data


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_v3_candidate(review_file, item):
    """Migrate one legacy UI item on demand without treating old status as a v3 decision."""
    source_path = REJECTED_DIR / review_file
    source_hash = _file_hash(source_path)
    identity_hash = hashlib.sha256(review_file.encode("utf-8")).hexdigest()
    run_id = f"legacy_ui_{identity_hash[:16]}"
    document_hash = _file_hash(evidence_path(review_file)) if evidence_path(review_file).exists() else source_hash
    repository = ReviewRepository(V3_DATABASE_PATH)
    repository.initialize()
    try:
        repository.get_run(run_id)
    except NotFoundError:
        ontology_path = ROOT / "ontology" / "ontology.json"
        ontology_hash = load_snapshot(ontology_path).ontology_version if ontology_path.exists() else "legacy_unknown"
        repository.create_run({
            "run_id": run_id, "schema_version": "triple_review_v3", "mode": "legacy",
            "source_hash": document_hash, "ontology_hash": ontology_hash,
            "source_path": str(source_path.relative_to(ROOT)).replace("\\", "/"),
        })
    review_id = str(item.get("review_id") or "")
    candidate_id = "cand_legacy_" + hashlib.sha256(
        f"{run_id}\n{review_id}".encode("utf-8")
    ).hexdigest()[:20]
    try:
        return repository, repository.get_candidate(candidate_id)
    except NotFoundError:
        ontology_path = ROOT / "ontology" / "ontology.json"
        ontology_hash = load_snapshot(ontology_path).ontology_version if ontology_path.exists() else "legacy_unknown"
        quote = item.get("evidence_quote") or item.get("evidence") or evidence_chunk_for(
            review_file, item.get("source_id", "")
        )
        triple = {
            "head": item.get("head"),
            "surface_relation": item.get("surface_relation") or item.get("relation"),
            "relation_id": item.get("relation_id") or item.get("corrected_relation") or item.get("relation"),
            "relation_label": item.get("corrected_relation") or item.get("relation"),
            "tail": item.get("tail"), "tail_kind": item.get("tail_kind", "entity"),
            "qualifiers": item.get("qualifiers") or {"polarity": item.get("polarity", "positive")},
            "head_type": item.get("head_type"), "tail_type": item.get("tail_type"),
        }
        candidate = {
            "schema_version": "triple_review_v3", "run_id": run_id,
            "candidate_id": candidate_id, "revision": 1,
            "document_id": str(item.get("document_id") or Path(review_file).stem),
            "document_version": document_hash, "source_chunk_id": item.get("source_id"),
            "section_id": item.get("section_id"), "ontology_version": ontology_hash,
            "domain": item.get("domain") or "firearm_artillery",
            "fact_namespace": item.get("fact_namespace") or item.get("domain") or "firearm_artillery",
            "triple": triple, "extraction_quote": quote,
            "extraction_model": item.get("extraction_model", "legacy_unknown"),
            "extraction_prompt_version": item.get("extraction_prompt_version", "legacy_unknown"),
        }
        candidate["fact_key"] = fact_key_for(candidate)
        return repository, repository.add_candidate(candidate)


def normalize_triplet(item):
    result = copy.deepcopy(item)
    if result.get("corrected_relation"):
        result["original_relation"] = result.get("relation")
        result["relation"] = result["corrected_relation"]
    for key in ["discard_reason", "needs_review", "corrected_relation", "review_status"]:
        result.pop(key, None)
    result["review_decision"] = "accepted"
    result["decision"] = "human_accept"
    result["review_time"] = now_iso()
    return result


def append_unique_triplet(review_file, item):
    path = target_triplets_path(review_file)
    triplets = read_json(path, [])
    if not isinstance(triplets, list):
        raise ValueError(f"{path.name} 必须是 JSON 数组")

    candidate = normalize_triplet(item)
    legacy_written = append_unique_to_path(path, candidate)

    v2_path = RUNS_V2_ACCEPTED_DIR / target_triplets_path(review_file).name
    append_unique_to_path(v2_path, candidate)
    return legacy_written


def append_unique_to_path(path, candidate):
    triplets = read_json(path, [])
    if not isinstance(triplets, list):
        raise ValueError(f"{path.name} 必须是 JSON 数组")
    key = (
        candidate.get("head", ""),
        candidate.get("relation", ""),
        candidate.get("tail", ""),
        candidate.get("source_id", ""),
    )
    for existing in triplets:
        if (
            existing.get("head", ""),
            existing.get("relation", ""),
            existing.get("tail", ""),
            existing.get("source_id", ""),
        ) == key:
            return False
    triplets.append(candidate)
    write_json(path, triplets)
    return True


def append_review_log(review_file, log_item):
    path = REVIEW_LOG_DIR / f"{Path(review_file).stem}_review_log.json"
    logs = read_json(path, [])
    if not isinstance(logs, list):
        logs = []
    logs.append(log_item)
    write_json(path, logs)


def build_summary(review_file, min_confidence=None, max_confidence=None, include_done=False):
    items = load_review_items(review_file)
    groups = {}
    total = pending = accepted = rejected = deferred = 0
    filtered = []

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        total += 1
        status = review_status(item)
        if status == "accepted":
            accepted += 1
        elif status == "rejected":
            rejected += 1
        elif status == "deferred":
            deferred += 1
        else:
            pending += 1

        if not include_done and status in {"accepted", "rejected"}:
            continue
        conf = confidence_value(item)
        if min_confidence is not None and conf < min_confidence:
            continue
        if max_confidence is not None and conf > max_confidence:
            continue
        relation = item_relation(item)
        groups.setdefault(relation, 0)
        groups[relation] += 1
        filtered.append(index)

    return {
        "file": review_file,
        "total": total,
        "pending": pending,
        "accepted": accepted,
        "rejected": rejected,
        "deferred": deferred,
        "filtered": len(filtered),
        "groups": [{"relation": name, "count": count} for name, count in sorted(groups.items())],
    }


def parse_float_arg(query, name):
    value = query.get(name, [""])[0].strip()
    if not value:
        return None
    return float(value)


FRONTEND_DIR = ROOT / "frontend" / "triplet_review"

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



class ReviewHandler(BaseHTTPRequestHandler):
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
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.send_html()
            elif parsed.path.startswith("/static/"):
                frontend_path = frontend_file_for(parsed.path)
                if frontend_path is None:
                    self.send_json({"error": "not found"}, 404)
                else:
                    self.send_file(frontend_path)
            elif parsed.path == "/api/files":
                self.send_json(list_review_files())
            elif parsed.path == "/api/runs":
                self.send_json(get_default_service().runs(
                    int(query.get("page", ["1"])[0]), int(query.get("page_size", ["20"])[0])
                ))
            elif parsed.path == "/api/config-status":
                self.send_json(get_default_service().config_status())
            elif parsed.path == "/api/summary" and query.get("run_id", [""])[0]:
                self.send_json(get_default_service().summary(query["run_id"][0]))
            elif parsed.path == "/api/items" and query.get("run_id", [""])[0]:
                self.send_json(get_default_service().items(
                    query["run_id"][0], status=query.get("status", [None])[0],
                    relation_id=query.get("relation_id", [None])[0],
                    page=int(query.get("page", ["1"])[0]),
                    page_size=int(query.get("page_size", ["20"])[0]),
                ))
            elif re.fullmatch(r"/api/audit-jobs/[^/]+", parsed.path):
                self.send_json(get_default_service().job(parsed.path.rsplit("/", 1)[-1]))
            elif re.fullmatch(r"/api/candidates/[^/]+/audit", parsed.path):
                candidate_id = parsed.path.split("/")[-2]
                self.send_json(get_default_service().audit(candidate_id, query.get("audit_id", [None])[0]))
            elif re.fullmatch(r"/api/candidates/[^/]+", parsed.path):
                candidate_id = parsed.path.rsplit("/", 1)[-1]
                revision = query.get("revision", [None])[0]
                self.send_json(get_default_service().candidate(
                    candidate_id, int(revision) if revision is not None else None
                ))
            elif re.fullmatch(r"/api/evidence/[^/]+/context", parsed.path):
                evidence_id = parsed.path.split("/")[-2]
                self.send_json(get_default_service().evidence_context(
                    evidence_id, before=int(query.get("before", ["1"])[0]),
                    after=int(query.get("after", ["1"])[0]),
                    audit_id=query.get("audit_id", [None])[0],
                    round_id=int(query["round_id"][0]) if query.get("round_id") else None,
                ))
            elif parsed.path == "/api/summary":
                review_file = query.get("file", [""])[0]
                self.ensure_safe_file(review_file)
                self.send_json(build_summary(
                    review_file,
                    min_confidence=parse_float_arg(query, "min_confidence"),
                    max_confidence=parse_float_arg(query, "max_confidence"),
                    include_done=query.get("include_done", ["0"])[0] == "1",
                ))
            elif parsed.path == "/api/items":
                self.handle_items(query)
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
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/audit-jobs":
                self.send_json(get_default_service().create_audit_job(self.read_payload()), 202)
            elif re.fullmatch(r"/api/audit-jobs/[^/]+/cancel", parsed.path):
                self.send_json(get_default_service().cancel_job(parsed.path.split("/")[-2]))
            elif re.fullmatch(r"/api/candidates/[^/]+/reaudit", parsed.path):
                payload = self.read_payload()
                self.send_json(get_default_service().create_reaudit_job(
                    parsed.path.split("/")[-2], payload
                ), 202)
            elif parsed.path == "/api/decision":
                payload = self.read_payload()
                if payload.get("file"):
                    self.handle_decision(payload)
                else:
                    self.send_json(get_default_service().decide(payload))
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

    def handle_items(self, query):
        review_file = query.get("file", [""])[0]
        self.ensure_safe_file(review_file)
        relation = query.get("relation", [""])[0]
        min_confidence = parse_float_arg(query, "min_confidence")
        max_confidence = parse_float_arg(query, "max_confidence")
        include_done = query.get("include_done", ["0"])[0] == "1"

        result = []
        for item in load_review_items(review_file):
            if not isinstance(item, dict):
                continue
            if not include_done and review_status(item) in {"accepted", "rejected"}:
                continue
            conf = confidence_value(item)
            if min_confidence is not None and conf < min_confidence:
                continue
            if max_confidence is not None and conf > max_confidence:
                continue
            if relation and item_relation(item) != relation:
                continue
            result.append(item_for_response(review_file, item))
        result.sort(key=lambda x: (item_relation(x), x.get("_confidence", 0.0)))
        self.send_json({"items": result})

    def handle_decision(self, payload):
        review_file = payload.get("file", "")
        self.ensure_safe_file(review_file)
        review_id = payload.get("review_id", "")
        decision = payload.get("decision", "")
        if decision not in {"accept", "reject", "modify", "defer"}:
            raise ValueError("decision 必须是 accept/reject/modify/defer")

        path = REJECTED_DIR / review_file
        data = load_review_items(review_file)
        for item in data:
            if isinstance(item, dict) and item.get("review_id") == review_id:
                target = item
                break
        else:
            raise ValueError("未找到对应待审三元组")

        original = copy.deepcopy(target)
        repository, candidate = ensure_v3_candidate(review_file, target)
        annotation = payload.get("annotation") if isinstance(payload.get("annotation"), dict) else {}
        labels = {
            "valid_for_kg": annotation.get("valid_for_kg", annotation.get("triple_correct")),
            "source_support": annotation.get("source_support", annotation.get("evidence_support")),
            "relation_mapping_correct": annotation.get("relation_mapping_correct"),
            "schema_compatible": annotation.get("schema_compatible"),
        }
        modifications = payload.get("modifications") or None
        if decision == "modify" and modifications:
            modifications = {"triple": {
                "head": str(modifications.get("head", target.get("head", ""))).strip(),
                "surface_relation": str(modifications.get("relation", target.get("relation", ""))).strip(),
                "relation_id": modifications.get("relation_id"),
                "relation_label": str(modifications.get("relation", target.get("relation", ""))).strip(),
                "tail": str(modifications.get("tail", target.get("tail", ""))).strip(),
            }}
        request = {
            "run_id": candidate["run_id"], "candidate_id": candidate["candidate_id"],
            "revision": candidate["revision"], "expected_row_version": candidate["row_version"],
            "idempotency_key": payload.get("idempotency_key") or f"legacy-ui-{uuid4().hex}",
            "action": decision, "reason_code": payload.get("reason_code"),
            "labels": labels, "comment": payload.get("comment"),
            "evidence_ids": payload.get("evidence_ids") or [], "modifications": modifications,
            "reviewer": "local_operator",
            "active_review_seconds": payload.get("active_review_seconds"),
        }
        ontology_path = ROOT / "ontology" / "ontology.json"
        guard = OntologyGuard(load_snapshot(ontology_path, candidate.get("ontology_version"))) if ontology_path.exists() else None
        result = FeedbackService(repository, ontology_guard=guard).submit(request)

        # Keep the legacy file as a rebuildable view; it no longer grants import eligibility.
        target["v3_candidate_id"] = candidate["candidate_id"]
        target["v3_event_id"] = result["event"]["event_id"]
        target["review_status"] = result["candidate"]["human_status"]
        target["review_decision"] = decision
        target["review_comment"] = payload.get("comment")
        target["review_time"] = now_iso()
        target["review_annotation"] = result["event"]["labels"]
        if decision == "modify":
            target["proposed_revision"] = result["candidate"]["revision"]
        write_json(path, data)
        self.send_json({"ok": True, "accepted_written": False, **result})

    def ensure_safe_file(self, review_file):
        if not review_file or Path(review_file).name != review_file or review_file not in list_review_files():
            raise ValueError("非法或不存在的待审文件")

    def log_message(self, fmt, *args):
        return


def main():
    host = "127.0.0.1"
    port = int(os.environ.get("TRIPLET_REVIEW_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), ReviewHandler)
    print(f"三元组审核界面已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止服务")
    server.serve_forever()


if __name__ == "__main__":
    main()
