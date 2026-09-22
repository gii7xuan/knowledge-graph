import copy
import json
import os
import re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
REJECTED_DIR = ROOT / "rejected_triplets"
TRIPLETS_DIR = ROOT / "triplets"
REVIEW_LOG_DIR = ROOT / "review_logs"
EVIDENCE_DIR = ROOT / "evidence"
RUNS_V2_ACCEPTED_DIR = ROOT / "runs" / "v2" / "accepted"
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
    changed = False
    for index, item in enumerate(data):
        if isinstance(item, dict) and not item.get("review_id"):
            item["review_id"] = item_id(item, index)
            changed = True
    if changed:
        write_json(path, data)
    return data


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
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/decision":
            self.send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.handle_decision(payload)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

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
        annotation = payload.get("annotation") or {}
        if not isinstance(annotation, dict):
            annotation = {}
        annotation.setdefault("triple_correct", 1 if decision == "accept" else 0)
        annotation.setdefault("evidence_support", "full" if decision in {"accept", "modify"} else "none")
        annotation.setdefault("relation_mapping_correct", 1 if decision == "accept" else 0)
        if decision == "modify":
            annotation["triple_correct"] = 0
            annotation["relation_mapping_correct"] = 0
            annotation["corrected_triple"] = payload.get("modifications") or annotation.get("corrected_triple")
        else:
            annotation.setdefault("corrected_triple", None)

        if decision == "modify":
            modifications = payload.get("modifications") or {}
            for key in ["head", "relation", "tail"]:
                if key in modifications:
                    target[key] = str(modifications[key]).strip()
            target.pop("corrected_relation", None)

        target["review_annotation"] = annotation

        accepted_written = False
        if decision in {"accept", "modify"}:
            target["decision"] = "human_accept"
            accepted_written = append_unique_triplet(review_file, target)
            target["review_status"] = "accepted"
        elif decision == "reject":
            target["decision"] = "human_reject"
            target["review_status"] = "rejected"
        else:
            target["review_status"] = "deferred"

        target["review_decision"] = decision
        target["review_comment"] = payload.get("comment")
        target["review_time"] = now_iso()
        write_json(path, data)

        append_review_log(review_file, {
            "review_id": review_id,
            "decision": decision,
            "accepted_written": accepted_written,
            "comment": payload.get("comment"),
            "annotation": annotation,
            "time": target["review_time"],
            "original": original,
            "current": copy.deepcopy(target),
        })
        self.send_json({"ok": True, "accepted_written": accepted_written})

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
