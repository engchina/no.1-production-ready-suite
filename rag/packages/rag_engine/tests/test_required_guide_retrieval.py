"""必須ガイド取得と通常の追加検索の枠を区別する。外部 I/O なし。"""

import pytest
from test_guide_steps import _cover, _patch_search, _settings, context, parent, plan


def test_five_required_steps_are_not_cut_by_optional_kind_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_search(monkeypatch, {})
    guide = plan(*({"id": f"s{i}", "title": f"合成工程{i}"} for i in range(5)))
    _merged, trace, sources = _cover(context(parent("base")), _settings(guide_step_max_queries=4, extra_retrieval_max_queries=8), guide)
    assert len(calls) == 5
    assert len(sources) == 5
    assert trace["unsearched_step_ids"] == []


def _refs(count):
    return plan(
        *(
            {
                "id": f"s{i}",
                "title": f"合成工程{i}",
                "references": [{"document_id": f"d{i}", "page_ranges": [{"page_start": 1, "page_end": 1}]}],
            }
            for i in range(count)
        )
    )


def test_five_reference_bodies_survive_optional_reference_cap(monkeypatch):
    from test_guide_steps import _paged

    from rag_engine.generation import answering as a

    calls = []

    def retrieve(*args, **kwargs):
        doc = kwargs["page_ranges"][0]["document_id"]
        calls.append(doc)
        return context(_paged(doc, doc, 1))

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", retrieve)
    merged, trace, sources = _cover(context(parent("base")), _settings(guide_step_max_queries=0, guide_reference_max_chunks=4), _refs(5))
    assert calls == [f"d{i}" for i in range(5)]
    assert {r.chunk_uid for r in merged.records} == {"run:base", *(f"run:d{i}" for i in range(5))}
    assert all(keys for _, _, keys in sources)
    acquisition = trace["required_acquisition"]
    assert acquisition["incomplete_target_ids"] == []
    assert a._guide_acquisition_notices(trace) == ()
    assert all(row["retained_body_chars"] > 0 and row["scope_coverage"] == "unknown" for row in acquisition["targets"])


def test_reused_first_eight_targets_do_not_starve_later_targets(monkeypatch):
    from test_guide_steps import _paged

    from rag_engine.generation import answering as a

    calls = []

    def retrieve(*args, **kwargs):
        doc = kwargs["page_ranges"][0]["document_id"]
        calls.append(doc)
        return context(_paged(doc, doc, 1))

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", retrieve)
    merged, trace, _ = _cover(context(*(_paged(f"d{i}", f"d{i}", 1) for i in range(8))), _settings(guide_step_max_queries=0), _refs(12))
    assert calls == [f"d{i}" for i in range(8, 12)]
    assert len(merged.records) == 12
    assert trace["required_acquisition"]["incomplete_target_ids"] == []
    assert trace["budget"]["used"] == {"guide_required": 4}


def test_one_step_with_two_refs_does_not_hide_unprocessed_second_ref(monkeypatch):
    from test_guide_steps import _paged

    from rag_engine.generation import answering as a

    def retrieve(*args, **kwargs):
        doc = kwargs["page_ranges"][0]["document_id"]
        return context(_paged(doc, doc, 1))

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", retrieve)
    guide = plan({"id": "s", "title": "合成工程", "references": [step.references[0].to_payload() for step in _refs(2).steps]})
    merged, trace, _ = _cover(context(parent("base")), _settings(extra_retrieval_max_queries=1), guide)
    assert len(merged.records) == 2
    rows = trace["required_acquisition"]["targets"]
    assert [r["execution"] for r in rows] == ["completed", "not_run"]
    assert rows[1]["reason"] == "required_budget_exhausted"
    assert trace["required_acquisition"]["incomplete_step_ids"] == ["s"]
    assert trace["unsearched_step_ids"] == []


def test_bad_legacy_metadata_does_not_abort_other_candidates():
    from dataclasses import replace

    from test_guide_steps import _paged

    from rag_engine.retrieval.guide_steps import required_guide_targets, target_records

    target = required_guide_targets(_refs(1))[0]
    good = _paged("good", "d0", 1).record
    assert target_records(target, [replace(good, metadata=[]), replace(good, metadata=None), good]) == (good,)


