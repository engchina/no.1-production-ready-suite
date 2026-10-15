"""回答の生成の失敗を、利用者向けの文と「詳細」の項目にする（#2023）。

検索（REST・SSE）・チャット・MCP・品質評価の 1 ケースは、回答の pipeline の例外を同じ形で返す。

- LLM の応答が未完了（`rag_engine.adapters.oci.IncompleteResponse`。Responses API の
  `status=incomplete`）で終わった失敗は、理由ごとの文（出力の上限・安全の判定）と直し方を返す。
  送り直し（上限を広げる・推論の量を下げる）は adapter が済ませている。
- それ以外の予期しない例外は、定型文（``fallback``）のまま返す（内部の文を利用者に出さない）。

どちらも「詳細」（messaging.md §10.3）に出す項目（エラーの種類・失敗した工程・未完了の理由・
上限と token）を持つ。trace_id は呼び出し側が持つので、ここでは扱わない。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from rag_engine.adapters.oci import IncompleteResponse

# 未完了の応答の error_type（画面・SSE・MCP の error code）。3 製品の共通の error code と同じ値。
ANSWER_INCOMPLETE_ERROR_TYPE = "IncompleteResponse"
ANSWER_INCOMPLETE_ERROR_CODE = "ENTERPRISE_AI_OUTPUT_INCOMPLETE"
# 工程の記録（`execution_steps`）で、例外で終わった工程の status
# （`rag_engine.generation.execution_record`）。
_FAILED_STEP_STATUS = "エラーで中断"


@dataclass(frozen=True)
class AnswerFailure:
    """回答の失敗の、利用者向けの文と「詳細」の項目。"""

    message: str
    error_type: str
    # 未完了の理由（`incomplete_details.reason`）。未完了でなければ None。
    reason: str | None = None
    # 「詳細」に出す技術的な文（出力の上限・使った token・推論の量）。
    detail: str | None = None
    # 例外で終わった工程の名前（分かるときだけ）。
    stage: str | None = None

    @property
    def incomplete(self) -> bool:
        return self.reason is not None

    def event_fields(self) -> dict[str, Any]:
        """SSE の `error` event に足す項目（値の無いものは入れない）。"""
        fields: dict[str, Any] = {"message": self.message, "error_type": self.error_type}
        for key in ("reason", "detail", "stage"):
            if (value := getattr(self, key)) is not None:
                fields[key] = value
        return fields


def _causes(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def find_incomplete_response(exc: BaseException) -> IncompleteResponse | None:
    """例外の連鎖の中の、利用者向けの文を持つ未完了の応答（一番外の IncompleteResponse）。"""
    found: IncompleteResponse | None = None
    for item in _causes(exc):
        if isinstance(item, IncompleteResponse):
            if item.user_message:
                return item
            found = found or item
    return found


def failed_stage(exc: BaseException) -> str | None:
    """回答の工程の記録（`AnswerExecutionError.execution_steps`）で、例外で終わった最も内側の工程の名前。"""
    for item in _causes(exc):
        steps = getattr(item, "execution_steps", None)
        if not steps:
            continue
        failed = [
            str(step.get("name"))
            for step in steps
            if isinstance(step, dict)
            and step.get("status") == _FAILED_STEP_STATUS
            and step.get("name")
        ]
        if failed:
            return failed[-1]
    return None


def describe_answer_failure(exc: BaseException, fallback: str) -> AnswerFailure:
    """回答の失敗を、利用者向けの文と「詳細」の項目にする。

    未完了でなければ ``fallback`` の定型文。
    """
    stage = failed_stage(exc)
    incomplete = find_incomplete_response(exc)
    if incomplete is not None:
        return AnswerFailure(
            message=incomplete.user_message or fallback,
            error_type=ANSWER_INCOMPLETE_ERROR_TYPE,
            reason=incomplete.reason or "unknown",
            detail=incomplete.detail or None,
            stage=stage,
        )
    return AnswerFailure(message=fallback, error_type=type(exc).__name__, stage=stage)
