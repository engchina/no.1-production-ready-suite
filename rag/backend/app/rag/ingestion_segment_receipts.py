"""受理した解析区間を抽出版へ固定する。checkpointの永続状態とは別の記録。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.document import IngestionSegment

SEGMENT_RECEIPT_KEY = "ingestion_segment_receipt"


class AcceptedSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    document_id: str
    recipe_id: str | None
    page_start: int | None = None
    page_end: int | None = None
    parser_backend: str
    parser_profile: str
    progress_unit: str = "source"
    progress_start: int | None = None
    progress_end: int | None = None
    attempt_count: int = 0
    artifact_path: str | None = None
    parse_key: str = Field(min_length=1, max_length=256)
    artifact_kind: Literal["full", "segment"] = "segment"

    @property
    def segment(self) -> IngestionSegment:
        """受理済みの結果view。DB checkpointの書込成功を表すものではない。"""
        return IngestionSegment.model_validate(
            {**self.model_dump(exclude={"parse_key", "artifact_kind"}), "status": "SUCCEEDED"}
        )


class SegmentReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    document_id: str
    recipe_id: str | None
    segments: list[AcceptedSegment]


def accepted_segment_receipt(
    document_id: str,
    recipe_id: str | None,
    segments: Sequence[IngestionSegment],
    parse_keys: Mapping[tuple[int | None, int | None], str],
    *,
    artifact_kind: Literal["full", "segment"] = "segment",
) -> dict[str, object]:
    rows = []
    for segment in segments:
        page_range = (segment.page_start, segment.page_end)
        if (
            segment.document_id != document_id
            or segment.recipe_id != recipe_id
            or (page_range[0] is None) != (page_range[1] is None)
        ):
            raise ValueError("受理した解析区間の範囲が一致しません。")
        values = segment.model_dump(exclude={"status", "error_code", "error_message"})
        rows.append(
            AcceptedSegment.model_validate(
                {**values, "parse_key": parse_keys[page_range], "artifact_kind": artifact_kind}
            )
        )
    return SegmentReceipt(document_id=document_id, recipe_id=recipe_id, segments=rows).model_dump(
        mode="json"
    )


def read_segment_receipt(
    extraction: Mapping[str, object], document_id: str, recipe_id: str
) -> SegmentReceipt | None:
    artifacts = extraction.get("parser_artifacts")
    if not isinstance(artifacts, dict) or SEGMENT_RECEIPT_KEY not in artifacts:
        return None
    try:
        receipt = SegmentReceipt.model_validate(artifacts[SEGMENT_RECEIPT_KEY])
    except ValidationError:
        return None
    if not receipt.segments or receipt.document_id != document_id or receipt.recipe_id != recipe_id:
        return None
    seen = set()
    previous_end = 0
    for row in receipt.segments:
        if (
            row.document_id != document_id
            or row.recipe_id != recipe_id
            or row.segment_id in seen
            or (row.page_start is None) != (row.page_end is None)
            or (
                row.page_start is not None
                and row.page_end is not None
                and (row.page_start <= previous_end or row.page_end < row.page_start)
            )
        ):
            return None
        if row.page_end is not None:
            previous_end = row.page_end
        seen.add(row.segment_id)
    return receipt
