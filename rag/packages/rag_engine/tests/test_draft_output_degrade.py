"""完了したが大きすぎる・壊れた回答文の生成の出力の扱い（#3849）を、決定論の応答で確かめる。

- 同じ内容の計画・説明の繰り返し（長い出力でモデルが計画を上限まで繰り返す）は、検証の前に最初の 1 件へ寄せ、
  寄せた数を診断に残す。内容の違う entry は落とさず、上限を超えれば従来どおり不合格。
- 1 組目の生成の出力が再実行の後も契約を満たさなければ、回答全体を失敗にせず、関係する資料の記載を示す。
"""

import json

import pytest
from citation_unit_support import generated_fixture
from pydantic import ValidationError
from test_grounded_answer import LIST_TEXT, FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import GroundedDraft

GOOD = "一覧表を選択し、処理を選択します。"
PLAN_LIMIT = 64
ITEM_LIMIT = 40


def _context():
    ctx = context(record(0, LIST_TEXT, "売上-(3)年次集計明細作成"))
    return ctx, ids(ctx)[LIST_TEXT]


def _plan(eid: str, request_id: str = "Q1") -> dict:
    return {
        "request_id": request_id,
        "evidence_scope": [{"evidence_id": eid, "target": "集計表出力", "relation": "direct"}],
    }


def _wire_item(text: str, start: int = 1) -> dict:
    return {
        "kind": "operation",
        "request_id": "Q1",
        "text": text,
        "citations": [{"evidence_id": "E1", "line_start": start, "line_end": start}],
    }


def _wire_json(**fields) -> str:
    return json.dumps({"items": [_wire_item(GOOD)], "summary": "", "confidence": "high", **fields}, ensure_ascii=False)


def _oversized_items_error() -> ValidationError:
    """内容の違う説明が上限（40 件）を超える出力の、SDK の検証と同じ例外。"""
    payload = _wire_json(items=[_wire_item(f"合成の手順 {number}", number) for number in range(ITEM_LIMIT + 1)])
    with pytest.raises(ValidationError) as raised:
        generation_draft_wire_model().model_validate_json(payload)
    return raised.value


def test_64_identical_plans_are_collapsed_and_recorded_without_changing_the_schema():
    wire = generation_draft_wire_model()
    parsed = wire.model_validate_json(_wire_json(request_coverage=[_plan("E1")] * PLAN_LIMIT))

    assert [plan.request_id for plan in parsed.request_coverage] == ["Q1"]
    assert parsed._collapsed_duplicates == {"request_coverage": PLAN_LIMIT - 1}
    canonical = canonical_generation_draft(parsed)
    assert len(canonical.request_coverage) == 1
    assert canonical._collapsed_duplicates == {"request_coverage": PLAN_LIMIT - 1}
    # モデルに渡す schema（上限）は変えない。
    assert wire.model_json_schema()["properties"]["request_coverage"]["maxItems"] == PLAN_LIMIT


def test_distinct_entries_are_kept_and_a_distinct_overflow_still_fails():
    wire = generation_draft_wire_model()
    # 内容の違う entry は寄せない（順序も保つ）。
    other = {**_plan("E1"), "evidence_scope": []}
    parsed = wire.model_validate_json(_wire_json(request_coverage=[_plan("E1"), other, _plan("E1"), other]))
    assert [len(plan.evidence_scope) for plan in parsed.request_coverage] == [1, 0]
    assert parsed._collapsed_duplicates == {"request_coverage": 2}
    # 上限は寄せた後の件数で判定する。内容の違う entry が上限を超えれば不合格のまま（切り詰めない）。
    distinct = [_plan("E1", f"Q{number}") for number in range(PLAN_LIMIT + 1)]
    with pytest.raises(ValidationError, match="request_coverage"):
        wire.model_validate_json(_wire_json(request_coverage=distinct))
    assert "items" in str(_oversized_items_error())


