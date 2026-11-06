"""解析の受理事実をjobの試行へ固定する。進捗の周期保存とは独立する。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.rag.ingestion_segment_receipts import (
    SEGMENT_RECEIPT_KEY,
    SegmentReceipt,
    read_segment_receipt,
)
from app.schemas.document import IngestionSegment

logger = logging.getLogger(__name__)
ATTEMPT_RECEIPT_KEY = "_segment_attempt_receipt"
Writer = Callable[[dict[str, Any]], Awaitable[None]]
_ACTIVE: ContextVar[Writer | None] = ContextVar("ingestion_attempt_receipt", default=None)


class RemainingSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    status: Literal["FAILED", "CANCELLED", "QUEUED"]


class AttemptReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    job_id: str
    attempt_count: int = Field(ge=1)
    document_id: str
    recipe_id: str
    recipe_revision: int = Field(ge=1)
    accepted: SegmentReceipt
    remaining: list[RemainingSegment]
    remaining_segments: list[IngestionSegment] = Field(default_factory=list)


@contextmanager
def attempt_receipt_scope(writer: Writer) -> Iterator[None]:
    token = _ACTIVE.set(writer)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


async def record_attempt_receipt(payload: dict[str, Any]) -> None:
    writer = _ACTIVE.get()
    if writer is None:
        return
    try:
        await writer(payload)
    except Exception as exc:  # noqa: BLE001 - 元の解析例外を優先する
        logger.warning(
            "ingestion_attempt_receipt_unrecorded", extra={"error_type": type(exc).__name__}
        )


def read_attempt_receipt(
    payload: Mapping[str, Any],
    *,
    job_id: str,
    attempt_count: int,
    document_id: str,
    recipe_id: str,
    recipe_revision: int,
) -> AttemptReceipt | None:
    try:
        receipt = AttemptReceipt.model_validate(payload.get(ATTEMPT_RECEIPT_KEY))
    except ValidationError:
        return None
    if any(
        getattr(receipt, key) != value
        for key, value in dict(
            job_id=job_id,
            attempt_count=attempt_count,
            document_id=document_id,
            recipe_id=recipe_id,
            recipe_revision=recipe_revision,
        ).items()
    ):
        return None
    if receipt.accepted.document_id != document_id or receipt.accepted.recipe_id != recipe_id:
        return None
    if (
        receipt.accepted.segments
        and read_segment_receipt(
            {"parser_artifacts": {SEGMENT_RECEIPT_KEY: receipt.accepted.model_dump(mode="json")}},
            document_id,
            recipe_id,
        )
        is None
    ):
        return None
    accepted = receipt.accepted.segments
    ids = [item.segment_id for item in accepted] + [item.segment_id for item in receipt.remaining]
    if len(ids) != len(set(ids)) or any(
        item.document_id != document_id or item.recipe_id != recipe_id for item in accepted
    ):
        return None
    if len(receipt.remaining_segments) != len(receipt.remaining):
        return None
    if receipt.remaining and {(r.segment_id, r.status) for r in receipt.remaining_segments} != {
        (r.segment_id, r.status) for r in receipt.remaining
    }:
        return None
    if any(
        r.document_id != document_id or r.recipe_id != recipe_id for r in receipt.remaining_segments
    ):
        return None
    return receipt
