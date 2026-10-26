"""Conservative document-local entity identity; no fuzzy cross-document merging."""

import hashlib
import json


def local_entity_id(document_id, document_version, surface):
    # Case is significant for symbols and engineering names.
    payload = [document_id, document_version, str(surface).strip()]
    return "local_" + hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()
