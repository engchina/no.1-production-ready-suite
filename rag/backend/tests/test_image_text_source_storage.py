"""実producer・Oracle保存トランザクション・親復元を合成接続で検証する。"""

import copy
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from rag_engine.chunking import builder
from rag_engine.chunking.metadata import _project_chunk_metadata

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient, StoredDocument
from app.rag.answer_engine import _SearchState, _stored_child, _stored_parents
from app.rag.chunking_small_to_big import _backend_chunk
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction


def synthetic_chunks(*, repetitions: int = 1) -> tuple[list[Any], Any]:
    children = []
    for index, (category, text, purpose) in enumerate(
        [
            ("Text", "合成の本文 900。", None),
            ("Picture", "合成画像の値 123。", "record"),
            ("Picture", "合成画像の値 123。", "example"),
        ]
        * repetitions,
        1,
    ):
        raw = {"vision_image_purpose": purpose} if purpose else {}
        record = dict(
            id=f"element-{index}",
            engine="docling",
            page=1,
            seq_no=index,
            category=category,
            raw_type=category.lower(),
            text=text,
            raw=raw,
        )
        child = builder._child_chunk(
            index,
            [record],
            source_run_id="abcdef0123456789",
            source_file_name="synthetic.pdf",
            source_engine_id="docling",
            source_engine_label="Docling",
            metadata_by_page={},
            section_path=(),
            atomic=True,
        )
        child.parent_chunk_id = "synthetic-parent"
        child.metadata = _project_chunk_metadata(child.metadata)
        children.append(child)
    parent = builder._parent_chunk(
        "synthetic-parent",
        1,
        children,
        source_run_id="abcdef0123456789",
        source_file_name="synthetic.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
    )
    backend = [_backend_chunk(child, i, parent, {}) for i, child in enumerate(children)]
    return backend, parent


class SyntheticTransaction:
    def __init__(self) -> None:
        self.scope = {
            "document_id": "synthetic-doc",
            "recipe_id": "synthetic-recipe",
            "chunk_set_id": "synthetic-set",
            "scope_accessible": 1,
        }
        self.scope_reads = 0
        self.rows: list[dict[str, Any]] = []
        self.statements: list[str] = []

    def fetch(
        self, _connection: Any, statement: str, _binds: Any = None, **_options: Any
    ) -> list[dict[str, Any]]:
        if "rag_chunk_sets" in statement and "recipe_id" in statement:
            self.scope_reads += 1
            return [copy.deepcopy(self.scope)]
        if "COUNT(*)" in statement:
            return [{"cnt": len(self.rows)}]
        return []

    def execute(
        self, _connection: Any, statement: str, _binds: Any = None, **_options: Any
    ) -> None:
        self.statements.append(statement)

    def many(self, _connection: Any, statement: str, rows: Any, **_options: Any) -> None:
        self.statements.append(statement)
        self.rows = copy.deepcopy(rows)


