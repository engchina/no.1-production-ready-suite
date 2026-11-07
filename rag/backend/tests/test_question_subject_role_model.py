"""質問の対象の役割の判定の段のモデル（#4351）の設定を rag_engine の設定へ渡す（合成の設定）。

既定は xai.grok-4.7。登録モデルならその接続・推論の量・出力の上限を渡し、登録していない
モデルは接続を渡さない（段が設定の不足で失敗し、回答は対象の名前の照合を飛ばして続ける）。
空は回答のモデルのまま。
"""

from pathlib import Path
from typing import Any

import pytest
from rag_engine.config import QUESTION_SUBJECT_ROLE_LLM_PROVIDER

from app import config as config_module
from app.config import EnterpriseAiConfiguredModel, Settings
from app.rag.answer_engine import build_engine_settings

PRIMARY = "https://primary.example/openai/v1"
SECONDARY = "https://secondary.example/openai/v1"
ANSWER_MODEL = "synthetic-answer-model"
ROLE_MODEL = "xai.grok-4.7"


def _settings(**overrides: Any) -> Settings:
    return Settings(
        oci_enterprise_ai_endpoint=PRIMARY,
        oci_enterprise_ai_api_key="sk-primary",
        oci_enterprise_ai_secondary_endpoint=SECONDARY,
        oci_enterprise_ai_secondary_project_ocid="ocid1.project.secondary",
        oci_enterprise_ai_secondary_api_key="sk-secondary",
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(
                model_id=ANSWER_MODEL, display_name="A", reasoning_effort="medium"
            ),
            EnterpriseAiConfiguredModel(
                model_id=ROLE_MODEL,
                display_name="Role",
                connection_id="secondary",
                reasoning_effort="low",
                max_output_tokens=16_000,
            ),
        ],
        oci_enterprise_ai_default_text_model=ANSWER_MODEL,
        oci_enterprise_ai_default_vision_model=ANSWER_MODEL,
        **overrides,
    )


def test_default_role_model_and_env_example() -> None:
    assert Settings().rag_question_subject_role_model == ROLE_MODEL
    example = (Path(config_module.BACKEND_ROOT) / ".env.example").read_text(encoding="utf-8")
    assert f"RAG_QUESTION_SUBJECT_ROLE_MODEL={ROLE_MODEL}" in example.splitlines()


def test_registered_role_model_passes_its_connection(tmp_path: Path) -> None:
    engine = build_engine_settings(_settings(), output_dir=tmp_path)
    provider = engine.llm_providers[QUESTION_SUBJECT_ROLE_LLM_PROVIDER]
    assert engine.question_subject_role_llm == QUESTION_SUBJECT_ROLE_LLM_PROVIDER
    assert (provider.model, provider.base_url, provider.api_key, provider.project_id) == (
        ROLE_MODEL,
        SECONDARY,
        "sk-secondary",
        "ocid1.project.secondary",
    )
    # 推論の量と出力の上限はそのモデルの登録値。回答のモデルの provider は変えない。
    assert (provider.reasoning_effort, provider.max_output_tokens) == ("low", 16_000)
    assert engine.llm_providers[engine.default_answer_llm].model == ANSWER_MODEL
    assert engine.llm_providers[engine.default_answer_llm].reasoning_effort == "medium"


def test_unregistered_role_model_passes_no_connection(tmp_path: Path) -> None:
    engine = build_engine_settings(
        _settings(rag_question_subject_role_model="model-not-registered"), output_dir=tmp_path
    )
    provider = engine.llm_providers[QUESTION_SUBJECT_ROLE_LLM_PROVIDER]
    assert engine.question_subject_role_llm == QUESTION_SUBJECT_ROLE_LLM_PROVIDER
    assert (provider.model, provider.base_url, provider.api_key) == ("model-not-registered", "", "")


@pytest.mark.parametrize("value", ["", "  "])
def test_empty_role_model_keeps_the_answer_model(tmp_path: Path, value: str) -> None:
    engine = build_engine_settings(
        _settings(rag_question_subject_role_model=value), output_dir=tmp_path
    )
    assert QUESTION_SUBJECT_ROLE_LLM_PROVIDER not in engine.llm_providers
    assert engine.question_subject_role_llm == ""
