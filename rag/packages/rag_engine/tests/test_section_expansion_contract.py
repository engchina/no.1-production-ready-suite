"""章節の最低範囲と共有予算の契約を合成資料で検証する。"""

from dataclasses import replace

import pytest

from rag_engine.generation import section_expansion as sections
from rag_engine.generation.answer_models import AnswerContext
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence, context_bundle_from_parent_evidence
from tests.test_section_expansion import (
    SectionStub,
    _child_ids,
    _expand,
    _members,
    _seed_context,
    _settings,
)


def test_every_retrieved_owner_is_read_instead_of_only_the_first_two():
    stub = SectionStub()
    result = _expand(_seed_context("c31a", "c32a", "c4a"), _settings(), stub)
    assert stub.read_calls == ["s3-1", "s3-2", "s4"]
    assert result.trace["minimum_scope_complete"]
    assert set(_members("s4")) <= set(_child_ids(result.context))


@pytest.mark.parametrize("mode", ["empty", "partial", "raise"])
def test_partial_reads_cannot_be_overruled_by_an_optimistic_grader(mode):
    stub = SectionStub()
    original = stub.chunks

    def chunks(**kwargs):
        if mode == "raise":
            raise PermissionError("unavailable")
        return sections.SectionRead(original(**kwargs)[:1] if mode == "partial" else [], complete=False)

    stub.chunks = chunks
    result = _expand(_seed_context("c31a"), _settings(), stub)
    assert not result.trace["minimum_scope_complete"]
    assert result.stop_reason != sections.STOP_SCOPE_READ
    assert result.unread


def test_minimum_scope_requires_every_owner_even_if_the_other_one_was_read():
    stub = SectionStub()
    original = stub.chunks
    stub.chunks = lambda **kw: sections.SectionRead([], complete=False) if kw["section_id"] == "s4" else original(**kw)
    result = _expand(_seed_context("c31a", "c4a"), _settings(), stub)
    assert not result.trace["minimum_scope_complete"]
    assert result.stop_reason != sections.STOP_SCOPE_READ
    assert "c31b" in _child_ids(result.context)


def test_standard_completion_has_no_sufficiency_grader_and_stops_at_owner():
    stub = SectionStub()
    result = sections.expand_sections(
        "操作方法",
        _seed_context("c31a"),
        _settings(),
        section_outline=stub.outline,
        section_chunks=stub.chunks,
    )
    assert stub.read_calls == ["s3-1"]
    assert result.stop_reason == sections.STOP_SCOPE_READ
    assert result.trace["minimum_scope_complete"]


def test_shared_reader_completes_new_owners_without_rereading_old_ones():
    stub = SectionStub()
    reader = sections.SectionReader(stub.chunks, set())
    first = _expand(_seed_context("c31a"), _settings(), stub, reader=reader)
    context = sections._merge(first.context, _seed_context("c4a").evidence_tree)
    second = _expand(context, _settings(), stub, reader=reader)
    assert stub.read_calls == ["s3-1", "s4"]
    assert second.trace["minimum_scope_complete"]
    ids = [record.chunk_uid for record in second.context.records]
    assert len(ids) == len(set(ids))


def test_same_uid_with_only_an_excerpt_is_replaced_by_full_original():
    stub = SectionStub()
    context = _seed_context("c31a", "c31b")
    short = replace(context.records[1], text="本文", body_text="本文")
    context = replace(
        context,
        records=[context.records[0], short],
        evidence_tree=(context.evidence_tree[0], replace(context.evidence_tree[1], record=short)),
    )
    result = _expand(context, _settings(), stub)
    assert result.trace["minimum_scope_complete"]
    records = {record.chunk_uid: record for record in result.context.records}
    assert len(records) == 2
    assert len(records[short.chunk_uid].text) > len(short.text)


def test_generation_budget_does_not_exceed_limit_when_initial_context_is_too_big():
    from rag_engine.generation.answering import _section_evidence_budget

    context = _seed_context("c31a", "c31b")
    result = sections.SectionExpansionResult(context=context, applied=True)
    assert _section_evidence_budget(context, _settings(answer_max_input_tokens=400), result) <= 200


