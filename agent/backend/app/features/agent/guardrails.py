"""Agent の Run の入力と最終回答の安全チェック（OCI Guardrails。3 製品共通の設定。#5062）。

判定は platform の `pr_system_settings.guardrails`。止めた入力では Run を作らず（ツールもモデルも
呼ばない）、個人情報は伏せた文で続ける。ツールの結果（RAG の根拠・資料の本文など）は検査しない。
"""

from __future__ import annotations

from pr_system_settings.guardrails import GuardrailPolicy

from app.settings import get_settings

GUARDRAIL_BLOCKED_CODE = "guardrail_blocked"


class GuardrailBlockedError(ValueError):
    """安全チェックで Run の入力を止めた（画面は 422、MCP はツールのエラー）。"""

    code = GUARDRAIL_BLOCKED_CODE


def guardrail_policy() -> GuardrailPolicy:
    return GuardrailPolicy(get_settings())


def checked_goal(goal: str) -> str:
    """Run の入力（目標・チャットの発言）を検査し、伏せた後の文を返す。"""
    result = guardrail_policy().validate_query(goal)
    if not result.allowed:
        raise GuardrailBlockedError(" ".join([result.sanitized_text, *result.warnings]))
    return result.sanitized_text


def checked_answer(answer: str) -> tuple[str, list[str]]:
    """最終回答を検査する。止めたときは停止の文、伏せたときは伏せた後の文と警告を返す。"""
    result = guardrail_policy().validate_answer(answer)
    return result.sanitized_text, result.warnings
