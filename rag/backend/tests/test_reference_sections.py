"""文書全体への参照の参照先を、目次から選んだ章節で読む(#5263)。

文書名・ファイル名だけの参照は、参照先の文書の目次を回答のモデルに 1 回渡して章節を選ばせ、
その章節を読む。選べないときは文書全体を読む方式へ退避し、退避で足した chunk は章節の拡張の
起点にしない。番号つきの参照は今どおり番号で定位する。
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from app.clients.section_map_store import ReadableChunkSet, SectionChunkRow
from app.rag import section_map_service
from app.rag.answer_engine import _SearchState
from app.rag.cross_references import REFERENCE_SECTION_SEED_KEY, ReferenceTarget
from app.rag.reference_sections import (
    FALLBACK_NO_OUTLINE,
    FALLBACK_NO_SELECTION,
    FALLBACK_SECTIONS_EMPTY,
    FALLBACK_SELECTION_ERROR,
    FALLBACK_UNKNOWN_IDS,
    READ_MODE_FULL,
    READ_MODE_SECTIONS,
    REFERENCE_SECTION_PROMPT,
    DocumentReadPlan,
    ReferenceSectionChoice,
    build_catalog,
    normalized_section_label,
    resolve_choice,
    without_descendants,
)
from app.rag.section_chunk_map import ROOT_SECTION_ID, SectionNode
from app.rag.section_map_service import SectionNotFoundError, SectionOutline, SectionText
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_cross_references import (
    CHAPTER3,
    FakeGenAi,
    ReferenceOracle,
    _engine,
    _retrieved,
    _section_chunks,
)

DOC_A, DOC_B = "doc-a", "doc-b"
FILE_A, FILE_B = "ops-a.txt", "ops-b.txt"
TARGET_A = ReferenceTarget(FILE_A, "document", "", FILE_A)
TARGET_B = ReferenceTarget(FILE_B, "document", "", FILE_B)
QUESTION = "調査の進め方を、資料の具体的な操作まで含めて教えて"


def _doc_chunks(document_id: str, file_name: str, count: int) -> list[RetrievedChunk]:
    """設定項目の説明が続き、最後の章節にだけ操作の本文がある合成の文書。"""
    rows = [
        _retrieved(
            f"{document_id}:cs:{n}",
            f"設定項目 {n} の説明。",
            f"設定項目 {n}",
            document_id=document_id,
            file_name=file_name,
            chunk_set_id="cs",
        )
        for n in range(count)
    ]
    rows.append(
        _retrieved(
            f"{document_id}:cs:body",
            "操作の本文: tool-x run --since 2h を実行する。",
            "操作の手順",
            document_id=document_id,
            file_name=file_name,
            chunk_set_id="cs",
        )
    )
    return rows


def _node(
    section_id: str, title: str, *, parent: str = ROOT_SECTION_ID, level: int = 1
) -> SectionNode:
    return SectionNode(section_id, title, level, 1, parent, None, None, chunk_count=1, chars=10)


class SectionMap:
    """章節の目次と章節の読取の代役(``section_map_service`` の 2 つの関数を差し替える)。"""

    def __init__(self, chunks: list[RetrievedChunk], *, outlines: set[str]) -> None:
        self.chunks = chunks
        # 目次を持つ文書(それ以外は目次が無い)。
        self.outlines = outlines
        self.empty_sections: set[str] = set()
        self.outline_calls: list[str] = []
        self.read_calls: list[tuple[str, str]] = []

    def sections(self, document_id: str) -> list[SectionNode]:
        nodes = [SectionNode(ROOT_SECTION_ID, "", 0, 0, None, None, None, chunk_count=9)]
        for chunk in self.chunks:
            if chunk.document_id == document_id:
                path = str(chunk.metadata["section_path"])
                nodes.append(_node(f"{document_id}#{path}", path))
        return nodes

    async def outline(
        self, document_id: str, *, chunk_set_id: str | None = None, oracle: Any = None
    ) -> SectionOutline | None:
        self.outline_calls.append(document_id)
        if document_id not in self.outlines:
            return None
        file_name = next(c.file_name for c in self.chunks if c.document_id == document_id)
        return SectionOutline(
            chunk_set=ReadableChunkSet(document_id, chunk_set_id or "cs", file_name, False),
            sections=self.sections(document_id),
        )

    async def read(
        self,
        document_id: str,
        section_id: str,
        *,
        chunk_set_id: str | None = None,
        limit: int | None = None,
        filters: Any = None,
        oracle: Any = None,
        from_order: int = 0,
    ) -> SectionText:
        self.read_calls.append((document_id, section_id))
        outline = await self.outline(document_id, chunk_set_id=chunk_set_id)
        assert outline is not None
        node = outline.get(section_id)
        if node is None:
            raise SectionNotFoundError(section_id)
        rows = [
            SectionChunkRow(order, chunk)
            for order, chunk in enumerate(
                c
                for c in self.chunks
                if c.document_id == document_id
                and f"{document_id}#{c.metadata['section_path']}" == section_id
                and section_id not in self.empty_sections
            )
        ]
        return SectionText(outline=outline, section=node, chunks=rows, complete=True)


class FakeLlm:
    """回答のモデルの代役。目次から ID を選ぶ関数で応答を作る(呼び出しを記録する)。"""

    def __init__(self, pick: Any) -> None:
        self.pick = pick
        self.calls: list[dict[str, Any]] = []

    async def generate(self, prompt: str, context: str, **kwargs: Any) -> str:
        self.calls.append({"prompt": prompt, "context": context, **kwargs})
        result = self.pick(context)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, str) else json.dumps({"section_ids": result})


def _label(context: str, title: str) -> str:
    """目次の行から章節の名前の ID(``S12``)を引く(モデルが選ぶ代わり)。"""
    line = next(line for line in context.splitlines() if line.strip().endswith(title))
    return line.split("[", 1)[1].split("]", 1)[0]


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outlines: set[str],
    pick: Any,
    targets: list[ReferenceTarget] | None = None,
    enabled: bool = True,
    **settings: Any,
) -> tuple[Any, ReferenceOracle, SectionMap, FakeLlm]:
    chunks = [*_doc_chunks(DOC_A, FILE_A, 30), *_doc_chunks(DOC_B, FILE_B, 30)]
    targets = targets or [TARGET_A, TARGET_B]
    anchor = _retrieved(
        "doc-index:a",
        "調査は次の資料に従う。" + "、".join(target.label for target in targets),
        "索引",
        document_id="doc-index",
        file_name="index.md",
        targets=targets,
    )
    oracle = ReferenceOracle([anchor], chunks)
    oracle.documents = [(DOC_A, FILE_A, ()), (DOC_B, FILE_B, ())]
    sections = SectionMap(chunks, outlines=outlines)
    monkeypatch.setattr(section_map_service, "section_outline", sections.outline)
    monkeypatch.setattr(section_map_service, "read_section", sections.read)
    engine = _engine(
        oracle, FakeGenAi(), rag_reference_outline_selection_enabled=enabled, **settings
    )
    llm = FakeLlm(pick)
    engine._reference_llm = llm
    return engine, oracle, sections, llm


async def _search(engine: Any) -> tuple[Any, _SearchState]:
    state = _SearchState()
    result = await engine._search(
        SearchRequest(query=QUESTION), state, retrieval_queries=[QUESTION]
    )
    return result, state


def _selections(state: _SearchState) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], state.reference_sources[0]["section_selections"])


def _reads(state: _SearchState) -> dict[str, dict[str, Any]]:
    (record,) = state.reference_sources
    reads = cast(list[dict[str, Any]], record["document_reads"])
    return {str(item["document_id"]): item for item in reads}


async def test_selected_sections_are_read_with_one_call_for_all_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 つの参照先の目次を 1 回の呼び出しで渡し、選んだ章節(操作の本文)を読む。"""

    def pick(context: str) -> list[str]:
        # 2 つの文書の「操作の手順」は別の ID になる(文書をまたいで一意)。
        labels = [
            line.split("[", 1)[1].split("]", 1)[0]
            for line in context.splitlines()
            if line.strip().endswith("操作の手順")
        ]
        assert len(set(labels)) == 2
        return labels

    engine, oracle, sections, llm = _setup(monkeypatch, outlines={DOC_A, DOC_B}, pick=pick)

    result, state = await _search(engine)

    added = {child.chunk_uid for child in result.expansion_chunks}
    assert added == {f"{DOC_A}:cs:body", f"{DOC_B}:cs:body"}
    # 文書の中の検索(全体で読む退避)はしない。章節は rag_read_section と同じ読取で読む。
    assert oracle.search_calls == []
    assert sorted(sections.read_calls) == [
        (DOC_A, f"{DOC_A}#操作の手順"),
        (DOC_B, f"{DOC_B}#操作の手順"),
    ]
    (call,) = llm.calls
    assert call["prompt"] == QUESTION
    assert call["system_prompt"] == REFERENCE_SECTION_PROMPT
    assert call["response_schema"] == ReferenceSectionChoice.model_json_schema()
    # 目次は文書ごとのブロックで、各行に ID を付ける。
    assert f'<document name="{FILE_A}">' in call["context"]
    assert "[S1] 設定項目 0" in call["context"]
    reads = _reads(state)
    assert {item["read_mode"] for item in reads.values()} == {READ_MODE_SECTIONS}
    assert {item["fallback_reason"] for item in reads.values()} == {None}
    assert reads[DOC_A]["selected_sections"] == [
        {"section_id": f"{DOC_A}#操作の手順", "title": "操作の手順"}
    ]
    # 目次から選んだ章節の chunk は章節の拡張の起点のまま(退避の印を付けない)。
    assert REFERENCE_SECTION_SEED_KEY not in state.chunks[f"{DOC_A}:cs:body"].metadata
    assert state.reference_full_read_ids == set()
    (selection,) = _selections(state)
    assert selection["outcome"] == "selected"
    assert selection["documents"] == 2


