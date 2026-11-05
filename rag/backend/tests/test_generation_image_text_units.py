"""正式scopeの用途位置を、生成本文・U・原生事実へ結ぶ合成回帰。"""

import copy
import json
from dataclasses import replace
from typing import Any

import pytest
from rag_engine.generation import grounded
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.retrieval.evidence_selection import _span_origin, evidence_spans

from tests.test_image_text_source_storage import (
    SyntheticTransaction,
    restored_from_rows,
    save_synthetic,
    synthetic_chunks,
)


async def prepared(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], SyntheticTransaction, list[Any], Any]:
    chunks, _ = synthetic_chunks()
    for chunk in chunks:
        chunk.metadata["engine_search_text"] = "合成の検索用見出し\n" + chunk.text
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    parents, state = restored_from_rows(tx.rows, tx)
    return chunks, tx, parents, state


async def test_child_generation_uses_verified_body_without_changing_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, tx, _, state = await prepared(monkeypatch)
    from app.rag.answer_engine import _stored_child

    hits = list(state.chunks.values())
    for index, hit in enumerate(hits):
        stored = _stored_child(hit, rrf_score=1)
        original = stored.retrieval_text
        answer = _stored_chunk_answer_record(stored)
        assert stored.retrieval_text == original
        assert answer.text == (stored.text if index else original)
        if index:
            metadata = json.loads(tx.rows[index]["metadata_json"])
            metadata.pop("image_text_source_scope_json")
            tx.rows[index]["metadata_json"] = json.dumps(metadata)
    _, legacy = restored_from_rows(tx.rows, tx)
    for hit in list(legacy.chunks.values())[1:]:
        stored = _stored_child(hit, rrf_score=1)
        assert _stored_chunk_answer_record(stored).text == stored.retrieval_text


async def test_mixed_units_preserve_native_and_image_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, parents, _ = await prepared(monkeypatch)
    record = _stored_chunk_answer_record(parents[0])
    native = record.metadata["layout"]["native_text_ranges"][0]
    assert _span_origin(record, native["start"], native["end"], []) == "document_text"
    spans = grounded.tag_spans(
        "合成の内容は何ですか。",
        evidence_spans("合成の内容は何ですか。", [record], preserve_records=True),
    )
    span = spans[0]
    view = grounded._citation_view(span, explicit_units=True)
    assert view.contract_version == "explicit_image_units_v1"
    images = record.metadata["layout"]["text_source_map"]["ranges"]
    for left, right in view.units:
        assert (
            len(
                {
                    r["vision_image_purpose"]
                    for r in images
                    if r["start"] < right and left < r["end"]
                }
            )
            <= 1
        )
    assert any(
        record.text[a:b] == record.text[native["start"] : native["end"]] for a, b in view.units
    )
    assert all(span["text"][a:b] in view.rendered for a, b in view.units)
    assert grounded.scope_evidence_text(span).count("| [") == len(view.units)
    from rag_engine.generation.image_text_sources import span_source_context

    purposes: set[str] = set()
    for own in spans:
        context = span_source_context(own)
        assert context is not None
        purposes.update(row["vision_image_purpose"] for row in context["ranges"])
        own_view = grounded._citation_view(own, explicit_units=True)
        assert own_view.contract_version == "explicit_image_units_v1"
        for left, right in own_view.units:
            assert (
                len(
                    {
                        row["vision_image_purpose"]
                        for row in context["ranges"]
                        if row["start"] < right and left < row["end"]
                    }
                )
                <= 1
            )
    assert purposes == {"record", "example"}


@pytest.mark.parametrize("bad", [{}, [], ["image_generated"]])
async def test_untyped_metadata_stays_unknown_without_crashing(
    monkeypatch: pytest.MonkeyPatch, bad: Any
) -> None:
    from rag_engine.generation.image_text_sources import (
        image_source_signature,
        span_image_source_context,
    )

    _, _, parents, _ = await prepared(monkeypatch)
    record = _stored_chunk_answer_record(parents[0])
    metadata = copy.deepcopy(record.metadata)
    metadata.pop("answer_image_text_source_map", None)
    metadata.pop("image_evidence", None)
    metadata["origin"] = bad
    no_images = replace(record, source_record_refs=(), metadata=metadata)
    assert image_source_signature(no_images) is None
    metadata = copy.deepcopy(record.metadata)
    metadata["layout"] = bad
    projected = replace(record, metadata=metadata)
    assert (
        span_image_source_context(projected, 0, len(record.text), record.text)[
            "image_text_source_context"
        ]["native_text_ranges"]
        == []
    )


