"""省略内容の補写根拠を束縛する。スタブは判定の接続を検証し、意味判定の精度を証明しない。"""

import json

import pytest
from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.config import get_settings
from rag_engine.generation import grounded
from rag_engine.generation.answering import _grounded_spans
from rag_engine.generation.request_recovery import request_recovery_plan
from rag_engine.models.llm import GroundedAudit

OPEN = "対象Aの画面を開きます。"
SAVE = "対象Aの保存ボタンを押します。"
CONDITION = "保存は承認済みの場合に行います。"
SOURCE = "sample.pdf"
QUESTION = "対象Aの画面を開いて保存する手順は？"


def review(*supports, missing=SAVE, status="available"):
    return {
        "request_id": "Q1",
        "status": "partial",
        "reason": "保存の説明が不足",
        "missing_content": missing,
        "evidence_status": status,
        "evidence_ids": [row["evidence_id"] for row in supports],
        "evidence_support": list(supports),
    }


def support(evidence_id, quote, source=SOURCE):
    return {"evidence_id": evidence_id, "source": source, "quote": quote}


def bind(row, texts, *, unused=()):
    result = GroundedAudit.model_validate(audit(requests=[row], summary_supported=False))
    inputs = {"evidence": texts, "evidence_sources": {key: SOURCE for key in texts}, "unused_evidence": list(unused)}
    grounded._validate_missing_content_evidence(result, inputs)
    return result.request_reviews[0]


@pytest.mark.parametrize(
    "row",
    [
        review(support("open", SAVE)),  # 開く ID に保存の原文を捏造する。
        review(support("save", SAVE, "other.pdf")),
        review(support("save", "")),
        review({"evidence_id": "save"}),
        review(support("absent", SAVE)),
        review(support("save", SAVE), missing=""),
        {**review(support("save", SAVE)), "evidence_ids": ["open"]},
        {**review(), "evidence_ids": ["open"]},  # 旧式の ID だけでは不足箇所の根拠を確認できない。
    ],
)
def test_invalid_omission_binding_retains_unknown_without_search_or_correction(row):
    result = bind(row, {"open": OPEN, "save": SAVE})
    assert result.evidence_status == "unknown" and result.evidence_ids == []
    plan = request_recovery_plan([{**result.model_dump(), "reviewed": True}], (), {"open", "save"})
    assert plan[0]["action"] == "retain"


def test_joint_direct_sources_are_preserved_for_the_missing_part():
    row = review(support("save", SAVE), support("condition", CONDITION), missing="承認済みの場合の保存操作")
    result = bind(row, {"save": SAVE, "condition": CONDITION})
    assert result.evidence_status == "available"
    assert result.evidence_ids == ["save", "condition"]
    assert [entry.quote for entry in result.evidence_support] == [SAVE, CONDITION]


def test_reusing_a_source_that_supports_both_parts_is_allowed():
    result = bind(review(support("both", OPEN + SAVE)), {"both": OPEN + SAVE})
    assert result.evidence_status == "available"


def test_an_unseen_suffix_is_not_accepted_as_direct_material():
    row = review(support("save", SAVE))
    result = bind(row, {}, unused=[{"evidence_id": "save", "source": SOURCE, "text": OPEN}])
    assert result.evidence_status == "unknown"


