"""上位の根拠の読み直し（#4231）。合成の文書と質問で、原則と例外・2 つの分岐・複合の質問を確かめる。"""

from __future__ import annotations

from dataclasses import replace

import pytest
from audit_fixtures import legacy_audit_result
from pydantic import ValidationError

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation.answering import AnswerContext, AnswerRecord, _grounded_spans, synthesize_grounded_answer
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.generation.coverage_review import (
    COVERAGE_REVIEW_MAX_ADDED,
    COVERAGE_REVIEW_MAX_CHARS,
    COVERAGE_REVIEW_MAX_SPANS,
    coverage_review_input,
    coverage_review_model,
    coverage_review_targets,
    merge_coverage_review,
)
from rag_engine.models.audit_wire import is_audit_wire
from rag_engine.retrieval.context_builder import ContextParentEvidence


def _span(label: str, text: str = "合成の原文。", **extra) -> dict:
    return {"label": label, "evidence_id": f"id-{label}", "text": text, **extra}


def _draft(items: list[dict], summary: str = "草稿の結論。"):
    wire = generation_draft_wire_model().model_validate(
        {"items": items, "summary": summary, "summary_item_indexes": [0], "confidence": "high"}
    )
    return canonical_generation_draft(wire)


def _item(label: str, start: int, end: int, text: str = "草稿の説明。") -> dict:
    return {
        "request_id": "Q1",
        "kind": "rule",
        "text": text,
        "citations": [{"evidence_id": label, "line_start": start, "line_end": end}],
        "applies": "matched",
        "condition": "",
    }


UNITS = [(0, 12), (12, 24), (24, 36)]
SOURCE = "対象 B は 8 桁とする。" + "対象 C は 6 桁とする。" + "対象 D は 4 桁とする。"


def _review(labels: list[str], additions: list[dict], *, points: list[str] | None = None, summary: str = "合わせた結論。"):
    return coverage_review_model(labels).model_validate({"points": points or ["観点"], "additions": additions, "summary": summary})


def _add(label: str, start: int, end: int, text: str, point: int = 0) -> dict:
    return {"point": point, "evidence_id": label, "line_start": start, "line_end": end, "text": text}


def _merge(draft, review, targets):
    return merge_coverage_review(draft, review, targets, units=lambda _span: UNITS, section_label=lambda _span: "節", request_id="Q1")


def test_only_given_labels_can_be_cited() -> None:
    model = coverage_review_model(["E1", "E2"])
    with pytest.raises(ValidationError):
        model.model_validate({"points": [], "additions": [_add("E9", 1, 1, "説明。")], "summary": ""})


def test_exception_in_the_same_section_is_added_with_its_own_citation() -> None:
    # 原則だけを答えた草稿に、同じ節の例外を足す。
    draft = _draft([_item("E1", 1, 1, "対象 A は 10 桁以上とする。")])
    targets = [_span("E1", SOURCE), _span("E2", SOURCE)]
    review = _review(["E1", "E2"], [_add("E2", 1, 2, "対象 B は 8 桁とする。")], summary="対象 A は 10 桁以上、対象 B は 8 桁とする。")
    merged, record = _merge(draft, review, targets)
    assert [item.text for item in merged.items] == ["対象 A は 10 桁以上とする。", "対象 B は 8 桁とする。"]
    assert merged.items[1].citations[0].evidence_id == "E2"
    assert merged.items[1].citations[0].lines == "1-2"
    assert merged.items[1].request_id == "Q1"
    assert merged.summary == "対象 A は 10 桁以上、対象 B は 8 桁とする。"
    assert merged.summary_item_indexes == [0, 1]
    assert record["added"] == ["E2"]


def test_compound_question_gets_the_other_target_from_its_own_evidence() -> None:
    # 2 つの対象を尋ねる質問で、草稿が片方だけを答えたとき、別の根拠からもう片方を足す。
    draft = _draft([_item("E1", 1, 1, "対象 B は 8 桁とする。")])
    review = _review(["E1", "E2"], [_add("E2", 2, 2, "対象 C は 6 桁とする。", point=1)], points=["対象 B の桁", "対象 C の桁"])
    merged, record = _merge(draft, review, [_span("E1", SOURCE), _span("E2", SOURCE)])
    assert [item.text for item in merged.items] == ["対象 B は 8 桁とする。", "対象 C は 6 桁とする。"]
    assert record["points"] == ["対象 B の桁", "対象 C の桁"]


