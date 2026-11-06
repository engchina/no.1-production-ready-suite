"""検索専用語がSQL bindの検索本文にだけ残り、引用単位へ入らない。"""

from types import SimpleNamespace
from typing import cast

import pytest
from rag_engine.chunking.builder import build_small_to_big_chunks
from rag_engine.chunking.constants import ChunkingConfig
from rag_engine.generation import grounded
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.parsing.picture_descriptions import vision_text_projection
from rag_pipeline_core.chunking import Chunk

from app.clients.oracle import OracleClient, StoredDocument
from app.rag.answer_engine import _stored_child
from app.rag.chunking_small_to_big import _backend_chunk
from app.schemas.search import RetrievedChunk


def projected_chunks() -> list[Chunk]:
    response = {
        "retrieval_text": "設定画面には保存ボタンがあります。",
        "visible_fields": ["検索語"],
        "visible_buttons": ["保存"],
        "search_keywords": ["索引専用甲"],
        "query_rewrites": ["索引専用乙"],
        "answerable_questions": ["索引専用丙"],
    }
    projection = vision_text_projection(response)
    records = [
        {
            "id": "image-1",
            "engine": "docling",
            "page": 1,
            "seq_no": 1,
            "category": "Picture",
            "raw_type": "picture",
            "bbox": [10, 10, 90, 90],
            "page_width": 100,
            "page_height": 100,
            "text": projection.answer_description,
            "raw": {"vision_description": response, "vision_text_projection": projection.dumps()},
        }
    ]
    chunks = build_small_to_big_chunks(
        {"records": records, "engines": [{"engine": "docling", "label": "Docling"}]},
        source_run_id="synthetic-run",
        selected_engine_ids=["docling"],
        config=ChunkingConfig(),
    )
    parent = next(c for c in chunks if c.chunk_level == "parent")
    return [
        _backend_chunk(c, n, parent, {1: (100, 100)})
        for n, c in enumerate(chunks)
        if c.chunk_level == "child"
    ]


def stored_document() -> StoredDocument:
    return cast(
        StoredDocument,
        SimpleNamespace(
            tenant_id_hash="synthetic-tenant", file_name="synthetic.pdf", category_name=None
        ),
    )


def saved_hits(document_id: str) -> list[RetrievedChunk]:
    chunks = projected_chunks()
    # 実SQL writerのbind row作成と保存直後の読取変換。接続/transactionは実行しない。
    rows = OracleClient._chunk_insert_rows(
        document_id, stored_document(), chunks, [None] * len(chunks), "set-1"
    )
    assert all("索引専用甲" in str(row["search_text"]) for row in rows)
    assert all("索引専用甲" not in str(row["chunk_text"]) for row in rows)
    return OracleClient._retrieved_chunks_from_insert_rows(document_id, stored_document(), rows)


def test_sql_bind_read_answer_units_and_render_keep_search_metadata_outside_quotes() -> None:
    for hit in saved_hits("document-a"):
        stored = _stored_child(hit, rrf_score=1.0)
        record = _stored_chunk_answer_record(stored)
        assert (
            stored.metadata["answer_text_projection"] == record.metadata["answer_text_projection"]
        )
        assert any(
            ref.get("vision_text_origin") == "image_generated_description"
            for ref in stored.source_record_refs
        )
        assert any(
            ref.get("vision_text_projection_status") == "verified"
            for ref in record.source_record_refs
        )
        assert record.text == hit.text
        assert "索引専用甲" in stored.retrieval_text
        assert "索引専用甲" not in record.text
        span = {
            "source_id": record.chunk_uid,
            "evidence_id": "E1",
            "source": "合成資料",
            "text": record.text,
            "page": 1,
        }
        units = grounded.citation_units(record.text, image_text=True)
        snapshot = EvidenceUnitSnapshot.create(span, units, "synthetic-v1")
        catalog = SentUnitCatalog([snapshot])
        bindings = catalog.bind(snapshot.unit_ids)
        assert bindings and all("索引専用甲" not in b.text for b in bindings)
        item = grounded._quote_item(span, 0, len(record.text), "合成の範囲選択")
        rendered, refs = grounded.render_with_citations("", [item])
        assert "検索語" in rendered and "保存" in rendered
        assert all(word not in rendered for word in ["索引専用甲", "索引専用乙", "索引専用丙"])
        assert refs


def test_identical_text_from_foreign_document_cannot_reuse_unit_ids() -> None:
    snapshots = []
    for document_id in ["document-a", "document-b"]:
        hit = saved_hits(document_id)[0]
        record = _stored_chunk_answer_record(_stored_child(hit, rrf_score=1.0))
        span = {"source_id": record.chunk_uid, "text": record.text}
        snapshots.append(EvidenceUnitSnapshot.create(span, [(0, len(record.text))], "synthetic-v1"))
    assert snapshots[0].unit_ids != snapshots[1].unit_ids
    with pytest.raises(ValueError, match="今回の送信にない"):
        SentUnitCatalog([snapshots[0]]).bind(snapshots[1].unit_ids)


def test_same_config_cannot_reuse_old_vision_parse_or_chunk_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings
    from app.rag import variant_keys

    enabled = Settings(rag_vision_enabled=True)
    disabled = Settings(rag_vision_enabled=False)
    with monkeypatch.context() as old:
        old.setattr(variant_keys, "VISION_TEXT_FORMAT_VERSION", 2)
        old_parse = variant_keys.compute_extraction_recipe_id("synthetic-hash", enabled)
        old_chunk = variant_keys.compute_chunk_set_id("synthetic-hash", enabled)
        native = variant_keys.compute_chunk_set_id("synthetic-hash", disabled)
    assert variant_keys.compute_extraction_recipe_id("synthetic-hash", enabled) != old_parse
    assert variant_keys.compute_chunk_set_id("synthetic-hash", enabled) != old_chunk
    assert variant_keys.compute_chunk_set_id("synthetic-hash", disabled) == native
