"""Resolve review slots without copying credentials into review configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def validator_settings(slot, *, root=ROOT):
    connection = {}
    source = slot.get("connection_config")
    if source:
        path = Path(source)
        path = path if path.is_absolute() else Path(root) / path
        if path.suffix.lower() == ".json":
            registry = json.loads(path.read_text(encoding="utf-8-sig"))
            model_id = slot.get("connection_model_id")
            if not model_id:
                raise ValueError(f"{slot['id']}: JSON connection registry requires connection_model_id")
            matches = [item for item in registry.get("models", []) if item.get("id") == model_id]
            if len(matches) != 1:
                raise ValueError(f"{slot['id']}: connection_model_id must identify exactly one saved model")
            connection = matches[0]
        else:
            import yaml

            connection = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("llm") or {}

    def env(name):
        return os.environ.get(str(slot.get(name + "_env") or ""), "").strip()

    source_url = str(connection.get("base_url") or "").strip()
    base_url = env("base_url") or source_url
    key = env("api_key")
    if source_url and base_url.rstrip("/") != source_url.rstrip("/") and not key:
        # Never send a saved provider's key to a different environment-supplied URL.
        raise ValueError(f"{slot['id']}: overriding base URL requires an explicit API key")
    verify_ssl = slot.get("verify_ssl") if base_url.rstrip("/") == source_url.rstrip("/") else None
    if verify_ssl is not None and not isinstance(verify_ssl, bool):
        raise ValueError(f"{slot['id']}: verify_ssl must be boolean")
    return {
        "id": slot["id"],
        "model": env("model") or str(slot.get("model") or "").strip(),
        "family": env("family") or str(slot.get("family") or "").strip(),
        "api_key": key or str(connection.get("api_key") or "").strip(),
        "base_url": base_url,
        "verify_ssl": verify_ssl,
    }


def configured_planner(config):
    """Same explicit connection resolution, separate role and model identity."""
    slot = {"id": "planner", **(config.get("planner") or {})}
    return validator_settings(slot)


def create_planner_client(config):
    from .llm_clients import OpenAICompatibleClient
    settings = configured_planner(config)
    if not (config.get("planner") or {}).get("enabled", False):
        return None
    if not all(settings[k] for k in ("model", "family", "api_key", "base_url")):
        return None
    return OpenAICompatibleClient(api_key=settings["api_key"], base_url=settings["base_url"],
        model_id=settings["model"], model_family=settings["family"], verify_ssl=settings["verify_ssl"],
        timeout_seconds=float((config.get("budget") or {}).get("per_call_timeout_seconds", 45)))
