"""Best-effort progress snapshots; SQLite remains authoritative for review results."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4


def write_status_snapshot(path, progress, *, max_attempts=4, backoff_seconds=.02):
    """Retry atomic replacement when a Windows reader temporarily denies deletion."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(max(1, int(max_attempts))):
            try:
                os.replace(temporary, path)
                return
            except OSError:
                if attempt + 1 >= max(1, int(max_attempts)):
                    raise
                time.sleep(min(.1, max(0., backoff_seconds) * 2 ** attempt))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