async def save_synthetic(
    monkeypatch: pytest.MonkeyPatch, tx: SyntheticTransaction, chunks: list[Any]
) -> list[Any]:
    client = OracleClient()
    doc = StoredDocument(
        id="synthetic-doc",
        file_name="synthetic.pdf",
        status=FileStatus.CHUNKED,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(oracle_module, "_select_document_state", lambda *_args: doc)
    monkeypatch.setattr(oracle_module, "_fetch_all", tx.fetch)
    monkeypatch.setattr(oracle_module, "_execute", tx.execute)
    monkeypatch.setattr(oracle_module, "_executemany", tx.many)

    async def transaction(operation: Any) -> Any:
        return operation(object())

    monkeypatch.setattr(client, "_run_transaction", transaction)
    return await client._save_index_with_oracle(
        "synthetic-doc",
        StructuredExtraction(raw_text="合成本文"),
        chunks,
        [None] * len(chunks),
        chunk_set_id="synthetic-set",
    )


async def test_actual_writer_binds_authoritative_scope_and_parent_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks, parent = synthetic_chunks()
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    assert tx.scope_reads == 1
    state = _SearchState()
    children = []
    for saved in json.loads(json.dumps(tx.rows)):
        hit = oracle_module._retrieved_chunk_from_row(
            {**saved, **tx.scope, "file_name": "synthetic.pdf"}
        )
        scope = json.loads(str(hit.metadata["image_text_source_scope_json"]))
        assert scope["document_id"] == tx.scope["document_id"]
        assert scope["recipe_id"] == tx.scope["recipe_id"]
        assert scope["chunk_set_id"] == tx.scope["chunk_set_id"]
        state.chunks[hit.chunk_id] = hit
        children.append(_stored_child(hit, rrf_score=1))
    restored = _stored_parents(children, state)[0]
    assert restored.text == parent.text
    assert (
        restored.metadata["layout"]["text_source_map"]["ranges"]
        == parent.metadata["layout"]["text_source_map"]["ranges"]
    )
    assert restored.metadata["image_text_source_map_status"] == "complete"


def test_producer_keeps_small_append_placements_without_parent_map_copies() -> None:
    chunks, parent = synthetic_chunks()
    placements = parent.metadata["layout"]["text_source_map"]["children"]
    assert len(placements) == 3
    cursor = 0
    for chunk, position in zip(chunks, placements, strict=True):
        assert position["start"] == cursor
        assert position["end"] == cursor + len(chunk.text)
        small = json.loads(str(chunk.metadata["parent_text_placement_json"]))
        assert small["start"] == cursor
        assert small["child_count"] == 3
        assert "ranges" not in small
        cursor += len(chunk.text) + 2


def restored_from_rows(
    rows: list[dict[str, Any]], tx: SyntheticTransaction
) -> tuple[list[Any], _SearchState]:
    state = _SearchState()
    children = []
    for row in rows:
        hit = oracle_module._retrieved_chunk_from_row(
            {**row, **tx.scope, "file_name": "synthetic.pdf"}
        )
        state.chunks[hit.chunk_id] = hit
        children.append(_stored_child(hit, rrf_score=1))
    return _stored_parents(children, state), state


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_id", "other-doc"),
        ("recipe_id", "other-recipe"),
        ("chunk_set_id", "other-set"),
        ("version", True),
    ],
)
async def test_foreign_scope_is_unknown_without_rebinding(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    chunks, parent = synthetic_chunks()
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    row = tx.rows[1]
    metadata = json.loads(str(row["metadata_json"]))
    scope = json.loads(metadata["image_text_source_scope_json"])
    scope[field] = value
    metadata["image_text_source_scope_json"] = json.dumps(scope)
    row["metadata_json"] = json.dumps(metadata)
    restored = restored_from_rows(tx.rows, tx)[0][0]
    assert restored.text == parent.text
    assert restored.metadata["image_text_source_map_status"] == "partial"
    ranges = restored.metadata["layout"]["text_source_map"]["ranges"]
    assert len(ranges) == 1
    assert ranges[0]["vision_image_purpose"] == "example"


@pytest.mark.parametrize(
    "bad",
    ["scope", "placement", "parent_hash", "map_hash", "record_id", "image_id", "scope_version"],
)
async def test_missing_or_damaged_binding_is_unknown(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    chunks, parent = synthetic_chunks()
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    for row in tx.rows:
        metadata = json.loads(str(row["metadata_json"]))
        if bad == "scope":
            metadata.pop("image_text_source_scope_json", None)
        elif bad == "placement":
            metadata.pop("parent_text_placement_json", None)
        elif bad == "parent_hash":
            place = json.loads(metadata["parent_text_placement_json"])
            place["parent_text_sha256"] = "0" * 64
            metadata["parent_text_placement_json"] = json.dumps(place)
        else:
            engine = json.loads(metadata["engine_metadata_json"])
            if bad == "map_hash":
                engine["layout"]["text_source_map"]["text_sha256"] = "0" * 64
            elif bad == "record_id":
                metadata["source_record_refs_json"] = "[]"
            elif bad == "image_id":
                for item in engine["layout"]["text_source_map"]["ranges"]:
                    item["image_id"] = "other-image"
            else:
                scope = json.loads(metadata["image_text_source_scope_json"])
                scope["version"] = "1"
                metadata["image_text_source_scope_json"] = json.dumps(scope)
            metadata["engine_metadata_json"] = json.dumps(engine)
        row["metadata_json"] = json.dumps(metadata)
    restored = restored_from_rows(tx.rows, tx)[0][0]
    assert restored.text == parent.text
    assert restored.metadata["image_text_source_map_status"] in {"unknown", "partial"}
    assert not restored.metadata.get("layout", {}).get("text_source_map", {}).get("ranges")


async def test_partial_read_keeps_offsets_and_never_claims_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks, parent = synthetic_chunks()
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    restored = restored_from_rows(tx.rows[2:], tx)[0][0]
    assert restored.text == parent.text
    assert restored.metadata["image_text_source_map_status"] == "partial"
    rows = restored.metadata["layout"]["text_source_map"]["ranges"]
    assert len(rows) == 1 and rows[0]["start"] > len(chunks[0].text)
    assert rows[0]["vision_image_purpose"] == "example"


async def test_wrong_storage_document_fails_before_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks, _parent = synthetic_chunks()
    tx = SyntheticTransaction()
    tx.scope["document_id"] = "other-document"
    with pytest.raises(KeyError):
        await save_synthetic(monkeypatch, tx, chunks)
    assert tx.scope_reads == 1
    assert tx.statements == []


async def test_embedding_update_preserves_valid_scope_and_drops_wrong_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks, _parent = synthetic_chunks()
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    stored = []
    for chunk, row in zip(chunks, tx.rows, strict=True):
        stored.append(
            type(chunk)(
                text=chunk.text,
                index=chunk.index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                metadata=json.loads(str(row["metadata_json"])),
            )
        )
    expected = stored[1].metadata["image_text_source_scope_json"]
    wrong = json.loads(str(stored[2].metadata["image_text_source_scope_json"]))
    wrong["chunk_set_id"] = "other-version"
    stored[2].metadata["image_text_source_scope_json"] = json.dumps(wrong)
    client = OracleClient()

    async def transaction(operation: Any) -> Any:
        return operation(object())

    monkeypatch.setattr(client, "_run_transaction", transaction)
    tx.scope_reads = 0
    await client._update_chunk_set_embeddings_with_oracle(
        chunk_set_id="synthetic-set", chunks=stored, embeddings=[[0.1] * 1536] * 3
    )
    assert tx.scope_reads == 1
    assert json.loads(str(tx.rows[1]["metadata_json"]))["image_text_source_scope_json"] == expected
    assert "image_text_source_scope_json" not in json.loads(str(tx.rows[2]["metadata_json"]))


async def test_clone_keeps_unproven_mapping_unknown_and_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tx = SyntheticTransaction()
    client = OracleClient()
    monkeypatch.setattr(oracle_module, "_execute", tx.execute)

    async def transaction(operation: Any) -> Any:
        return operation(object())

    monkeypatch.setattr(client, "_run_transaction", transaction)
    await client.clone_recipe_chunk_set_chunks(
        recipe_id="synthetic-recipe", source_chunk_set_id="source", target_chunk_set_id="target"
    )
    statement = tx.statements[0]
    assert "'image_text_source_scope_json' VALUE NULL" in statement
    assert "dst.recipe_id = src.recipe_id" in statement
    assert "dst.document_id = src.document_id" in statement
    assert "tenant_id_hash" in statement


async def test_old_unscoped_map_is_not_silently_bound_on_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks, _parent = synthetic_chunks()
    for chunk in chunks:
        chunk.metadata.pop("image_text_source_ready", None)
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    assert all(
        "image_text_source_scope_json" not in json.loads(str(row["metadata_json"]))
        for row in tx.rows
    )
    assert (
        restored_from_rows(tx.rows, tx)[0][0].metadata["image_text_source_map_status"] == "unknown"
    )


async def test_authoritative_scope_denial_has_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks, _parent = synthetic_chunks()
    tx = SyntheticTransaction()

    def denied(_connection: Any, statement: str, _binds: Any = None) -> list[Any]:
        assert "CASE WHEN" in statement and "scope_accessible" in statement
        assert "JOIN rag_documents d" in statement
        tx.scope_reads += 1
        return [
            {
                "chunk_set_id": "synthetic-set",
                "document_id": None,
                "recipe_id": None,
                "scope_accessible": 0,
            }
        ]

    monkeypatch.setattr(tx, "fetch", denied)
    with pytest.raises(KeyError):
        await save_synthetic(monkeypatch, tx, chunks)
    assert tx.scope_reads == 1
    assert tx.statements == []


def test_duplicate_producer_child_id_has_no_guessed_placement() -> None:
    from dataclasses import replace

    from rag_engine.chunking.builder import _chunk_from_payload

    # 本文は同じでも、同じ識別子を二つの位置へ推定して割り当てない。
    rec = dict(
        id="image",
        engine="docling",
        page=1,
        seq_no=1,
        category="Picture",
        text="合成画像です。",
        raw={"vision_image_purpose": "record"},
    )
    child = builder._child_chunk(
        1,
        [rec],
        source_run_id="abcdef0123456789",
        source_file_name="synthetic.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
        metadata_by_page={},
        section_path=(),
        atomic=True,
    )
    duplicate = replace(child, metadata=copy.deepcopy(child.metadata))
    parent = builder._parent_chunk(
        "parent",
        1,
        [child, duplicate],
        source_run_id=child.source_run_id,
        source_file_name=child.source_file_name,
        source_engine_id=child.source_engine_id,
        source_engine_label=child.source_engine_label,
    )
    parent.metadata = _project_chunk_metadata(parent.metadata)
    parent = _chunk_from_payload(json.loads(json.dumps(parent.to_dict())))
    for index, item in enumerate([child, duplicate]):
        chunk = _backend_chunk(item, index, parent, {})
        assert "parent_text_placement_json" not in chunk.metadata


@pytest.mark.parametrize("mode", ["absent", "legacy"])
async def test_missing_or_legacy_chunk_set_keeps_body_without_invented_scope(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    chunks, _ = synthetic_chunks()
    tx = SyntheticTransaction()
    if mode == "absent":
        original = tx.fetch

        def missing(connection: Any, statement: str, binds: Any = None, **options: Any) -> Any:
            if "rag_chunk_sets" in statement and "recipe_id" in statement:
                tx.scope_reads += 1
                return []
            return original(connection, statement, binds, **options)

        monkeypatch.setattr(tx, "fetch", missing)
    else:
        tx.scope["recipe_id"] = None
    await save_synthetic(monkeypatch, tx, chunks)
    assert tx.scope_reads == 1
    assert [row["chunk_text"] for row in tx.rows] == [chunk.text for chunk in chunks]
    assert all(
        "image_text_source_scope_json" not in json.loads(row["metadata_json"]) for row in tx.rows
    )


@pytest.mark.parametrize("mode", ["absent", "denied"])
async def test_embedding_without_authorized_row_never_updates(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    chunks, _ = synthetic_chunks()
    tx = SyntheticTransaction()
    monkeypatch.setattr(
        oracle_module,
        "_fetch_all",
        lambda *_: (
            []
            if mode == "absent"
            else [
                {
                    "chunk_set_id": "synthetic-set",
                    "scope_accessible": 0,
                    "document_id": None,
                    "recipe_id": None,
                }
            ]
        ),
    )
    monkeypatch.setattr(oracle_module, "_execute", tx.execute)
    monkeypatch.setattr(oracle_module, "_executemany", tx.many)
    client = OracleClient()

    async def transaction(operation: Any) -> Any:
        return operation(object())

    monkeypatch.setattr(client, "_run_transaction", transaction)
    with pytest.raises(KeyError):
        await client._update_chunk_set_embeddings_with_oracle(
            chunk_set_id="synthetic-set", chunks=chunks, embeddings=[[0.1] * 1536] * len(chunks)
        )
    assert tx.statements == []


@pytest.mark.parametrize("field", ["source_parser", "source_file_name", "source_run_id"])
async def test_provenance_mismatch_is_not_bound(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    chunks, _ = synthetic_chunks()
    for chunk in chunks:
        if field == "source_parser":
            chunk.metadata[field] = "other_layout"
        else:
            engine = json.loads(chunk.metadata["engine_metadata_json"])
            engine["layout"]["text_source_map"][field] = "different"
            chunk.metadata["engine_metadata_json"] = json.dumps(engine)
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    # 原生本文の空mapには独立した画像出典がないため、画像を持つ行で検証する。
    assert all(
        "image_text_source_scope_json" not in json.loads(row["metadata_json"])
        for row in tx.rows[1:]
    )


async def test_overlapping_parent_placements_are_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.image_text_sources import _digest

    chunks, _ = synthetic_chunks()
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    first = json.loads(tx.rows[1]["metadata_json"])
    second = json.loads(tx.rows[2]["metadata_json"])
    place = json.loads(second["parent_text_placement_json"])
    start = json.loads(first["parent_text_placement_json"])["start"]
    place.update(start=start, end=start + len(chunks[2].text))
    second["parent_text_placement_json"] = json.dumps(place)
    scope = json.loads(second["image_text_source_scope_json"])
    scope["placement_sha256"] = _digest(place)
    second["image_text_source_scope_json"] = json.dumps(scope)
    tx.rows[2]["metadata_json"] = json.dumps(second)
    restored = restored_from_rows(tx.rows, tx)[0][0]
    assert restored.metadata["image_text_source_map_status"] == "unknown"
    assert "text_source_map" not in restored.metadata["layout"]


@pytest.mark.parametrize("mode", ["unknown_sources", "duplicate_ranges", "overlap_ranges"])
async def test_incomplete_child_is_not_promoted_to_complete(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    chunks, _ = synthetic_chunks()
    for chunk in chunks[1:]:
        engine = json.loads(chunk.metadata["engine_metadata_json"])
        value = engine["layout"]["text_source_map"]
        if mode == "unknown_sources":
            value["unknown_sources"] = ["missing-record"]
        else:
            row = copy.deepcopy(value["ranges"][0])
            if mode == "overlap_ranges":
                row["start"] += 1
            value["ranges"].append(row)
        chunk.metadata["engine_metadata_json"] = json.dumps(engine)
    tx = SyntheticTransaction()
    await save_synthetic(monkeypatch, tx, chunks)
    restored = restored_from_rows(tx.rows, tx)[0][0]
    assert restored.metadata["image_text_source_map_status"] == "partial"
    value = restored.metadata["layout"]["text_source_map"]
    assert value["unknown_sources"]
    if mode == "unknown_sources":
        assert len(value["ranges"]) == 2
    else:
        assert value["ranges"] == []
