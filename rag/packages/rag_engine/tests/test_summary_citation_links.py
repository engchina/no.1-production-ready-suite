"""摘要と最終本文の引用対応を、意味監査の実施とは別に検証する。"""

import pytest
from test_audit_modes import _run
from test_grounded_answer import FakeModel, context, draft, ids, item, record

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedDraft


def _case(indexes=None):
    text = "項目 A は記録の識別子です。"
    ctx = context(record(0, text, "項目の説明", source="synthetic-a.txt"))
    value = draft(item(ids(ctx)[text], text, text), summary="項目 A は記録を識別します。")
    if indexes is not None:
        value["summary_item_indexes"] = indexes
    else:
        value.pop("summary_item_indexes", None)
    return ctx, value


def test_linked_paraphrase_has_no_extra_call_or_semantic_success():
    ctx, value = _case([0])
    model = FakeModel([value], [])
    result = _run(model, ctx, grounded.AUDIT_MODE_NONE).response
    assert [name for name, _ in model.prompts] == ["GroundedDraftWire"]
    assert "要約を根拠で確かめていない" not in result.answer_text
    summary = result.generation_trace["finalization"]["summary"]
    assert summary["citations"]["status"] == "linked"
    link = summary["citations"]["links"][0]
    assert link["item_index"] == 0
    assert link["citations"][0]["evidence_id"] == value["items"][0]["citations"][0]["evidence_id"]
    assert summary["semantic_validation"]["execution"] == "not_run"
    assert summary["semantic_validation"]["adopted"] is False
    assert "summary_supported" not in summary


@pytest.mark.parametrize("indexes", [None, [], [39]])
def test_legacy_missing_or_invalid_links_are_unresolved(indexes):
    ctx, value = _case(indexes)
    result = _run(FakeModel([value], []), ctx, grounded.AUDIT_MODE_NONE).response
    assert result.generation_trace["finalization"]["summary"]["citations"]["status"] == "unresolved"
    assert any("summary_unaudited" in str(mark["reasons"]) for mark in result.envelope["passage_marks"])


def test_collapsed_identical_items_reindex_summary_links():
    _, value = _case([1, 2])
    value["items"] *= 3
    model = GroundedDraft.model_validate(value)
    assert len(model.items) == 1
    assert model.summary_item_indexes == [0]


@pytest.mark.parametrize("change", ["removed", "withdrawn", "unmatched", "text", "span", "marked", "summary"])
def test_final_body_or_summary_change_invalidates_links(change):
    _, value = _case([0])
    model = GroundedDraft.model_validate(value)
    entry = grounded.CheckedItem(model.items[0], span={"evidence_id": "E1", "source_id": "S1", "page": 1}, draft_index=0)
    summary = model.summary
    if change in {"removed", "withdrawn", "unmatched"}:
        setattr(entry, change, True)
    elif change == "text":
        entry.item = entry.item.model_copy(update={"text": "修正した別の説明。"})
    elif change == "span":
        entry.span = None
    elif change == "summary":
        summary = "後工程で書き換えた別の摘要。"
    else:
        entry.mark("unsupported", "根拠が不足")
    current = grounded.Round(summary, model, [entry], [], None, summary_warning="監査なし")
    linked = grounded.link_summary_evidence(current)
    assert linked.timing["summary_citations"]["status"] == "unresolved"
    assert linked.summary_warning


def test_empty_summary_needs_no_citations():
    _, value = _case([])
    model = GroundedDraft.model_validate(value)
    current = grounded.Round("", model, [], [], None, summary_warning="監査なし")
    linked = grounded.link_summary_evidence(current)
    assert linked.timing["summary_citations"]["status"] == "not_applicable"
    assert not linked.summary_warning


def test_multiple_body_items_keep_all_actual_citations():
    ctx, value = _case([0, 1, 0])
    text = "項目 B は処理の状態です。"
    ctx.records.append(record(1, text, "項目の説明", source="synthetic-b.txt"))
    value["items"].append(item(ids(ctx)[text], text, text))
    value["summary"] = "項目 A は記録を識別し、項目 B は処理の状態を表します。"
    result = _run(FakeModel([value], []), ctx, grounded.AUDIT_MODE_NONE).response
    links = result.generation_trace["finalization"]["summary"]["citations"]
    assert links["status"] == "linked"
    assert [link["item_index"] for link in links["links"]] == [0, 1]
    assert len({link["citations"][0]["evidence_id"] for link in links["links"]}) == 2


def test_semantic_observation_is_not_promoted_to_verification():
    from test_grounded_answer import audit

    ctx, value = _case([0])
    value["summary"] = "項目 A は状態を表します。"
    model = FakeModel([value], [audit((0, "supported", "matched", ""), requests=[("Q1", "addressed")], summary_supported=False)])
    result = _run(model, ctx, grounded.AUDIT_MODE_DETAILED).response
    summary = result.generation_trace["finalization"]["summary"]
    assert summary["citations"]["status"] == "linked"
    assert summary["semantic_validation"] == {"execution": "completed", "adopted": False}
    observed = result.generation_trace["semantic_audit_observation"]
    assert observed["audit"]["summary_supported"] is False
    assert observed["adopted"] is False
    assert len(model.prompts) == 2


def test_audit_failure_is_distinct_from_disabling_audit():
    _, value = _case([0])
    model = GroundedDraft.model_validate(value)
    entry = grounded.CheckedItem(model.items[0], span={"evidence_id": "E1"}, draft_index=0)
    current = grounded.Round(model.summary, model, [entry], [], None, audit_calls=1, timing={"audit_error_type": "TimeoutError"})
    result = grounded.link_summary_evidence(current)
    assert result.timing["summary_citations"]["status"] == "linked"
    assert result.review_execution == "error"
    assert result.summary_warning
