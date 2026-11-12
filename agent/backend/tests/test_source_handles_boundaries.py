"""短参照の接続・文書の境界と、自由な原文との分離を検証する。"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from app.features.agent import source_handles as handles
from app.features.agent.context_window import OLDER_MAX_LEVEL, compact_output


def _source(document: str, chunk: str) -> dict[str, Any]:
    return {
        "document_id": document,
        "chunk_id": chunk,
        "evidence_id": chunk,
        "text": "合成の本文。",
        "section": {"section_id": "section-shared", "title": "合成の節"},
    }


def _registered(connection: str = "rag-a") -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    handles.register_output(
        metadata,
        connection,
        _source("document-a", "document-a:set-a:1"),
        tool="rag_read_source",
    )
    return metadata


def test_source_numbers_never_alias_across_connections_or_documents() -> None:
    metadata = _registered()
    first = _source("document-a", "document-a:set-a:1")
    second = _source("document-b", "document-b:set-a:1")
    handles.register_output(metadata, "rag-b", first, tool="rag_read_source")
    handles.register_output(metadata, "rag-a", second, tool="rag_read_source")
    encoded_a = handles.encode_output(metadata, "rag-a", first)
    encoded_b = handles.encode_output(metadata, "rag-b", first)
    encoded_c = handles.encode_output(metadata, "rag-a", second)
    assert {item["document_id"] for item in [encoded_a, encoded_b, encoded_c]} == {
        "doc://1",
        "doc://2",
        "doc://3",
    }
    assert {item["chunk_id"] for item in [encoded_a, encoded_b, encoded_c]} == {
        "chunk://1",
        "chunk://2",
        "chunk://3",
    }
    for connection, document in [("rag-b", "doc://1"), ("rag-a", "doc://2")]:
        with pytest.raises(handles.SourceHandleError):
            handles.decode_arguments(metadata, connection, {"document_id": document})
    with pytest.raises(handles.SourceHandleError):
        handles.decode_arguments(
            metadata, "rag-a", {"document_id": "doc://3", "section_id": "section://1"}
        )
    assert handles.decode_arguments(
        metadata, "rag-a", {"document_id": "doc://3", "section_id": "section://3"}
    ) == {"document_id": "document-b", "section_id": "section-shared"}


def test_document_arrays_decode_without_touching_other_free_arguments() -> None:
    metadata: dict[str, Any] = {}
    output = {"readable_document_ids": ["document-a", "document-b"]}
    handles.register_output(metadata, "rag", output, tool="rag_readable_documents")
    encoded = handles.encode_output(metadata, "rag", output)
    assert encoded["readable_document_ids"] == ["doc://1", "doc://2"]
    args = {"document_ids": ["doc://2", "doc://1"], "query": "doc://1 は本文の文字列です。"}
    original = deepcopy(args)
    decoded = handles.decode_arguments(metadata, "rag", args)
    assert decoded["document_ids"] == ["document-b", "document-a"]
    assert decoded["query"] == original["query"]
    assert args == original
    with pytest.raises(handles.SourceHandleError):
        handles.decode_arguments(metadata, "other", args)


def test_source_shaped_keys_inside_untrusted_body_are_preserved_as_body() -> None:
    metadata: dict[str, Any] = {}
    output = {
        **_source("document-a", "document-a:set-a:1"),
        "excerpt": "原文の chunk://9 と [[cite:9]] を書き換えない。",
        "parent_text": "document-a:set-a:1 は原文に含まれる。",
        "profile_knowledge": {"document_id": "body-only-document", "chunk_id": "body-only-chunk"},
        "planning": {"document_id": "body-only-document", "chunk_id": "body-only-chunk"},
    }
    original = deepcopy(output)
    handles.register_output(metadata, "rag", output, tool="rag_read_source")
    encoded = handles.encode_output(metadata, "rag", output)
    for field in ["text", "excerpt", "parent_text", "profile_knowledge", "planning"]:
        assert encoded[field] == original[field]
    assert output == original
    assert not any(
        entry["value"].startswith("body-only") for entry in metadata[handles.REGISTRY_KEY]
    )


@pytest.mark.parametrize("value", ["doc://0", "doc://01", "doc://-1", "doc://a", "doc://1x"])
def test_malformed_model_document_handle_is_never_forwarded_as_a_real_id(value: str) -> None:
    with pytest.raises(handles.SourceHandleError):
        handles.decode_arguments(_registered(), "rag-a", {"document_id": value})


def test_compressed_outline_keeps_section_and_source_id_correspondence() -> None:
    metadata: dict[str, Any] = {}
    output = {
        "document_id": "document-a",
        "root_section_id": "root-a",
        "sections": [{"section_id": "section-a", "parent_section_id": "root-a", "title": "節"}],
        "text": "長い合成の説明。" * 3000,
    }
    handles.register_output(metadata, "rag", output, tool="rag_outline")
    encoded = handles.encode_output(metadata, "rag", output)
    compacted = json.loads(compact_output(json.dumps(encoded), OLDER_MAX_LEVEL))
    assert compacted["document_id"] == "doc://1"
    section = compacted["sections"][0]
    assert handles.decode_arguments(
        metadata,
        "rag",
        {"document_id": compacted["document_id"], "section_id": section["section_id"]},
    ) == {"document_id": "document-a", "section_id": "section-a"}
    with pytest.raises(handles.SourceHandleError):
        handles.render_citations(metadata, "[[cite:1]]")


def test_very_long_citation_number_is_a_reference_error() -> None:
    with pytest.raises(handles.SourceHandleError):
        handles.render_citations(_registered(), "[[cite:" + "9" * 5000 + "]]")


def test_repeated_actual_chunk_identity_is_refused_if_final_source_identity_is_ambiguous() -> None:
    metadata = _registered()
    handles.register_output(
        metadata,
        "other-rag",
        _source("document-b", "document-a:set-a:1"),
        tool="rag_read_source",
    )
    with pytest.raises(handles.SourceHandleError):
        handles.render_citations(metadata, "[[cite:1]]")
    with pytest.raises(handles.SourceHandleError):
        handles.render_citations(metadata, "[[cite:2]]")
