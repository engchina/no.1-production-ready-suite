"""DocRAG 回答エンジン(rag_poc 回答フロー + backend 検索)の統合テスト(外部 I/O はスタブ)。"""

import json
import re
from typing import Any

import pytest
from docrag.models.llm import (
    CragRetrievalGradeOutput,
    GroundedAudit,
    GroundedDraft,
    QueryRoutingOutput,
)

from app.config import Settings
from app.rag.docrag_answer import DocragAnswerEngine
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest

PARENT_TEXT = "受注入力画面\n受注番号を入力し、登録ボタンを押します。"


def _chunk(chunk_id: str, text: str, seq: int) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        chunk_id=chunk_id,
        text=text,
        score=0.9,
        file_name="manual.pdf",
        metadata={
            "chunk_group_id": "chunk-docling-p000001",
            "docrag_parent_text": PARENT_TEXT,
            "docrag_search_text": f"Source file: manual.pdf\nChild text: {text}",
            "docrag_chunk_seq": seq,
            "page_start": 1,
            "page_end": 1,
            "docrag_metadata_json": json.dumps(
                {
                    "schema_version": 4,
                    "active": True,
                    "atomic": False,
                    "content_hash": f"h{seq}",
                    "classification": {},
                    "source_categories": ["Text"],
                    "section_path": ["受注入力画面"],
                    "section_path_sources": ["section_header"],
                }
            ),
            "docrag_source_record_refs_json": json.dumps(
                [{"record_id": f"docling-p1-{seq}", "page": 1, "seq_no": seq}]
            ),
        },
    )


class FakeOracle:
    def __init__(self) -> None:
        self.filters: list[dict[str, str]] = []
        self.chunks = [
            _chunk("doc-1:c1", "受注番号を入力し、登録ボタンを押します。", 1),
            _chunk("doc-1:c2", "登録後は受注一覧に表示されます。", 2),
        ]

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        self.filters.append(dict(filters or {}))
        return self.chunks[:1]

    async def context_group_siblings(
        self, anchors: list[RetrievedChunk], *, max_chunks_per_group: int
    ) -> list[RetrievedChunk]:
        return self.chunks[1:]


class FakeGenAi:
    async def embed(
        self, texts: list[str], *, input_type: str = "SEARCH_DOCUMENT"
    ) -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        return [(index, 1.0 - index * 0.1) for index in range(len(documents))][:top_n]


def _fake_llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
    if schema is QueryRoutingOutput:
        return QueryRoutingOutput(
            strategy="simple_retrieval", reason="単純な手順の質問", queries=[]
        )
    if schema is CragRetrievalGradeOutput:
        return CragRetrievalGradeOutput(
            reason="十分", sufficient=True, confidence=0.9, rewritten_query=""
        )
    if schema is GroundedDraft:
        body = "受注番号を入力し、登録ボタンを押します。"
        match = re.search(r"\[(E[0-9]+)\].*?" + re.escape(body), prompt, re.DOTALL)
        items = (
            [{"kind": "rule", "text": body, "evidence_id": match.group(1), "quote": body}]
            if match
            else []
        )
        return GroundedDraft.model_validate(
            {"summary": "手順", "items": items, "confidence": "high"}
        )
    if schema is GroundedAudit:
        return GroundedAudit.model_validate(
            {
                "goal_alignment": "aligned",
                "summary_supported": True,
                "reviews": [
                    {
                        "index": 0,
                        "support": "supported",
                        "applicability": "matched",
                        "reason": "原文",
                    }
                ],
            }
        )
    raise AssertionError(f"unexpected schema: {schema.__name__}")


async def test_docrag_engine_answers_with_backend_search_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docrag.adapters.oci as docrag_oci

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    oracle = FakeOracle()
    engine = DocragAnswerEngine(
        Settings(rag_answer_engine="docrag", rag_domain_keywords=["受注番号"]),
        oracle=oracle,  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )
    request = SearchRequest(query="受注の登録方法は？", filters={"knowledge_base_id": "kb-1"})

    outcome = await engine.run(request)

    assert "登録ボタン" in outcome.answer
    assert oracle.filters and oracle.filters[0]["knowledge_base_id"] == "kb-1"
    assert outcome.citations
    assert outcome.citations[0].chunk_id == "doc-1:c1"
    assert "docrag_role" in outcome.citations[0].metadata
    diagnostics = outcome.diagnostics
    assert diagnostics["execution_steps"]
    assert diagnostics["evidence_tree"]
    assert diagnostics["evidence_tree"][0]["children"]


