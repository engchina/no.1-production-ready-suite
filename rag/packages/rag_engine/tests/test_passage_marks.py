"""回答の段落の印（#2738。``platform/contracts/answer-passages/passage-marks.json``）。

RAG の生成で、合成の item と監査の結果から、本文に残す段落・印・外す段落・回答全体の警告が契約の形で出ることを
確かめる。根拠・質問・回答は合成。
"""

import json
from pathlib import Path

import pytest
from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.generation import passage_marks
from rag_engine.generation.answer_labels import answer_labels


def _contract() -> dict:
    for parent in Path(__file__).resolve().parents:
        path = parent / "platform/contracts/answer-passages/passage-marks.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("passage-marks.json")


CONTRACT = _contract()
CODES = CONTRACT["reason_codes"]

STEPS = "設定の変更\n①対象の行の終了日を入力します。\n②新しい開始日の行を追加します。\n変更は当月から反映されます。"
TABLE = (
    "<table><tr><th>区分</th><th>選択肢の値</th></tr>"
    "<tr><td>区分Ⅰ</td><td>01</td></tr>"
    "<tr><td>区分Ⅱ</td><td>02</td></tr>"
    "<tr><td>区分Ⅲ</td><td>03</td></tr></table>"
)
QUESTION = "設定を変更する手順と、区分Ⅱで選ぶ選択肢の値を教えてください。"
SUMMARY = "設定は 2 段階で変更します。"


def _assert_contract_shape(record: dict) -> None:
    """記録の 3 つが契約の形（キー・状態・理由の code と label）であること。"""
    example = CONTRACT["examples"][0]
    assert set(record) == {"passage_marks", "removed_passages", "answer_warnings"}
    for mark in record["passage_marks"]:
        assert set(mark) == set(example["passage_marks"][0])
        assert mark["state"] in CONTRACT["states"]
        # state は理由の状態のうち unverified を warning より優先したもの。
        states = {CODES[reason["code"]]["state"] for reason in mark["reasons"]}
        assert mark["state"] == ("unverified" if "unverified" in states else "warning")
        for reason in mark["reasons"]:
            assert set(reason) == {"code", "label", "detail"}
            assert CODES[reason["code"]]["kind"] == "passage"
            assert reason["label"] == CODES[reason["code"]]["label"]
    for passage in record["removed_passages"]:
        assert set(passage) == set(example["removed_passages"][0])
        assert [CODES[reason["code"]]["kind"] for reason in passage["reasons"]] == ["removed"] * len(passage["reasons"])
    for warning in record["answer_warnings"]:
        assert set(warning) == {"code", "label", "detail"}
        assert CODES[warning["code"]]["kind"] == "answer"
        assert warning["label"] == CODES[warning["code"]]["label"]


def test_vocabulary_matches_the_contract():
    """code・状態・label は契約の reason_codes と同じ（label は言語資源から）。"""
    passage = {code: spec["state"] for code, spec in CODES.items() if spec["kind"] == "passage"}
    assert passage == passage_marks.PASSAGE_REASON_STATES
    assert {code for code, spec in CODES.items() if spec["kind"] == "removed"} == passage_marks.REMOVED_REASONS
    assert {code for code, spec in CODES.items() if spec["kind"] == "answer"} == passage_marks.ANSWER_WARNING_REASONS
    assert set(CONTRACT["states"]) == {passage_marks.UNVERIFIED, passage_marks.WARNING}
    assert answer_labels().passage_reason_labels == {code: spec["label"] for code, spec in CODES.items()}
    with pytest.raises(ValueError):
        passage_marks.reason("unknown_code")


def test_contract_examples_have_the_contract_shape():
    for example in CONTRACT["examples"]:
        _assert_contract_shape({key: example[key] for key in ("passage_marks", "removed_passages", "answer_warnings")})
        # 外した段落は published に無く、印の段落は published にある。
        for passage in example["removed_passages"]:
            assert passage["text"] not in example["published"]
        for mark in example["passage_marks"]:
            assert mark["text"] in example["published"]


def test_state_prefers_unverified_and_reasons_are_merged_per_passage():
    reasons = [passage_marks.reason("item_check", "a"), passage_marks.reason("unsupported", "b"), passage_marks.reason("item_check", "a")]
    mark = passage_marks.passage_mark("・説明", reasons)
    assert mark["state"] == "unverified"
    assert [reason["code"] for reason in mark["reasons"]] == ["item_check", "unsupported"]
    merged = passage_marks.merge_marks(
        [
            passage_marks.passage_mark("・説明", [passage_marks.reason("item_check", "a")]),
            passage_marks.passage_mark("・説明", reasons[1:2]),
        ]
    )
    assert [(item["text"], item["state"]) for item in merged] == [("・説明", "unverified")]
    assert passage_marks.passage_mark("・説明", [passage_marks.reason("contradicted")]) is None
    assert passage_marks.mark_counts(
        {
            "passage_marks": [mark, passage_marks.passage_mark("・別", [passage_marks.reason("superseded")])],
            "removed_passages": [{"text": "x", "reasons": []}],
            "answer_warnings": [],
        }
    ) == {"removed": 1, "unverified": 1, "warning": 1, "answer_warnings": 0}


def _run_example(step_review: str = "supported"):
    question = "①設定を変更する手順を教えてください。②区分Ⅱで選ぶ選択肢の値を教えてください。"
    ctx = context(record(0, STEPS, "設定-(1)変更"), record(1, TABLE, "設定-(2)区分", page=21))
    found = ids(ctx, question)
    steps, table = found[STEPS], found[TABLE]
    value = draft(
        item(steps, "対象の行の終了日を入力します。", "①対象の行の終了日を入力します。", request_id="Q1"),
        item(steps, "新しい開始日の行を追加します。", "②新しい開始日の行を追加します。", request_id="Q1"),
        item(steps, "変更は翌月から反映されます。", "変更は当月から反映されます。", kind="rule", request_id="Q1"),
        item(table, "区分Ⅱの選択肢の値は 01 です。", "区分Ⅱ 02", kind="rule", request_id="Q1"),
        summary=SUMMARY,
    )
    value["summary_item_indexes"] = [0, 1]
    review = audit(
        (0, step_review, "matched", ""),
        (1, "supported", "matched", ""),
        (2, "contradicted", "matched", ""),
        (3, "supported", "matched", ""),
        requests=[("Q1", "addressed")],
    )
    review["summary_basis"] = ["対象の行の終了日を入力します。", "新しい開始日の行を追加します。"]
    scope_plan = {
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_scope": [
                    {"evidence_id": steps, "target": "設定の変更", "relation": "direct"},
                    {"evidence_id": table, "target": "区分の選択肢", "relation": "direct"},
                ],
            }
        ]
    }
    return run(FakeModel([value], [review], scope_plan=scope_plan), ctx, question).response


def test_legacy_language_labels_fall_back_for_new_citation_causes():
    from rag_parser_core.language import bind_pack, build_pack, builtin_pack

    raw = builtin_pack().to_dict()
    raw["answer_labels"]["passage_reason_labels"] = [
        value
        for value in raw["answer_labels"]["passage_reason_labels"]
        if not value.split("=", 1)[1].startswith("citation_") or value.endswith("=citation_error")
    ]
    with bind_pack(build_pack(raw)):
        result = passage_marks.reason("citation_extraction_failed")
    assert result["label"] == CODES["citation_extraction_failed"]["label"]
    assert passage_marks.passage_mark("合成の説明。", [result])["state"] == "unverified"
