"""Immutable UTF-8 source and canonical text identities; offsets are code points."""

import hashlib
import json
from pathlib import Path

NORMALIZER_VERSION = "canonical_text_v1"


def json_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def canonicalize(text):
    # Preserve links, formulae, fences, whitespace and wording. Only transport
    # markers and newline conventions change; never claim PDF character offsets.
    return str(text).removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def make_snapshot(text, document_id, *, raw_bytes=None):
    raw = raw_bytes if raw_bytes is not None else str(text).encode("utf-8")
    raw_text = raw.decode("utf-8")
    canonical = canonicalize(raw_text)
    identity = {"document_id": document_id, "document_version": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                "raw_file_hash": hashlib.sha256(raw).hexdigest(), "normalizer_version": NORMALIZER_VERSION,
                "offset_unit": "unicode_code_point", "span_convention": "[start,end)",
                "pdf_character_mapping": "unavailable"}
    return {**identity, "snapshot_id": json_hash(identity), "raw_text": raw_text, "canonical_text": canonical}


def read_snapshot(path, document_id=None):
    from .hierarchy_index import normalize_document_id
    path = Path(path)
    raw = path.read_bytes()
    return make_snapshot("", document_id or normalize_document_id(path.name), raw_bytes=raw)


def validate_snapshot(snapshot):
    if snapshot.get("normalizer_version") != NORMALIZER_VERSION:
        raise ValueError("unsupported source normalizer version")
    rebuilt = make_snapshot(snapshot["raw_text"], snapshot["document_id"])
    for key, expected in rebuilt.items():
        if snapshot.get(key) != expected:
            raise ValueError(f"source snapshot mismatch: {key}")
    return True
