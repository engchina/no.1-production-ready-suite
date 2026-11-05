"""単位選択のguide投影は原文・出典を広げず、後方を切り捨てない。"""

from unittest.mock import patch

from rag_engine.evaluation.answer_eval import _evidence_fragments
from rag_engine.evaluation.final_validation_units import prepare_unit_request
from rag_engine.evaluation.guide_step_validation import _items, _source_catalog, validate_guide_steps
from rag_engine.retrieval.guide_steps import GuidePlan


def selected(text, selected_index=-1):
    evidence = [{"id": "chunk-a", "document_id": "doc-a", "page_start": 1, "page_end": 1, "text": text}]
    fragments = list(_evidence_fragments(evidence))
    prepared = prepare_unit_request({"evidence_items": fragments})
    row = prepared.bind([list(prepared.units)[selected_index]])
    spans = [{"source_id": "chunk-a", "text": text}]
    claim = {"status": "supported", "answer_quote": "確認を押します。", "answer_passage_id": "P1"}
    return evidence, row, spans, claim


def test_one_fragment_two_steps_keeps_only_selected_unit_and_runs_guide():
    evidence, rows, spans, claim = selected("対象を選びます。\n確認を押します。")
    checked, paragraphs = _items([claim], spans, _source_catalog(evidence, spans), {0: rows})
    assert [item.item.citations[0].quote for item in checked] == ["確認を押します。"]
    assert paragraphs == [claim["answer_quote"]]
    plan = GuidePlan.from_payload(
        "",
        1,
        [
            {"id": "step-a", "title": "対象を選びます。", "retrieval_hints": ["対象"]},
            {"id": "step-b", "title": "確認を押します。", "retrieval_hints": ["確認"]},
        ],
    )
    assert plan is not None
    result = validate_guide_steps(plan, [claim], evidence, selected_evidence={0: rows})
    assert [step["step_id"] for step in result["steps"]] == ["step-a", "step-b"]
    assert all(step["status"] != "explained" for step in result["steps"])


def test_selected_unit_after_600_reaches_guide_without_prefix():
    text = "説明の原文です。" * 90 + "\n確認を押します。"
    evidence, rows, spans, claim = selected(text)
    assert rows[0]["start"] > 600
    checked, _ = _items([claim], spans, _source_catalog(evidence, spans), {0: rows})
    assert [item.item.citations[0].quote for item in checked] == ["確認を押します。"]


def test_unsplittable_long_unit_is_not_lost_after_guide_600_char_boundary():
    text = "あ" * 1301
    evidence, rows, spans, claim = selected(text)
    checked, _ = _items([claim], spans, _source_catalog(evidence, spans), {0: rows})
    quotes = [item.item.citations[0].quote for item in checked]
    assert list(map(len, quotes)) == [600, 600, 101]
    assert "".join(quotes) == text and rows[0]["text"] == text


def test_unit_from_other_source_never_moves_to_same_text_source():
    evidence, rows, spans, claim = selected("確認を押します。")
    rows[0]["source_id"] = "other-source"
    plan = GuidePlan.from_payload("", 1, [{"id": "step-a", "title": "確認", "retrieval_hints": ["確認"]}])
    assert plan is not None
    result = validate_guide_steps(plan, [claim], evidence, selected_evidence={0: rows})
    coverage = result["steps"][0]["validation"]["body_coverage"]
    assert coverage == {"execution": "not_run", "status": "unknown", "reason": "unit_source_unconfirmed"}


def test_budget_failure_is_explicit_unknown_and_never_calls_embedding():
    evidence, rows, _spans, claim = selected("確認を押します。")
    plan = GuidePlan.from_payload("", 1, [{"id": "step-a", "title": "確認", "retrieval_hints": ["確認"]}])
    assert plan is not None
    with patch("rag_engine.evaluation.guide_step_validation.grounded.GuideStepSemantics") as semantics:
        result = validate_guide_steps(plan, [claim], evidence, selected_evidence={0: rows}, max_input_bytes=1)
    semantics.assert_not_called()
    assert result["steps"][0]["status"] == "unknown"
    assert result["steps"][0]["validation"]["body_coverage"]["reason"] == "guide_input_too_large"


def test_question_selection_is_not_a_document_operation():
    evidence, rows, spans, claim = selected("確認を押します。")
    checked, _ = _items([claim], spans, _source_catalog(evidence, spans), {0: [{**rows[0], "source_id": "question:1"}]})
    assert checked == []
