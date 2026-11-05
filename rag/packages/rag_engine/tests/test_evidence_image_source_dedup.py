"""同文でも未確認の画像や異用途の出典は別根拠として保持する。"""

import copy
import hashlib
from dataclasses import replace

import pytest

from rag_engine.generation.answer_models import AnswerRecord
from rag_engine.retrieval.evidence_selection import (
    collapse_source_spans,
    new_evidence_count,
    record_fingerprint,
    source_span_relation,
)


def _record(uid: str, *, image: bool = True) -> AnswerRecord:
    return AnswerRecord(
        id=uid,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="ParentChunk",
        text="合成画像の値は123です。合成の操作を確認します。",
        source="synthetic.pdf",
        source_run_id="synthetic-run",
        chunk_id=uid,
        chunk_uid=uid,
        chunk_level="parent",
        source_record_refs=({"record_id": "record-1", "category": "Picture"},) if image else (),
        metadata={"document_id": "synthetic-doc", "element_ids": ["record-1"], "section_path": ["合成の節"]},
    )


def test_unverified_images_do_not_share_fingerprint() -> None:
    assert record_fingerprint(_record("left")) != record_fingerprint(_record("right"))


def test_unverified_images_do_not_collapse_by_source_range() -> None:
    left, right = _record("left"), _record("right")
    assert source_span_relation(left, right) is None
    assert len(collapse_source_spans([left, right])) == 2
    assert new_evidence_count([right], [left]) == 1


def test_native_duplicates_keep_existing_merge_behavior() -> None:
    left, right = _record("left", image=False), _record("right", image=False)
    assert record_fingerprint(left) == record_fingerprint(right)
    assert source_span_relation(left, right) == "same"
    assert len(collapse_source_spans([left, right])) == 1
    assert new_evidence_count([right], [left]) == 0


def _admitted(uid: str, *, purpose: str = "record") -> AnswerRecord:
    record = _record(uid)
    metadata = {
        **record.metadata,
        "recipe_id": "synthetic-recipe",
        "chunk_set_id": "synthetic-set",
    }
    metadata["answer_image_text_source_map"] = {
        "version": 1,
        "scope": {"document_id": "synthetic-doc", "recipe_id": "synthetic-recipe", "chunk_set_id": "synthetic-set"},
        "completeness": "complete",
        "source_map": {
            "version": 1,
            "source_run_id": "synthetic-run",
            "source_file_name": "synthetic.pdf",
            "source_engine_id": "docling",
            "text_sha256": hashlib.sha256(record.text.encode()).hexdigest(),
            "ranges": [
                {
                    "source_record_id": "record-1",
                    "container_record_id": "record-1",
                    "role": "image_description",
                    "start": 0,
                    "end": len(record.text),
                    "image_id": "image-1",
                    "vision_image_purpose": purpose,
                }
            ],
        },
    }
    return replace(record, metadata=metadata, body_text=record.text)


