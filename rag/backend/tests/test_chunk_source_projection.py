"""正式writerは保存する抽出と既存typed画像位置の出所を照合する。"""

import copy
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from rag_engine.models.layout import LayoutRecord
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_parser_core.vision_text import VisionTextProjection
from rag_parser_core.vision_text_map import VISION_CHUNK_MAP_KEY
from rag_pipeline_core.chunking import CHUNKING_STRATEGIES, Chunk

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient, StoredDocument
from app.rag.chunk_source_projection import _validate_chunk_source_projection
from app.rag.chunking import chunk_extraction_with_strategy
from app.rag.vision import _Layout, _Target, _write_back_adapter, _write_back_docling
from app.schemas.document import FileStatus
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline


def saved_extraction(identity: str = "") -> StructuredExtraction:
    body = "画像に保存ボタンがあります。"
    visual = LayoutRecord(
        "image-record" + identity,
        "unstructured",
        1,
        1,
        [0, 0, 100, 100],
        "image_top_left",
        100,
        100,
        "Picture",
        text=body,
        raw={
            "vision_status": "succeeded",
            "vision_text_projection": VisionTextProjection(
                body, body + "\n検索用の合成別表記"
            ).dumps(),
        },
    )
    return _write_back_adapter(
        StructuredExtraction(
            raw_text="元の画像文字",
            elements=[
                DocumentElement(
                    kind="figure",
                    text="元の画像文字",
                    element_id="figure-element" + identity,
                    source_parser="unstructured",
                    page_number=1,
                )
            ],
        ),
        _Layout(records=[visual], targets=[_Target(visual, element_index=0)]),
    )


def formal_chunks(extraction: StructuredExtraction, strategy: str) -> list[Chunk]:
    return chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=200, overlap=0, delimiter="\n\n"
    )


def changed_chunks(chunks: list[Chunk], field: str) -> list[Chunk]:
    result = copy.deepcopy(chunks)
    for chunk in result:
        value = json.loads(str(chunk.metadata[VISION_CHUNK_MAP_KEY]))
        for source in value["sources"]:
            if field == "source_projection":
                source["projection"] = json.loads(
                    VisionTextProjection("別の本文", "別の本文\n補助語").dumps()
                )
            else:
                source[field] = "foreign-identity"
        chunk.metadata[VISION_CHUNK_MAP_KEY] = json.dumps(value, ensure_ascii=False)
    return result


def docling_table_extraction() -> StructuredExtraction:
    """表の本文は変えず、Vision の補足を分割のときに足す Docling の抽出。"""
    body = "表の中の図の説明です。"
    record = LayoutRecord(
        "table-record",
        "docling",
        1,
        1,
        [0, 0, 100, 100],
        "image_top_left",
        100,
        100,
        "Table",
        text=body,
        raw={
            "vision_status": "succeeded",
            "vision_text_projection": VisionTextProjection(body, body + "\n索引専用語").dumps(),
            "table_vision_text": body,
        },
    )
    element = DocumentElement(
        kind="table",
        text="<table><tr><td>原生値</td></tr></table>",
        element_id="table-record",
        source_parser="docling_layout",
        page_number=1,
    )
    return _write_back_docling(
        StructuredExtraction(raw_text="", elements=[element]), _Layout(records=[record])
    )


def test_table_supplement_added_at_chunking_is_certified() -> None:
    extraction = docling_table_extraction()
    chunks = formal_chunks(extraction, "page_level")
    assert any(VISION_CHUNK_MAP_KEY in chunk.metadata for chunk in chunks)
    _validate_chunk_source_projection(extraction, chunks)


def test_table_supplement_from_another_record_is_rejected() -> None:
    extraction = docling_table_extraction()
    chunks = changed_chunks(formal_chunks(extraction, "page_level"), "record_id")
    with pytest.raises(ValueError, match="出所が一致しません"):
        _validate_chunk_source_projection(extraction, chunks)


