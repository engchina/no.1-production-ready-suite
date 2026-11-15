"""NL2SQL の質問と説明の文の安全チェック（OCI Guardrails。3 製品共通の設定。#5063）。

判定は platform の `pr_system_settings.guardrails`。止めた質問は保存せず、個人情報は伏せた文で
続ける。結果の表の値（DB の実データ）は検査に送らない。

自然言語を受け取るすべての入口は、利用者の文を使う最初の処理としてここを呼ぶ（#5110）。権限の
確認・設定の読み込みなど文を使わない処理は前にあってよい。止めた文は後段（LLM・embedding・SQL の
生成・検索・保存）に渡さず、伏せた文があれば後段にはそれを渡す。
"""

from __future__ import annotations

from typing import Any

from pr_system_settings.guardrails import GuardrailPolicy, GuardrailResult
from pydantic import BaseModel

from app.settings import get_settings

from .ontology_service import OntologyServiceError

GUARDRAIL_BLOCKED_ERROR_CODE = "GUARDRAIL_BLOCKED"


class GuardrailBlockedError(OntologyServiceError, ValueError):
    """安全チェックで質問を止めた（画面は 422、MCP はツールのエラー）。"""


def guardrail_policy() -> GuardrailPolicy:
    return GuardrailPolicy(get_settings())


def checked_question(text: str) -> str:
    """質問を検査し、伏せた後の文を返す。止めるときは GuardrailBlockedError。"""
    result = guardrail_policy().validate_query(text)
    if not result.allowed:
        raise GuardrailBlockedError(GUARDRAIL_BLOCKED_ERROR_CODE, _blocked_message(result))
    return result.sanitized_text


def checked_fields[ModelT: BaseModel](request: ModelT, *paths: str) -> ModelT:
    """要求の自然言語の欄を検査し、伏せた後の文に置き換えた写しを返す（#5110）。

    `paths` は欄の名前（入れ子は `select_ai_overrides.additional_instructions` のように `.` で
    つなぐ）。空・未指定の欄は検査しない。1 つでも止めたら GuardrailBlockedError。
    """
    updated: BaseModel = request
    for path in paths:
        updated = _checked_path(updated, path.split("."))
    return updated  # type: ignore[return-value]


def check_job_input[ModelT: BaseModel](request: ModelT) -> ModelT:
    """SQL の生成の要求（ジョブ・プレビュー）の質問と、Select AI への追加の指示を検査する。"""
    return checked_fields(request, "question", "select_ai_overrides.additional_instructions")


def checked_explanation(text: str) -> GuardrailResult:
    """回答の説明の文を検査する。止めたときの sanitized_text は停止の文。"""
    return guardrail_policy().validate_answer(text)


def _checked_path(model: BaseModel, names: list[str]) -> BaseModel:
    name, rest = names[0], names[1:]
    value: Any = getattr(model, name)
    if value is None:
        return model
    if rest:
        replaced: Any = _checked_path(value, rest)
    else:
        if not str(value).strip():
            return model
        replaced = checked_question(value)
    return model if replaced is value else model.model_copy(update={name: replaced})


def _blocked_message(result: GuardrailResult) -> str:
    return " ".join([result.sanitized_text, *result.warnings])