async def test_ids_not_in_the_outline_are_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """返った ID のうち目次に無いものは捨て、目次にある ID だけを読む。"""
    engine, _oracle, sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=lambda context: ["S9999", "第3章", f"[{_label(context, '操作の手順')}]"],
    )

    result, state = await _search(engine)

    assert [child.chunk_uid for child in result.expansion_chunks] == [f"{DOC_A}:cs:body"]
    assert sections.read_calls == [(DOC_A, f"{DOC_A}#操作の手順")]
    (selection,) = _selections(state)
    assert selection["discarded_ids"] == ["S9999", "第3章"]


@pytest.mark.parametrize(
    ("outlines", "pick", "reason"),
    [
        (set(), lambda context: [], FALLBACK_NO_OUTLINE),
        ({DOC_A}, lambda context: RuntimeError("model down"), FALLBACK_SELECTION_ERROR),
        ({DOC_A}, lambda context: "not json", FALLBACK_SELECTION_ERROR),
        ({DOC_A}, lambda context: [], FALLBACK_NO_SELECTION),
        ({DOC_A}, lambda context: ["S9999", "x"], FALLBACK_UNKNOWN_IDS),
    ],
    ids=["no-outline", "model-error", "invalid-output", "nothing-selected", "all-ids-unknown"],
)
async def test_falls_back_to_reading_the_whole_document(
    monkeypatch: pytest.MonkeyPatch, outlines: set[str], pick: Any, reason: str
) -> None:
    """目次が無い・選べない・何も選ばない・ID がすべて目次に無いときは、文書全体を読む。"""
    engine, oracle, sections, llm = _setup(
        monkeypatch, outlines=outlines, targets=[TARGET_A], pick=pick
    )

    result, state = await _search(engine)

    # 文書の中を全体の上限まで読み、本文の chunk も足す(入らない分は分割読取に任せる)。
    added = {child.chunk_uid for child in result.expansion_chunks}
    assert f"{DOC_A}:cs:body" in added
    assert len(added) == 31
    (search,) = oracle.search_calls
    assert search["filters"]["document_id"] == DOC_A
    assert sections.read_calls == []
    assert llm.calls == ([] if reason == FALLBACK_NO_OUTLINE else llm.calls)
    assert len(llm.calls) == (0 if reason == FALLBACK_NO_OUTLINE else 1)
    read = _reads(state)[DOC_A]
    assert (read["read_mode"], read["fallback_reason"]) == (READ_MODE_FULL, reason)
    assert read["read_chunk_count"] == 31
    # 退避で足した chunk は章節の拡張の起点にしない(rag_engine へ印を渡す)。
    assert state.chunks[f"{DOC_A}:cs:body"].metadata[REFERENCE_SECTION_SEED_KEY] is False
    assert {
        child.metadata["reference_expansion"].get("section_seed")
        for child in result.expansion_chunks
    } == {False}
    assert added <= state.reference_full_read_ids


