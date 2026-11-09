"""ID・行範囲の真正性と、意味支持の未検証を分離する合成回帰。"""

import hashlib
import json
from contextvars import copy_context

import pytest
from test_cite_lines import LONG, item
from test_grounded_answer import context, record

from rag_engine.generation import grounded
from rag_engine.generation.answering import _grounded_spans
from rag_engine.models.llm import GroundedCitation, GroundedDraft


def spans(text=LONG):
    return _grounded_spans("対象の操作手順は？", context(record(0, text, "対象の操作", source="sample.pdf")), (), None)


def check(proposal, evidence):
    entries, dropped = grounded.verify("対象の操作手順は？", GroundedDraft(items=[proposal], summary="", confidence="low"), evidence)
    return entries[0], dropped


def test_a_missing_sent_snapshot_is_not_reported_as_a_verified_sent_version():
    evidence = spans()
    entry, _ = check(item(evidence[0]["label"], "1"), evidence)
    assert entry.citation_resolution["authenticity"] == "verified"
    assert entry.citation_resolution["sent_view_status"] == "not_recorded"
    assert "sent_view_sha256" not in entry.citation_resolution


def test_display_snapshot_does_not_enter_span_json_or_model_schema():
    evidence = spans()
    before = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    grounded.format_functions(evidence)
    assert json.dumps(evidence, ensure_ascii=False, sort_keys=True) == before
    assert "_extraction" not in item(evidence[0]["label"], "1").model_dump_json()


@pytest.mark.parametrize("lines,code", [("", "citation_range_missing"), ("抽出条件を入力して抽出", "citation_range_invalid")])
def test_legacy_text_cannot_recover_a_missing_or_invalid_range(lines, code):
    evidence = spans()
    proposal = item(evidence[0]["label"], lines)
    cited, error = grounded.cited_quote(proposal, evidence)
    assert error and not cited.quote
    assert grounded._citation_extraction(cited)["reason_code"] == code


def test_display_snapshots_are_isolated_between_request_contexts():
    first, second = spans(), spans("別の本文の操作を行います。")
    local = copy_context()
    local.run(grounded.format_functions, first)
    grounded.format_functions(second)
    first_entry, _ = local.run(check, item(first[0]["label"], "1"), first)
    assert first_entry.citation_resolution["sent_view_status"] == "prepared"
    outside_entry, _ = check(item(first[0]["label"], "1"), first)
    assert outside_entry.citation_resolution["sent_view_status"] == "not_recorded"


def test_direct_display_id_is_not_ambiguous_because_of_another_sources_alias():
    evidence = spans()
    evidence.append({**evidence[0], "evidence_id": "other", "label": "E99", "source_id": evidence[0]["label"]})
    entry, _ = check(item(evidence[0]["label"], "1"), evidence)
    assert entry.citation_resolution["resolved_evidence_id"] == evidence[0]["evidence_id"]
    assert entry.citation_resolution["authenticity"] == "verified"


def test_width_normalization_in_the_display_does_not_change_raw_source_authenticity():
    evidence = spans()
    evidence[0]["text"] = "ＡＢＣ：" + LONG
    grounded.format_functions(evidence)
    source_hash = hashlib.sha256(evidence[0]["text"].encode()).hexdigest()
    entry, _ = check(item(evidence[0]["label"], "1"), evidence)
    assert entry.citation_resolution["content_sha256"] == source_hash
    assert entry.citation_resolution["sent_view_status"] == "prepared"
    assert entry.citation_resolution["authenticity"] == "verified"
    assert entry.citation_resolution["resolved_quote"].startswith("ＡＢＣ：")


def test_identical_quotes_from_multiple_sources_remain_bound_to_the_requested_ids():
    evidence = _grounded_spans(
        "対象の操作手順は？",
        context(record(0, LONG, "対象の操作", source="a.pdf"), record(1, LONG, "対象の操作", source="b.pdf")),
        (),
        None,
    )
    proposal = item(evidence[0]["label"], "1")
    proposal = proposal.model_copy(
        update={"citations": [*proposal.citations, GroundedCitation(evidence_id=evidence[1]["label"], lines="1")]}
    )
    entry, dropped = check(proposal, evidence)
    assert {entry.citation_resolution["resolved_evidence_id"], entry.also[0].citation_resolution["resolved_evidence_id"]} == {
        span["evidence_id"] for span in evidence
    }
    assert not dropped
