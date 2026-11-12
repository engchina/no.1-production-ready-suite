"""根拠 0 件で終えたときの段の判定（#2401）。合成の候補だけを使う。"""

import pytest

from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.zero_evidence import ZERO_EVIDENCE_STAGE_LABELS, zero_evidence_stage


def _record(record_id: str) -> AnswerRecord:
    return AnswerRecord(id=record_id, engine="docling", engine_label="docling", page=1, seq_no=1, category="Text", text="本文")


@pytest.mark.parametrize(
    ("context", "stage", "candidates"),
    [
        (AnswerContext(records=[], text=""), "retrieval", 0),
        (AnswerContext(records=[], text="", ranked_candidates=(_record("c1"), _record("c2"))), "context", 2),
    ],
)
def test_zero_evidence_stage(context: AnswerContext, stage: str, candidates: int) -> None:
    result = zero_evidence_stage(context)
    assert result["stage"] == stage
    assert result["label"] == ZERO_EVIDENCE_STAGE_LABELS[stage]
    assert result["candidate_count"] == candidates
