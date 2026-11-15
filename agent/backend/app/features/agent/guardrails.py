"""Agent の入力と最終回答の安全チェック（OCI Guardrails。3 製品共通の設定。#5062・#5109）。

判定は platform の `pr_system_settings.guardrails`。入力の検査は、利用者の文を使う最初の処理と
して、次の共通の経路だけで行う（入口ごとに個別に呼ばない）。

- Run の目標（チャット・MCP・自動実行・品質評価・再実行）: Run を作る唯一の公開の経路
  `AgentRuntimeRepository.create_builtin_run`。止めた入力では Run を作らない（ツールもモデルも
  呼ばない）。
- 回答前の確認への自由入力（画面・MCP）: `AgentRuntimeRepository.decide_confirmation`。
- 業務 Agent の下書きの提案の説明: その API の、候補の取得とモデルの呼び出しの前。

個人情報は伏せた文で続ける。ツールの結果（RAG の根拠・資料の本文など）は検査しない。
"""

from __future__ import annotations

from collections.abc import Sequence

from pr_system_settings.guardrails import GuardrailPolicy

from app.features.agent.confirmation import ConfirmationAnswer
from app.settings import get_settings

GUARDRAIL_BLOCKED_CODE = "guardrail_blocked"


class GuardrailBlockedError(ValueError):
    """安全チェックで入力を止めた（画面は 422、MCP はツールのエラー）。"""

    code = GUARDRAIL_BLOCKED_CODE


def guardrail_policy() -> GuardrailPolicy:
    return GuardrailPolicy(get_settings())


def checked_input(text: str) -> str:
    """利用者の入力の文を検査し、伏せた後の文を返す（止めたら `GuardrailBlockedError`）。"""
    result = guardrail_policy().validate_query(text)
    if not result.allowed:
        raise GuardrailBlockedError(" ".join([result.sanitized_text, *result.warnings]))
    return result.sanitized_text


def checked_confirmation_answers(
    answers: Sequence[ConfirmationAnswer],
) -> list[ConfirmationAnswer]:
    """回答前の確認への答えの自由入力（`other_text`）を検査し、伏せた後の文に置き換える。

    選択肢（`option_ids`）は確認を出した側の値なので検査しない。
    """
    return [
        answer.model_copy(update={"other_text": checked_input(answer.other_text)})
        if answer.other_text.strip()
        else answer
        for answer in answers
    ]


def checked_answer(answer: str) -> tuple[str, list[str]]:
    """最終回答を検査する。止めたときは停止の文、伏せたときは伏せた後の文と警告を返す。"""
    result = guardrail_policy().validate_answer(answer)
    return result.sanitized_text, result.warnings