def test_shared_exact_reference_is_one_attempt_but_never_semantic_completion(monkeypatch):
    from test_guide_steps import _paged

    calls = _patch_search(monkeypatch, {"reference": context(_paged("shared", "d0", 1))})
    reference = _refs(1).steps[0].references[0].to_payload()
    guide = plan(*({"id": f"s{i}", "title": f"異なる操作{i}", "references": [reference]} for i in range(5)))
    _merged, trace, sources = _cover(context(parent("base")), _settings(guide_step_max_queries=0), guide)
    assert len(calls) == 1
    assert all(keys == ("run:shared",) for _, _, keys in sources)
    row = trace["required_acquisition"]["targets"][0]
    assert len(row["step_ids"]) == 5
    assert row["semantic_support"] == "not_run" and row["scope_coverage"] == "unknown"


def test_candidate_rejected_from_context_remains_an_explicit_acquisition_gap(monkeypatch):
    from test_guide_steps import _paged

    from rag_engine.generation import answering as a

    _patch_search(monkeypatch, {"reference": context(_paged("new", "d0", 1))})
    original = context(parent("base"))
    monkeypatch.setattr(a, "_merge_crag_evidence", lambda *args, **kwargs: context(_paged("new", "d0", 1)))
    merged, trace, _ = _cover(original, _settings(guide_step_max_queries=0), _refs(1))
    assert merged is original
    row = trace["required_acquisition"]["targets"][0]
    assert row["candidate_source_ids"] == ["run:new"]
    assert row["retained_source_ids"] == [] and row["retained_body_chars"] == 0
    assert trace["required_acquisition"]["incomplete_step_ids"] == ["s0"]
    assert a._guide_acquisition_notices(trace)


def test_empty_search_is_not_evidence_that_reference_has_no_answer(monkeypatch):
    _patch_search(monkeypatch, {})
    _merged, trace, _ = _cover(context(parent("base")), _settings(guide_step_max_queries=0), _refs(1))
    row = trace["required_acquisition"]["targets"][0]
    assert row["execution"] == "completed" and row["acquisition"] == "no_candidates"
    assert row["scope_coverage"] == "unknown" and row["semantic_support"] == "not_run"
    assert trace["required_acquisition"]["incomplete_step_ids"] == ["s0"]


def test_large_required_body_is_kept_alongside_old_body_for_batched_generation(monkeypatch):
    from dataclasses import replace

    from test_guide_steps import _paged

    from rag_engine.generation import answering as a

    huge = replace(_paged("large", "d0", 1).record, text="合成の長い原文。" * a.MAX_CONTEXT_CHARS)
    from rag_engine.retrieval.context_builder import ContextParentEvidence

    _patch_search(monkeypatch, {"reference": context(ContextParentEvidence(record=huge, role="synthesis_parent", reason="top"))})
    original = context(parent("base", "既存の有効な本文。"))
    merged, trace, _ = _cover(original, _settings(guide_step_max_queries=0), _refs(1))
    assert [r.chunk_uid for r in merged.records] == ["run:base", "run:large"]
    row = trace["required_acquisition"]["targets"][0]
    assert row["candidate_source_ids"] == ["run:large"]
    assert row["retained_body_chars"] == len(huge.text)
    assert row["retained_source_ids"] == ["run:large"]
    assert trace["required_acquisition"]["incomplete_step_ids"] == []


def test_prompt_acquisition_size_follows_step_count_not_reference_count():
    import json

    from rag_engine.generation.guide_acquisition import prompt_acquisition_summary

    rows = [
        {
            "step_ids": [f"s{step}"],
            "filter": {"document_id": f"document-{ref}"},
            "execution": "not_run",
            "reason": "required_budget_exhausted",
            "retained_source_ids": [],
        }
        for step in range(50)
        for ref in range(100)
    ]
    summary = prompt_acquisition_summary(rows)
    assert len(summary) == 50 and all(row["incomplete_ref_count"] == 100 for row in summary)
    assert {row["step_id"] for row in summary} == {f"s{step}" for step in range(50)}
    assert len(json.dumps(summary, ensure_ascii=False)) < 8000
    assert "document-" not in json.dumps(summary)
