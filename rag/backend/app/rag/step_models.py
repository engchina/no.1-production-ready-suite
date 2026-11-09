"""回答の工程の段ごとのモデルの設定(#4351・#4349・#4564・#4565)。

段のモデルの設定はモデル設定の登録モデルの ID(空は回答のモデルのまま)。rag_engine へは
``RAG_ENGINE_<段>_*`` の env で、登録モデルの接続・推論の量・入出力の上限と、段の推論の量の上書き
(``effort_field`` のある段だけ)を渡す。登録していない
モデルは接続を渡さず model だけを渡す(段の呼び出しが設定の不足で失敗し、回答はその段の結果なしで
続ける。回答のモデルやプライマリ接続へ暗黙に切り替えない)。
"""

from __future__ import annotations

from dataclasses import dataclass

from pr_system_settings.model import EnterpriseAiModelNotRegisteredError

from app.config import (
    Settings,
    enterprise_ai_connection_for_model,
    enterprise_ai_max_input_tokens,
    enterprise_ai_output_token_budget,
    enterprise_ai_reasoning_effort_to_send,
)


@dataclass(frozen=True)
class StepModelSetting:
    """段のモデルの設定 1 つ。"""

    # 診断の models・検索の設定の診断・設定の API で使う段の名前(API の項目は ``<key>_model``)。
    key: str
    # `Settings` のフィールド(.env の変数はその大文字)。
    field: str
    # rag_engine へ渡す env の接頭辞(rag_engine の `STEP_LLM_PROVIDERS` と同じ)。
    engine_prefix: str
    # 設定の API の 422 の文言に使う段の名前。
    label: str
    # 段の推論の量の上書き(`Settings` のフィールド。model は登録値のまま)。無い段は空(登録値)。
    effort_field: str = ""


STEP_MODEL_SETTINGS: tuple[StepModelSetting, ...] = (
    StepModelSetting(
        "question_subject_role",
        "rag_question_subject_role_model",
        "RAG_ENGINE_QUESTION_SUBJECT_ROLE",
        "質問の対象の役割の判定",
    ),
)


def step_model_id(settings: Settings, step: StepModelSetting) -> str:
    """段のモデルの設定の値(前後の空白を除く。空は回答のモデル)。"""
    return str(getattr(settings, step.field)).strip()


def step_model_environ(settings: Settings) -> dict[str, str]:
    """段のモデルの接続を rag_engine へ渡す env(空の設定の段は何も渡さない)。"""
    environ: dict[str, str] = {}
    for step in STEP_MODEL_SETTINGS:
        prefix = step.engine_prefix
        if step.effort_field:
            # 段の推論の量の上書き(回答のモデルのままの段にも当てる。model は空 = 登録値のまま)。
            effort = str(getattr(settings, step.effort_field))
            environ[f"{prefix}_REASONING_EFFORT_OVERRIDE"] = "" if effort == "model" else effort
        model_id = step_model_id(settings, step)
        if not model_id:
            continue
        environ[f"{prefix}_MODEL"] = model_id
        try:
            connection = enterprise_ai_connection_for_model(settings, model_id)
        except EnterpriseAiModelNotRegisteredError:
            continue
        environ.update(
            {
                f"{prefix}_ENDPOINT": connection.base_url,
                f"{prefix}_API_KEY": connection.api_key,
                f"{prefix}_PROJECT_OCID": connection.project_ocid,
                f"{prefix}_REASONING_EFFORT": enterprise_ai_reasoning_effort_to_send(
                    settings, model_id
                ),
                f"{prefix}_MAX_INPUT_TOKENS": str(
                    enterprise_ai_max_input_tokens(settings, model_id)
                ),
                f"{prefix}_MAX_OUTPUT_TOKENS": str(
                    enterprise_ai_output_token_budget(settings, model_id)
                ),
            }
        )
    return environ