def test_identical_items_are_collapsed_and_applications_follow_the_kept_item():
    first, second, third = (_wire_item(text, start) for start, text in enumerate(("手順 A", "手順 B", "手順 C"), 1))
    application = {"premises": [], "reasoning": "合成の当てはめ", "result": "合成の結果"}
    parsed = GroundedDraft.model_validate(
        {
            "items": [{**first, "citations": []}, {**second, "citations": []}, {**first, "citations": []}, {**third, "citations": []}],
            "applications": [{**application, "item_indexes": [2, 3]}, {**application, "item_indexes": [0, 2]}],
            "summary": "",
            "confidence": "high",
        }
    )
    assert [entry.text for entry in parsed.items] == ["手順 A", "手順 B", "手順 C"]
    assert [entry.item_indexes for entry in parsed.applications] == [[0, 2], [0]]
    assert parsed._collapsed_duplicates == {"items": 1}


def test_duplicated_plan_answer_keeps_one_plan_and_records_the_collapse():
    ctx, eid = _context()
    duplicated = {**draft(item(eid, GOOD, GOOD, request_id="Q1")), "request_coverage": [_plan(eid)] * PLAN_LIMIT}
    model = FakeModel([duplicated], [audit((0, "supported", "matched", ""))])

    result = run(model, ctx).response

    assert GOOD in result.answer_text
    trace = result.generation_trace
    assert len(trace["grounded_draft"]["request_coverage"]) == 1
    output = trace["rounds"][0]["generation_citation_trace"]["outputs"][0]
    assert output["collapsed_duplicates"] == {"request_coverage": PLAN_LIMIT - 1}
    assert len(output["canonical_output"]["request_coverage"]) == 1


def _round_error() -> ValidationError:
    """最終生成の出力の検証と同じ、型に合わない出力の例外。"""
    with pytest.raises(ValidationError) as raised:
        generation_draft_wire_model().model_validate_json("{}")
    return raised.value


@pytest.mark.parametrize(
    "error", [TypeError("合成"), KeyError("合成"), AttributeError("合成"), RuntimeError("合成")], ids=lambda e: type(e).__name__
)
def test_implementation_errors_are_not_reported_as_an_answer(monkeypatch, error):
    """#4091: 構造化出力の検証の失敗でない実装の例外は、参考情報の回答にしない。"""
    ctx, _eid = _context()

    def broken_round(*args, **kwargs):
        raise error

    monkeypatch.setattr(grounded, "run_round", broken_round)
    with pytest.raises(type(error)):
        run(FakeModel([draft()], [audit()]), ctx)


def test_validation_failure_detail_names_the_failed_model_without_contents():
    """#4091: 型の名前と検査の種類で、モデルの出力の不合格か実装の不具合かを切り分けられる。本文は残さない。"""
    secret = "合成の根拠の本文"
    with pytest.raises(ValidationError) as raised:
        GroundedDraft.model_validate({"items": [], "summary": "", "confidence": secret * 100})

    detail = grounded.generation_failure_detail(raised.value)

    assert detail == {"category": "schema_enum", "model": "GroundedDraft", "validation_error_types": {"literal_error": 1}}
    assert secret not in json.dumps(detail, ensure_ascii=False)


COMPOUND_QUESTION = "同じ版の資料を使ってください。①合成の項目の意味は？②合成の処理の意味は？"
LONG_RECORD = "合成の項目は入力を区別する識別子です。" * 60


def test_compound_question_with_long_evidence_is_answered_without_a_generation_failure():
    """#4091: 字数の上限より長い根拠でも、小問の計画が有効なら生成まで進み、失敗の退避にしない。"""
    ctx = context(record(0, LONG_RECORD, "合成の項目"))
    answer = "合成の項目は入力を区別する識別子です。"
    label = "E1"
    calls = []

    def model(system, prompt, settings, schema, **options):
        calls.append(schema)
        return generated_fixture(
            schema,
            prompt,
            {
                "summary": "",
                "confidence": "high",
                "items": [
                    {
                        "kind": "rule",
                        "request_id": "Q1",
                        "text": answer,
                        "citations": [{"evidence_id": label, "line_start": 1, "line_end": 1}],
                    }
                ],
            },
        )

    result = run(model, ctx, question=COMPOUND_QUESTION).response

    assert "generation_failure" not in result.generation_trace
    assert len(calls) == 1
    assert answer in result.answer_text