async def test_catalog_schema_render_and_old_snapshot_stay_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rag_engine.generation.batched_evidence import (
        _labeled_text,
        reading_unit_catalog,
        reading_units,
    )
    from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog

    _, _, parents, _ = await prepared(monkeypatch)
    record = _stored_chunk_answer_record(parents[0])
    spans = grounded.tag_spans(
        "合成の内容は何ですか。",
        evidence_spans("合成の内容は何ですか。", [record], preserve_records=True),
    )
    view = grounded._citation_view(spans[0], explicit_units=True)
    catalog = grounded.generation_unit_catalog(spans)
    assert catalog.snapshots[0].units == view.units
    rendered = "\n".join(
        grounded._citation_view(span, explicit_units=True).rendered for span in spans
    )
    assert all(uid in rendered for uid in catalog.unit_ids)
    schema = json.dumps(catalog.selection_model().model_json_schema())
    assert all(uid in schema for uid in catalog.unit_ids)
    for bound in catalog.bind(catalog.unit_ids):
        source = bound.snapshot.span()
        left = source["start"] + bound.snapshot.units[bound.first][0]
        right = source["start"] + bound.snapshot.units[bound.last][1]
        assert bound.text == record.text[left:right]
    reading = reading_unit_catalog(spans)
    assert reading.snapshots[0].units == tuple(reading_units(spans[0])) == view.units
    shown = _labeled_text("B1", spans[0])
    assert all(uid in shown for uid in reading.snapshots[0].unit_ids)
    legacy_span = copy.deepcopy(spans[0])
    legacy_span.pop("image_text_source_context")
    legacy_units = grounded.citation_units(legacy_span["text"])
    old = EvidenceUnitSnapshot.create(legacy_span, legacy_units, "explicit_units_v5")
    old_catalog = SentUnitCatalog([old])
    original = old_catalog.bind(old.unit_ids)[0].text
    with pytest.raises(ValueError, match="今回の送信にない"):
        catalog.bind(old.unit_ids)
    legacy_span["text"] = "後から変わった本文"
    assert old_catalog.bind(old.unit_ids)[0].text == original
    assert old.contract == "explicit_units_v5"


async def test_budget_excerpt_recalculates_units_on_actual_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks, _ = synthetic_chunks(repetitions=20)
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    parents, _ = restored_from_rows(tx.rows, tx)
    record = _stored_chunk_answer_record(parents[0])
    diagnostics: list[dict[str, Any]] = []
    spans = grounded.tag_spans(
        "合成画像の値は何ですか。",
        evidence_spans(
            "合成画像の値は何ですか。",
            [record],
            budget=600,
            preserve_records=False,
            diagnostics=diagnostics,
        ),
    )
    assert spans
    assert any(span["excerpt_only"] for span in spans)
    assert sum(len(span["text"]) for span in spans) < len(record.text)
    for span in spans:
        assert span["text"] == record.text[span["start"] : span["end"]]
        view = grounded._citation_view(span, explicit_units=True)
        for left, right in view.units:
            assert span["text"][left:right] in view.rendered
            context = span["image_text_source_context"]
            assert (
                len(
                    {
                        row["vision_image_purpose"]
                        for row in context["ranges"]
                        if row["start"] < right and left < row["end"]
                    }
                )
                <= 1
            )
    rejected: list[dict[str, Any]] = []
    evidence_spans(
        "合成の内容は何ですか。",
        [record],
        budget=20,
        verified_quotes={record.id: [record.text]},
        diagnostics=rejected,
    )
    assert any(
        row.get("stage") == "verified_quote" and row.get("status") == "excluded_by_budget"
        for row in rejected
    )


async def test_adjacent_merge_rebuilds_image_positions_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rag_engine.generation.image_text_sources import (
        span_image_source_context,
        span_source_context,
    )
    from rag_engine.retrieval.evidence_selection import _merge_adjacent_spans

    _, _, parents, _ = await prepared(monkeypatch)
    record = _stored_chunk_answer_record(parents[0])
    spans = evidence_spans("合成の内容は何ですか。", [record], preserve_records=True)
    first = {**spans[0], "start": 0, "end": 18, "text": record.text[:18], "origin": "unclassified"}
    second = {
        **spans[0],
        "start": 18,
        "end": len(record.text),
        "text": record.text[18:],
        "origin": "unclassified",
    }
    for span in (first, second):
        span.update(span_image_source_context(record, span["start"], span["end"], span["text"]))
    merged = _merge_adjacent_spans([first, second])
    assert len(merged) == 1
    assert merged[0]["text"] == record.text
    context = span_source_context(merged[0])
    assert context is not None
    assert (
        context["ranges"]
        == span_image_source_context(record, 0, len(record.text), record.text)[
            "image_text_source_context"
        ]["ranges"]
    )


async def test_batch_partial_selection_rebases_image_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rag_engine.generation.batched_evidence import _labeled_text, _slice, reading_unit_catalog
    from rag_engine.generation.image_text_sources import span_source_context

    _, _, parents, _ = await prepared(monkeypatch)
    record = _stored_chunk_answer_record(parents[0])
    spans = grounded.tag_spans(
        "合成の内容は何ですか。",
        evidence_spans("合成の内容は何ですか。", [record], preserve_records=True),
    )
    span = next(span for span in spans if span["image_text_source_context"]["ranges"])
    selected = _slice(span, 1, len(span["text"]) - 1)
    context = span_source_context(selected)
    assert context is not None
    assert selected["text"] == record.text[selected["start"] : selected["end"]]
    assert grounded.evidence_unit_contract(selected) == "explicit_image_units_v1"
    assert all(0 <= row["start"] < row["end"] <= len(selected["text"]) for row in context["ranges"])
    catalog = reading_unit_catalog([selected])
    assert all(uid in _labeled_text("B1", selected) for uid in catalog.unit_ids)
