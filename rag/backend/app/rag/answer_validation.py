"""回答の最終の検証（#1246、handoff §12）。

呼び出し側（Agent など）が渡した根拠の参照（document_id・chunk_id）を、今の利用者の権限と今の版で
読み直し（呼び出し側の本文は信じない）、回答の主張を rag_engine の監査（モデル 1 回）で確かめる。
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.answer_engine import build_engine_settings

# 主張の監査で「検証に通った」とみなさない判定。
_BLOCKING_STATUSES = frozenset({"unsupported", "contradicted", "citation_error", "unassessed"})


@dataclass(frozen=True)
class EvidenceRef:
    document_id: str
    chunk_id: str


@dataclass
class AnswerValidation:
    status: str
    valid: bool
    claim_checks: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    missing_evidence: list[EvidenceRef] = field(default_factory=list)
    stale_evidence: list[EvidenceRef] = field(default_factory=list)
    evidence_truncated: bool = False


def is_valid(status: str, counts: dict[str, int], *, unreadable: int) -> bool:
    """矛盾・裏付けの無い主張・読めない根拠が無く、裏付けのある主張が 1 つ以上あるか。"""
    if status != "completed" or unreadable:
        return False
    if any(counts.get(item, 0) for item in _BLOCKING_STATUSES):
        return False
    return counts.get("supported", 0) > 0


async def validate_answer(
    question: str,
    answer: str,
    refs: Sequence[EvidenceRef],
    settings: Settings,
    *,
    oracle: OracleClient | None = None,
) -> AnswerValidation:
    oracle = oracle or OracleClient()
    items: list[dict[str, Any]] = []
    missing: list[EvidenceRef] = []
    stale: list[EvidenceRef] = []
    for ref in dict.fromkeys(refs):
        chunk = await oracle.retrievable_chunk(ref.document_id, ref.chunk_id)
        if chunk is None:
            exists = await oracle.accessible_chunk_exists(ref.document_id, ref.chunk_id)
            (stale if exists else missing).append(ref)
            continue
        metadata = dict(chunk.metadata)
        items.append(
            {
                "id": chunk.chunk_id,
                "source": chunk.file_name or chunk.document_id,
                "page_start": metadata.get("page_start"),
                "page_end": metadata.get("page_end"),
                "text": chunk.text,
            }
        )
    if not items:
        return AnswerValidation(
            status="no_evidence", valid=False, missing_evidence=missing, stale_evidence=stale
        )

    def run() -> dict[str, Any]:
        from rag_engine.evaluation.answer_validation import validate_answer_claims

        with tempfile.TemporaryDirectory(prefix="rag-engine-validate-") as work:
            engine_settings = build_engine_settings(settings, output_dir=Path(work))
            return validate_answer_claims(question, answer, items, engine_settings)

    result = await asyncio.to_thread(run)
    counts = {str(key): int(value) for key, value in (result.get("counts") or {}).items()}
    status = str(result.get("status") or "error")
    return AnswerValidation(
        status=status,
        valid=is_valid(status, counts, unreadable=len(missing) + len(stale)),
        claim_checks=list(result.get("claim_checks") or []),
        counts=counts,
        missing_evidence=missing,
        stale_evidence=stale,
        evidence_truncated=bool(result.get("evidence_truncated")),
    )


__all__ = ["AnswerValidation", "EvidenceRef", "is_valid", "validate_answer"]