async def test_selected_sections_that_read_nothing_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """選んだ章節がどれも読めなければ、文書全体を読む。"""
    engine, oracle, sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=lambda context: [_label(context, "操作の手順")],
    )
    sections.empty_sections.add(f"{DOC_A}#操作の手順")

    result, state = await _search(engine)

    assert f"{DOC_A}:cs:body" in {child.chunk_uid for child in result.expansion_chunks}
    assert len(oracle.search_calls) == 1
    read = _reads(state)[DOC_A]
    assert (read["read_mode"], read["fallback_reason"]) == (READ_MODE_FULL, FALLBACK_SECTIONS_EMPTY)


async def test_only_unselected_document_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 回の呼び出しで片方の文書だけ選んだら、選ばなかった文書だけを全体で読む。"""
    engine, oracle, _sections, llm = _setup(
        monkeypatch,
        outlines={DOC_A, DOC_B},
        pick=lambda context: [_label(context.split("</document>")[0], "操作の手順")],
    )

    _result, state = await _search(engine)

    assert len(llm.calls) == 1
    reads = _reads(state)
    assert (reads[DOC_A]["read_mode"], reads[DOC_A]["fallback_reason"]) == (
        READ_MODE_SECTIONS,
        None,
    )
    assert (reads[DOC_B]["read_mode"], reads[DOC_B]["fallback_reason"]) == (
        READ_MODE_FULL,
        FALLBACK_NO_SELECTION,
    )
    assert [call["filters"]["document_id"] for call in oracle.search_calls] == [DOC_B]


async def test_numbered_reference_does_not_use_the_outline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """番号つきの参照(第 3 章)は今どおり番号で定位し、目次もモデルも使わない。"""
    anchor = _retrieved("doc-1:a", "承認の条件は第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    oracle = ReferenceOracle([anchor], _section_chunks(2))
    sections = SectionMap([], outlines=set())
    monkeypatch.setattr(section_map_service, "section_outline", sections.outline)
    engine = _engine(oracle, rag_reference_outline_selection_enabled=True)
    llm = FakeLlm(lambda context: [])
    engine._reference_llm = llm
    state = _SearchState()

    result = await engine._search(SearchRequest(query="q"), state, retrieval_queries=["q"])

    assert [child.chunk_uid for child in result.expansion_chunks] == [
        "doc-1:cs-1:s0",
        "doc-1:cs-1:s1",
    ]
    assert sections.outline_calls == []
    assert llm.calls == []
    assert state.reference_reads == {}
    assert REFERENCE_SECTION_SEED_KEY not in state.chunks["doc-1:cs-1:s0"].metadata


def test_resolve_choice_keeps_only_catalog_ids_once() -> None:
    plan = DocumentReadPlan(DOC_A, "cs", FILE_A, sections=[_node("x1", "一"), _node("x2", "二")])
    catalog = build_catalog([plan], [["一", "二"]], [0])

    result = resolve_choice(
        ReferenceSectionChoice(section_ids=["Ｓ２", "s2", "[S1]", "S3", "一"]), catalog
    )

    assert result.selected == {0: ["x2", "x1"]}
    assert result.discarded == ["S3", "一"]
    assert normalized_section_label(" [ｓ１] ") == "S1"


def test_without_descendants_reads_the_parent_once() -> None:
    nodes = [_node("p", "親"), _node("c", "子", parent="p", level=2), _node("g", "孫", parent="c")]

    assert without_descendants(nodes, ["c", "p", "g"]) == ["p"]
    assert without_descendants(nodes, ["g"]) == ["g"]


def test_prompt_uses_steps_and_schema_holds_the_field_meaning() -> None:
    assert REFERENCE_SECTION_PROMPT.startswith("## Step 1: ")
    assert "## Step 2: " in REFERENCE_SECTION_PROMPT
    schema = ReferenceSectionChoice.model_json_schema()
    assert schema["properties"]["section_ids"]["description"]
    assert schema.get("additionalProperties") is False


def test_limits_default_to_the_previous_constants() -> None:
    """目次の字数・読む章節の数・読む chunk の上限の既定は、設定にする前の定数と同じ(#5335)。"""
    from app.config import Settings

    settings = Settings(_env_file=None)
    assert settings.rag_reference_outline_selection_catalog_max_chars == 40000
    assert settings.rag_reference_outline_selection_max_sections == 12
    assert settings.rag_reference_outline_selection_read_max_chunks == 400


@pytest.mark.parametrize(
    ("name", "low", "high"),
    [
        ("rag_reference_outline_selection_catalog_max_chars", 2000, 80000),
        ("rag_reference_outline_selection_max_sections", 1, 50),
        ("rag_reference_outline_selection_read_max_chunks", 10, 1000),
    ],
)
def test_limits_reject_values_out_of_range(name: str, low: int, high: int) -> None:
    from pydantic import ValidationError

    from app.config import Settings

    def build(value: int) -> Settings:
        values: dict[str, Any] = {name: value}
        return Settings(_env_file=None, **values)

    assert getattr(build(low), name) == low
    assert getattr(build(high), name) == high
    for invalid in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            build(invalid)


async def test_catalog_limit_is_split_between_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """目次の字数の上限は設定の値で、参照先ごとに均等に割る(#5335)。"""
    from app.rag import section_catalog

    limits: list[int] = []
    original = section_catalog.compact_section_lines

    async def recording(*args: Any, max_chars: int, **kwargs: Any) -> tuple[list[str], int]:
        limits.append(max_chars)
        return await original(*args, max_chars=max_chars, **kwargs)

    monkeypatch.setattr(section_catalog, "compact_section_lines", recording)
    engine, _oracle, _sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A, DOC_B},
        pick=lambda context: [],
        rag_reference_outline_selection_catalog_max_chars=3000,
    )

    await _search(engine)

    assert limits == [1500, 1500]


