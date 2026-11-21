"""保存済みの参照と段落を、許可された文書の現在版に対応づける。"""

from unittest.mock import AsyncMock

import pytest
from rag_parser_core.extraction import DocumentElement, StructuredExtraction

from app.rag.cross_references import REFERENCE_TARGETS_KEY, ReferenceTarget
from app.rag.wiki_models import fingerprint
from app.rag.wiki_source_context import bind_paragraphs
from app.rag.wiki_source_inputs import source_inputs
from tests.test_wiki_compiler import units


def test_plain_source_paragraphs_come_from_the_saved_text_before_chunking() -> None:
    body = "最初の文。折り返す\n次の文。\n\n別の段落。"
    start, end = body.index("折り"), body.index("す") + 1
    part = body[start:end]
    unit = units()[0].model_copy(update={"text": part, "source_hash": fingerprint(part)})
    extraction = StructuredExtraction(raw_text=body, elements=[DocumentElement(text=body)])
    context = bind_paragraphs([unit], extraction, {unit.source_chunk_ids[0]: (start, end)})[
        unit.unit_id
    ]
    context.verify(unit)
    assert context.text == "最初の文。折り返す\n次の文。\n\n"
    assert len(context.paragraphs) == 1 and context.paragraphs[0].sentence_ends == (
        len("最初の文。"),
    )


@pytest.mark.asyncio
async def test_stored_reference_requires_its_literal_and_the_current_document_version() -> None:
    sample = units()[:3]
    body = ["架空の記録は第2章を参照する。", "架空の後続手順。", "架空の付記。"]
    path = [["資料 A", f"第{i + 1}章"] for i in range(len(sample))]
    text = "\n\n".join(body)
    elements = [
        DocumentElement(element_id=f"e{i}", text=part, section_path=path[i])
        for i, part in enumerate(body)
    ]
    parsed = StructuredExtraction(
        raw_text=text, elements=elements, parser_artifacts={"xml_tree_json_format_version": 1}
    )
    active = [
        u.model_copy(
            update={
                "text": part,
                "source_hash": fingerprint(part),
                "section_path": [],
                "source_chunk_hashes": {u.source_chunk_ids[0]: fingerprint(part)},
            }
        )
        for u, part in zip(sample, body, strict=True)
    ]
    data: dict[str, dict[str, object]] = {}
    for u, part in zip(active, body, strict=True):
        a = text.index(part)
        data[u.source_chunk_ids[0]] = {"start_offset": a, "end_offset": a + len(part)}
    data[active[0].source_chunk_ids[0]][REFERENCE_TARGETS_KEY] = [
        ReferenceTarget("第2章", "chapter", "2").to_dict(),
        ReferenceTarget("第3章", "chapter", "3").to_dict(),
    ]
    oracle = AsyncMock()
    did, version = active[0].document_id, active[0].chunk_set_id
    oracle.chunk_set_extraction_recipe_ids.return_value = {version: "parser-a"}
    record = {
        "document_id": did,
        "extraction_recipe_id": "parser-a",
        "extraction_json": parsed.model_dump(),
    }
    oracle.get_document_extraction_artifact.return_value = record
    authorize = AsyncMock()
    bound, related = await source_inputs(oracle, active, data, authorize)
    assert len(bound) == 3 and related == {active[0].unit_id: [active[1].unit_id]}
    for u in active:
        bound[u.unit_id].verify(u)
    assert authorize.await_count == 3
    oracle.get_document_extraction_artifact.assert_awaited_once_with(
        document_id=did, extraction_recipe_id="parser-a"
    )
    oracle.get_document_extraction_artifact.return_value = {**record, "extraction_recipe_id": "old"}
    assert await source_inputs(oracle, active, data, authorize) == ({}, {})
    oracle.reset_mock()
    with pytest.raises(PermissionError):
        await source_inputs(oracle, active, data, AsyncMock(side_effect=PermissionError))
    oracle.chunk_set_extraction_recipe_ids.assert_not_awaited()