def test_identical_confirmed_image_source_can_still_merge() -> None:
    left, right = _admitted("route-left"), _admitted("route-right")
    left = replace(left, chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    right = replace(right, chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    assert record_fingerprint(left) == record_fingerprint(right)
    assert source_span_relation(left, right) == "same"
    assert len(collapse_source_spans([left, right])) == 1


@pytest.mark.parametrize("difference", ["purpose", "image", "role", "recipe", "chunk_set", "source_run", "range"])
def test_different_confirmed_image_sources_are_never_merged(difference: str) -> None:
    left = replace(_admitted("route-left"), chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    right = replace(_admitted("route-right"), chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    metadata = copy.deepcopy(right.metadata)
    marker = metadata["answer_image_text_source_map"]
    mapping = marker["source_map"]
    if difference == "purpose":
        mapping["ranges"][0]["vision_image_purpose"] = "example"
    elif difference == "image":
        mapping["ranges"][0]["image_id"] = "other-image"
    elif difference == "role":
        mapping["ranges"][0]["role"] = "image_ocr"
    elif difference == "recipe":
        marker["scope"]["recipe_id"] = metadata["recipe_id"] = "other-recipe"
    elif difference == "chunk_set":
        marker["scope"]["chunk_set_id"] = metadata["chunk_set_id"] = "other-set"
    elif difference == "source_run":
        mapping["source_run_id"] = "other-run"
    else:
        mapping["ranges"][0]["start"] = 1
    right = replace(right, metadata=metadata)
    assert record_fingerprint(left) != record_fingerprint(right)
    assert source_span_relation(left, right) is None
    assert len(collapse_source_spans([left, right])) == 2
    assert new_evidence_count([right], [left]) == 1


@pytest.mark.parametrize("damage", ["partial", "hash", "scope", "unknown_purpose"])
def test_unconfirmed_or_partial_images_remain_record_isolated(damage: str) -> None:
    left, right = _admitted("left"), _admitted("right")
    damaged = []
    for record in (left, right):
        metadata = copy.deepcopy(record.metadata)
        marker = metadata["answer_image_text_source_map"]
        if damage == "partial":
            marker["completeness"] = "partial"
            marker["source_map"]["unknown_sources"] = ["missing-record"]
        elif damage == "hash":
            marker["source_map"]["text_sha256"] = "0" * 64
        elif damage == "scope":
            marker["scope"]["chunk_set_id"] = "other-set"
        else:
            marker["source_map"]["ranges"][0]["vision_image_purpose"] = "unknown"
        damaged.append(replace(record, metadata=metadata))
    left, right = damaged
    assert record_fingerprint(left) != record_fingerprint(right)
    assert source_span_relation(left, right) is None
    assert len(collapse_source_spans(damaged)) == 2


def test_same_image_and_local_map_with_distinct_chunk_locations_stay_separate() -> None:
    """子段内の位置だけでは別chunkの同画像が同じ出所位置だと証明しない。"""
    left, right = _admitted("stored-left"), _admitted("stored-right")
    assert record_fingerprint(left) != record_fingerprint(right)
    assert source_span_relation(left, right) is None
    assert len(collapse_source_spans([left, right])) == 2


def test_image_sources_reach_separate_evidence_spans() -> None:
    from rag_engine.retrieval.evidence_selection import evidence_spans

    left, right = _admitted("stored-left", purpose="record"), _admitted("stored-right", purpose="example")
    spans = evidence_spans("合成画像の値を確認します。", [left, right], preserve_records=True)
    assert {span["source_id"] for span in spans} == {"stored-left", "stored-right"}
    assert len({span["evidence_id"] for span in spans}) == len(spans)
    purposes = {}
    for span in spans:
        assert span["text"] == left.text[span["start"] : span["end"]]
        assert span["source_aliases"] == [span["source_id"]]
        context = span["image_text_source_context"]
        assert context["scope"] == left.metadata["answer_image_text_source_map"]["scope"]
        purposes[span["source_id"]] = {row["vision_image_purpose"] for row in context["ranges"]}
    assert purposes == {"stored-left": {"record"}, "stored-right": {"example"}}


def test_native_spans_do_not_gain_image_context() -> None:
    from rag_engine.retrieval.evidence_selection import evidence_spans

    spans = evidence_spans("合成の操作を確認します。", [_record("left", image=False), _record("right", image=False)])
    assert len(spans) == 1
    assert spans[0]["source_aliases"] == ["left", "right"]
    assert "image_text_source_context" not in spans[0]


def test_conflicting_maps_for_same_chunk_do_not_reuse_evidence_id() -> None:
    """同じ本文とIDでも用途の矛盾を別Eへ残し、一方の用途に上書きしない。"""
    from rag_engine.retrieval.evidence_selection import evidence_spans

    left = replace(_admitted("route-left", purpose="record"), chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    right = replace(_admitted("route-right", purpose="example"), chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    spans = evidence_spans("合成画像の値を確認します。", [left, right], preserve_records=True)
    assert len(spans) == 2
    assert len({span["evidence_id"] for span in spans}) == 2
    assert {span["source_id"] for span in spans} == {"same-stored-chunk"}
    assert {span["image_text_source_context"]["ranges"][0]["vision_image_purpose"] for span in spans} == {"record", "example"}


def test_collapse_validates_image_signatures_once_per_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_engine.generation import image_text_sources

    original = image_text_sources.image_source_signature
    calls = []

    def counted(record):
        calls.append(record.chunk_uid)
        return original(record)

    monkeypatch.setattr(image_text_sources, "image_source_signature", counted)
    records = [_record(f"native-{index}", image=False) for index in range(40)]
    assert len(collapse_source_spans(records)) == 1
    assert calls == [record.chunk_uid for record in records]
    collapse_source_spans(records)
    assert len(calls) == 80  # 別の操作は前の検証結果を再利用しない。


def test_new_evidence_count_shares_only_its_local_signature_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from rag_engine.generation import image_text_sources

    original = image_text_sources.image_source_signature
    calls = []

    def counted(record):
        calls.append(record.chunk_uid)
        return original(record)

    monkeypatch.setattr(image_text_sources, "image_source_signature", counted)
    seen = [_record(f"seen-{index}", image=False) for index in range(10)]
    incoming = [_record(f"incoming-{index}", image=False) for index in range(20)]
    assert new_evidence_count(incoming, seen) == 0
    assert len(calls) == 30


def test_mapping_change_between_operations_is_revalidated() -> None:
    left = replace(_admitted("route-left"), chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    right = replace(_admitted("route-right"), chunk_uid="same-stored-chunk", chunk_id="same-stored-chunk")
    before = copy.deepcopy(left.metadata)
    assert new_evidence_count([right], [left]) == 0
    assert left.metadata == before
    right.metadata["answer_image_text_source_map"]["source_map"]["ranges"][0]["vision_image_purpose"] = "example"
    assert new_evidence_count([right], [left]) == 1
    assert left.metadata == before


@pytest.mark.parametrize("damaged_hash", [False, True])
def test_legacy_native_only_marker_does_not_change_native_dedup(damaged_hash: bool) -> None:
    """画像部位を含まない旧mapは、通常本文へ画像用途を推定して付けない。"""
    plain = _record("plain", image=False)
    metadata = copy.deepcopy(_admitted("marker").metadata)
    mapping = metadata["answer_image_text_source_map"]["source_map"]
    mapping["ranges"] = [
        {"source_record_id": "record-1", "container_record_id": "record-1", "role": "unclassified", "start": 0, "end": len(plain.text)}
    ]
    if damaged_hash:
        mapping["text_sha256"] = "0" * 64
    projected = replace(plain, id="projected", chunk_uid="projected", chunk_id="projected", metadata=metadata, body_text=plain.text)
    unmarked_metadata = {key: value for key, value in metadata.items() if key != "answer_image_text_source_map"}
    unmarked = replace(plain, metadata=unmarked_metadata, body_text=plain.text)
    assert record_fingerprint(projected) == record_fingerprint(unmarked)
    assert source_span_relation(projected, unmarked) == "same"
    assert len(collapse_source_spans([projected, unmarked])) == 1