def test_line_end_past_the_evidence_is_moved_to_its_last_line() -> None:
    draft = _draft([_item("E1", 1, 1)])
    merged, record = _merge(draft, _review(["E2"], [_add("E2", 3, 9, "対象 D は 4 桁とする。")]), [_span("E2", SOURCE)])
    assert merged.items[1].citations[0].lines == "3"
    assert record["added"] == ["E2"]


def test_rewording_of_a_known_item_is_not_added() -> None:
    draft = _draft([_item("E1", 1, 1, "対象 B は 8 桁とする。対象 C は 6 桁とする。")])
    review = _review(["E2"], [_add("E2", 1, 2, "対象 C は 6 桁、対象 B は 8 桁とする。")])
    merged, record = _merge(draft, review, [_span("E2", SOURCE)])
    assert merged is draft
    assert record["dropped"] == {"same_content": 1}


def test_no_addition_keeps_the_draft_and_its_summary() -> None:
    # 足りない説明が無ければ、モデルが書いた結論も採らない。根拠の無い質問へ答えを作らない。
    draft = _draft([_item("E1", 1, 1)])
    merged, record = _merge(draft, _review(["E1"], [], summary="書き直した結論。"), [_span("E1", SOURCE)])
    assert merged is draft
    assert merged.summary == "草稿の結論。"
    assert record["added"] == []


def test_text_of_another_evidence_is_not_added() -> None:
    # 別の根拠の内容を、この根拠の行に結び付けた説明は足さない。
    draft = _draft([_item("E1", 1, 1)])
    review = _review(["E2"], [_add("E2", 1, 1, "申請の期限は翌月の末日までに届け出ること。")])
    merged, record = _merge(draft, review, [_span("E2", SOURCE)])
    assert merged is draft
    assert record["dropped"] == {"unsupported_text": 1}


def test_code_rejects_invalid_ranges_and_duplicates() -> None:
    draft = _draft([_item("E1", 1, 3, "対象 D は 4 桁とする。")])
    targets = [_span(label, SOURCE) for label in ("E1", "E2", "E3", "E4")]
    review = _review(
        ["E1", "E2", "E3", "E4"],
        [
            _add("E1", 2, 2, "対象 C は 6 桁とする。"),
            _add("E2", 9, 9, "範囲の外。"),
            _add("E3", 3, 3, "対象 D は 4 桁とする。"),
            _add("E4", 3, 2, "逆の範囲。"),
        ],
    )
    merged, record = _merge(draft, review, targets)
    assert merged is draft
    assert record["dropped"] == {"already_cited": 1, "invalid_range": 2, "same_content": 1}


def test_additions_are_limited_in_rank_order() -> None:
    labels = [f"E{index}" for index in range(1, COVERAGE_REVIEW_MAX_ADDED + 4)]
    draft = _draft([_item("E1", 3, 3)])
    # 根拠ごとに別の字の並びにして、同じ内容として外れないようにする。
    texts = {label: "".join(chr(0x4E00 + index * 40 + offset) for offset in range(30)) for index, label in enumerate(labels)}
    # モデルが順位の低い根拠から返しても、順位の高い根拠を残す。
    additions = [_add(label, 1, 1, texts[label]) for label in reversed(labels)]
    merged, record = merge_coverage_review(
        draft,
        _review(labels, additions),
        [_span(label, texts[label]) for label in labels],
        units=lambda span: [(0, len(span["text"]))],
        section_label=lambda _span: "節",
        request_id="Q1",
    )
    assert record["added"] == labels[:COVERAGE_REVIEW_MAX_ADDED]
    assert record["dropped"]["over_limit"] == 3
    assert len(merged.items) == 1 + COVERAGE_REVIEW_MAX_ADDED


def test_targets_follow_rank_and_stay_within_the_limits() -> None:
    long_text = "長" * (COVERAGE_REVIEW_MAX_CHARS + 1)
    spans = [
        _span("E1"),
        _span("E2", long_text),  # 字数に入らない根拠は飛ばして次を読む
        _span("E3"),
        _span("E4", conversation_attachment=True),
        _span("E5"),
    ]
    assert [span["label"] for span in coverage_review_targets(spans, excluded_labels=["E5"])] == ["E1", "E3"]
    many = [_span(f"E{index}") for index in range(1, COVERAGE_REVIEW_MAX_SPANS + 5)]
    assert len(coverage_review_targets(many)) == COVERAGE_REVIEW_MAX_SPANS


