"""docling サービス内の DocRAG 解析設定(env から解決)。

rag_poc の Settings のうち解析・Vision に必要な項目だけを持つ。LLM 接続は
backend が橋渡しする OCI_ENTERPRISE_AI_* env(OciEnterpriseAiConfig.from_env)を使う。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig

LLM_API_MODE = "openai_responses"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class LlmProvider:
    """Vision checkpoint key と記録に使う LLM 接続先の識別情報。"""

    provider_id: str
    model: str
    region: str
    base_url: str
    project_id: str


@dataclass
class Settings:
    output_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DOCRAG_OUTPUT_DIR", "/tmp/docrag-runs"))
    )
    render_dpi: int = field(default_factory=lambda: _env_int("DOCRAG_RENDER_DPI", 150))
    docling_device: str = field(default_factory=lambda: os.environ.get("DOCLING_DEVICE", "cpu"))
    docling_num_threads: int = field(default_factory=lambda: _env_int("DOCLING_NUM_THREADS", 4))
    docling_do_ocr: bool = field(default_factory=lambda: _env_bool("DOCLING_DO_OCR", True))
    docling_do_table_structure: bool = field(
        default_factory=lambda: _env_bool("DOCLING_DO_TABLE_STRUCTURE", True)
    )
    docling_keep_picture_child_text: bool = field(
        default_factory=lambda: _env_bool("DOCLING_KEEP_PICTURE_CHILD_TEXT", False)
    )
    answer_max_tokens: int = field(
        default_factory=lambda: _env_int("DOCRAG_VISION_MAX_TOKENS", 8000)
    )
    llm_api_mode: str = LLM_API_MODE
    llm: OciEnterpriseAiConfig = field(default_factory=OciEnterpriseAiConfig.from_env)

    @property
    def vision_model(self) -> str:
        return (self.llm.vision_model_id or self.llm.default_model_id).strip()


def get_vision_provider(settings: Settings) -> LlmProvider:
    return LlmProvider(
        provider_id="oci_enterprise_ai",
        model=settings.vision_model,
        region="",
        base_url=settings.llm.oci_enterprise_ai_endpoint,
        project_id=settings.llm.oci_enterprise_ai_project_ocid,
    )


def get_settings() -> Settings:
    return Settings()