async def test_section_and_chunk_limits_follow_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """読む章節の数と、参照先 1 つから読む chunk の上限は設定の値(#5335)。"""

    def pick_all(context: str) -> list[str]:
        return [
            line.split("[", 1)[1].split("]", 1)[0] for line in context.splitlines() if "[S" in line
        ]

    engine, _oracle, sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=pick_all,
        rag_reference_outline_selection_max_sections=3,
    )
    await _search(engine)
    assert len(sections.read_calls) == 3

    engine, _oracle, sections, _llm = _setup(
        monkeypatch,
        outlines={DOC_A},
        targets=[TARGET_A],
        pick=pick_all,
        rag_reference_outline_selection_max_sections=50,
        rag_reference_outline_selection_read_max_chunks=10,
    )
    result, state = await _search(engine)
    # 章節は 1 つに 1 chunk の合成なので、10 件で読むのを止める。
    assert len(sections.read_calls) == 10
    assert len(result.expansion_chunks) == 10
    assert _reads(state)[DOC_A]["read_chunk_count"] == 10


async def test_full_read_fallback_uses_the_chunk_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """選べずに文書全体で読む退避も、参照先 1 つから読む chunk の上限に従う(#5335)。"""
    engine, oracle, _sections, _llm = _setup(
        monkeypatch,
        outlines=set(),
        targets=[TARGET_A],
        pick=lambda context: [],
        rag_reference_outline_selection_read_max_chunks=10,
    )

    result, state = await _search(engine)

    assert len(oracle.search_calls) == 1
    assert len(result.expansion_chunks) == 10
    read = _reads(state)[DOC_A]
    assert (read["read_mode"], read["read_chunk_count"]) == (READ_MODE_FULL, 10)


