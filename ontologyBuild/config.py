"""Configuration and model-client creation for ontology extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
VALID_ENGINES = {"llm", "mock"}
VALID_BASE_POLICIES = {"strict", "draft"}
VALID_CORPUS_ROLES = {"build", "train", "validation", "test"}


def _legacy_candidate_defaults() -> dict[str, str]:
    """Read legacy single-file extractor defaults without exposing credentials."""
    try:
        from ontologyBuild import ontology_candidate
    except Exception:
        return {}
    return {
        "api_key": getattr(ontology_candidate, "API_KEY", "") or "",
        "base_url": getattr(ontology_candidate, "BASE_URL", "") or "",
        "model": getattr(ontology_candidate, "MODEL", "") or "",
    }


@dataclass(frozen=True)
class OntologyPipelineConfig:
    extraction_engine: str = "llm"
    review_mode: str = "manual"
    auto_accept_new: bool = False
    corpus_role: str = "build"
    timeout_seconds: int = 180
    max_retries: int = 2
    context_max_chars: int = 8000
    chunk_size: int = 600
    chunk_overlap: int = 80
    max_output_tokens: int = 1500
    adaptive_retry_chunk_size: int = 400
    adaptive_retry_chunk_overlap: int = 40
    base_policy: str = "strict"
    enable_shacl: bool = False
    enable_owl: bool = False
    model: str = ""
    api_key: str = ""
    openai_base_url: str = ""
    qwen_base_url: str = ""
    max_chunks: Optional[int] = None
    project_root: Path = Path(__file__).resolve().parents[1]

    @classmethod
    def from_env(cls, environ: Optional[dict[str, str]] = None, **overrides: Any) -> "OntologyPipelineConfig":
        env = os.environ if environ is None else environ
        legacy_defaults = _legacy_candidate_defaults()

        def bool_env(name: str, default: bool = False) -> bool:
            value = str(env.get(name, "")).strip().lower()
            if not value:
                return default
            return value in TRUE_VALUES

        def int_env(name: str, default: int) -> int:
            value = str(env.get(name, "")).strip()
            if not value:
                return default
            try:
                return int(value)
            except ValueError as exc:
                raise ValueError(f"{name} 必须是整数") from exc

        max_chunks_value = str(env.get("ONTOLOGY_MAX_CHUNKS", "")).strip()
        max_chunks = int(max_chunks_value) if max_chunks_value else None
        values = {
            "extraction_engine": env.get("ONTOLOGY_EXTRACTION_ENGINE", "llm").strip().lower(),
            "review_mode": env.get("ONTOLOGY_REVIEW_MODE", "manual").strip().lower(),
            "auto_accept_new": bool_env("ONTOLOGY_AUTO_ACCEPT_NEW", False),
            "corpus_role": env.get("ONTOLOGY_CORPUS_ROLE", "build").strip().lower(),
            "timeout_seconds": int_env("ONTOLOGY_TIMEOUT_SECONDS", 180),
            "max_retries": int_env("ONTOLOGY_MAX_RETRIES", 2),
            "context_max_chars": int_env("ONTOLOGY_CONTEXT_MAX_CHARS", 8000),
            "chunk_size": int_env("ONTOLOGY_CHUNK_SIZE", 600),
            "chunk_overlap": int_env("ONTOLOGY_CHUNK_OVERLAP", 80),
            "max_output_tokens": int_env("ONTOLOGY_MAX_OUTPUT_TOKENS", 1500),
            "adaptive_retry_chunk_size": int_env("ONTOLOGY_ADAPTIVE_RETRY_CHUNK_SIZE", 400),
            "adaptive_retry_chunk_overlap": int_env("ONTOLOGY_ADAPTIVE_RETRY_CHUNK_OVERLAP", 40),
            "base_policy": env.get("ONTOLOGY_BASE_POLICY", "strict").strip().lower(),
            "enable_shacl": bool_env("ONTOLOGY_ENABLE_SHACL", False),
            "enable_owl": bool_env("ONTOLOGY_ENABLE_OWL", False),
            "model": env.get("ONTOLOGY_MODEL")
            or env.get("OPENAI_MODEL")
            or env.get("QWEN_MODEL")
            or legacy_defaults.get("model", ""),
            "api_key": env.get("OPENAI_API_KEY") or env.get("QWEN_API_KEY") or legacy_defaults.get("api_key", ""),
            "openai_base_url": env.get("OPENAI_BASE_URL", ""),
            "qwen_base_url": env.get("QWEN_BASE_URL") or legacy_defaults.get("base_url", ""),
            "max_chunks": max_chunks,
        }
        values.update(overrides)
        config = cls(**values)
        config.validate()
        return config

    def validate(self) -> None:
        if self.extraction_engine not in VALID_ENGINES:
            raise ValueError(f"ONTOLOGY_EXTRACTION_ENGINE 必须是 {sorted(VALID_ENGINES)} 之一")
        if self.base_policy not in VALID_BASE_POLICIES:
            raise ValueError(f"ONTOLOGY_BASE_POLICY 必须是 {sorted(VALID_BASE_POLICIES)} 之一")
        if self.corpus_role not in VALID_CORPUS_ROLES:
            raise ValueError(f"ONTOLOGY_CORPUS_ROLE 必须是 {sorted(VALID_CORPUS_ROLES)} 之一")
        if self.review_mode not in {"manual", "hybrid", "auto"}:
            raise ValueError("ONTOLOGY_REVIEW_MODE 必须是 manual/hybrid/auto")
        if self.timeout_seconds <= 0:
            raise ValueError("ONTOLOGY_TIMEOUT_SECONDS 必须大于 0")
        if self.max_retries < 0:
            raise ValueError("ONTOLOGY_MAX_RETRIES 不能小于 0")
        if self.context_max_chars < 1000:
            raise ValueError("ONTOLOGY_CONTEXT_MAX_CHARS 不能小于 1000")
        if self.chunk_size < 100:
            raise ValueError("ONTOLOGY_CHUNK_SIZE 不能小于 100")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError("ONTOLOGY_CHUNK_OVERLAP 必须大于等于 0 且小于 ONTOLOGY_CHUNK_SIZE")
        if self.max_output_tokens <= 0:
            raise ValueError("ONTOLOGY_MAX_OUTPUT_TOKENS 必须大于 0")
        if self.adaptive_retry_chunk_size < 100:
            raise ValueError("ONTOLOGY_ADAPTIVE_RETRY_CHUNK_SIZE 不能小于 100")
        if (
            self.adaptive_retry_chunk_overlap < 0
            or self.adaptive_retry_chunk_overlap >= self.adaptive_retry_chunk_size
        ):
            raise ValueError(
                "ONTOLOGY_ADAPTIVE_RETRY_CHUNK_OVERLAP 必须大于等于 0 且小于 "
                "ONTOLOGY_ADAPTIVE_RETRY_CHUNK_SIZE"
            )
        if self.max_chunks is not None and self.max_chunks <= 0:
            raise ValueError("ONTOLOGY_MAX_CHUNKS 必须大于 0")


def configured_api_key(environ: Optional[dict[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    return env.get("OPENAI_API_KEY") or env.get("QWEN_API_KEY") or _legacy_candidate_defaults().get("api_key", "")


def configured_base_url(config: OntologyPipelineConfig) -> str:
    return config.openai_base_url or config.qwen_base_url


def llm_verify_ssl(environ: Optional[dict[str, str]] = None) -> bool:
    """Resolve TLS verification without silently weakening the default."""
    env = os.environ if environ is None else environ
    value = str(env.get("KG_LLM_VERIFY_SSL", "true")).strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError("KG_LLM_VERIFY_SSL 必须是 true/false")


def create_llm_client(config: OntologyPipelineConfig, *, environ: Optional[dict[str, str]] = None):
    """Create the OpenAI-compatible client only when a live LLM run is requested."""
    api_key = config.api_key or configured_api_key(environ)
    base_url = configured_base_url(config)
    if not api_key:
        raise ValueError("缺少 OPENAI_API_KEY 或 QWEN_API_KEY，不能执行真实 LLM 本体抽取")
    if not base_url:
        raise ValueError("缺少 OPENAI_BASE_URL 或 QWEN_BASE_URL，不能执行真实 LLM 本体抽取")
    if not config.model:
        raise ValueError("缺少 ONTOLOGY_MODEL，不能执行真实 LLM 本体抽取")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("缺少 openai 依赖，请先安装 openai 包") from exc
    try:
        import httpx
    except ImportError:
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
        http_client=httpx.Client(
            timeout=config.timeout_seconds,
            trust_env=False,
            verify=llm_verify_ssl(environ),
        ),
    )


def public_config(config: OntologyPipelineConfig) -> dict[str, Any]:
    """Return run configuration without credentials."""
    return {
        "extraction_engine": config.extraction_engine,
        "review_mode": config.review_mode,
        "auto_accept_new": config.auto_accept_new,
        "corpus_role": config.corpus_role,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "context_max_chars": config.context_max_chars,
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "max_output_tokens": config.max_output_tokens,
        "adaptive_retry_chunk_size": config.adaptive_retry_chunk_size,
        "adaptive_retry_chunk_overlap": config.adaptive_retry_chunk_overlap,
        "base_policy": config.base_policy,
        "enable_shacl": config.enable_shacl,
        "enable_owl": config.enable_owl,
        "model": config.model,
        "api_key_configured": bool(config.api_key or configured_api_key()),
        "base_url_configured": bool(configured_base_url(config)),
        "tls_verify": llm_verify_ssl(),
        "max_chunks": config.max_chunks,
    }
