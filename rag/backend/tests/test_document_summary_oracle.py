"""文書全体の要約（#4793）の保存と索引を、実 Oracle AI Database で確かめる。

合成の文書（架空の手順書）で、取込の job の後段が要約を 1 回だけ作ること、要約の chunk が
検索と同じ条件で 1 つだけ当たること、作り直し・索引だけの作り直し・失敗・文書の削除で
行と chunk が決めたとおりになることを確かめる。未到達なら oracle_db fixture が skip し、
作成行は後始末する（AI は決定論スタブ）。
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest

from app.clients.document_summary_store import (
    STATUS_FAILED,
    STATUS_READY,
    DocumentSummaryStore,
)
from app.clients.oracle import OracleClient
from app.config import get_settings
from app.main import app
from app.rag.chunking import Chunk
from app.rag.document_summary import (
    SummaryUnavailable,
    generate_document_summary,
    summary_chunk,
)
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import SearchMode
from tests._ingestion_jobs import run_ingestion_job_and_queued_followups
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
pytestmark = pytest.mark.usefixtures("oracle_db")

_EMBEDDING = [1.0] + [0.0] * 1535
_SUMMARY_FILTER = {"content_kind": "document_summary"}


@pytest.fixture
def summary_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rag_document_summary_enabled", True)


def _body_chunks() -> list[Chunk]:
    return [
        Chunk(
            text=f"架空の装置 A の点検の手順 {index}。",
            index=index,
            start_offset=0,
            end_offset=10,
            metadata={"content_kind": "text", "section_path": "手順書 > 第 1 章"},
        )
        for index in range(3)
    ]


async def _indexed_document(
    oracle: OracleClient, *, knowledge_base_id: str, token: str
) -> tuple[str, str, str]:
    """検索対象（INDEXED・active な chunk_set）の文書。(文書 ID, レシピ ID, chunk_set ID)。"""
    detail = await oracle.create_document(
        file_name=f"summary-{token}.pdf",
        object_storage_path=f"local://summary-{token}.pdf",
        content_type="application/pdf",
        knowledge_base_ids=[knowledge_base_id],
    )
    recipes = await oracle.list_document_recipes(detail.id)
    recipe_id = str(recipes[0]["recipe_id"])
    chunk_set_id = f"cs_summary_{uuid4().hex[:16]}"
    await oracle.upsert_chunk_set(
        chunk_set_id=chunk_set_id,
        document_id=detail.id,
        recipe_id=recipe_id,
        extraction_recipe_id=f"ex_{token}",
    )
    chunks = _body_chunks()
    await oracle.save_index(
        detail.id,
        StructuredExtraction(raw_text="本文", confidence=0.9, document_type="手順書"),
        chunks,
        [_EMBEDDING] * len(chunks),
        chunk_set_id=chunk_set_id,
    )
    await oracle.mark_chunk_set_indexed(
        chunk_set_id=chunk_set_id, chunk_count=len(chunks), vector_count=len(chunks)
    )
    await oracle.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=chunk_set_id,
        expected_base_chunk_set_id=None,
        materialized_revision=int(str(recipes[0].get("config_revision") or 1)),
    )
    await oracle.update_document_status(detail.id, FileStatus.INDEXED)
    return detail.id, recipe_id, chunk_set_id


class _SyntheticExtraction:
    """抽出結果だけを合成の値にし、ほかは実 Oracle に任せる。"""

    def __init__(self, oracle: OracleClient) -> None:
        self._oracle = oracle

    async def get_document(self, document_id: str) -> Any:
        return await self._oracle.get_document(document_id)

    async def get_document_extraction_artifact(self, **_: object) -> dict[str, object]:
        return {
            "extraction_json": {
                "raw_text": "架空の装置 A の点検の手順書。\n月に 1 回点検する。",
                "document_type": "手順書",
            }
        }


class _Model:
    def __init__(self, answer: str | Exception) -> None:
        self.answer = answer
        self.calls = 0

    async def generate(self, prompt: str, context: str, **_: Any) -> str:
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Embedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(_EMBEDDING) for _ in texts]


async def _summary_hits(oracle: OracleClient, knowledge_base_id: str) -> list[Any]:
    return await oracle.hybrid_search(
        "架空の装置 A",
        list(_EMBEDDING),
        10,
        SearchMode.HYBRID,
        {"knowledge_base_id": knowledge_base_id, **_SUMMARY_FILTER},
    )


async def test_summary_chunk_is_indexed_once_and_follows_the_document() -> None:
    oracle = OracleClient()
    store = DocumentSummaryStore(oracle)
    token = uuid4().hex[:8]
    kb = await oracle.create_knowledge_base(name=f"summary-{token}")
    document_id, recipe_id, chunk_set_id = await _indexed_document(
        oracle, knowledge_base_id=kb.id, token=token
    )
    model = _Model('{"summary": "架空の装置 A の点検の手順書です。", "topics": ["点検"]}')

    async def generate(**options: Any) -> Any:
        return await generate_document_summary(
            document_id,
            oracle=_SyntheticExtraction(oracle),
            settings=get_settings().model_copy(update={"rag_document_summary_enabled": True}),
            vlm=model,
            genai=_Embedder(),
            store=store,
            **options,
        )

    assert await store.get(document_id) is None
    [target] = await store.targets(document_id)
    assert (target.chunk_set_id, target.recipe_id) == (chunk_set_id, recipe_id)

    row = await generate()

    assert row is not None
    assert (row.status, row.summary_text, row.topics) == (
        STATUS_READY,
        "架空の装置 A の点検の手順書です。",
        ("点検",),
    )
    assert (row.chunk_set_id, row.input_truncated, row.generating_since) == (
        chunk_set_id,
        False,
        None,
    )
    # 要約の chunk は検索と同じ条件で 1 つだけ当たり、本文の chunk の後ろの番号に入る。
    [hit] = await _summary_hits(oracle, kb.id)
    assert hit.chunk_id == f"{document_id}:{chunk_set_id}:3"
    # 本文は要約の文だけ（話題の語は検索用の文にだけ入る）。
    assert hit.text == "架空の装置 A の点検の手順書です。"
    assert hit.metadata["engine_search_text"] == "架空の装置 A の点検の手順書です。\n点検"
    assert hit.metadata["content_kind"] == "document_summary"
    assert await oracle.count_chunk_set_chunks(chunk_set_id) == 4

    # 同じ入力の 2 回目は LLM を呼ばず、chunk は 1 つのまま。作り直しは LLM を呼び、入れ替える。
    await generate()
    assert model.calls == 1
    model.answer = '{"summary": "作り直した合成の要約です。", "topics": []}'
    await generate(force=True)
    assert model.calls == 2
    [hit] = await _summary_hits(oracle, kb.id)
    assert hit.text == "作り直した合成の要約です。"
    assert await oracle.count_chunk_set_chunks(chunk_set_id) == 4

    # 作り直しの失敗は、同じ版の要約と chunk を残す（最後の失敗の理由だけ残る）。
    model.answer = RuntimeError("synthetic model failure")
    with pytest.raises(SummaryUnavailable):
        await generate(force=True)
    kept = await store.get(document_id)
    assert kept is not None
    assert (kept.status, kept.summary_text, kept.failure_code) == (
        STATUS_READY,
        "作り直した合成の要約です。",
        "model_failed",
    )
    assert len(await _summary_hits(oracle, kb.id)) == 1

    # 索引だけの作り直しの複製には、要約の chunk を含めない（切替の後の後段が入れ直す）。
    candidate = f"cs_summary_{uuid4().hex[:16]}"
    await oracle.upsert_chunk_set(
        chunk_set_id=candidate, document_id=document_id, recipe_id=recipe_id, status="CHUNKED"
    )
    await oracle.clone_recipe_chunk_set_chunks(
        recipe_id=recipe_id, source_chunk_set_id=chunk_set_id, target_chunk_set_id=candidate
    )
    assert await oracle.count_chunk_set_chunks(candidate) == 3

    # 別の版の失敗（再取込の後の失敗）は、前の版の要約の文を残さない。
    await store.save_failed(document_id, chunk_set_id=candidate, code="timeout")
    failed = await store.get(document_id)
    assert failed is not None
    assert (failed.status, failed.summary_text, failed.failure_code) == (
        STATUS_FAILED,
        None,
        "timeout",
    )

    # 文書を消すと、要約の行も消える。
    await oracle.delete_document(document_id)
    assert await store.get(document_id) is None


async def test_mark_generating_keeps_the_previous_summary() -> None:
    oracle = OracleClient()
    store = DocumentSummaryStore(oracle)
    token = uuid4().hex[:8]
    kb = await oracle.create_knowledge_base(name=f"summary-mark-{token}")
    document_id, _recipe_id, chunk_set_id = await _indexed_document(
        oracle, knowledge_base_id=kb.id, token=token
    )

    await store.mark_generating(document_id)
    pending = await store.get(document_id)
    assert pending is not None
    assert (pending.status, pending.summary_text) == ("PENDING", None)
    assert pending.generating_since is not None

    chunk = summary_chunk(
        "合成の要約です。",
        [],
        document_title="summary.pdf",
        context_header_enabled=True,
        truncated=True,
    )
    inserted = await store.save_ready(
        document_id,
        chunk_set_ids=[chunk_set_id, "cs_not_active"],
        summary_text="合成の要約です。",
        topics=[],
        input_hash="h1",
        input_truncated=True,
        input_chars=10,
        source_chars=100,
        chunk=chunk,
        embedding=_EMBEDDING,
    )
    # 有効でない chunk_set には入れない。
    assert inserted == 1
    await store.mark_generating(document_id)
    row = await store.get(document_id)
    assert row is not None
    assert (row.status, row.summary_text, row.input_truncated) == (
        STATUS_READY,
        "合成の要約です。",
        True,
    )
    assert (row.input_chars, row.source_chars) == (10, 100)
    assert row.generating_since is not None


@pytest.mark.usefixtures("summary_enabled")
def test_ingestion_job_creates_the_summary_after_indexing() -> None:
    """取込の job の後段が要約を作り、API と検索に出る（LLM は決定論スタブ）。"""
    token = uuid4().hex[:8]
    sample = (
        f"合成の手順書 {token}\n架空の装置 A は月に 1 回点検します。\n点検の結果は記録します。"
    ).encode()
    upload = client.post(
        "/api/documents/upload",
        files={"file": (f"summary-flow-{token}.txt", sample, "text/plain")},
    )
    assert upload.status_code == 200
    document_id = upload.json()["data"]["id"]
    assert client.get(f"/api/documents/{document_id}/summary").json()["data"] == {
        "enabled": True,
        "summary": None,
    }

    job = cast(
        dict[str, Any], client.post(f"/api/documents/{document_id}/ingestion-jobs").json()["data"]
    )
    run_ingestion_job_and_queued_followups(client, cast(str, job["id"]), document_id)

    detail = client.get(f"/api/documents/{document_id}").json()["data"]
    assert detail["status"] == "INDEXED"
    state = client.get(f"/api/documents/{document_id}/summary").json()["data"]
    summary = state["summary"]
    assert (summary["status"], summary["generating"], summary["failure_code"]) == (
        "ready",
        False,
        None,
    )
    assert summary["text"].startswith(f"合成の要約: 合成の手順書 {token}")
    assert summary["topics"] == ["合成の話題"]

    rows = asyncio.run(
        OracleClient().hybrid_search(
            "合成の手順書",
            list(_EMBEDDING),
            10,
            SearchMode.HYBRID,
            {"document_id": document_id, **_SUMMARY_FILTER},
        )
    )
    assert len(rows) == 1
    assert rows[0].text == summary["text"]

    # 作り直しは同じ API の POST。要約の chunk は 1 つのまま。
    again = client.post(f"/api/documents/{document_id}/summary")
    assert again.status_code == 200
    assert again.json()["data"]["summary"]["status"] == "ready"
    rows = asyncio.run(
        OracleClient().hybrid_search(
            "合成の手順書",
            list(_EMBEDDING),
            10,
            SearchMode.HYBRID,
            {"document_id": document_id, **_SUMMARY_FILTER},
        )
    )
    assert len(rows) == 1


def test_regeneration_is_rejected_while_the_feature_is_disabled() -> None:
    token = uuid4().hex[:8]
    upload = client.post(
        "/api/documents/upload",
        files={"file": (f"summary-off-{token}.txt", "合成の本文".encode(), "text/plain")},
    )
    document_id = upload.json()["data"]["id"]

    response = client.post(f"/api/documents/{document_id}/summary")

    assert response.status_code == 409
    assert client.get(f"/api/documents/{document_id}/summary").json()["data"]["enabled"] is False
    assert client.get("/api/documents/no-such-document/summary").status_code == 404