def test_input_passes_the_draft_after_the_evidence_and_folds_width() -> None:
    draft = _draft([_item("E1", 1, 1, "草稿の説明。")])
    text = coverage_review_input(
        "ＡとＢは?",
        draft,
        [[_span("E1", "原文。")]],
        numbered_text=lambda span: "1| " + span["text"],
        section_label=lambda _span: "節",
    )
    assert "AとB" in text
    assert text.index('"evidence"') < text.index('"draft_items"')


# ---- 回答の流れの中で ------------------------------------------------------

QUESTION = "対象 A と対象 B の上限はいくつですか。"


TEXT_A, TEXT_B = "対象 A の上限は 10 とする。", "対象 B の上限は 8 とする。"


def _record(uid: str, text: str, section: str) -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        chunk_uid=uid,
        chunk_id=uid,
        source="合成の規程.pdf",
        source_run_id="run",
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        chunk_level="parent",
        text=text,
        metadata={"section_path": [section]},
    )


def _context() -> AnswerContext:
    records = [_record("p1", TEXT_A, "節 1"), _record("p2", TEXT_B, "節 2")]
    tree = tuple(ContextParentEvidence(record=record, role="synthesis_parent", reason="r") for record in records)
    return AnswerContext(records=records, text=TEXT_A + "\n" + TEXT_B, evidence_tree=tree)


class _Model:
    def __init__(self, addition: dict | None = None) -> None:
        self.calls: list[str] = []
        self.addition = addition

    def __call__(self, system, prompt, settings, schema, **options):
        self.calls.append(schema.__name__)
        if is_audit_wire(schema):
            return legacy_audit_result({"reviews": [], "request_reviews": [], "summary_supported": True}, schema)
        if schema.__name__ == "CoverageReview":
            return schema.model_validate(
                {
                    "points": ["対象 A", "対象 B"],
                    "additions": [self.addition] if self.addition else [],
                    "summary": "対象 A の上限は 10、対象 B の上限は 8 とする。",
                }
            )
        return schema.model_validate(
            {
                "request_coverage": [
                    {"request_id": "Q1", "evidence_scope": [{"evidence_id": "E1", "target": "対象 A", "relation": "direct"}]}
                ],
                "items": [_item("E1", 1, 1, "対象 A の上限は 10 とする。")],
                "summary": "対象 A の上限は 10 とする。",
                "summary_item_indexes": [0],
                "confidence": "high",
            }
        )


def _answer(model: _Model, *, enabled: bool = True):
    def unused(*_a, **_k):
        raise AssertionError("unexpected I/O")

    settings = replace(get_settings(), answer_audit_mode=grounded.AUDIT_MODE_NONE, answer_coverage_review=enabled)
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        return synthesize_grounded_answer(QUESTION, _context(), settings, image_prompt_mode="text_only").response


def test_missing_branch_reaches_the_answer_through_the_normal_checks() -> None:
    assert len(_grounded_spans(QUESTION, _context(), (), None)) == 2
    model = _Model(_add("E2", 1, 1, "対象 B の上限は 8 とする。", point=1))
    response = _answer(model)
    assert "CoverageReview" in model.calls
    assert "対象 B の上限は 8" in response.answer_text
    assert "対象 A の上限は 10、対象 B の上限は 8" in response.answer_text


def test_invalid_range_is_not_added_and_summary_stays() -> None:
    model = _Model(_add("E2", 5, 5, "対象 B の上限は 8 とする。", point=1))
    response = _answer(model)
    # 範囲が無効な説明は足さず、結論も書き換えない。
    assert "対象 B の上限は 8" not in response.answer_text


def test_disabled_setting_makes_no_extra_call() -> None:
    model = _Model()
    _answer(model, enabled=False)
    assert "CoverageReview" not in model.calls


def test_gap_only_draft_is_not_forced_to_answer() -> None:
    class GapModel(_Model):
        def __call__(self, system, prompt, settings, schema, **options):
            self.calls.append(schema.__name__)
            if is_audit_wire(schema):
                return legacy_audit_result({"reviews": [], "request_reviews": [], "summary_supported": True}, schema)
            return schema.model_validate(
                {
                    "request_coverage": [{"request_id": "Q1", "evidence_scope": []}],
                    "items": [
                        {
                            "request_id": "Q1",
                            "kind": "gap",
                            "text": "資料に記載がありません。",
                            "citations": [],
                            "applies": "matched",
                            "condition": "",
                        }
                    ],
                    "summary": "",
                    "summary_item_indexes": [],
                    "confidence": "low",
                }
            )

    model = GapModel()
    _answer(model)
    assert "CoverageReview" not in model.calls
