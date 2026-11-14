"""NL2SQL の質問と説明の文の安全チェック（OCI Guardrails。3 製品共通の設定。#5063）。

判定は platform の `pr_system_settings.guardrails`。止めた質問は保存せず、個人情報は伏せた文で
続ける。結果の表の値（DB の実データ）は検査に送らない。
"""

from __future__ import annotations

from pr_system_settings.guardrails import GuardrailPolicy, GuardrailResult

from app.settings import get_settings

from .ontology_service import OntologyServiceError

GUARDRAIL_BLOCKED_ERROR_CODE = "GUARDRAIL_BLOCKED"


class GuardrailBlockedError(OntologyServiceError, ValueError):
    """安全チェックで質問を止めた（画面は 422 / 400、MCP はツールのエラー）。"""


def guardrail_policy() -> GuardrailPolicy:
    return GuardrailPolicy(get_settings())


def checked_question(text: str) -> str:
    """質問を検査し、伏せた後の文を返す。止めるときは GuardrailBlockedError。"""
    result = guardrail_policy().validate_query(text)
    if not result.allowed:
        raise GuardrailBlockedError(GUARDRAIL_BLOCKED_ERROR_CODE, _blocked_message(result))
    return result.sanitized_text


def checked_explanation(text: str) -> GuardrailResult:
    """回答の説明の文を検査する。止めたときの sanitized_text は停止の文。"""
    return guardrail_policy().validate_answer(text)


def _blocked_message(result: GuardrailResult) -> str:
    return " ".join([result.sanitized_text, *result.warnings])
