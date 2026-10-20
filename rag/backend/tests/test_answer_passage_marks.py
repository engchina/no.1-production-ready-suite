"""回答の記録・評価の段落の印（#2738。platform/contracts/answer-passages/passage-marks.json）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.rag.evaluation import score_case_answers, summarize_case_results
from app.rag.stored_answer import PASSAGE_MARK_RECORD_KEYS, passage_mark_record
from app.schemas.evaluation import EvaluationCase
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchResponse

CONTRACT: dict[str, Any] = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "platform/contracts/answer-passages/passage-marks.json"
    ).read_text("utf-8")
)


def _example(example_id: str) -> dict[str, Any]:
    return next(item for item in CONTRACT["examples"] if item["id"] == example_id)


def test_record_keeps_the_contract_fields_only() -> None:
    """記録は契約の 3 つの一覧と項目だけを持つ。契約の例はそのまま通る。"""
    for example in CONTRACT["examples"]:
        envelope = {key: example[key] for key in PASSAGE_MARK_RECORD_KEYS}
        assert passage_mark_record(envelope) == envelope
    noisy = {
        "passage_marks": [
            {"text": "・説明", "state": "unverified", "reasons": [{"code": "unsupported", "x": 1}]},
            {"text": " ", "state": "warning", "reasons": []},
            "壊れた値",
        ],
        "removed_passages": None,
    }
    assert passage_mark_record(noisy) == {
        "passage_marks": [
            {
                "text": "・説明",
                "state": "unverified",
                "reasons": [{"code": "unsupported", "label": "", "detail": ""}],
            }
        ],
        "removed_passages": [],
        "answer_warnings": [],
    }
    # 保存済みの過去の回答（印の記録が無い）は空の一覧として読む。
    assert passage_mark_record(None) == {
        "passage_marks": [],
        "removed_passages": [],
        "answer_warnings": [],
    }


def _response(answer: str, details: dict[str, Any]) -> SearchResponse:
    return SearchResponse(
        answer=answer,
        citations=[RetrievedChunk(chunk_id="doc-1:0", document_id="doc-1", text=answer, score=1.0)],
        trace_id="trace-1",
        guardrail_warnings=[],
        elapsed_ms=1.0,
        diagnostics=SearchDiagnostics(answer=details),
    )


def test_evaluation_records_the_marks_per_answer_and_averages_them() -> None:
    """回答ごとに矛盾で外した数・未確認の数・要注意の数を記録し、1 回答あたりの平均を出す。"""
    example = _example("keep-unverified-remove-contradicted")
    case = EvaluationCase(id="case-1", query="設定の変更", relevant_document_ids=["doc-1"])
    details = {"outcome": "conditional", **{key: example[key] for key in PASSAGE_MARK_RECORD_KEYS}}
    marked = score_case_answers(case, [(_response(example["published"], details), {})])
    old = score_case_answers(
        case.model_copy(update={"id": "case-2"}),
        [(_response("回答", {"outcome": "answered"}), {})],
    )
    counts = (marked.removed_passages, marked.unverified_passages, marked.warning_passages)
    assert counts == (1, 0, 1)
    assert (old.removed_passages, old.unverified_passages, old.warning_passages) == (
        None,
        None,
        None,
    )
    metrics = summarize_case_results([marked, old])
    assert metrics.removed_passages_per_answer == 1.0
    assert metrics.warning_passages_per_answer == 1.0
    assert metrics.metric_case_counts["removed_passages_per_answer"] == 1


def test_faithfulness_counts_the_body_of_an_all_unverified_answer() -> None:
    """本文がすべて未確認（対応は insufficient_evidence）でも、印の段落は本文として照合する。"""
    case = EvaluationCase(id="case-1", query="通知", relevant_document_ids=["doc-1"])
    unverified = {
        "outcome": "insufficient_evidence",
        "passage_marks": [
            {
                "text": "登録後は自動で通知されます。",
                "state": "unverified",
                "reasons": [{"code": "unsupported", "label": "根拠で確かめられない", "detail": ""}],
            }
        ],
        "removed_passages": [],
        "answer_warnings": [],
    }
    response = _response("登録後は自動で通知されます。", unverified)
    result = score_case_answers(case, [(response, {})])
    assert result.abstained is True
    assert result.faithfulness is not None
    assert result.unverified_passages == 1
