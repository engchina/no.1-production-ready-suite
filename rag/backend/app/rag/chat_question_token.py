"""確認と送信で共有する質問を、保存履歴・設定・利用者へ束縛する。"""

import hashlib
import json
import math
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
    history_rewrite_elapsed_seconds: float | None = None,
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


def with_rule_selection_answers(
    secret: str, token: str, answers: list[dict[str, Any]], *, elapsed_seconds: float = 0.0
) -> str:
    """現在の質問の署名に、原文照合済みの選択だけを束縛する。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    claims: dict[str, Any] = {
        "confirmed_fact_ids": payload.get("confirmed_fact_ids", []),
        "binding": payload["binding"],
        "question": payload["question"],
        "rule_selection_answers": answers,
        "rule_selection_elapsed_seconds": elapsed_seconds,
    }
    if "history_rewrite_elapsed_seconds" in payload:
        claims["history_rewrite_elapsed_seconds"] = payload["history_rewrite_elapsed_seconds"]
    return issue_signed_token(_key(secret), version=_VERSION, claims=claims)[0]


def read_rule_selection_answers(secret: str, token: str) -> list[dict[str, Any]]:
    """read_question_token で現在のbindingを検証した後に、同じ署名の選択を読む。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    values = payload.get("rule_selection_answers", [])
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise ValueError("確認した選択の形式が正しくありません。")
    return values


def _elapsed(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def read_rule_selection_elapsed(secret: str, token: str) -> float:
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    return _elapsed(payload.get("rule_selection_elapsed_seconds", 0.0)) or 0.0


def read_history_rewrite_elapsed(secret: str, token: str) -> float | None:
    """準備の中で履歴による補完を実行したときの所要時間（秒）。実行していなければ None。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    return _elapsed(payload.get("history_rewrite_elapsed_seconds"))
