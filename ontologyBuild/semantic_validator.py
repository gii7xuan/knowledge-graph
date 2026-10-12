"""Semantic support validation interface.

The first implementation records explicit not_run/unavailable states. It keeps
the pipeline honest: a missing semantic validator is visible and cannot be
mistaken for a passed check.
"""

from __future__ import annotations

from typing import Any


def semantic_support_not_run(reason: str = "未配置语义核验客户端") -> dict[str, Any]:
    return {
        "semantic_support_status": "not_run",
        "semantic_support_passed": None,
        "semantic_support_reason": reason,
    }


def attach_semantic_status(candidates: dict[str, Any], reason: str = "未配置语义核验客户端") -> dict[str, Any]:
    status = semantic_support_not_run(reason)
    for values in candidates.values():
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                item.update(status)
    return candidates
