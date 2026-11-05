"""正式画像mapを持つ無anchorの原文だけを画像由来へ分類する。"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest
from rag_engine.generation.answer_models import AnswerContext
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.generation.answering import _grounded_spans
from rag_engine.generation.image_text_sources import ANSWER_IMAGE_SOURCE_KEY, admitted_record_map
from rag_engine.retrieval.evidence_selection import _span_origin

from app.rag.answer_engine import _stored_child
from tests.test_image_text_source_storage import (
    SyntheticTransaction,
    restored_from_rows,
    save_synthetic,
    synthetic_chunks,
)


async def records(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    chunks, _ = synthetic_chunks()
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    parents, state = restored_from_rows(tx.rows, tx)
    return [
        _stored_chunk_answer_record(parents[0]),
        *[
            _stored_chunk_answer_record(_stored_child(hit, rrf_score=1))
            for hit in state.chunks.values()
        ],
    ]


async def test_actual_saved_parent_and_children_classify_image_without_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, native, record, example = await records(monkeypatch)
    native_range = parent.metadata["layout"]["native_text_ranges"][0]
    assert _span_origin(parent, native_range["start"], native_range["end"], []) == "document_text"
    assert _span_origin(native, 0, len(native.text), []) == "document_text"
    for current in (parent, record, example):
        value = admitted_record_map(current)
        assert value is not None
        for row in value["ranges"]:
            assert _span_origin(current, row["start"], row["end"], []) == "image_extraction"
    assert _span_origin(parent, 0, len(parent.text), []) == "unclassified"


async def test_actual_grounded_origin_and_tags_keep_unit_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rag_engine.generation import grounded

    _, _, record, example = await records(monkeypatch)
    for current in (record, example):
        spans = _grounded_spans(
            "資料に記録された値を教えてください。",
            AnswerContext(records=[current], text=current.text),
            [],
            None,
            preserve_records=True,
        )
        assert spans
        for span in spans:
            assert span["origin"] == "image_extraction"
            assert "image_generated" in span["tags"]
            view = grounded._citation_view(span, explicit_units=True)
            assert view.contract_version == "explicit_image_units_v1"
            assert "image_purposes=" in view.rendered
            assert "origin=image_extraction" in view.rendered
            assert "tags=image_generated" in view.rendered


@pytest.mark.parametrize("purpose", ["record", "example", "unknown"])
async def test_origin_does_not_decide_image_purpose(
    monkeypatch: pytest.MonkeyPatch, purpose: str
) -> None:
    _, _, current, _ = await records(monkeypatch)
    for row in current.metadata[ANSWER_IMAGE_SOURCE_KEY]["source_map"]["ranges"]:
        row["vision_image_purpose"] = purpose
    assert _span_origin(current, 0, len(current.text), []) == "image_extraction"
    assert (
        current.metadata[ANSWER_IMAGE_SOURCE_KEY]["source_map"]["ranges"][0]["vision_image_purpose"]
        == purpose
    )


@pytest.mark.parametrize(
    "bad", ["missing", "scope", "hash", "text_coordinates", "uncovered", "overlap"]
)
async def test_missing_or_unproven_positions_stay_unclassified(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _, _, current, _ = await records(monkeypatch)
    marker = current.metadata[ANSWER_IMAGE_SOURCE_KEY]
    if bad == "missing":
        current.metadata.pop(ANSWER_IMAGE_SOURCE_KEY)
    elif bad == "scope":
        marker["scope"]["recipe_id"] = "different-recipe"
    elif bad == "hash":
        marker["source_map"]["text_sha256"] = "0" * 64
    elif bad == "text_coordinates":
        current = replace(current, text="検索用の前置き\n" + current.text)
    elif bad == "uncovered":
        marker["source_map"]["ranges"][0]["end"] -= 1
    elif bad == "overlap":
        marker["source_map"]["ranges"].append(copy.deepcopy(marker["source_map"]["ranges"][0]))
    assert _span_origin(current, 0, len(current.text), []) == "unclassified"


async def test_partial_map_does_not_hide_an_uncovered_nonblank_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, current, _ = await records(monkeypatch)
    marker = current.metadata[ANSWER_IMAGE_SOURCE_KEY]
    marker["completeness"] = "partial"
    marker["source_map"]["unknown_sources"] = ["missing-source"]
    assert _span_origin(current, 0, len(current.text), []) == "image_extraction"
    marker["source_map"]["ranges"][0]["end"] -= 1
    assert _span_origin(current, 0, len(current.text), []) == "unclassified"


async def test_existing_native_and_anchor_classification_take_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, _, record, _ = await records(monkeypatch)
    native = parent.metadata["layout"]["native_text_ranges"][0]
    assert (
        _span_origin(parent, native["start"], native["end"], [(0, len(parent.text), 1)])
        == "document_text"
    )
    record.metadata.pop(ANSWER_IMAGE_SOURCE_KEY)
    assert (
        _span_origin(record, 0, len(record.text), [(0, len(record.text), 1)]) == "image_extraction"
    )


async def test_empty_or_whitespace_and_invalid_ranges_do_not_gain_image_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, record, _ = await records(monkeypatch)
    assert _span_origin(record, 0, 0, []) == "unclassified"
    assert _span_origin(record, -1, len(record.text), []) == "unclassified"
    whitespace = record.text.index(" ")
    assert _span_origin(record, whitespace, whitespace + 1, []) == "unclassified"


async def test_two_mapped_images_can_share_only_whitespace_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent, _, _, _ = await records(monkeypatch)
    value = admitted_record_map(parent)
    assert value is not None
    left, right = value["ranges"]
    assert not parent.text[left["end"] : right["start"]].strip()
    assert _span_origin(parent, left["start"], right["end"], []) == "image_extraction"


async def test_actual_text_parse_sees_image_origin_and_same_unit_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rag_engine.config import get_settings
    from rag_engine.generation import grounded

    class Captured(BaseException):
        pass

    _, _, current, _ = await records(monkeypatch)
    question = "資料に記録された値を教えてください。"
    spans = _grounded_spans(
        question,
        AnswerContext(records=[current], text=current.text),
        [],
        None,
        preserve_records=True,
    )
    prompt = grounded.evidence_block_text(spans)
    captured: dict[str, Any] = {}

    def parse(system: str, user: str, settings: Any, schema: Any, **options: Any) -> Any:
        captured.update(system=system, user=user, schema=schema.model_json_schema())
        raise Captured()

    with pytest.raises(Captured):
        grounded.run_round(
            question,
            spans,
            get_settings(environ={}, dotenv_path=None),
            prompt=prompt,
            image_paths=[],
            provider_id=None,
            parse_text=parse,
            parse_images=lambda *args, **kwargs: pytest.fail("画像呼び出しは禁止"),
            audit_mode=grounded.AUDIT_MODE_NONE,
        )
    assert "origin=image_extraction" in captured["user"]
    assert "image_generated" in captured["user"]
    assert "image_purposes=" in captured["user"]
    assert '"purpose":"record"' in captured["user"]
    assert current.text in captured["user"]
