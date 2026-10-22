"""回答の段落の印（#2738。``platform/contracts/answer-passages/passage-marks.json``）。

RAG の生成で、合成の item と監査の結果から、本文に残す段落・印・外す段落・回答全体の警告が契約の形で出ることを
確かめる。根拠・質問・回答は合成。
"""

import json
from pathlib import Path

import pytest
from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.evaluation.answer_eval import _answer_passages
from rag_engine.evaluation.answer_validation import claim_mark_code
from rag_engine.generation import grounded, passage_marks
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
    ctx = context(record(0, STEPS, "設定-(1)変更"), record(1, TABLE, "設定-(2)区分", page=21))
    found = ids(ctx, QUESTION)
    steps, table = found[STEPS], found[TABLE]
    value = draft(
        item(steps, "対象の行の終了日を入力します。", "①対象の行の終了日を入力します。", request_id="Q1"),
        item(steps, "新しい開始日の行を追加します。", "②新しい開始日の行を追加します。", request_id="Q1"),
        item(steps, "変更は翌月から反映されます。", "変更は当月から反映されます。", kind="rule", request_id="Q1"),
        item(table, "区分Ⅱの選択肢の値は 01 です。", "区分Ⅱ 02", kind="rule", request_id="Q2"),
        summary=SUMMARY,
    )
    review = audit(
        (0, step_review, "matched", ""),
        (1, "supported", "matched", ""),
        (2, "contradicted", "matched", ""),
        (3, "supported", "matched", ""),
        requests=[("Q1", "addressed"), ("Q2", "addressed")],
    )
    return run(FakeModel([value], [review]), ctx, QUESTION).response


def test_contradicted_and_table_mismatched_items_are_removed():
    """監査が矛盾とした説明と、根拠の表の対応を取り違えた説明を外し、表の正しい対応を理由に出す（#2998）。"""
    result = _run_example()
    record = {key: result.envelope[key] for key in ("passage_marks", "removed_passages", "answer_warnings")}
    _assert_contract_shape(record)
    labels = answer_labels()
    body = result.answer_text.split(labels.removed_section, 1)[0]
    assert result.answer_text.startswith(SUMMARY)
    assert "対象の行の終了日を入力します。" in body and "新しい開始日の行を追加します。" in body
    assert "変更は翌月から反映されます。" not in body
    assert "区分Ⅱの選択肢の値は 01 です。" not in body
    removed, table = record["removed_passages"]
    assert removed == {"text": "変更は翌月から反映されます。", "reasons": [{"code": "contradicted", "label": "根拠と矛盾", "detail": "r"}]}
    assert table["text"] == "区分Ⅱの選択肢の値は 01 です。"
    assert [reason["code"] for reason in table["reasons"]] == ["contradicted"]
    assert table["reasons"][0]["detail"].startswith("根拠の表では『区分Ⅱ』の値は『02』（『01』は別の行の値）です。")
    assert record["passage_marks"] == [] and record["answer_warnings"] == []
    # 本文の節は外した段落と理由の label だけ（表の正しい対応は記録の理由の detail にあり、画面が出す）。
    assert labels.removed_section in result.answer_text and "「区分Ⅱの選択肢の値は 01 です。」" in result.answer_text
    assert result.envelope["outcome"] == "conditional"
    assert result.generation_trace["finalization"]["passage_mark_counts"] == {
        "removed": 2,
        "unverified": 0,
        "warning": 0,
        "answer_warnings": 0,
    }


def test_unverified_step_stays_in_the_body_with_a_mark():
    result = _run_example(step_review="unsupported")
    marks = {mark["text"]: mark for mark in result.envelope["passage_marks"]}
    assert marks["・対象の行の終了日を入力します。"]["state"] == "unverified"
    assert "・対象の行の終了日を入力します。" in result.answer_text
    # 印の段落の根拠は、確かめた段落の根拠と同じなので確認済み（表の対応を取り違えた説明は外したので、その根拠は使わない）。
    assert {entry["answer_use"] for entry in result.cited_evidence} == {"confirmed"}


def test_off_goal_keeps_the_body_and_warns_the_whole_answer():
    """契約の例 guide-error-keeps-body と同じ形: 回答全体の警告を出し、本文は定型文に置き換えない。"""
    ctx = context(record(0, STEPS, "設定-(1)変更"))
    eid = ids(ctx, QUESTION)[STEPS]
    value = draft(
        item(eid, "対象の行の終了日を入力します。", "①対象の行の終了日を入力します。"),
        item(eid, "登録後は自動で通知されます。", "②新しい開始日の行を追加します。", kind="rule"),
        summary="",
    )
    off = {**audit((0, "supported", "matched", ""), (1, "unsupported", "matched", "")), "goal_alignment": "off_target"}
    result = run(FakeModel([value], [off]), ctx, QUESTION).response
    passages = {key: result.envelope[key] for key in ("passage_marks", "removed_passages", "answer_warnings")}
    _assert_contract_shape(passages)
    assert [warning["code"] for warning in passages["answer_warnings"]] == ["off_goal"]
    assert [(mark["text"], mark["state"]) for mark in passages["passage_marks"]] == [("・登録後は自動で通知されます。", "unverified")]
    assert "対象の行の終了日を入力します。" in result.answer_text
    assert result.envelope["outcome"] == "conditional"


def test_system_sections_are_not_claims():
    """「根拠と矛盾し除いた内容」「確認が必要な点」の行はシステムの行で、主張の監査・評価に数えない。"""
    result = _run_example(step_review="unsupported")
    generated = grounded.generated_section_lines(result.answer_text)
    labels = answer_labels()
    tail = result.answer_text.split(labels.removed_section, 1)[1]
    for line in tail.splitlines():
        line = line.strip()
        if line.startswith(labels.item_bullet):
            assert line in generated
        elif line:
            assert grounded.is_structural_line(line), line
    passages = [passage["text"] for passage in _answer_passages(result.answer_text)]
    assert not any("根拠と矛盾（" in text for text in passages)
    assert any("対象の行の終了日を入力します。" in text for text in passages)  # 印の段落は本文の主張として数える
    assert not any("区分Ⅱの選択肢の値は 01 です。" in text for text in passages)  # 外した説明は数えない


@pytest.mark.parametrize(
    ("status", "support_check", "expected"),
    [
        ("contradicted", "", "contradicted"),
        ("unsupported", "", "unsupported"),
        ("unsupported", "target_mismatch", "target_mismatch"),
        ("unsupported", "value_unbound", "value_unbound"),
        ("citation_error", "", "citation_error"),
        ("citation_error", "quote_unmatched", "quote_unmatched"),
        ("unassessed", "", "unassessed"),
        ("figure_only", "", "figure_not_read"),
        ("supported", "", ""),
        ("data_confirmation", "", ""),
        ("not_a_claim", "", ""),
    ],
)
def test_validation_verdicts_map_to_contract_codes(status, support_check, expected):
    """最終の検証の判定（語彙は変えない）を、契約の理由の code に写す。"""
    code = claim_mark_code(status, support_check)
    assert code == expected
    assert not code or code in CODES