@pytest.mark.parametrize("mode", ["standard", "detailed", "none"])
def test_existing_audit_routes_only_the_missing_operation_and_adds_no_call(mode):
    ctx = context(record(0, OPEN, "設定", source=SOURCE), record(1, SAVE, "設定", source=SOURCE))
    refs = ids(ctx, QUESTION)
    received = {}

    def assess(prompt):
        payload = json.loads(prompt)
        received.update(payload)
        available = next((row for row in payload.get("unused_evidence", []) if row["text"] == SAVE), None)
        row = review(support(available["evidence_id"], SAVE, available["source"])) if available else review(missing=SAVE, status="unknown")
        result = audit((0, "supported", "matched", ""), requests=[row], summary_supported=False)
        if mode == "standard":
            result.pop("unused_evidence_ids", None)
        return result

    model = FakeModel([draft(item(refs[OPEN], OPEN, OPEN, request_id="Q1"), summary="")], [assess])
    spans = _grounded_spans(QUESTION, ctx, (), None)
    result = grounded.run_round(
        QUESTION,
        spans,
        get_settings(environ={}, dotenv_path=None),
        prompt="合成の操作説明",
        image_paths=(),
        provider_id=None,
        parse_text=model,
        parse_images=lambda *args, **kwargs: pytest.fail("画像は不要"),
        audit_mode=mode,
    )
    assert len(model.prompts) == (1 if mode == "none" else 2)
    assert result.audit_calls == (0 if mode == "none" else 1)
    if mode == "none":
        assert result.review_execution == "not_run"
        assert all(not row.get("reviewed") for row in result.unanswered)
        return
    assert received["items"][0]["citations"] == [{"evidence_id": refs[OPEN], "quote": OPEN}]
    plan = request_recovery_plan(result.unanswered, (), set(refs.values()))
    if mode == "detailed":
        assert plan[0]["action"] == "correction" and plan[0]["evidence_ids"] == [refs[SAVE]]
        [feedback] = result.feedback()["unanswered_requests"]
        assert feedback["missing_content"] == SAVE
        assert feedback["evidence_support"] == [support(refs[SAVE], SAVE)]
        assert refs[OPEN] not in feedback["evidence_ids"]
    else:
        assert plan[0]["action"] == "retain"


def test_related_but_unsupported_material_does_not_become_a_correction_reason():
    # 意味の不支持は既存監査の判定。存在する ID を確認しても supported には昇格させない。
    result = bind(review(support("open", OPEN), status="unknown"), {"open": OPEN})
    assert result.evidence_status == "unknown" and result.evidence_ids == [] and result.evidence_support == []


def test_missing_verdict_is_not_silently_overridden_by_id_existence():
    # 実モデル校準で観測した材料不足の偽判定は、存在照合だけでは直せない。回帰で限界を明示する。
    result = bind(review(support("save", SAVE), status="missing"), {"save": SAVE})
    assert result.evidence_status == "missing" and result.evidence_ids == []


def test_duplicate_request_with_a_gap_does_not_block_another_valid_omission_review():
    ctx = context(record(0, OPEN + SAVE, "設定", source=SOURCE))
    eid = ids(ctx, QUESTION)[OPEN + SAVE]
    initial = audit(
        (0, "supported", "matched", ""),
        requests=[review(support(eid, SAVE)), ("Q2", "addressed"), ("Q2", "addressed")],
        summary_supported=False,
    )
    model = FakeModel(
        [
            draft(item(eid, OPEN, OPEN, request_id="Q1"), {"kind": "gap", "request_id": "Q2", "text": "確認できません。"}, summary=""),
            draft(item(eid, OPEN, OPEN, request_id="Q1"), item(eid, SAVE, SAVE, request_id="Q1"), summary=""),
        ],
        [initial, audit((0, "supported", "matched", ""), (1, "supported", "matched", ""), requests=[("Q1", "addressed")])],
    )
    result = run(model, ctx, QUESTION).response
    decisions = result.generation_trace["rounds"][0]["request_recovery"]["decisions"]
    decision = next(row for row in decisions if row["request_id"] == "Q1")
    blocked = next(row for row in decisions if row["request_id"] == "Q2")
    assert blocked["action"] == "retain" and blocked["cause"] == "invalid_request_id"
    assert result.generation_trace["rounds"][0]["validation_dimensions"]["answer_completeness"]["execution"] == "partial"
    assert decision["request_id"] == "Q1" and decision["action"] == "correction"
    assert decision["evidence_ids"] == [eid]