def writer(
    monkeypatch: pytest.MonkeyPatch, *, visible: bool = True
) -> tuple[OracleClient, FakeOraclePool, StoredDocument]:
    pool = FakeOraclePool()
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)
    document = StoredDocument(
        id="synthetic-doc",
        file_name="synthetic.pdf",
        status=FileStatus.CHUNKED,
        uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(
        oracle_module, "_select_document_state", lambda *_: document if visible else None
    )
    return client, pool, document


def writes(pool: FakeOraclePool) -> list[Any]:
    return [
        c
        for c in pool.connection.calls
        if c.statement.lstrip().upper().startswith(("UPDATE", "DELETE", "MERGE", "INSERT"))
    ] + pool.connection.many_calls


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
@pytest.mark.parametrize("damage", [None, "element_id", "record_id", "source_projection"])
async def test_actual_writer_checks_same_saved_source_before_first_write(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
    damage: str | None,
) -> None:
    extraction = saved_extraction()
    chunks = formal_chunks(extraction, strategy)
    if damage is not None:
        chunks = changed_chunks(chunks, damage)
    client, pool, document = writer(monkeypatch)
    if damage is not None:
        with pytest.raises(ValueError):
            await client._save_index_with_oracle(
                document.id, extraction, chunks, [None] * len(chunks), chunk_set_id="synthetic-set"
            )
        assert not writes(pool)
        assert pool.connection.commits == 0
        assert pool.connection.rollbacks == 1
        assert pool.acquire_calls == 1
    else:
        expected = client._chunk_insert_rows(
            document.id, document, chunks, [None] * len(chunks), chunk_set_id="synthetic-set"
        )
        saved = await client._save_index_with_oracle(
            document.id, extraction, chunks, [None] * len(chunks), chunk_set_id="synthetic-set"
        )
        assert len(saved) == len(chunks)
        assert pool.connection.many_calls[0].rows == expected
        assert pool.connection.commits == 1
        assert pool.connection.rollbacks == 0


@pytest.mark.parametrize("kind", ["text", "figure", "table"])
async def test_native_ocr_and_html_without_map_are_noop(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    from app.rag import chunk_source_projection as source_check

    text = (
        "<table><tr><td>原生本文</td></tr></table>"
        if kind == "table"
        else "原生の検索語とOCR本文です。"
    )
    element = DocumentElement(
        kind=kind,
        element_id="native-a",
        text=text,
        metadata={"figure_text_source": "ocr"} if kind == "figure" else {},
    )
    extraction = StructuredExtraction(raw_text=text, elements=[element])
    chunks = formal_chunks(extraction, "structure_aware")
    assert chunks and all(VISION_CHUNK_MAP_KEY not in c.metadata for c in chunks)
    before = copy.deepcopy(chunks)

    def forbidden(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("原生本文で画像の元要素を読み直さない")

    monkeypatch.setattr(source_check, "projected_element_text", forbidden)
    client, pool, doc = writer(monkeypatch)
    expected = client._chunk_insert_rows(doc.id, doc, chunks, [None] * len(chunks))
    await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert pool.connection.many_calls[0].rows == expected
    assert chunks == before


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
async def test_partial_chunks_and_same_text_distinct_sources_stay_valid(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    left, right = saved_extraction("-a"), saved_extraction("-b")
    extraction = StructuredExtraction(
        raw_text=left.raw_text + "\n\n" + right.raw_text,
        elements=[*left.elements, *right.elements],
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy=strategy, chunk_size=9, overlap=2, delimiter="\n\n"
    )
    before = copy.deepcopy(chunks)
    client, pool, doc = writer(monkeypatch)
    expected = client._chunk_insert_rows(doc.id, doc, chunks, [None] * len(chunks))
    await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert chunks == before
    assert pool.connection.many_calls[0].rows == expected


async def test_source_is_resolved_once_for_multiple_partial_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import chunk_source_projection as source_check

    extraction = saved_extraction()
    chunks = chunk_extraction_with_strategy(
        extraction, strategy="fixed_size", chunk_size=8, overlap=2
    )
    assert len(chunks) > 1
    from rag_parser_core.vision_text_map import projected_element_text

    original = projected_element_text
    calls = []

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(source_check, "projected_element_text", counted)
    client, _pool, doc = writer(monkeypatch)
    await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert calls == ["figure-element"]


async def test_acl_denial_precedes_projection_validation_and_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import chunk_source_projection as source_check

    extraction = saved_extraction()
    chunks = changed_chunks(formal_chunks(extraction, "fixed_size"), "element_id")
    calls = []
    monkeypatch.setattr(
        source_check, "_validate_chunk_source_projection", lambda *_: calls.append(True)
    )
    client, pool, doc = writer(monkeypatch, visible=False)
    with pytest.raises(KeyError):
        await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert not calls
    assert not writes(pool)
    assert pool.connection.rollbacks == 1
    assert pool.connection.commits == 0


@pytest.mark.parametrize("damage", ["missing", "duplicate", "changed_body"])
async def test_ambiguous_or_different_saved_source_cannot_certify_chunk(
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    extraction = saved_extraction()
    chunks = formal_chunks(extraction, "fixed_size")
    if damage == "missing":
        changed = extraction.model_copy(update={"elements": []})
    elif damage == "duplicate":
        changed = extraction.model_copy(
            update={
                "elements": [*extraction.elements, extraction.elements[0].model_copy(deep=True)]
            }
        )
    else:
        changed = extraction.model_copy(
            update={
                "elements": [extraction.elements[0].model_copy(update={"text": "変更した本文"})]
            }
        )
    client, pool, doc = writer(monkeypatch)
    with pytest.raises(ValueError):
        await client._save_index_with_oracle(doc.id, changed, chunks, [None] * len(chunks))
    assert not writes(pool)
    assert pool.connection.rollbacks == 1


@pytest.mark.parametrize("value", [None, "not-json", "{}"])
async def test_present_invalid_map_is_not_native_absence(
    monkeypatch: pytest.MonkeyPatch, value: Any
) -> None:
    extraction = saved_extraction()
    chunks = formal_chunks(extraction, "fixed_size")
    chunks[0].metadata[VISION_CHUNK_MAP_KEY] = value
    client, pool, doc = writer(monkeypatch)
    with pytest.raises(ValueError):
        await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert not writes(pool)


async def test_existing_layout_small_to_big_without_element_map_stays_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.chunking_small_to_big import build_parent_child_chunks

    extraction = StructuredExtraction(
        raw_text="技術本文",
        parser_artifacts={
            "layout_records": {
                "version": 1,
                "records": [
                    {
                        "id": "record-a",
                        "engine": "docling",
                        "page": 1,
                        "seq_no": 1,
                        "bbox": [0, 0, 100, 20],
                        "coord_system": "image_top_left",
                        "page_width": 100,
                        "page_height": 100,
                        "category": "Text",
                        "text": "技術本文",
                        "raw_type": "text",
                        "raw": {},
                    }
                ],
            }
        },
    )
    chunks = build_parent_child_chunks(extraction, source_name="synthetic.pdf")
    assert chunks
    client, pool, doc = writer(monkeypatch)
    expected = client._chunk_insert_rows(doc.id, doc, chunks, [None] * len(chunks))
    await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert pool.connection.many_calls[0].rows == expected


@pytest.mark.parametrize("strategy", CHUNKING_STRATEGIES)
async def test_native_table_and_image_supplement_remain_separate(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
) -> None:
    native = "<table><tr><td>原生の値7。</td></tr></table>"
    body = "表内画像の補足です。"
    visual = LayoutRecord(
        "table-record",
        "unstructured",
        1,
        1,
        [0, 0, 100, 100],
        "image_top_left",
        100,
        100,
        "Table",
        text=native,
        raw={
            "vision_status": "succeeded",
            "table_vision_text": body,
            "vision_text_projection": VisionTextProjection(body, body + "\n索引語").dumps(),
        },
    )
    extraction = _write_back_adapter(
        StructuredExtraction(
            raw_text=native,
            elements=[
                DocumentElement(
                    kind="table",
                    element_id="table-element",
                    text=native,
                    source_parser="unstructured",
                )
            ],
        ),
        _Layout(records=[visual], targets=[_Target(visual, element_index=0)]),
    )
    chunks = formal_chunks(extraction, strategy)
    client, pool, doc = writer(monkeypatch)
    expected = client._chunk_insert_rows(doc.id, doc, chunks, [None] * len(chunks))
    await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert pool.connection.many_calls[0].rows == expected


async def test_existing_structure_parent_image_map_stays_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag.chunking_small_to_big import build_parent_child_chunks

    extraction = saved_extraction()
    chunks = build_parent_child_chunks(extraction, source_name="synthetic.pdf")
    assert chunks
    client, pool, doc = writer(monkeypatch)
    expected = client._chunk_insert_rows(doc.id, doc, chunks, [None] * len(chunks))
    await client._save_index_with_oracle(doc.id, extraction, chunks, [None] * len(chunks))
    assert pool.connection.many_calls[0].rows == expected
