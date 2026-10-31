"""再帰分割の番号参照を実 SQL で取得し、追加と既存候補の重複回避を確かめる。"""

from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.chunking import Chunk, chunk_extraction_with_strategy
from app.rag.cross_references import annotate_cross_references
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import SearchRequest
from tests.test_answer_engine import FakeGenAi
from tests.test_answer_scope_oracle import _indexed_document


@pytest.mark.usefixtures("oracle_db")
@pytest.mark.parametrize("other_document", [False, True], ids=["same-document", "other-document"])
async def test_recursive_numbered_reference_sql_adds_once(other_document: bool) -> None:
    oracle = OracleClient()
    token = uuid4().hex[:8]
    target_name = f"合成参照資料B{token}"
    ref = f"『{target_name}』の第5章" if other_document else "第5章"
    source_text = f"第1章 概要\n詳細は{ref}を参照。\n"
    target_text = "第5章 準備\n合成の準備には項目Aを使います。\n"

    def split(text: str) -> list[Chunk]:
        chunks = chunk_extraction_with_strategy(
            StructuredExtraction(raw_text=text),
            strategy="recursive_character",
            chunk_size=240,
            overlap=0,
            min_chars=0,
        )
        annotate_cross_references(chunks)
        return chunks

    kb = await oracle.create_knowledge_base(name=f"合成参照 KB {token}")
    source_id = await _indexed_document(
        oracle,
        file_name=f"synthetic-reference-{token}.pdf",
        chunks=split(source_text if other_document else source_text + target_text),
        knowledge_base_ids=[kb.id],
    )
    target_id = source_id
    if other_document:
        target_id = await _indexed_document(
            oracle,
            file_name=f"{target_name}.pdf",
            chunks=split(target_text),
            knowledge_base_ids=[kb.id],
        )
    scope = {"knowledge_base_id": kb.id}
    anchors = await oracle.retrieval_reference_chunks(
        scope,
        document_id=source_id,
        section_path="第1章 概要",
        limit=1,
    )
    assert len(anchors) == 1
    engine = AnswerEngine(Settings(), oracle=oracle, genai=FakeGenAi())  # type: ignore[arg-type]
    state = _SearchState(chunks={anchors[0].chunk_id: anchors[0]})
    request = SearchRequest(query="合成の準備は？", filters=scope)
    added = await engine._reference_expansion(
        request, state, anchors, existing={anchors[0].chunk_id}
    )
    targets = added[anchors[0].chunk_id]
    assert len(targets) == 1
    assert targets[0].document_id == target_id
    assert targets[0].metadata["section_path"] == "第5章 準備"
    assert (
        "added"
        in cast(list[dict[str, Any]], state.reference_sources[-1]["sources"])[0]["target_outcomes"]
    )
    state = _SearchState(chunks={c.chunk_id: c for c in [*anchors, *targets]})
    assert not await engine._reference_expansion(
        request,
        state,
        anchors,
        existing={c.chunk_id for c in [*anchors, *targets]},
    )
    assert (
        "present"
        in cast(list[dict[str, Any]], state.reference_sources[-1]["sources"])[0]["target_outcomes"]
    )
