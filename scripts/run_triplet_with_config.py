"""Run triple extraction with credentials loaded from the existing YAML file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (ROOT / path).resolve()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="使用 YAML 中的 Qwen 配置运行三元组抽取")
    parser.add_argument("--input", required=True)
    parser.add_argument("--ontology", required=True)
    parser.add_argument("--output-stem", required=True)
    parser.add_argument("--llm-config", required=True)
    parser.add_argument("--review-config", default="config/review_agent.json")
    parser.add_argument("--review-mode", default="shadow")
    parser.add_argument("--job-dir", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    input_path = resolve(args.input)
    ontology_path = resolve(args.ontology)
    llm_config_path = resolve(args.llm_config)
    review_config_path = resolve(args.review_config)
    job_dir = resolve(args.job_dir)
    status_path = job_dir / "status.json"
    state: dict[str, Any] = {
        "status": "starting",
        "started_at": now_iso(),
        "finished_at": None,
        "input": str(input_path),
        "ontology": str(ontology_path),
        "ontology_sha256": None,
        "output_stem": args.output_stem,
        "model": None,
        "errors": [],
    }
    write_json(status_path, state)
    try:
        for path, label in (
            (input_path, "输入文档"),
            (ontology_path, "本体快照"),
            (llm_config_path, "Qwen 配置"),
            (review_config_path, "审核配置"),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"找不到{label}: {path}")

        llm = (yaml.safe_load(llm_config_path.read_text(encoding="utf-8")) or {}).get("llm") or {}
        api_key = str(llm.get("api_key") or "").strip()
        base_url = str(llm.get("base_url") or "").strip()
        model = str(llm.get("model_name") or "Qwen").strip()
        if not api_key or not base_url:
            raise ValueError("Qwen 配置缺少 api_key 或 base_url")
        os.environ["OPENAI_API_KEY"] = api_key
        os.environ["OPENAI_BASE_URL"] = base_url
        os.environ["OPENAI_MODEL"] = model
        os.environ["TRIPLET_MODEL"] = model
        os.environ["TRIPLET_ONTOLOGY_FILE"] = str(ontology_path)
        if llm.get("timeout"):
            os.environ.setdefault("KG_LLM_TIMEOUT_SECONDS", str(llm["timeout"]))

        state["model"] = model
        state["ontology_sha256"] = hashlib.sha256(ontology_path.read_bytes()).hexdigest()
        state["status"] = "running"
        write_json(status_path, state)

        # Import only after configuring the environment because the module creates
        # its OpenAI-compatible client during import.
        from extract_triplets.extrct import main as extraction_main

        extraction_main(
            [
                "--input", str(input_path),
                "--ontology", str(ontology_path),
                "--output-stem", args.output_stem,
                "--config", str(review_config_path),
                "--review-mode", args.review_mode,
            ]
        )
        state["status"] = "succeeded"
        state["finished_at"] = now_iso()
        write_json(status_path, state)
        return 0
    except Exception as exc:
        state["status"] = "failed"
        state["finished_at"] = now_iso()
        state["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        write_json(status_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
