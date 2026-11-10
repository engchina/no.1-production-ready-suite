"""確認と送信で共有する質問を、保存履歴・設定・利用者へ束縛する。"""

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from app.rag.history_question import HistoryIntent
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
    history_rewrite_elapsed_seconds: float | None = None,
    history_intent: str = "search",
) -> str:
    """``history_rewrite_elapsed_seconds`` は、準備の中で履歴による補完を実行したときの所要時間。

    送信の側は補完を呼び直さず、この時間で処理の経過に同じ段を残す（実行しなかったときは None）。
    """
    claims: dict[str, Any] = {
        "binding": dict(binding),
        "question": question,
        "confirmed_fact_ids": list(confirmed_fact_ids),
    }
    if history_rewrite_elapsed_seconds is not None:
        claims["history_rewrite_elapsed_seconds"] = history_rewrite_elapsed_seconds
    if history_intent != "search":
        # 補完と同じ呼び出しで分けた発話の種類（#4787。検索の要らない発話のときだけ）。
        claims["history_intent"] = history_intent
    return issue_signed_token(_key(secret), version=_VERSION, claims=claims)[0]


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


def _elapsed(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def read_history_rewrite_elapsed(secret: str, token: str) -> float | None:
    """準備の中で履歴による補完を実行したときの所要時間（秒）。実行していなければ None。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    return _elapsed(payload.get("history_rewrite_elapsed_seconds"))


def read_history_intent(secret: str, token: str) -> HistoryIntent:
    """準備の補完が分けた最新の発話の種類（#4787）。無い・不明な値は search（検索する）。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    value = payload.get("history_intent", "search")
    return value if value in ("greeting", "small_talk") else "search"
