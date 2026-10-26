"""Extract with an explicitly published release, or build legacy candidates for review."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ontologyBuild.config import OntologyPipelineConfig
from ontologyBuild.pipeline import run_ontology_pipeline
from ontologyBuild.release_catalog import ReleaseCatalog, select_release


ROOT = Path(__file__).resolve().parents[1]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_llm_config(path: Path) -> None:
    """Load the existing Qwen YAML without putting credentials on the command line."""
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    llm = payload.get("llm") or {}
    api_key = str(llm.get("api_key") or "").strip()
    base_url = str(llm.get("base_url") or "").strip()
    model = str(llm.get("model_name") or "Qwen").strip()
    if not api_key or not base_url:
        raise ValueError(f"Qwen 配置缺少 api_key 或 base_url: {path}")

    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_BASE_URL"] = base_url
    os.environ["OPENAI_MODEL"] = model
    os.environ["ONTOLOGY_MODEL"] = model
    os.environ["TRIPLET_MODEL"] = model
    if llm.get("timeout"):
        timeout = str(llm["timeout"])
        os.environ.setdefault("ONTOLOGY_TIMEOUT_SECONDS", timeout)
        os.environ.setdefault("KG_LLM_TIMEOUT_SECONDS", timeout)
    if llm.get("max_tokens"):
        os.environ.setdefault("ONTOLOGY_MAX_OUTPUT_TOKENS", str(llm["max_tokens"]))
    if llm.get("retry_count") is not None:
        os.environ.setdefault("ONTOLOGY_MAX_RETRIES", str(llm["retry_count"]))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="先运行本体候选流水线，成功后运行三元组抽取")
    parser.add_argument("--input", required=True, help="Markdown 输入文件")
    parser.add_argument("--output-stem", required=True, help="三元组输出文件名（不含扩展名）")
    parser.add_argument("--llm-config", required=True, help="包含 Qwen 连接信息的 YAML")
    parser.add_argument("--review-config", default="config/review_agent.json")
    parser.add_argument("--review-mode", default="shadow")
    parser.add_argument("--chain-id", required=True)
    parser.add_argument("--base-policy", choices=["strict", "draft"], default="draft")
    parser.add_argument("--ontology-release", help="已发布 release 目录或 release_manifest.json；提供时跳过候选构建")
    parser.add_argument("--max-chunks", type=int, help="候选v4抽取的来源范围上限；未处理范围记录为不完整")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    input_path = (ROOT / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    llm_config = (ROOT / args.llm_config).resolve() if not Path(args.llm_config).is_absolute() else Path(args.llm_config)
    review_config = (ROOT / args.review_config).resolve() if not Path(args.review_config).is_absolute() else Path(args.review_config)
    run_dir = ROOT / "runs" / "chains" / args.chain_id
    status_path = run_dir / "status.json"
    state: dict[str, Any] = {
        "chain_id": args.chain_id,
        "status": "starting",
        "stage": "ontology",
        "started_at": now_iso(),
        "finished_at": None,
        "input": str(input_path),
        "model": None,
        "ontology_run_id": None,
        "triplet_exit_code": None,
        "errors": [],
        "note": "仅使用显式发布的 release 抽取；本体候选生成成功不代表发布成功。",
    }
    write_json(status_path, state)

    try:
        if not input_path.is_file():
            raise FileNotFoundError(f"找不到输入文档: {input_path}")
        if not llm_config.is_file():
            raise FileNotFoundError(f"找不到 Qwen 配置: {llm_config}")
        if not review_config.is_file():
            raise FileNotFoundError(f"找不到审核配置: {review_config}")

        load_llm_config(llm_config)
        state["model"] = os.environ["ONTOLOGY_MODEL"]
        state["status"] = "running"
        write_json(status_path, state)

        release = None
        if args.ontology_release:
            release_path = Path(args.ontology_release)
            if str(release_path) != "current" and not release_path.is_absolute():
                release_path = ROOT / release_path
            release = select_release(release_path, root=ROOT / "runs" / "ontology_releases")
        else:
            release = ReleaseCatalog(ROOT / "runs" / "ontology_releases").current()
        if release is not None:
            state.update(release.binding())
            state["ontology_status"] = "published"
        else:
            ontology_config = OntologyPipelineConfig.from_env(
                base_policy=args.base_policy, review_mode="manual",
            )
            ontology_manifest = run_ontology_pipeline(input_path, ontology_config, write_compatibility=True)
            state["ontology_run_id"] = ontology_manifest.get("run_id")
            state["ontology_status"] = ontology_manifest.get("status")
            state["ontology_manifest"] = str(
                ROOT / "runs" / "ontology" / str(ontology_manifest.get("run_id")) / "manifest.json"
            )
            state["status"] = "stopped"
            state["stage"] = "awaiting_ontology_release"
            state["finished_at"] = now_iso()
            state["errors"].append(
                {
                    "type": "OntologyReleaseRequired",
                    "message": f"本体候选状态为 {ontology_manifest.get('status')}；请审核发布后通过 --ontology-release 指定版本",
                }
            )
            write_json(status_path, state)
            return 2

        state["stage"] = "triplet_extraction"
        state["status"] = "running"
        state["triplet_started_at"] = now_iso()
        write_json(status_path, state)

        command = [
            sys.executable,
            "-u",
            "-m",
            "extract_triplets.extrct",
            "--input",
            str(input_path),
            "--output-stem",
            args.output_stem,
            "--config",
            str(review_config),
            "--review-mode",
            args.review_mode,
            "--ontology-release",
            str(release.path),
        ]
        if args.max_chunks is not None:
            command.extend(["--max-chunks", str(args.max_chunks)])
        completed = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), check=False)
        state["triplet_exit_code"] = completed.returncode
        state["finished_at"] = now_iso()
        if completed.returncode == 0:
            state["status"] = "succeeded"
            state["stage"] = "complete"
        else:
            state["status"] = "failed"
            state["errors"].append(
                {
                    "type": "TripletExtractionFailed",
                    "message": f"三元组抽取退出码为 {completed.returncode}",
                }
            )
        write_json(status_path, state)
        return completed.returncode
    except Exception as exc:
        state["status"] = "failed"
        state["finished_at"] = now_iso()
        state["errors"].append({"type": type(exc).__name__, "message": str(exc)})
        write_json(status_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
