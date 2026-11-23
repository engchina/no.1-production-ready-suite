"""実行の記録（trace）の外で呼んだモデルの呼び出しを、後で回答の記録に残す（#5773）。

送信の前の準備（質問の補完）と、送信の入口の業務ガイドの条件の読み取りは、回答の
``operation_trace`` が始まる前に LLM を呼ぶ。その ``gen_ai.chat`` の span は記録の外なので捨てられて
いた。ここで終わった span（時刻・属性・入力と応答の詳細）を集め、回答の記録の中で実際の時刻のまま
``record_span`` で残し直す。

- 同じ要求の中（送信の入口）: 集めた span をそのまま渡す。業務ガイドの条件の読み取りは
  ``defer_model_calls`` で置き、``RagPipeline.run`` が記録の始めに残す（比較の各回答に同じものを
  残す）。
- 別の要求（送信の前の準備 → 送信）: ``question_context_token`` に入れて渡す。入力の詳細は
  system_admin だけが見られる内容なので、token は署名だけでなく AES-GCM で暗号化する（画面へ平文で
  出さない）。送信の前の登録済みQ&Aの候補・確認の候補は、工程ごと（``PreparedStage``）に入れ、
  送信の入口が ``defer_prepared_stages`` で置き、``RagPipeline.run`` が記録の始めに工程として残す
  （#5776）。

集めるのは LLM の呼び出し（``gen_ai.chat``）と、埋め込み・並べ替え（``gen_ai.embeddings`` /
``gen_ai.rerank``。登録済みQ&Aの候補の照合など）。
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pr_backend_core.observability.tracing import (
    SpanUpdate,
    observe_spans,
    operation_trace_active,
    record_span,
)
from pydantic import TypeAdapter, ValidationError

from app.rag.signed_url import signing_key

MODEL_CALL_SPANS = frozenset({"gen_ai.chat", "gen_ai.embeddings", "gen_ai.rerank"})
# token（要求の上限 300000 文字）に入れる暗号文の上限。超えたら入力と応答の詳細を外して
# 時刻・属性だけ残す。
SEALED_CALLS_MAX = 200_000
_NONCE_BYTES = 12
_CALLS = TypeAdapter(list[SpanUpdate])
# 業務ガイドの条件の読み取りなど、送信の入口で記録の外で呼んだモデルの呼び出し（要求ごと）。
_DEFERRED: ContextVar[tuple[SpanUpdate, ...]] = ContextVar("rag_deferred_model_calls", default=())


@dataclass(frozen=True, slots=True)
class PreparedStage:
    """送信の前の別の要求で済ませた工程。

    工程の名前・開始と終了の時刻（epoch のナノ秒）・中のモデルの呼び出しを持つ。
    """

    stage: str
    window: tuple[int, int]
    calls: tuple[SpanUpdate, ...] = ()


# 送信の前に済ませた工程（token から読んだもの。要求ごと）。
_DEFERRED_STAGES: ContextVar[tuple[PreparedStage, ...]] = ContextVar(
    "rag_deferred_prepared_stages", default=()
)


@contextmanager
def collect_model_calls() -> Iterator[list[SpanUpdate]]:
    """範囲の中で、実行の記録の外で終わったモデルの呼び出しを集める（記録の中のものは重ねない）。"""
    calls: list[SpanUpdate] = []

    def listen(update: SpanUpdate) -> None:
        if (
            update.operation_name in MODEL_CALL_SPANS
            and update.finished_at is not None
            and update.status != "running"
            and not operation_trace_active()
        ):
            calls.append(update)

    with observe_spans(listen):
        yield calls


def record_model_calls(calls: Sequence[SpanUpdate]) -> None:
    """集めた呼び出しを、今の span の子として実際の時刻のまま残す。"""
    for call in calls:
        record_span(
            call.operation_name,
            started_at=call.started_at,
            finished_at=call.finished_at or call.started_at,
            status="done" if call.status == "running" else call.status,
            kind=call.span_kind,
            attributes=call.attributes,
            display=call.display,
        )


def reset_deferred_model_calls() -> None:
    """置いた呼び出し・工程を空にする（質問の文脈を解き直す前。前の質問のものを持ち越さない）。"""
    _DEFERRED.set(())
    _DEFERRED_STAGES.set(())


def defer_prepared_stages(stages: Sequence[PreparedStage]) -> None:
    """送信の前に済ませた工程を、この要求の後の回答の記録に残すように置く。"""
    _DEFERRED_STAGES.set(tuple(stages))


def deferred_prepared_stages() -> tuple[PreparedStage, ...]:
    return _DEFERRED_STAGES.get()


@contextmanager
def defer_model_calls() -> Iterator[None]:
    """範囲の中の記録の外の呼び出しを、この要求の後の回答の記録に残すように置く。"""
    with collect_model_calls() as calls:
        yield
    _DEFERRED.set((*_DEFERRED.get(), *calls))


def record_deferred_model_calls() -> None:
    """置いた呼び出しを今の span（回答の記録の root）の子として残す。

    比較の各回答で同じものを残す。
    """
    record_model_calls(_DEFERRED.get())


def _key(secret: str) -> bytes:
    return signing_key(
        secret,
        info=b"rag/chat-question-calls/v1",
        unavailable_message="質問の確認を準備できません。",
    )


def seal_model_calls(
    secret: str, calls: Sequence[SpanUpdate], *, limit: int | None = None
) -> str | None:
    """呼び出しを暗号化した文字列にする（無い・詳細を外しても ``limit`` を超えるなら None）。

    ``limit`` の既定は ``SEALED_CALLS_MAX``。
    """
    if not calls:
        return None
    limit = SEALED_CALLS_MAX if limit is None else limit
    aead = AESGCM(_key(secret))

    def seal(items: Sequence[SpanUpdate]) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        plain = _CALLS.dump_json(list(items))
        return base64.urlsafe_b64encode(nonce + aead.encrypt(nonce, plain, None)).decode("ascii")

    sealed = seal(calls)
    if len(sealed) <= limit:
        return sealed
    # ponytail: 大きすぎるときは詳細をまとめて外す。詳細の一部だけ残す調整は要るときに足す。
    sealed = seal([call.model_copy(update={"display": None}) for call in calls])
    return sealed if len(sealed) <= limit else None


def open_model_calls(secret: str, sealed: object) -> tuple[SpanUpdate, ...]:
    """``seal_model_calls`` の値を戻す。形・鍵の違う値は空（記録だけを諦め、回答は止めない）。"""
    if not isinstance(sealed, str) or not sealed:
        return ()
    try:
        data = base64.urlsafe_b64decode(sealed.encode("ascii"))
        plain = AESGCM(_key(secret)).decrypt(data[:_NONCE_BYTES], data[_NONCE_BYTES:], None)
        return tuple(_CALLS.validate_python(json.loads(plain)))
    except (ValueError, InvalidTag, ValidationError):
        return ()