async def test_pipeline_delegates_to_docrag_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import docrag.adapters.oci as docrag_oci

    from app.rag.pipeline import RagPipeline

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    pipeline = RagPipeline(
        settings=Settings(rag_answer_engine="docrag"),
        oracle=FakeOracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    response = await pipeline.run(SearchRequest(query="受注の登録方法は？"))

    assert "登録ボタン" in response.answer
    assert response.citations[0].chunk_id == "doc-1:c1"
    assert response.diagnostics.retrieval_strategy == "docrag"
    assert response.diagnostics.docrag is not None
    assert response.diagnostics.docrag["evidence_tree"]


async def test_docrag_pipeline_records_search_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    import docrag.adapters.oci as docrag_oci

    import app.rag.pipeline as pipeline_module

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(
        pipeline_module, "record_rag_search_audit", lambda **kwargs: audits.append(kwargs)
    )
    pipeline = pipeline_module.RagPipeline(
        settings=Settings(rag_answer_engine="docrag"),
        oracle=FakeOracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    response = await pipeline.run(SearchRequest(query="受注の登録方法は？"))

    assert len(audits) == 1
    assert audits[0]["outcome"] == "success"
    assert audits[0]["citations"] == response.citations
    assert audits[0]["diagnostics"].docrag is not None


def test_business_view_overrides_text_search_tokenizer() -> None:
    from app.rag.business_view_config import BusinessViewConfig, resolve_business_view_settings
    from app.rag.kb_adapter_config import KnowledgeBaseQueryConfig

    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1"],
        query=KnowledgeBaseQueryConfig(text_search_tokenizer="sudachi"),
    )

    settings, _ = resolve_business_view_settings(Settings(), config)

    assert settings.rag_text_search_tokenizer == "sudachi"


def test_business_view_overrides_docrag_answer_options() -> None:
    from app.rag.business_view_config import BusinessViewConfig, resolve_business_view_settings
    from app.rag.kb_adapter_config import KnowledgeBaseQueryConfig

    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1"],
        query=KnowledgeBaseQueryConfig(
            docrag_query_strategy="hyde",
            docrag_answer_flow="standard_rag",
            docrag_neighbor_child_count=0,
            docrag_rerank_enabled=False,
        ),
    )

    settings, _ = resolve_business_view_settings(Settings(), config)

    assert settings.rag_docrag_query_strategy == "hyde"
    assert settings.rag_docrag_answer_flow == "standard_rag"
    assert settings.rag_docrag_neighbor_child_count == 0
    assert settings.rag_docrag_rerank_enabled is False


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        # 既定値は移植前(answer_question_result の既定)と同じ。
        (Settings(), ("auto_routing", "crag", 3, True)),
        (
            Settings(
                rag_docrag_query_strategy="rag_fusion",
                rag_docrag_answer_flow="standard_rag",
                rag_docrag_neighbor_child_count=7,
                rag_docrag_rerank_enabled=False,
            ),
            ("rag_fusion", "standard_rag", 7, False),
        ),
    ],
)
async def test_docrag_engine_passes_answer_options(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, expected: tuple[object, ...]
) -> None:
    import docrag.generation.answering as answering

    captured: dict[str, Any] = {}

    def fake_answer(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(answering, "answer_question_result", fake_answer)
    engine = DocragAnswerEngine(
        settings,
        oracle=FakeOracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="stop"):
        await engine.run(SearchRequest(query="受注の登録方法は？"))

    assert (
        captured["query_strategy"],
        captured["answer_flow"],
        captured["chunk_neighbor_count"],
        captured["rerank_enabled"],
    ) == expected


async def test_docrag_standard_flow_without_rerank_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docrag.adapters.oci as docrag_oci

    class NoRerankGenAi(FakeGenAi):
        async def rerank(
            self, query: str, documents: list[str], top_n: int
        ) -> list[tuple[int, float]]:
            raise AssertionError("rerank は無効")

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    engine = DocragAnswerEngine(
        Settings(
            rag_docrag_query_strategy="simple_retrieval",
            rag_docrag_answer_flow="standard_rag",
            rag_docrag_rerank_enabled=False,
        ),
        oracle=FakeOracle(),  # type: ignore[arg-type]
        genai=NoRerankGenAi(),  # type: ignore[arg-type]
    )

    outcome = await engine.run(SearchRequest(query="受注の登録方法は？"))

    assert "登録ボタン" in outcome.answer


def _pdf_with_figure() -> bytes:
    import fitz  # type: ignore[import-untyped]

    document = fitz.open()
    page = document.new_page(width=500, height=700)
    page.draw_rect(fitz.Rect(0, 0, 50, 35), color=(0, 0, 1), fill=(0, 0, 1))
    data: bytes = document.tobytes()
    document.close()
    return data


def _figure_oracle() -> FakeOracle:
    oracle = FakeOracle()
    figure = oracle.chunks[0]
    metadata = json.loads(str(figure.metadata["docrag_metadata_json"]))
    metadata["image_evidence"] = [
        {
            "image_id": "docling-p1-5",
            "record_id": "docling-p1-5",
            "page": 1,
            "bbox": [0, 0, 100, 70],
            "crop_path": "docling/visuals/missing.png",
            "embedding_modality": "image_caption_fallback",
        }
    ]
    oracle.chunks[0] = figure.model_copy(
        update={
            "metadata": {
                **figure.metadata,
                "page_width": 1000,
                "page_height": 1400,
                "docrag_metadata_json": json.dumps(metadata),
            }
        }
    )
    return oracle


async def test_docrag_attaches_cropped_evidence_images_when_vision_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docrag.adapters.oci as docrag_oci

    import app.rag.docrag_answer as engine_module

    loaded: list[str] = []

    async def fake_source(oracle: object, document_id: str) -> bytes:
        loaded.append(document_id)
        return _pdf_with_figure()

    images_seen: list[list[str]] = []

    def fake_multimodal(
        system: str, prompt: str, image_paths: list[Any], settings: Any, schema: type, **kw: Any
    ) -> Any:
        from pathlib import Path

        images_seen.append([str(path) for path in image_paths])
        assert all(Path(path).read_bytes().startswith(b"\x89PNG") for path in image_paths)
        return _fake_llm(system, prompt, settings, schema, **kw)

    monkeypatch.setattr(engine_module, "load_parsed_source", fake_source)
    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    monkeypatch.setattr(docrag_oci, "parse_multimodal_response", fake_multimodal)
    engine = DocragAnswerEngine(
        Settings(rag_answer_engine="docrag", rag_docrag_answer_vision_enabled=True),
        oracle=_figure_oracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    outcome = await engine.run(SearchRequest(query="受注登録画面のボタンは？"))

    assert loaded == ["doc-1"]
    assert images_seen and images_seen[0][0].endswith("crops/docling-p1-5.png")
    assert "登録ボタン" in outcome.answer


async def test_docrag_does_not_crop_when_vision_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import docrag.adapters.oci as docrag_oci

    import app.rag.docrag_answer as engine_module

    async def fail_source(oracle: object, document_id: str) -> bytes:
        raise AssertionError("vision disabled では原本を読まない")

    monkeypatch.setattr(engine_module, "load_parsed_source", fail_source)
    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    engine = DocragAnswerEngine(
        Settings(rag_answer_engine="docrag"),
        oracle=_figure_oracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    outcome = await engine.run(SearchRequest(query="受注登録画面のボタンは？"))

    assert "登録ボタン" in outcome.answer


def _nfkc(text: str) -> str:
    """rag_poc は質問を NFKC 正規化してから検索する(全角「？」→「?」)。"""
    import unicodedata

    return unicodedata.normalize("NFKC", text)


class QueryRecordingOracle(FakeOracle):
    def __init__(self) -> None:
        super().__init__()
        self.queries: list[str] = []

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        self.queries.append(query)
        return await super().hybrid_search(query, embedding, top_k, mode, filters)


class RewriteLlm:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        self.calls.append((prompt, context))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def _run_chat(
    monkeypatch: pytest.MonkeyPatch, llm: RewriteLlm, *, history: bool
) -> tuple[Any, QueryRecordingOracle]:
    import docrag.adapters.oci as docrag_oci

    from app.rag.pipeline import ChatTurn, RagPipeline

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    oracle = QueryRecordingOracle()
    pipeline = RagPipeline(
        settings=Settings(rag_answer_engine="docrag"),
        oracle=oracle,  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
    )
    turns = (
        [
            ChatTurn(role="USER", content="受注入力画面の使い方は？"),
            ChatTurn(role="ASSISTANT", content="受注番号を入力して登録します。"),
        ]
        if history
        else None
    )
    response = await pipeline.run(SearchRequest(query="それの登録方法は？"), history=turns)
    return response, oracle


async def test_docrag_chat_rewrites_question_from_history(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = RewriteLlm("受注入力画面での受注の登録方法は？")

    response, oracle = await _run_chat(monkeypatch, llm, history=True)

    assert llm.calls and "受注入力画面の使い方" in llm.calls[0][1]
    assert oracle.queries[0] == _nfkc("受注入力画面での受注の登録方法は？")
    assert response.diagnostics.docrag is not None
    assert response.diagnostics.docrag["original_question"] == "それの登録方法は？"
    assert response.diagnostics.docrag["rewritten_question"] == "受注入力画面での受注の登録方法は？"


async def test_docrag_single_question_does_not_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = RewriteLlm("使われないはず")

    response, oracle = await _run_chat(monkeypatch, llm, history=False)

    assert llm.calls == []
    assert oracle.queries[0] == _nfkc("それの登録方法は？")
    assert response.diagnostics.docrag is not None
    assert response.diagnostics.docrag["rewritten_question"] == ""


async def test_docrag_rewrite_failure_keeps_original_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, oracle = await _run_chat(monkeypatch, RewriteLlm(RuntimeError("down")), history=True)

    assert oracle.queries[0] == _nfkc("それの登録方法は？")
    assert "登録ボタン" in response.answer


class SavingOracle(FakeOracle):
    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.saved: list[dict[str, Any]] = []
        self.purged: list[int] = []
        self.fail = fail

    async def save_answer_record(self, record: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.saved.append(dict(record))

    async def purge_answer_records(self, retention_days: int) -> int:
        self.purged.append(retention_days)
        return 0


async def test_docrag_answer_is_saved_per_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    import docrag.adapters.oci as docrag_oci

    from app.rag.pipeline import RagPipeline

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    oracle = SavingOracle()
    pipeline = RagPipeline(
        settings=Settings(rag_answer_engine="docrag"),
        oracle=oracle,  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    response = await pipeline.run(
        SearchRequest(query="受注の登録方法は？", business_view_ids=["bv-1"]), trace_id="trace-1"
    )
    await pipeline.run(SearchRequest(query="受注の登録方法は？"), trace_id="trace-2", history=[])

    first, second = oracle.saved
    assert first["trace_id"] == "trace-1"
    assert first["business_view_id"] == "bv-1"
    assert first["surface"] == "search"
    assert first["answer"] == response.answer
    assert first["citations"][0]["chunk_id"] == "doc-1:c1"
    assert first["diagnostics"]["evidence_tree"]
    assert second["surface"] == "chat"
    assert oracle.purged == [90, 90]  # 既定の保持日数で保存のたびに期限切れを削除する


async def test_docrag_answer_purge_skipped_when_retention_unlimited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docrag.adapters.oci as docrag_oci

    from app.rag.pipeline import RagPipeline

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    oracle = SavingOracle()
    pipeline = RagPipeline(
        settings=Settings(rag_answer_engine="docrag", rag_answer_record_retention_days=0),
        oracle=oracle,  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    await pipeline.run(SearchRequest(query="受注の登録方法は？"), trace_id="trace-1")

    assert len(oracle.saved) == 1
    assert oracle.purged == []


async def test_docrag_answer_save_failure_still_returns_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import docrag.adapters.oci as docrag_oci

    from app.rag.pipeline import RagPipeline

    monkeypatch.setattr(docrag_oci, "parse_text_response", _fake_llm)
    pipeline = RagPipeline(
        settings=Settings(rag_answer_engine="docrag"),
        oracle=SavingOracle(fail=True),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    response = await pipeline.run(SearchRequest(query="受注の登録方法は？"))

    assert "登録ボタン" in response.answer
