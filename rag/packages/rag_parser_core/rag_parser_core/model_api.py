"""モデルを呼ぶ API の種類（接続ごと。#5546）。

システム設定 › モデル の接続のうち、クォータナリ接続（Anthropic Claude）は Messages API で呼び、
ほかの接続は OpenAI 互換の Responses API で呼ぶ。backend が接続から決めて、共有 core の
`OciEnterpriseAiConfig` と rag_engine の provider に渡す。この module は依存を持たない
（parser のサービスの venv でも import できる）。
"""

from __future__ import annotations

from typing import Final

# OpenAI 互換の Responses API（既定）。
MODEL_API_RESPONSES: Final = "responses"
# Anthropic Claude の Messages API（`pr_anthropic_messages`。クォータナリ接続）。
MODEL_API_ANTHROPIC_MESSAGES: Final = "anthropic_messages"


def uses_anthropic_messages(model_api: str) -> bool:
    """Messages API で呼ぶ接続か（空・不明の値は Responses API）。"""
    return (model_api or "").strip() == MODEL_API_ANTHROPIC_MESSAGES
