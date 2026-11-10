"""文書全体の要約（#4793）: 取込の後に 1 文書 1 回の LLM で作り、要約の chunk を索引に入れる。

合成の文書（架空の手順書）と決まった応答の LLM・Embedding・保存の代役で、成功・失敗・入力の
切り詰めの 3 通りと、保存済みの要約の使い直し、要約の chunk が回答の根拠にそのまま渡ることを
確かめる（DB と OCI は呼ばない。状態はテストごとに作る）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from rag_engine.retrieval.source_context import source_context

from app.clients import ai_calls
from app.clients.document_summary_store import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    DocumentSummaryRow,
    SummaryTarget,
)
from app.config import Settings
from app.mcp import tools as mcp_tools
from app.rag.answer_engine import AnswerEngine
from app.rag.answer_prompts import EDITABLE_PROMPT_KEYS, PROMPT_STAGES, default_prompt
from app.rag.chunking import Chunk
from app.rag.derived_evidence import (
    derived_kind,
    derived_source_chunks,
    is_derived_chunk,
    keeps_own_evidence,
)
from app.rag.document_summary import (
    DOCUMENT_SUMMARY_PROMPT_KEY,
    FAILURE_INTERRUPTED,
    FAILURE_INVALID_OUTPUT,
    FAILURE_MODEL,
    FAILURE_NO_CONTENT,
    FAILURE_TIMEOUT,
    SUMMARY_MAX_CHARS,
    SummarySource,
    SummaryUnavailable,
    build_summary_input,
    content_summary_view,
    generate_document_summary,
    parse_summary_output,
    summary_chunk,
    summary_source,
)
from app.rag.ingestion_prompts import DOCUMENT_SUMMARY_PROMPT
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm

DOCUMENT_ID = "doc-1"
TITLE = "sample-procedure.pdf"
SUMMARY = "架空の装置 A の点検の手順書です。月に 1 回の点検の項目と記録の方法を定めています。"
TOPICS = ["架空の装置 A", "点検の手順", "記録の方法"]
ANSWER = json.dumps({"summary": SUMMARY, "topics": TOPICS}, ensure_ascii=False)


def _extraction(paragraphs: int = 3) -> dict[str, Any]:
    """合成の抽出結果（章が 2 つ、章ごとに段落。要約の派生の要素を 1 つ含む）。"""
    elements: list[dict[str, Any]] = []
    for chapter in (1, 2):
        path = [f"第 {chapter} 章 合成の章 {chapter}"]
        elements.append({"kind": "title", "text": path[0], "section_path": path})
        for index in range(paragraphs):
            elements.append(
                {
                    "kind": "text",
                    "text": f"章 {chapter} の段落 {index} の本文です。架空の装置 A を点検します。",
                    "section_path": [*path, f"{chapter}.{index % 2 + 1} 合成の節"],
                }
            )
    elements.append(
        {
            "kind": "text",
            "text": "章節の要約（派生の要素）",
            "content_kind": "section_summary",
            "metadata": {"nav_summary": True},
            "section_path": ["第 1 章 合成の章 1"],
        }
    )
    navigation = [
        {"section_id": "s1", "title": "第 1 章 合成の章 1", "depth": 1},
        {"section_id": "s1-1", "title": "1.1 合成の節", "depth": 2},
        {"section_id": "s1-2", "title": "1.2 合成の節", "depth": 2},
        {"section_id": "s2", "title": "第 2 章 合成の章 2", "depth": 1},
        {"section_id": "s2-1", "title": "2.1 合成の節", "depth": 2},
    ]
    return {
        "raw_text": "",
        "document_type": "手順書",
        "elements": elements,
        "navigation": navigation,
    }


class FakeStore:
    """``DocumentSummaryStore`` の代役（1 文書の行と、保存した chunk を持つ）。"""

    def __init__(self, targets: Sequence[SummaryTarget] | None = None) -> None:
        self.row: DocumentSummaryRow | None = None
        self.targets_value = list(
            targets
            if targets is not None
            else [SummaryTarget(chunk_set_id="cs-1", recipe_id="r-1", extraction_recipe_id="ex-1")]
        )
        self.generating_marks = 0
        self.saved: list[dict[str, Any]] = []
        self.failures: list[tuple[str | None, str]] = []
        self.fail_save = False

    async def get(self, document_id: str) -> DocumentSummaryRow | None:
        return self.row

    async def targets(self, document_id: str) -> list[SummaryTarget]:
        return list(self.targets_value)

    async def mark_generating(self, document_id: str) -> None:
        self.generating_marks += 1

    async def save_failed(self, document_id: str, *, chunk_set_id: str | None, code: str) -> None:
        self.failures.append((chunk_set_id, code))
        if self.row is not None and self.row.status == STATUS_READY:
            return
        self.row = _row(STATUS_FAILED, failure_code=code)

    async def save_ready(self, document_id: str, **kwargs: Any) -> int:
        if self.fail_save:
            raise RuntimeError("synthetic save failure")
        self.saved.append(kwargs)
        self.row = _row(
            STATUS_READY,
            summary_text=kwargs["summary_text"],
            topics=tuple(kwargs["topics"]),
            input_hash=kwargs["input_hash"],
            input_truncated=kwargs["input_truncated"],
            generated_at=datetime.now(UTC),
        )
        return len(kwargs["chunk_set_ids"])


def _row(status: str, **values: Any) -> DocumentSummaryRow:
    base: dict[str, Any] = {
        "document_id": DOCUMENT_ID,
        "status": status,
        "summary_text": None,
        "topics": (),
        "input_hash": None,
        "input_truncated": False,
        "input_chars": None,
        "source_chars": None,
        "chunk_set_id": "cs-1",
        "failure_code": None,
        "generating_since": None,
        "generated_at": None,
    }
    return DocumentSummaryRow(**{**base, **values})


class FakeDocuments:
    """``OracleClient`` の代役（文書の詳細と抽出結果だけ）。"""

    def __init__(self, extraction: Mapping[str, Any] | None) -> None:
        self.extraction = extraction
        self.extraction_calls: list[str] = []

    async def get_document(self, document_id: str) -> Any:
        return SimpleNamespace(id=document_id, file_name=TITLE)

    async def get_document_extraction_artifact(
        self, *, document_id: str, extraction_recipe_id: str
    ) -> dict[str, object] | None:
        self.extraction_calls.append(extraction_recipe_id)
        if self.extraction is None:
            return None
        return {"extraction_json": dict(self.extraction)}


class FakeTextModel:
    """決まった応答を返す LLM（呼ばれた引数を残す）。"""

    def __init__(self, *answers: str | Exception, delay: float = 0.0) -> None:
        self.answers = list(answers)
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, "context": context, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeEmbedder:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.inputs.extend(texts)
        return [[0.1] * 4 for _ in texts]


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """再試行の待ちを実時間で待たない。"""

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(ai_calls, "_sleep", no_wait)


async def _generate(
    store: FakeStore,
    model: FakeTextModel,
    *,
    extraction: Mapping[str, Any] | None = None,
    settings: Settings | None = None,
    embedder: FakeEmbedder | None = None,
    **options: Any,
) -> DocumentSummaryRow | None:
    return await generate_document_summary(
        DOCUMENT_ID,
        oracle=FakeDocuments(_extraction() if extraction is None else extraction),
        settings=settings or Settings(),
        vlm=model,
        genai=embedder or FakeEmbedder(),
        store=store,  # type: ignore[arg-type]
        **options,
    )


# ---- 設定とプロンプト ----


def test_document_summary_is_enabled_by_default() -> None:
    settings = Settings()

    assert settings.rag_document_summary_enabled is True
    assert settings.rag_document_summary_max_input_chars == 16000
    assert settings.rag_document_summary_timeout_seconds == 120.0


def test_prompt_is_step_formatted_and_registered_for_editing() -> None:
    assert DOCUMENT_SUMMARY_PROMPT.startswith("## Step 1: ")
    assert "## Step 2: " in DOCUMENT_SUMMARY_PROMPT
    assert "## Step 3: " in DOCUMENT_SUMMARY_PROMPT
    # 入力の印（truncated・見出しの一覧）の読み方と、文書の言語で書く指示がある。
    for word in ("document_body", "truncated", "document_outline", "同じ言語"):
        assert word in DOCUMENT_SUMMARY_PROMPT
    assert DOCUMENT_SUMMARY_PROMPT_KEY in EDITABLE_PROMPT_KEYS
    assert default_prompt(DOCUMENT_SUMMARY_PROMPT_KEY) == DOCUMENT_SUMMARY_PROMPT
    stage = next(stage for stage in PROMPT_STAGES if stage.id == DOCUMENT_SUMMARY_PROMPT_KEY)
    assert stage.phase == "ingestion"


# ---- 入力の組み立て ----


def test_summary_source_excludes_derived_elements_and_reads_headings() -> None:
    source = summary_source(StructuredExtraction.model_validate(_extraction()))

    assert "章 1 の段落 0 の本文です。" in source.body
    assert "章節の要約（派生の要素）" not in source.body
    assert source.headings[0] == (1, "第 1 章 合成の章 1")
    assert (2, "1.1 合成の節") in source.headings


def test_short_document_passes_the_whole_body_without_outline() -> None:
    source = SummarySource(body="短い本文です。\n2 行目です。", headings=((1, "第 1 章"),))

    built = build_summary_input(source, max_chars=2000)

    assert built.truncated is False
    assert (
        built.text
        == '<document_body truncated="false">\n短い本文です。\n2 行目です。\n</document_body>'
    )
    assert (built.body_chars, built.source_chars, built.heading_count) == (15, 15, 0)


def test_long_document_is_cut_at_a_line_and_gets_the_outline() -> None:
    lines = [f"行 {index:04d} の本文です。架空の装置 A の記録。" for index in range(400)]
    body = "\n".join(lines)
    headings = ((1, "第 1 章 概要"), (2, "1.1 目的"), (1, "第 2 章 手順"), (2, "2.1 準備"))
    max_chars = 2000

    built = build_summary_input(SummarySource(body=body, headings=headings), max_chars=max_chars)

    assert built.truncated is True
    assert built.source_chars == len(body)
    assert f'truncated="true" shown_chars="{built.body_chars}" total_chars="{len(body)}"' in (
        built.text
    )
    shown = built.text.split("\n</document_body>")[0].split("\n", 1)[1]
    # 本文は先頭からで、行の途中で切らない。本文と見出しの一覧の合計は予算に収まる。
    assert body.startswith(shown)
    assert shown.split("\n")[-1] in lines
    outline = built.text.split("<document_outline>\n")[1].split("\n</document_outline>")[0]
    assert outline == "第 1 章 概要\n  1.1 目的\n第 2 章 手順\n  2.1 準備"
    assert built.heading_count == 4
    assert built.body_chars + len(outline) <= max_chars


def test_outline_drops_deep_headings_first_when_over_budget() -> None:
    headings = tuple(
        item
        for chapter in range(1, 21)
        for item in ((1, f"第 {chapter} 章 合成"), (2, f"{chapter}.1 とても長い合成の節の名前"))
    )
    body = "本文。\n" * 2000

    built = build_summary_input(SummarySource(body=body, headings=headings), max_chars=2000)

    outline = built.text.split("<document_outline>\n")[1].split("\n</document_outline>")[0]
    # 予算（入力の 1/4）に収まらない深い階層を落とし、章だけを残す。
    assert len(outline) <= 500
    assert outline.split("\n") == [f"第 {chapter} 章 合成" for chapter in range(1, 21)]
    assert built.heading_count == 20


# ---- 構造化出力の検証 ----


def test_output_is_validated_and_topics_are_normalized() -> None:
    raw = json.dumps(
        {"summary": f"  {SUMMARY} ", "topics": ["点検", " 点検 ", "", *map(str, range(20))]},
        ensure_ascii=False,
    )

    output = parse_summary_output(f"```json\n{raw}\n```")

    assert output.summary == SUMMARY
    assert output.topics[:2] == ["点検", "0"]
    assert len(output.topics) == 10


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"summary": "x" * (SUMMARY_MAX_CHARS + 1), "topics": []}),
        json.dumps({"summary": "要約", "topics": [], "extra": 1}),
        json.dumps({"topics": []}),
    ],
    ids=["too-long", "extra-key", "missing-summary"],
)
def test_output_of_a_wrong_shape_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_summary_output(raw)


def test_output_without_json_is_rejected() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_summary_output("要約できませんでした。")


# ---- 生成（成功・失敗・切り詰め） ----


async def test_success_calls_the_model_once_and_indexes_one_summary_chunk() -> None:
    store = FakeStore(
        [
            SummaryTarget(chunk_set_id="cs-1", recipe_id="r-1", extraction_recipe_id="ex-1"),
            SummaryTarget(chunk_set_id="cs-2", recipe_id="r-2", extraction_recipe_id="ex-2"),
        ]
    )
    model = FakeTextModel(ANSWER)
    embedder = FakeEmbedder()
    documents = FakeDocuments(_extraction())

    row = await generate_document_summary(
        DOCUMENT_ID,
        oracle=documents,
        settings=Settings(),
        vlm=model,
        genai=embedder,
        store=store,  # type: ignore[arg-type]
        recipe_id="r-2",
    )

    assert row is not None and row.status == STATUS_READY
    assert (row.summary_text, row.topics) == (SUMMARY, tuple(TOPICS))
    # LLM は 1 回。指示はシステムの文、文書の内容は未信頼のデータの側（context）で渡す。
    [call] = model.calls
    assert call["prompt"] == ""
    assert call["system_prompt"] == DOCUMENT_SUMMARY_PROMPT
    assert call["context"].startswith('<document_body truncated="false">')
    assert "章 2 の段落 0 の本文です。" in call["context"]
    assert TITLE not in call["context"]
    assert call["response_schema"]["properties"].keys() == {"summary", "topics"}
    # 元にする抽出結果は job の処理レシピのもの。
    assert documents.extraction_calls == ["ex-2"]
    # 要約の chunk は 1 つ。文書の有効な chunk_set のすべてに同じ chunk を入れる。
    [saved] = store.saved
    assert saved["chunk_set_ids"] == ["cs-1", "cs-2"]
    chunk: Chunk = saved["chunk"]
    assert chunk.text == f"{SUMMARY}\n{' / '.join(TOPICS)}"
    assert chunk.metadata["content_kind"] == "document_summary"
    assert chunk.metadata["document_summary"] is True
    assert chunk.metadata["context_header"] == TITLE
    assert saved["input_truncated"] is False
    # 埋め込みは 1 回 1 件で、文書名の文脈ヘッダを前に付ける。
    assert embedder.inputs == [f"{TITLE}\n{chunk.text}"]
    assert (store.generating_marks, store.failures) == (1, [])


async def test_same_input_reuses_the_saved_summary_without_the_model() -> None:
    store = FakeStore()
    model = FakeTextModel(ANSWER)

    await _generate(store, model)
    await _generate(store, model)

    # 2 回目（索引だけの作り直し・同じ解析の別の処理レシピ）は LLM を呼ばず、chunk だけ入れ直す。
    assert len(model.calls) == 1
    assert len(store.saved) == 2
    assert store.saved[1]["summary_text"] == SUMMARY

    await _generate(store, model, force=True)

    assert len(model.calls) == 2


async def test_changed_prompt_or_body_calls_the_model_again() -> None:
    store = FakeStore()
    model = FakeTextModel(ANSWER)

    async def edited_prompt() -> str:
        return DOCUMENT_SUMMARY_PROMPT + "- 箇条書きにしないでください。\n"

    await _generate(store, model)
    await _generate(store, model, load_prompt=edited_prompt)
    await _generate(store, model, load_prompt=edited_prompt, extraction=_extraction(paragraphs=4))

    assert len(model.calls) == 3
    assert model.calls[1]["system_prompt"].endswith("- 箇条書きにしないでください。\n")


async def test_model_failure_records_the_reason_and_does_not_index() -> None:
    store = FakeStore()
    model = FakeTextModel(RuntimeError("synthetic model failure"))

    with pytest.raises(SummaryUnavailable) as raised:
        await _generate(store, model)

    assert raised.value.code == FAILURE_MODEL
    assert store.failures == [("cs-1", FAILURE_MODEL)]
    assert store.saved == []
    assert store.row is not None and store.row.status == STATUS_FAILED


async def test_invalid_output_is_retried_then_recorded() -> None:
    store = FakeStore()
    model = FakeTextModel("要約できませんでした。")

    with pytest.raises(SummaryUnavailable) as raised:
        await _generate(store, model)

    assert raised.value.code == FAILURE_INVALID_OUTPUT
    # 形の誤りは回数を絞ってやり直す（共通の実行器の規則）。
    assert len(model.calls) == 3
    assert store.failures == [("cs-1", FAILURE_INVALID_OUTPUT)]


async def test_invalid_output_recovers_on_the_retry() -> None:
    store = FakeStore()
    model = FakeTextModel("{broken", ANSWER)

    row = await _generate(store, model)

    assert row is not None and row.status == STATUS_READY
    assert len(model.calls) == 2


async def test_timeout_records_the_reason() -> None:
    store = FakeStore()
    model = FakeTextModel(ANSWER, delay=5.0)
    settings = Settings().model_copy(update={"rag_document_summary_timeout_seconds": 0.05})

    with pytest.raises(SummaryUnavailable) as raised:
        await _generate(store, model, settings=settings)

    assert raised.value.code == FAILURE_TIMEOUT
    assert store.failures == [("cs-1", FAILURE_TIMEOUT)]
    assert store.saved == []


async def test_empty_summary_is_recorded_as_no_content() -> None:
    store = FakeStore()
    model = FakeTextModel(json.dumps({"summary": "", "topics": []}))

    with pytest.raises(SummaryUnavailable) as raised:
        await _generate(store, model)

    assert raised.value.code == FAILURE_NO_CONTENT
    assert len(model.calls) == 1


async def test_failed_regeneration_keeps_the_summary_of_the_same_version() -> None:
    store = FakeStore()

    await _generate(store, FakeTextModel(ANSWER))
    with pytest.raises(SummaryUnavailable):
        await _generate(store, FakeTextModel(RuntimeError("synthetic")), force=True)

    assert store.row is not None
    assert (store.row.status, store.row.summary_text) == (STATUS_READY, SUMMARY)


async def test_truncated_input_marks_the_cut_and_adds_the_outline() -> None:
    store = FakeStore()
    model = FakeTextModel(ANSWER)
    settings = Settings().model_copy(update={"rag_document_summary_max_input_chars": 2000})

    row = await _generate(store, model, settings=settings, extraction=_extraction(paragraphs=80))

    assert row is not None and row.input_truncated is True
    context = model.calls[0]["context"]
    assert context.startswith('<document_body truncated="true" shown_chars="')
    assert "<document_outline>\n第 1 章 合成の章 1\n  1.1 合成の節" in context
    assert "第 2 章 合成の章 2" in context.split("<document_outline>")[1]
    # 本文は先頭から。予算の外の章 2 の本文は渡さない。
    assert "章 1 の段落 0 の本文です。" in context
    assert "章 2 の段落 79 の本文です。" not in context
    assert len(context) < 2200
    [saved] = store.saved
    assert saved["input_truncated"] is True
    assert saved["source_chars"] > saved["input_chars"]
    assert saved["chunk"].metadata["summary_input_truncated"] is True


async def test_disabled_or_unindexed_document_does_nothing() -> None:
    model = FakeTextModel(ANSWER)
    disabled = Settings().model_copy(update={"rag_document_summary_enabled": False})

    assert await _generate(FakeStore(), model, settings=disabled) is None
    assert await _generate(FakeStore(targets=[]), model) is None
    assert model.calls == []


async def test_index_failure_is_recorded() -> None:
    store = FakeStore()
    store.fail_save = True

    with pytest.raises(SummaryUnavailable) as raised:
        await _generate(store, FakeTextModel(ANSWER))

    assert raised.value.code == "index_failed"
    assert store.failures == [("cs-1", "index_failed")]


# ---- 画面・API に出す状態 ----


def test_view_shows_ready_summary_and_regeneration() -> None:
    now = datetime(2026, 11, 10, tzinfo=UTC)
    row = _row(
        STATUS_READY,
        summary_text=SUMMARY,
        topics=tuple(TOPICS),
        input_truncated=True,
        generated_at=now,
        generating_since=now - timedelta(seconds=5),
    )

    view = content_summary_view(row, Settings(), now=now)

    assert view is not None
    assert (view.status, view.generating, view.text) == ("ready", True, SUMMARY)
    assert (view.topics, view.input_truncated, view.generated_at) == (TOPICS, True, now)


def test_view_treats_a_stale_pending_row_as_interrupted() -> None:
    now = datetime(2026, 11, 10, tzinfo=UTC)
    fresh = _row(STATUS_PENDING, generating_since=now - timedelta(seconds=10))
    stale = _row(STATUS_PENDING, generating_since=now - timedelta(seconds=1000))

    fresh_view = content_summary_view(fresh, Settings(), now=now)
    stale_view = content_summary_view(stale, Settings(), now=now)

    assert fresh_view is not None and (fresh_view.status, fresh_view.generating) == (
        "pending",
        True,
    )
    assert stale_view is not None
    assert (stale_view.status, stale_view.generating, stale_view.failure_code) == (
        "failed",
        False,
        FAILURE_INTERRUPTED,
    )
    assert content_summary_view(None, Settings()) is None


def test_view_hides_text_of_a_failed_summary() -> None:
    view = content_summary_view(_row(STATUS_FAILED, failure_code=FAILURE_TIMEOUT), Settings())

    assert view is not None
    assert (view.status, view.text, view.topics, view.failure_code) == (
        "failed",
        None,
        [],
        FAILURE_TIMEOUT,
    )


# ---- 検索で当たった要約の chunk の扱い ----


def _summary_hit(text: str = SUMMARY) -> RetrievedChunk:
    """索引に保存した要約の chunk を、検索の当たりの形にする。"""
    chunk = summary_chunk(
        text, TOPICS, document_title=TITLE, context_header_enabled=True, truncated=False
    )
    return RetrievedChunk(
        document_id=DOCUMENT_ID,
        chunk_id=f"{DOCUMENT_ID}:cs-1:9",
        text=chunk.text,
        score=0.9,
        file_name=TITLE,
        metadata={**chunk.metadata, "chunk_set_id": "cs-1", "chunk_index": 9},
    )


async def test_summary_chunk_is_derived_but_keeps_its_own_evidence() -> None:
    hit = _summary_hit()

    assert is_derived_chunk(hit)
    assert derived_kind(hit.metadata) == "document_summary"
    assert keeps_own_evidence(hit)
    # 置き換える原文は読まない（読む SQL を呼ばない）。
    assert await derived_source_chunks(object(), hit, filters={}) == []  # type: ignore[arg-type]
    assert mcp_tools._evidence(hit).origin == "derived"


def test_source_context_marks_the_summary_for_the_model() -> None:
    record = SimpleNamespace(
        source=TITLE, text=SUMMARY, metadata={"content_kind": "document_summary"}
    )
    plain = SimpleNamespace(source=TITLE, text="本文", metadata={"content_kind": "text"})

    assert source_context(record)["evidence_kind"] == "document_summary"
    assert "evidence_kind" not in source_context(plain)


class SummaryHitOracle(FakeOracle):
    """検索が文書全体の要約の chunk だけを返す Oracle の代役。"""

    def __init__(self, hit: RetrievedChunk) -> None:
        super().__init__()
        self.hit = hit
        self.sibling_anchor_ids: list[str] = []

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        return [self.hit]

    async def context_group_siblings(
        self,
        anchors: list[RetrievedChunk],
        *,
        max_chunks_per_group: int,
        neighbor_child_count: int | None = None,
    ) -> list[RetrievedChunk]:
        self.sibling_anchor_ids.extend(anchor.chunk_id for anchor in anchors)
        return []

    async def retrieval_reference_chunks(self, *args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        raise AssertionError("文書全体の要約から原文を読まない")

    async def retrieval_chunk_set_chunks(self, *args: Any, **kwargs: Any) -> list[RetrievedChunk]:
        raise AssertionError("文書全体の要約から原文を読まない")


async def test_summary_hit_reaches_the_answer_as_summary_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """要約の chunk は原文に置き換えず、要約と分かる印を付けて回答の根拠に渡す。"""
    import rag_engine.adapters.oci as engine_oci

    # ``_fake_llm`` はこの文を含む根拠を引用する。
    body = "受注番号を入力し、登録ボタンを押します。"
    hit = _summary_hit(f"合成の受注の手順書です。{body}")
    oracle = SummaryHitOracle(hit)
    prompts: list[str] = []

    def llm(system: str, prompt: str, settings: Any, schema: type, **options: Any) -> Any:
        prompts.append(prompt)
        return _fake_llm(system, prompt, settings, schema, **options)

    monkeypatch.setattr(engine_oci, "parse_text_response", llm)
    engine = AnswerEngine(
        Settings(rag_neighbor_child_count=1),
        oracle=oracle,  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    outcome = await engine.run(
        SearchRequest(query="この文書は何の文書ですか。", filters={"knowledge_base_id": "kb-1"})
    )

    assert [chunk.chunk_id for chunk in outcome.citations] == [hit.chunk_id]
    assert outcome.citations[0].metadata["content_kind"] == "document_summary"
    assert body in outcome.answer
    # モデルへの出典情報に、要約であることの印がある。
    assert any('"evidence_kind": "document_summary"' in prompt for prompt in prompts)
    # 要約は文書の中の位置を持たないので、前後の文脈の起点にしない。
    assert oracle.sibling_anchor_ids == []
