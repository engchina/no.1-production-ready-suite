"""確認と送信で共有する質問を、保存履歴・設定・利用者へ束縛する。"""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.rag.signed_url import issue_signed_token, signing_key, verify_signed_token

_VERSION = "chat-question-v1"


def context_digest(value: object) -> str:
    """外部へ履歴・設定を展開せず、更新だけを検出する。"""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _key(secret: str) -> bytes:
    return signing_key(
        secret, info=b"rag/chat-question/v1", unavailable_message="質問の確認を準備できません。"
    )


def issue_question_token(
    secret: str,
    binding: Mapping[str, Any],
    question: str,
    *,
    confirmed_fact_ids: tuple[str, ...] = (),
) -> str:
    return issue_signed_token(
        _key(secret),
        version=_VERSION,
        claims={
            "binding": dict(binding),
            "question": question,
            "confirmed_fact_ids": list(confirmed_fact_ids),
        },
    )[0]


def read_question_context(
    secret: str, token: str, binding: Mapping[str, Any]
) -> tuple[str, tuple[str, ...]]:
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    question = payload.get("question")
    if payload.get("binding") != dict(binding) or not isinstance(question, str) or not question:
        raise ValueError("質問の確認後に会話または設定が変わりました。")
    facts = payload.get("confirmed_fact_ids", [])
    if not isinstance(facts, list) or any(not isinstance(identity, str) for identity in facts):
        raise ValueError("確認回答の参照が不正です。")
    return question, tuple(dict.fromkeys(facts))


def read_question_token(secret: str, token: str, binding: Mapping[str, Any]) -> str:
    return read_question_context(secret, token, binding)[0]
