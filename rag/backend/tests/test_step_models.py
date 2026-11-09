"""段ごとのモデルの設定を rag_engine の設定へ渡す(合成の設定)。

質問の対象の役割の判定(#4351)、表の行の条件の当てはめ(#4349)、推導の監査の判定(#4565)と理由(#4564)の段。
既定は xai.grok-4.7。
登録モデルならその接続・推論の量・出力の上限を渡し、登録していないモデルは接続を渡さない(段が設定の
不足で失敗し、回答はその段の結果なしで続ける)。空は回答のモデルのまま。
"""

from pathlib import Path
from typing import Any

import pytest
from rag_engine.config import (
    QUESTION_SUBJECT_ROLE_LLM_PROVIDER,
)

from app import config as config_module
from app.config import EnterpriseAiConfiguredModel, Settings
from app.rag.answer_engine import build_engine_settings
from app.rag.diagnostics import rag_config_fingerprint
from app.rag.step_models import STEP_MODEL_SETTINGS

PRIMARY = "https://primary.example/openai/v1"
SECONDARY = "https://secondary.example/openai/v1"
ANSWER_MODEL = "synthetic-answer-model"
STEP_MODEL = "xai.grok-4.7"

# (設定のフィールド, rag_engine の provider ID, rag_engine の Settings のフィールド)
STEPS = [
    (
        "rag_question_subject_role_model",
        QUESTION_SUBJECT_ROLE_LLM_PROVIDER,
        "question_subject_role_llm",
    ),
]
STEP_IDS = [field for field, _provider, _engine_field in STEPS]


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
                model_id=STEP_MODEL,
                display_name="Step",
                connection_id="secondary",
                reasoning_effort="low",
                max_output_tokens=16_000,
            ),
        ],
        oci_enterprise_ai_default_text_model=ANSWER_MODEL,
        oci_enterprise_ai_default_vision_model=ANSWER_MODEL,
        **overrides,
    )


def test_step_table_matches_the_engine_steps() -> None:
    assert [step.field for step in STEP_MODEL_SETTINGS] == STEP_IDS


@pytest.mark.parametrize("field", STEP_IDS)
def test_default_step_model_and_env_example(field: str) -> None:
    assert getattr(Settings(), field) == STEP_MODEL
    example = (Path(config_module.BACKEND_ROOT) / ".env.example").read_text(encoding="utf-8")
    assert f"{field.upper()}={STEP_MODEL}" in example.splitlines()


@pytest.mark.parametrize(("field", "provider_id", "engine_field"), STEPS, ids=STEP_IDS)
def test_registered_step_model_passes_its_connection(
    tmp_path: Path, field: str, provider_id: str, engine_field: str
) -> None:
    engine = build_engine_settings(_settings(), output_dir=tmp_path)
    provider = engine.llm_providers[provider_id]
    assert getattr(engine, engine_field) == provider_id
    assert (provider.model, provider.base_url, provider.api_key, provider.project_id) == (
        STEP_MODEL,
        SECONDARY,
        "sk-secondary",
        "ocid1.project.secondary",
    )
    # 推論の量と出力の上限はそのモデルの登録値。回答のモデルの provider は変えない。
    assert (provider.reasoning_effort, provider.max_output_tokens) == ("low", 16_000)
    assert engine.llm_providers[engine.default_answer_llm].model == ANSWER_MODEL
    assert engine.llm_providers[engine.default_answer_llm].reasoning_effort == "medium"


@pytest.mark.parametrize(("field", "provider_id", "engine_field"), STEPS, ids=STEP_IDS)
def test_unregistered_step_model_passes_no_connection(
    tmp_path: Path, field: str, provider_id: str, engine_field: str
) -> None:
    engine = build_engine_settings(
        _settings(**{field: "model-not-registered"}), output_dir=tmp_path
    )
    provider = engine.llm_providers[provider_id]
    assert getattr(engine, engine_field) == provider_id
    assert (provider.model, provider.base_url, provider.api_key) == ("model-not-registered", "", "")


@pytest.mark.parametrize("value", ["", "  "], ids=["empty", "blank"])
@pytest.mark.parametrize(("field", "provider_id", "engine_field"), STEPS, ids=STEP_IDS)
def test_empty_step_model_keeps_the_answer_model(
    tmp_path: Path, value: str, field: str, provider_id: str, engine_field: str
) -> None:
    engine = build_engine_settings(_settings(**{field: value}), output_dir=tmp_path)
    assert provider_id not in engine.llm_providers
    assert getattr(engine, engine_field) == ""
    # ほかの段の設定は変わらない。
    others = [other for other in STEPS if other[0] != field]
    assert all(getattr(engine, other_field) == other_id for _f, other_id, other_field in others)


@pytest.mark.parametrize("field", STEP_IDS)
def test_step_model_changes_the_config_fingerprint(field: str) -> None:
    assert rag_config_fingerprint(_settings()) != rag_config_fingerprint(_settings(**{field: ""}))