def test_full_replacement_keeps_new_children_and_original_anchor_roles():
    from rag_engine.generation.answer_records import _stored_chunk_answer_record
    from rag_engine.retrieval.context_builder import ContextChildEvidence
    from tests.test_section_expansion import _child

    context = _seed_context("c31a")
    parent = context.evidence_tree[0]
    sibling = ContextChildEvidence(_stored_chunk_answer_record(_child("c31b")), "section_context", "section_expansion")
    full = replace(parent.record, text=parent.record.text + "追加の原文", body_text=parent.record.text + "追加の原文")
    added = replace(parent, record=full, children=(*parent.children, sibling))
    merged = sections._merge(context, (added,))
    assert len(merged.records) == 1
    assert _child_ids(merged) == ["c31a", "c31b"]
    assert merged.evidence_tree[0].children[0].role == "retrieved_anchor"


@pytest.mark.parametrize("exhausted", ["tokens", "time"])
def test_new_search_results_share_the_existing_input_and_time_budget(exhausted):
    stub = SectionStub()
    reader = sections.SectionReader(stub.chunks, set())
    settings = _settings(answer_max_input_tokens=2000)
    first = _expand(_seed_context("c31a"), settings, stub, reader=reader, started_at=0.0, clock=lambda: 0.0)
    context = sections._merge(first.context, _seed_context("c4a").evidence_tree)
    second = _expand(
        context,
        settings,
        stub,
        reader=reader,
        started_at=0.0,
        clock=lambda: 500.0 if exhausted == "time" else 0.0,
        deadline_remaining=lambda: 60.0 if exhausted == "time" else None,
    )
    assert second.unread
    assert not second.trace["minimum_scope_complete"]
    assert second.used_tokens <= second.trace["input_token_limit"]
    assert stub.read_calls.count("s3-1") == 1
    if exhausted == "time":
        assert second.stop_reason == sections.STOP_TIME_LIMIT


def test_section_without_stored_parent_keeps_every_chunk_text_as_evidence():
    """親の record を読めない章節は、先頭の chunk の本文だけで章節を代表させない（#5810）。"""
    from tests.test_section_expansion import _child

    stub = SectionStub()
    shared = "doc-1:missing-parent"
    members = _members("s3-1")
    stub.chunks = lambda **_: [replace(_child(chunk), parent_chunk_uid=shared, parent_chunk_id=shared) for chunk in members]
    outline = stub.outline(chunk_uid=members[0])
    section = next(item for item in outline.sections if item.section_id == "s3-1")
    tree = sections.SectionReader(stub.chunks, set()).groups(outline, section)
    texts = "".join(item.record.text for item in tree)
    assert all(f"本文{chunk}" in texts for chunk in members)


def test_anchor_inside_a_section_without_stored_parent_keeps_its_text_after_consolidation():
    """当たった chunk が章節の先頭でなくても、章節の拡張とまとめの後に本文が根拠に残る（#5810）。"""
    from rag_engine.retrieval.context_fill import consolidate_evidence_tree
    from tests.test_section_expansion import _child

    stub = SectionStub()
    shared = "doc-1:missing-parent"
    members = _members("s3-1")
    stub.chunks = lambda **_: [replace(_child(chunk), parent_chunk_uid=shared, parent_chunk_id=shared) for chunk in members]
    anchor = _stored_chunk_answer_record(replace(_child(members[-1]), parent_chunk_uid=shared, parent_chunk_id=shared))
    tree = (
        ContextParentEvidence(
            record=anchor,
            role="child_fallback",
            reason="retrieved_chunk",
            children=(ContextChildEvidence(record=anchor, role="retrieved_anchor", reason="retrieved", retrieval_rank=1),),
        ),
    )
    bundle = context_bundle_from_parent_evidence(tree, max_chars=24000)
    context = AnswerContext(records=list(bundle.records), text=bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree)
    expanded = _expand(context, _settings(), stub).context
    merged = context_bundle_from_parent_evidence(consolidate_evidence_tree(expanded.evidence_tree), max_chars=24000)
    texts = "".join(record.text for record in merged.records)
    assert all(f"本文{chunk}" in texts for chunk in members)
