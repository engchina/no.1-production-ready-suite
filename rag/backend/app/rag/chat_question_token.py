"""確認と送信で共有する質問を、保存履歴・設定・利用者へ束縛する。"""

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from pr_backend_core.observability.tracing import SpanUpdate

from app.rag.history_question import HistoryIntent
from app.rag.model_call_record import PreparedStage, open_model_calls, seal_model_calls
from app.rag.signed_url import issue_signed_token, signing_key, verify_signed_token

_VERSION = "chat-question-v1"
# 送信の前に済ませ、送信の回答の記録に工程として残すもの（#5776）。
APPROVED_FAQ_CANDIDATES_STAGE = "approved_faq_candidates"
CLARIFICATION_CANDIDATES_STAGE = "clarification_candidates"
_PREPARED_STAGES = frozenset({APPROVED_FAQ_CANDIDATES_STAGE, CLARIFICATION_CANDIDATES_STAGE})
# token の上限（要求の ``question_context_token`` は 300000 文字まで）。工程を足すときの余裕を残す。
_TOKEN_MAX_CHARS = 290_000


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
    history_rewrite_window: tuple[int, int] | None = None,
    history_rewrite_calls: str | None = None,
    history_intent: str = "search",
) -> str:
    """``history_rewrite_window`` は、準備の中で履歴による補完を実行した開始・終了の時刻
    （epoch のナノ秒）。``history_rewrite_calls`` はその中のモデルの呼び出し
    （``seal_model_calls`` で暗号化した値。#5773）。

    送信の側は補完を呼び直さず、この時刻と呼び出しで処理の経過に同じ段を残す（実行しなかったときは
    None）。
    """
    claims: dict[str, Any] = {
        "binding": dict(binding),
        "question": question,
        "confirmed_fact_ids": list(confirmed_fact_ids),
    }
    if history_rewrite_window is not None:
        claims["history_rewrite_window"] = list(history_rewrite_window)
    if history_rewrite_calls:
        claims["history_rewrite_calls"] = history_rewrite_calls
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


def read_history_rewrite(secret: str, token: str) -> tuple[tuple[int, int], str | None] | None:
    """準備の中で履歴による補完を実行した時刻（開始・終了の epoch のナノ秒）と、暗号化した
    モデルの呼び出し。実行していなければ None。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    window = _window(payload.get("history_rewrite_window"))
    if window is None:
        return None
    calls = payload.get("history_rewrite_calls")
    return window, calls if isinstance(calls, str) else None


def read_history_intent(secret: str, token: str) -> HistoryIntent:
    """準備の補完が分けた最新の発話の種類（#4787）。無い・不明な値は search（検索する）。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    value = payload.get("history_intent", "search")
    return value if value in ("greeting", "small_talk") else "search"


def add_prepared_stage(
    secret: str,
    token: str,
    stage: str,
    window: tuple[int, int],
    calls: Sequence[SpanUpdate],
) -> str:
    """token に送信の前に済ませた工程を足した token（期限・束縛はそのまま。#5776）。

    呼び出しは token の上限に収まる分だけ入れる（入らなければ工程の時刻だけ残す）。
    """
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    claims = {key: value for key, value in payload.items() if key not in ("iat", "exp")}
    entry: dict[str, Any] = {"stage": stage, "window": list(window)}
    # 暗号文は JSON に入れた後に base64 で 4/3 倍になる。
    sealed = seal_model_calls(
        secret, calls, limit=max(0, (_TOKEN_MAX_CHARS - len(token)) * 3 // 4 - 200)
    )
    if sealed:
        entry["calls"] = sealed
    claims["prepared_stages"] = [*claims.get("prepared_stages", []), entry]
    remaining = int(payload["exp"]) - int(time.time())
    return issue_signed_token(
        _key(secret), version=_VERSION, claims=claims, ttl_seconds=max(1, remaining)
    )[0]


def read_prepared_stages(secret: str, token: str) -> tuple[PreparedStage, ...]:
    """送信の前に済ませた工程（形の違うものは読み飛ばす）。"""
    payload = verify_signed_token(_key(secret), token, version=_VERSION)
    stages: list[PreparedStage] = []
    for entry in payload.get("prepared_stages", []):
        if not isinstance(entry, dict) or entry.get("stage") not in _PREPARED_STAGES:
            continue
        window = _window(entry.get("window"))
        if window is not None:
            stages.append(
                PreparedStage(entry["stage"], window, open_model_calls(secret, entry.get("calls")))
            )
    return tuple(stages)


def _window(value: object) -> tuple[int, int] | None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or not 0 <= value[0] <= value[1]
    ):
        return None
    return value[0], value[1]