async def test_disabled_setting_keeps_the_search_top_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """設定が無効なら今までどおり、参照先の中を質問で検索した上位 8 件を読む。

    目次もモデルも使わない(LLM の呼び出しは増えない)。
    """
    from app.config import Settings
    from app.rag.answer_engine import _REFERENCE_TARGET_CANDIDATES

    assert Settings(_env_file=None).rag_reference_outline_selection_enabled is True
    engine, oracle, sections, llm = _setup(
        monkeypatch, outlines={DOC_A}, targets=[TARGET_A], pick=lambda context: [], enabled=False
    )

    result, state = await _search(engine)

    added = [child.chunk_uid for child in result.expansion_chunks]
    assert len(added) == _REFERENCE_TARGET_CANDIDATES == 8
    # 本文の chunk は検索の上位に入らない(質問の語と重ならない合成)。
    assert f"{DOC_A}:cs:body" not in added
    (search,) = oracle.search_calls
    assert search["filters"]["document_id"] == DOC_A
    assert sections.outline_calls == [] and sections.read_calls == [] and llm.calls == []
    assert state.reference_reads == {} and state.reference_full_read_ids == set()
    (record,) = state.reference_sources
    assert record["document_reads"] == [] and record["section_selections"] == []
    assert {
        child.metadata["reference_expansion"].get("section_seed")
        for child in result.expansion_chunks
    } == {None}
