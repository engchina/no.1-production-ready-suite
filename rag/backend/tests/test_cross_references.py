"""文書の交差参照の抽出・解決と、回答の検索での参照先の追加(#1280)。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.rag.chunking import Chunk
from app.rag.cross_references import (
    MAX_REFERENCES_PER_CHUNK,
    REFERENCE_FROM_KEY,
    REFERENCE_LABEL_KEY,
    REFERENCE_TARGETS_KEY,
    ReferenceSpec,
    ReferenceTarget,
    SectionIndex,
    annotate_cross_references,
    extract_references,
    reference_targets,
    same_document_title,
    section_path_within,
)
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest

# ---- 抽出 ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("承認の条件は第3章を参照してください。", [("第3章", "chapter", "3", None)]),
        ("詳細は３．２節参照。", [("3.2節", "section", "3.2", None)]),
        ("様式は別紙1のとおり。", [("別紙1", "appendix", "別紙:1", None)]),
        ("第5条に定める手続きを行う。", [("第5条", "article", "5", None)]),
        ("第三章第二節を参照", [("第三章第二節", "section", "3.2", None)]),
        ("付録Aをご覧ください", [("付録A", "appendix", "付録:A", None)]),
        (
            "第3章及び第5章を参照",
            [("第3章", "chapter", "3", None), ("第5章", "chapter", "5", None)],
        ),
        ("本マニュアルの第2章を参照", [("第2章", "chapter", "2", None)]),
        (
            "経費精算マニュアルの「権限」を参照すること。",
            [("「権限」", "title", "権限", "経費精算マニュアル")],
        ),
        (
            "『経費精算マニュアル』の第4章を参照",
            [("第4章", "chapter", "4", "経費精算マニュアル")],
        ),
        ("詳細は出張旅費規程第6条による。", [("第6条", "article", "6", "出張旅費規程")]),
        (
            "See Section 3.2 of the Expense Manual for details.",
            [("See Section 3.2 of the Expense Manual", "section", "3.2", "Expense Manual")],
        ),
        ("Please refer to Appendix A.", [("refer to Appendix A", "appendix", "付録:A", None)]),
        # 手がかりの語の無い章の参照(「の」+ 名詞。#1382)。
        (
            "この章に無いシステムは、第 2 章の共通の保守枠で保守します。",
            [("第 2 章", "chapter", "2", None)],
        ),
        (
            "組織規程の第 4 章の代理の規定で決める。",
            [("第 4 章", "chapter", "4", "組織規程")],
        ),
        (
            "第3章と第5章の手順で行う。",
            [("第3章", "chapter", "3", None), ("第5章", "chapter", "5", None)],
        ),
        ("第5条の手続きで申請する。", [("第5条", "article", "5", None)]),
        ("別紙2の様式で提出する。", [("別紙2", "appendix", "別紙:2", None)]),
        ("3.2節の手順で行う。", [("3.2節", "section", "3.2", None)]),
    ],
    ids=[
        "chapter",
        "full-width-section",
        "appendix",
        "article",
        "kanji-chapter-section",
        "appendix-letter",
        "enumeration",
        "this-manual",
        "other-document-title",
        "quoted-document-chapter",
        "regulation-article",
        "english-section-of-document",
        "english-appendix",
        "possessive-chapter",
        "possessive-other-document",
        "possessive-enumeration",
        "possessive-article",
        "possessive-appendix",
        "possessive-numbered-section",
    ],
)
def test_extract_references(text: str, expected: list[tuple[str, str, str, str | None]]) -> None:
    specs = extract_references(text)

    assert [(s.label, s.kind, s.key, s.document_title) for s in specs] == expected


@pytest.mark.parametrize(
    "text",
    [
        "第3章では概要を説明する。",
        "1.5倍を参照",
        "「承認」ボタンを押す。",
        "バージョン 2 では変わった。",
        "",
        "本書は全12章の構成です。",
        "第2条の2の手続きで行う。",
        "別紙の様式で提出する。",
        "3.2の機能で動く。",
        "第 1 章 個別の保守枠",
    ],
    ids=[
        "no-cue",
        "decimal",
        "ui-label",
        "plain",
        "empty",
        "chapter-count",
        "branch-article",
        "unnumbered-appendix",
        "version-number",
        "heading",
    ],
)
def test_extract_references_ignores_text_without_reference(text: str) -> None:
    """参照の手がかりの語の無い表記・手がかりの語と離れた語は拾わない。"""
    assert extract_references(text) == []


def test_extract_references_marks_whole_document_reference() -> None:
    """『文書名』だけの参照は文書全体への参照(節を決めないので辿らない)。"""
    specs = extract_references("詳しくは『経費精算マニュアル』を参照。")

    assert [(s.kind, s.document_title) for s in specs] == [("document", "経費精算マニュアル")]


def test_extract_references_caps_count_and_dedupes() -> None:
    text = "。".join(f"第{n}章を参照" for n in range(1, 12)) + "。第1章を参照"

    specs = extract_references(text)

    assert len(specs) == MAX_REFERENCES_PER_CHUNK
    assert len({spec.key for spec in specs}) == len(specs)


# ---- 解決 ----

PATHS = [
    "第1章 総則",
    "第2章 手順 > 第1節 準備",
    "第2章 手順 > 第2節 入力",
    "第3章 承認 > 3.1 申請",
    "第3章 承認 > 3.2 上長承認",
    "第4章 権限 > 4.1 権限の設定",
    "第5章 監査 > 第2節 記録",
    "別紙1 申請書様式",
    "Appendix A Forms",
]


def _resolve(text: str, citing: tuple[str, ...] = ()) -> str | None:
    (spec,) = extract_references(text)
    return SectionIndex(PATHS).resolve(spec, citing_path=citing)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("第3章を参照", "第3章 承認"),
        ("3.2節を参照", "第3章 承認 > 3.2 上長承認"),
        ("別紙1のとおり", "別紙1 申請書様式"),
        ("「権限の設定」を参照", "第4章 権限 > 4.1 権限の設定"),
        ("「権限」を参照", "第4章 権限"),
        ("see Appendix A", "Appendix A Forms"),
        ("第2章第2節を参照", "第2章 手順 > 第2節 入力"),
        ("第9章を参照", None),
        ("「存在しない節」を参照", None),
    ],
    ids=[
        "chapter",
        "numbered",
        "appendix",
        "title-exact",
        "title-partial",
        "english-appendix",
        "chapter-section-as-jsection",
        "missing-chapter",
        "missing-title",
    ],
)
def test_section_index_resolves_reference(text: str, expected: str | None) -> None:
    assert _resolve(text) == expected


def test_section_index_prefers_section_near_citing_chunk() -> None:
    """「第2節」が複数の章にあるときは、参照元と同じ章の節を選ぶ。"""
    assert _resolve("第2節を参照", ("第5章 監査", "第1節 概要")) == "第5章 監査 > 第2節 記録"
    assert _resolve("第2節を参照", ("第2章 手順", "第1節 準備")) == "第2章 手順 > 第2節 入力"


def test_section_index_uses_numbered_heading_for_chapter() -> None:
    """章の見出しが「3 承認」のように番号だけの文書でも「第3章」を解決する。"""
    index = SectionIndex(["1 総則", "3 承認 > 3.1 申請"])

    assert index.resolve(ReferenceSpec("第3章", "chapter", "3")) == "3 承認"


def test_section_index_leaves_ambiguous_partial_title_unresolved() -> None:
    index = SectionIndex(["承認A", "承認B", "承認C"])

    assert index.resolve(ReferenceSpec("「承認」", "title", "承認")) is None


def test_section_path_within_and_document_title() -> None:
    assert section_path_within("第3章 承認 > 3.2 上長承認", "第3章 承認")
    assert section_path_within("第3章 承認", "第3章 承認")
    assert not section_path_within("第3章 承認2", "第3章 承認")
    assert not section_path_within(None, "第3章 承認")
    assert same_document_title("経費精算マニュアル", "経費精算マニュアル_v3.pdf")
    assert same_document_title("Expense Manual", "expense_manual.docx")
    assert not same_document_title("経費精算マニュアル", "出張旅費規程.pdf")


# ---- 取込 ----


def _chunk(index: int, text: str, section_path: str | None, **metadata: Any) -> Chunk:
    return Chunk(
        text=text,
        index=index,
        start_offset=0,
        end_offset=len(text),
        metadata={"section_path": section_path, **metadata},
    )


def test_annotate_cross_references_resolves_same_document_sections() -> None:
    chunks = [
        _chunk(0, "承認の条件は第3章を参照すること。様式は別紙1のとおり。", "第1章 総則"),
        _chunk(1, "上長の承認が必要である。", "第3章 承認 > 3.1 申請"),
        _chunk(2, "第3章を参照。3.1節を参照。", "第3章 承認 > 3.1 申請"),
        _chunk(3, "申請日を書く。", "別紙1 申請書様式"),
        _chunk(4, "第9章を参照。", "第1章 総則"),
        _chunk(5, "要約。第3章を参照。", "第1章 総則", raptor_summary=True),
    ]

    annotated = annotate_cross_references(chunks, document_title="経費精算規程.pdf")

    assert annotated == 2
    first = reference_targets(chunks[0].metadata)
    assert [(t.label, t.section_path, t.resolved) for t in first] == [
        ("第3章", "第3章 承認", True),
        ("別紙1", "別紙1 申請書様式", True),
    ]
    # 参照の無い chunk・要約の chunk には付けない。自分の節(祖先の節)だけを参照する chunk は
    # 空の列(取込で解決済みの印。回答のときに本文から抜き出し直さない。#1382)。
    assert REFERENCE_TARGETS_KEY not in chunks[1].metadata
    assert chunks[2].metadata[REFERENCE_TARGETS_KEY] == "[]"
    assert REFERENCE_TARGETS_KEY not in chunks[5].metadata
    # 見つからない節は未解決として残す(診断に使う)。
    assert [(t.label, t.resolved) for t in reference_targets(chunks[4].metadata)] == [
        ("第9章", False)
    ]


def test_annotate_cross_references_keeps_other_documents_unresolved() -> None:
    chunks = [
        _chunk(0, "経費精算マニュアルの「権限」を参照。", "第1章 総則"),
        _chunk(1, "経費精算規程の第2章を参照。", "第1章 総則"),
        _chunk(2, "権限を設定する。", "第2章 権限"),
    ]

    annotate_cross_references(chunks, document_title="経費精算規程.pdf")

    (other,) = reference_targets(chunks[0].metadata)
    assert other.document_title == "経費精算マニュアル"
    assert other.section_path is None
    # 自分の文書名の参照は、同じ文書の節として解決する。
    (own,) = reference_targets(chunks[1].metadata)
    assert own.document_title is None
    assert own.section_path == "第2章 権限"


def test_reference_targets_round_trip_and_ignore_broken_values() -> None:
    target = ReferenceTarget("第3章", "chapter", "3", None, "第3章 承認")

    assert ReferenceTarget.from_dict(target.to_dict()) == target
    assert reference_targets({REFERENCE_TARGETS_KEY: "{broken"}) == []
    assert reference_targets({REFERENCE_TARGETS_KEY: json.dumps([{"kind": "x"}, 1])}) == []
    assert reference_targets({}) == []


async def test_ingestion_records_cross_references_on_chunks() -> None:
    """取込の分割の後に、chunk の metadata へ参照先を残す。"""
    from app.rag.ingestion import IngestionPipeline
    from app.rag.ingestion_quality import build_ingestion_quality_report
    from app.schemas.extraction import StructuredExtraction

    extraction = StructuredExtraction(
        raw_text=(
            "経費精算規程\n\n# 第1章 総則\n\n承認の条件は第3章を参照すること。\n\n"
            "# 第3章 承認\n\n上長の承認が必要である。\n"
        )
    )
    pipeline = IngestionPipeline(
        vlm=cast(Any, object()),
        genai=cast(Any, object()),
        oracle=cast(Any, object()),
        object_storage=cast(Any, object()),
        settings=Settings(rag_chunking_strategy="structure_aware"),
    )

    chunks = await pipeline._build_chunks_for_extraction(
        trace_id="trace-1280",
        extraction=extraction,
        quality_report=build_ingestion_quality_report(extraction),
        parser_profile="unstructured",
        source_name="経費精算規程.pdf",
    )

    referencing = [chunk for chunk in chunks if REFERENCE_TARGETS_KEY in chunk.metadata]
    assert len(referencing) == 1
    assert [t.section_path for t in reference_targets(referencing[0].metadata)] == ["第3章 承認"]


# ---- 回答の検索 ----


def _retrieved(
    chunk_id: str,
    text: str,
    section_path: str,
    *,
    document_id: str = "doc-1",
    file_name: str = "経費精算規程.pdf",
    chunk_set_id: str = "cs-1",
    targets: list[ReferenceTarget] | None = None,
) -> RetrievedChunk:
    metadata: dict[str, Any] = {
        "section_path": section_path,
        "chunk_set_id": chunk_set_id,
        "chunk_group_id": chunk_id,
        "page_start": 1,
        "page_end": 1,
    }
    if targets:
        metadata[REFERENCE_TARGETS_KEY] = json.dumps(
            [target.to_dict() for target in targets], ensure_ascii=False
        )
    return RetrievedChunk(
        document_id=document_id,
        chunk_id=chunk_id,
        text=text,
        score=0.9,
        file_name=file_name,
        metadata=metadata,
    )


CHAPTER3 = ReferenceTarget("第3章", "chapter", "3", None, "第3章 承認")


class ReferenceOracle:
    """回答の検索が読む OracleClient の部分集合(交差参照の参照先を返す)。"""

    def __init__(
        self,
        hits: list[RetrievedChunk],
        sections: list[RetrievedChunk],
        *,
        knowledge_bases: dict[str, set[str]] | None = None,
    ) -> None:
        self.hits = hits
        self.sections = sections
        # ナレッジベース → 所属の文書(検索の範囲。None は範囲を見ない)。
        self.knowledge_bases = knowledge_bases
        self.reference_calls: list[dict[str, Any]] = []
        self.document_calls: list[dict[str, Any]] = []
        self.section_calls: list[dict[str, str]] = []
        self.documents: list[tuple[str, str]] = []
        self.fail = False

    async def retrieval_large_categories(self, filters: dict[str, str]) -> list[str]:
        return []

    async def document_classifications(self, ids: list[str]) -> dict[str, dict[str, object]]:
        return {}

    async def chunk_set_first_page_contexts(self, ids: list[str]) -> dict[str, dict[str, object]]:
        return {}

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        return list(self.hits)

    async def context_group_siblings(
        self, anchors: list[RetrievedChunk], *, max_chunks_per_group: int
    ) -> list[RetrievedChunk]:
        return []

    async def retrieval_reference_chunks(
        self,
        filters: dict[str, str],
        *,
        document_id: str,
        section_path: str,
        chunk_set_id: str | None = None,
        limit: int,
    ) -> list[RetrievedChunk]:
        self.reference_calls.append(
            {
                "filters": dict(filters),
                "document_id": document_id,
                "section_path": section_path,
                "chunk_set_id": chunk_set_id,
            }
        )
        if self.fail:
            raise RuntimeError("db down")
        return [
            chunk
            for chunk in self.sections
            if chunk.document_id == document_id
            and self._in_scope(filters, chunk)
            and section_path_within(chunk.metadata.get("section_path"), section_path)
            and (chunk_set_id is None or chunk.metadata.get("chunk_set_id") == chunk_set_id)
        ][:limit]

    def _in_scope(self, filters: dict[str, str], chunk: RetrievedChunk) -> bool:
        knowledge_base_id = filters.get("knowledge_base_id")
        if self.knowledge_bases is None or not knowledge_base_id:
            return True
        return chunk.document_id in self.knowledge_bases.get(knowledge_base_id, set())

    async def retrieval_reference_documents(
        self, filters: dict[str, str], *, title: str, limit: int
    ) -> list[tuple[str, str]]:
        self.document_calls.append({"filters": dict(filters), "title": title})
        return list(self.documents)

    async def retrieval_screen_sections(
        self, filters: dict[str, str]
    ) -> list[tuple[str, str, int]]:
        self.section_calls.append(dict(filters))
        document_id = filters.get("document_id")
        chunk_set_id = filters.get("chunk_set_id")
        paths = dict.fromkeys(
            str(chunk.metadata["section_path"])
            for chunk in self.sections
            if chunk.document_id == document_id
            and self._in_scope(filters, chunk)
            and (chunk_set_id is None or chunk.metadata.get("chunk_set_id") == chunk_set_id)
        )
        return [("x.pdf", path, 1) for path in paths]


class FakeGenAi:
    async def embed(self, texts: list[str], *, input_type: str = "") -> list[list[float]]:
        return [[0.1] * 4 for _ in texts]


def _engine(oracle: ReferenceOracle, **settings: Any) -> AnswerEngine:
    return AnswerEngine(
        Settings(**settings), oracle=cast(Any, oracle), genai=cast(Any, FakeGenAi())
    )


def _section_chunks(count: int, *, chunk_set_id: str = "cs-1") -> list[RetrievedChunk]:
    return [
        _retrieved(
            f"doc-1:{chunk_set_id}:s{n}",
            f"承認の条件 {n}",
            "第3章 承認 > 3.1 申請",
            chunk_set_id=chunk_set_id,
        )
        for n in range(count)
    ]


async def test_search_adds_referenced_section_after_citing_anchor() -> None:
    anchor = _retrieved("doc-1:a", "承認の条件は第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    other = _retrieved("doc-1:b", "精算の概要。", "第1章 総則")
    oracle = ReferenceOracle(
        [anchor, other],
        [*_section_chunks(3), *_section_chunks(1, chunk_set_id="cs-2")],
    )
    state = _SearchState()

    result = await _engine(oracle)._search(
        SearchRequest(
            query="承認の条件",
            filters={"knowledge_base_id": "kb-1", "file_name": "経費", "large_category": "業務A"},
        ),
        state,
        retrieval_queries=["承認の条件"],
    )

    order = [child.chunk_uid for child in result.child_chunks]
    # 参照先の節の先頭から 2 件を、参照元の直後に置く(rerank が無効でも根拠に入る)。
    assert order == ["doc-1:a", "doc-1:cs-1:s0", "doc-1:cs-1:s1", "doc-1:b"]
    child = result.child_chunks[1]
    assert child.metadata["rrf_score"] == pytest.approx(
        result.child_chunks[0].metadata["rrf_score"] * 0.5
    )
    # 参照先は参照元と同じ版(chunk_set)から読み、検索範囲は KB だけに絞る(文書名・分類は外す)。
    (call,) = oracle.reference_calls
    assert call["chunk_set_id"] == "cs-1"
    assert call["filters"] == {"knowledge_base_id": "kb-1"}
    added = state.chunks["doc-1:cs-1:s0"]
    assert added.metadata[REFERENCE_FROM_KEY] == "doc-1:a"
    assert added.metadata[REFERENCE_LABEL_KEY] == "第3章"
    assert set(state.reference_expansions) == {"doc-1:cs-1:s0", "doc-1:cs-1:s1"}
    # 親(small-to-big の文脈)も作る。
    assert any(chunk.chunk_level == "parent" for chunk in result.all_chunks)


async def test_search_reference_expansion_respects_budget_and_caches_targets() -> None:
    targets = [
        CHAPTER3,
        ReferenceTarget("別紙1", "appendix", "別紙:1", None, "別紙1 申請書様式"),
    ]
    anchors = [
        _retrieved(f"doc-1:a{n}", "第3章と別紙1を参照。", "第1章 総則", targets=targets)
        for n in range(3)
    ]
    appendix = [_retrieved(f"doc-1:cs-1:x{n}", "様式", "別紙1 申請書様式") for n in range(3)]
    oracle = ReferenceOracle(anchors, [*_section_chunks(3), *appendix])
    state = _SearchState()
    engine = _engine(oracle, rag_reference_expansion_max_chunks=3)

    result = await engine._search(SearchRequest(query="q"), state, retrieval_queries=["q"])
    await engine._search(SearchRequest(query="q"), state, retrieval_queries=["q2"])

    referenced = [
        child.chunk_uid
        for child in result.child_chunks
        if child.chunk_uid not in {a.chunk_id for a in anchors}
    ]
    assert referenced == ["doc-1:cs-1:s0", "doc-1:cs-1:s1", "doc-1:cs-1:x0"]
    # 同じ参照先は 1 回の回答で 1 回だけ読む(CRAG の各回でも読み直さない)。
    assert [call["section_path"] for call in oracle.reference_calls] == [
        "第3章 承認",
        "別紙1 申請書様式",
    ]


async def test_search_skips_reference_expansion_when_disabled_or_search_only() -> None:
    anchor = _retrieved("doc-1:a", "第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    oracle = ReferenceOracle([anchor], _section_chunks(2))

    disabled = await _engine(oracle, rag_reference_expansion_enabled=False)._search(
        SearchRequest(query="q"), _SearchState(), retrieval_queries=["q"]
    )
    retrieved = await _engine(oracle).retrieve(SearchRequest(query="q", top_k=1))

    assert [child.chunk_uid for child in disabled.child_chunks] == ["doc-1:a"]
    assert [chunk.chunk_id for chunk in retrieved] == ["doc-1:a"]
    assert oracle.reference_calls == []


async def test_search_continues_when_reference_target_cannot_be_read() -> None:
    anchor = _retrieved("doc-1:a", "第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    oracle = ReferenceOracle([anchor], _section_chunks(2))
    oracle.fail = True

    result = await _engine(oracle)._search(
        SearchRequest(query="q"), _SearchState(), retrieval_queries=["q"]
    )

    assert [child.chunk_uid for child in result.child_chunks] == ["doc-1:a"]


async def test_search_resolves_other_document_reference_within_scope() -> None:
    """他の文書への参照は、検索範囲の文書から文書名で探し、その文書の見出しで節を決める。"""
    target = ReferenceTarget("「権限」", "title", "権限", "経費精算マニュアル")
    anchor = _retrieved(
        "doc-1:a", "経費精算マニュアルの「権限」を参照。", "第1章", targets=[target]
    )
    manual = [
        _retrieved(
            f"doc-2:cs-9:{n}",
            "権限の設定",
            "第4章 権限 > 4.1 設定",
            document_id="doc-2",
            file_name="経費精算マニュアル.pdf",
            chunk_set_id="cs-9",
        )
        for n in range(2)
    ] + [
        _retrieved(
            "doc-2:cs-8:0",
            "権限の設定(別レシピ)",
            "第4章 権限 > 4.1 設定",
            document_id="doc-2",
            file_name="経費精算マニュアル.pdf",
            chunk_set_id="cs-8",
        )
    ]
    oracle = ReferenceOracle([anchor], manual)
    oracle.documents = [
        ("doc-2", "経費精算マニュアル.pdf"),
        ("doc-3", "旧経費精算マニュアル集.pdf"),
    ]

    result = await _engine(oracle)._search(
        SearchRequest(query="q", filters={"knowledge_base_id": "kb-1", "category_name": "経費"}),
        _SearchState(),
        retrieval_queries=["q"],
    )

    assert [child.chunk_uid for child in result.child_chunks] == [
        "doc-1:a",
        "doc-2:cs-9:0",
        "doc-2:cs-9:1",
    ]
    assert oracle.document_calls == [
        {"filters": {"knowledge_base_id": "kb-1"}, "title": "経費精算マニュアル"}
    ]
    assert oracle.section_calls == [{"knowledge_base_id": "kb-1", "document_id": "doc-2"}]


async def test_search_does_not_follow_other_document_outside_scope() -> None:
    target = ReferenceTarget("第2章", "chapter", "2", "出張旅費規程")
    anchor = _retrieved("doc-1:a", "出張旅費規程の第2章を参照。", "第1章", targets=[target])
    oracle = ReferenceOracle([anchor], [])

    result = await _engine(oracle)._search(
        SearchRequest(query="q"), _SearchState(), retrieval_queries=["q"]
    )

    assert [child.chunk_uid for child in result.child_chunks] == ["doc-1:a"]
    assert oracle.section_calls == []


async def test_search_resolves_references_of_chunks_without_ingest_targets() -> None:
    """取込で参照先を解決していない chunk(#1382 より前の取込)は、回答のときに本文から解決する。"""
    first = _retrieved("doc-1:a", "申請は第3章の手順で行う。", "第1章 総則")
    second = _retrieved("doc-1:b", "様式は第3章の規定で決める。", "第1章 総則")
    marked = _retrieved("doc-1:c", "第3章の手順で行う。", "第1章 総則")
    marked.metadata[REFERENCE_TARGETS_KEY] = "[]"
    plain = _retrieved("doc-1:d", "精算の概要。", "第1章 総則")
    oracle = ReferenceOracle([first, second, marked, plain], _section_chunks(3))
    state = _SearchState()

    result = await _engine(oracle)._search(
        SearchRequest(query="q", filters={"knowledge_base_id": "kb-1", "file_name": "経費"}),
        state,
        retrieval_queries=["q"],
    )

    order = [child.chunk_uid for child in result.child_chunks]
    # 2 つ目の候補は同じ参照先の残り(足した chunk は重ねない)。
    assert order == [
        "doc-1:a",
        "doc-1:cs-1:s0",
        "doc-1:cs-1:s1",
        "doc-1:b",
        "doc-1:cs-1:s2",
        "doc-1:c",
        "doc-1:d",
    ]
    # 見出しの列は文書と版(chunk_set)ごとに 1 回だけ、検索と同じ範囲(KB)で読む。取込で解決済みの
    # 印(空の列)のある chunk と、参照の無い chunk では読まない。
    assert oracle.section_calls == [
        {"knowledge_base_id": "kb-1", "document_id": "doc-1", "chunk_set_id": "cs-1"}
    ]
    assert [call["section_path"] for call in oracle.reference_calls] == ["第3章 承認"]
    assert {item["resolved_at"] for item in state.reference_expansions.values()} == {"query"}
    assert [t.section_path for t in state.query_reference_targets["doc-1:a"]] == ["第3章 承認"]


async def test_search_skips_section_read_for_other_document_references_only() -> None:
    anchor = _retrieved("doc-1:a", "出張旅費規程の第2章の規定で決める。", "第1章 総則")
    oracle = ReferenceOracle([anchor], [])

    result = await _engine(oracle)._search(
        SearchRequest(query="q"), _SearchState(), retrieval_queries=["q"]
    )

    assert [child.chunk_uid for child in result.child_chunks] == ["doc-1:a"]
    # 他の文書への参照だけなら、この文書の見出しは読まず、参照先の文書を範囲の中で探す。
    assert oracle.document_calls == [{"filters": {}, "title": "出張旅費規程"}]
    assert oracle.section_calls == []


# ---- 多段の評価セットの形(#1382) ----

MULTI_HOP_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "multi-hop"
_PLAN_KB = "kb-eval"


def _html_inner(pattern: str, html: str) -> str:
    match = re.search(pattern, html, re.S)
    assert match is not None
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _plan_chunks(file_name: str, document_id: str, chunk_set_id: str) -> list[RetrievedChunk]:
    """評価の資料(定期保守計画の原稿)を章ごとの chunk にする(Docling の章ごとの分割と同じ形)。"""
    html = (MULTI_HOP_DIR / "sources" / f"{Path(file_name).stem}.html").read_text(encoding="utf-8")
    title = _html_inner(r"<h1>(.*?)</h1>", html)
    chunks: list[RetrievedChunk] = []
    for index, part in enumerate(re.split(r"(?=<h2>)", html)[1:]):
        heading = _html_inner(r"<h2>(.*?)</h2>", part)
        body = "\n".join(
            re.sub(r"<[^>]+>", "", item).strip()
            for item in re.findall(r"<(?:p|li)>.*?</(?:p|li)>", part, re.S)
        )
        chunks.append(
            _retrieved(
                f"{document_id}:{chunk_set_id}:{index}",
                body,
                f"{title} > {heading}",
                document_id=document_id,
                file_name=file_name,
                chunk_set_id=chunk_set_id,
            )
        )
    return chunks


def _annotated(chunks: list[RetrievedChunk], file_name: str) -> list[RetrievedChunk]:
    """取込と同じく、chunk の本文の参照を同じ文書の見出しの列で解決して metadata に残す。"""
    staged = [
        _chunk(chunk_index, chunk.text, str(chunk.metadata["section_path"]))
        for chunk_index, chunk in enumerate(chunks)
    ]
    annotate_cross_references(staged, document_title=file_name)
    return [
        chunk.model_copy(update={"metadata": {**chunk.metadata, **staged_chunk.metadata}})
        for chunk, staged_chunk in zip(chunks, staged, strict=True)
    ]


def _common_window_cases() -> list[dict[str, Any]]:
    payload = json.loads((MULTI_HOP_DIR / "multi-hop.json").read_text(encoding="utf-8"))
    return [
        case
        for case in payload["cases"]
        if any(item["id"] == "mt-common" for item in case.get("required_evidence", []))
    ]


@pytest.mark.parametrize("resolved_at", ["ingest", "query"], ids=["ingest", "query"])
async def test_multi_hop_common_window_reference_reaches_chapter_two(resolved_at: str) -> None:
    """定期保守計画の第 1 章の「第 2 章の共通の保守枠で保守します」から第 2 章が文脈に入る。

    #1362 の評価で A(実体のレシピあり)が欠けた根拠 4 件(`mt-common`)の型。第 1 章だけが
    検索に当たっても、同じ文書の同じ版の第 2 章を足し、旧版(2025 年度)の第 2 章は足さない。
    """
    from app.rag.evaluation_handling import contains_normalized

    current = _plan_chunks("maintenance-plan.pdf", "doc-plan", "cs-plan")
    old = _plan_chunks("maintenance-plan-2025.pdf", "doc-plan-2025", "cs-old")
    if resolved_at == "ingest":
        current = _annotated(current, "maintenance-plan.pdf")
        old = _annotated(old, "maintenance-plan-2025.pdf")
    chapter_one = current[0]
    assert "第 2 章の共通の保守枠" in chapter_one.text
    # 旧版は新しい版に置き換えた文書なので、検索の範囲(既定)に入らない。
    oracle = ReferenceOracle(
        [chapter_one], [*current, *old], knowledge_bases={_PLAN_KB: {"doc-plan"}}
    )
    state = _SearchState()

    result = await _engine(oracle)._search(
        SearchRequest(query="保守枠", filters={"knowledge_base_id": _PLAN_KB}),
        state,
        retrieval_queries=["保守枠"],
    )

    context = "\n".join(child.text for child in result.child_chunks)
    cases = _common_window_cases()
    assert {case["id"] for case in cases} >= {"br-budget-change-window", "br-sales-change-window"}
    for case in cases:
        evidence = [
            item
            for item in case["required_evidence"]
            if item["document_id"] == "file:maintenance-plan.pdf"
        ]
        missing = [
            item["id"] for item in evidence if not contains_normalized(context, item["text"])
        ]
        assert missing == [], case["id"]
    assert "毎月最終土曜日" not in context
    added = state.chunks["doc-plan:cs-plan:1"]
    assert added.metadata[REFERENCE_FROM_KEY] == chapter_one.chunk_id
    assert added.metadata[REFERENCE_LABEL_KEY] == "第 2 章"
    assert {item["resolved_at"] for item in state.reference_expansions.values()} == {resolved_at}
    # 参照先は KB の範囲と参照元の版(chunk_set)だけで読む(文書名などの絞り込みは外す)。
    assert all(
        call["filters"] == {"knowledge_base_id": _PLAN_KB} for call in oracle.reference_calls
    )
    assert {call["chunk_set_id"] for call in oracle.reference_calls} == {"cs-plan"}


async def test_multi_hop_common_window_reference_stays_within_knowledge_base() -> None:
    """参照元の文書が検索の範囲(ナレッジベース)の外なら、見出しも参照先も読めず足さない。"""
    current = _plan_chunks("maintenance-plan.pdf", "doc-plan", "cs-plan")
    oracle = ReferenceOracle([current[0]], current, knowledge_bases={_PLAN_KB: set()})
    state = _SearchState()

    result = await _engine(oracle)._search(
        SearchRequest(query="保守枠", filters={"knowledge_base_id": _PLAN_KB}),
        state,
        retrieval_queries=["保守枠"],
    )

    assert [child.chunk_uid for child in result.child_chunks] == [current[0].chunk_id]
    assert state.reference_expansions == {}
    assert oracle.reference_calls == []


# ---- Oracle の SQL ----


async def test_oracle_reference_queries_use_search_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.clients.oracle import OracleClient

    client = OracleClient(settings=Settings())
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_fetch_all(
        statement: str, binds: dict[str, object] | None = None
    ) -> list[dict[str, object]]:
        calls.append((statement, dict(binds or {})))
        if "TRANSLATE" in statement:
            return [{"document_id": "doc-2", "file_name": "経費精算マニュアル.pdf"}]
        return []

    monkeypatch.setattr(client, "_fetch_all", fake_fetch_all)
    filters = {"knowledge_base_id": "kb-1"}

    assert (
        await client.retrieval_reference_chunks(
            filters, document_id="doc-1", section_path="第3章 承認", chunk_set_id="cs-1", limit=7
        )
        == []
    )
    assert await client.retrieval_reference_documents(
        filters, title="経費精算マニュアル", limit=5
    ) == [("doc-2", "経費精算マニュアル.pdf")]

    chunk_sql, chunk_binds = calls[0]
    assert "kb-1" in chunk_binds.values()
    assert chunk_binds["filter_document_id"] == "doc-1"
    assert chunk_binds["filter_chunk_set_id"] == "cs-1"
    assert chunk_binds["reference_section_path"] == "第3章 承認"
    assert chunk_binds["reference_section_prefix"] == "第3章 承認 > "
    assert chunk_binds["reference_prefix_length"] == len("第3章 承認 > ")
    assert chunk_binds["reference_limit"] == 7
    assert "ORDER BY c.chunk_set_id, c.chunk_index" in chunk_sql
    document_sql, document_binds = calls[1]
    assert "kb-1" in document_binds.values()
    assert document_binds["reference_title"] == "経費精算マニュアル"
    assert "superseded_by_document_id IS NULL" in document_sql


# ---- MCP の根拠 ----


def test_mcp_evidence_carries_references_and_origin() -> None:
    from app.mcp.tools import _evidence

    chunk = _retrieved("doc-1:a", "第3章を参照。", "第1章 総則", targets=[CHAPTER3])
    chunk.metadata[REFERENCE_FROM_KEY] = "doc-1:z"
    chunk.metadata[REFERENCE_LABEL_KEY] = "別紙1"

    evidence = _evidence(chunk)

    assert [ref.model_dump() for ref in evidence.references] == [
        {
            "label": "第3章",
            "document_title": None,
            "section_path": ["第3章 承認"],
            "resolved": True,
        }
    ]
    assert evidence.reference_from == "doc-1:z"
    assert evidence.reference_label == "別紙1"


# ---- 設定 ----


def test_reference_expansion_settings_and_evaluation_override() -> None:
    from app.rag.evaluation import evaluation_settings
    from app.schemas.evaluation import EvaluationRagOverrides

    settings = Settings()
    assert settings.rag_reference_expansion_enabled is True
    assert settings.rag_reference_expansion_max_chunks == 4
    with pytest.raises(ValueError):
        Settings(rag_reference_expansion_max_chunks=0)

    overridden = evaluation_settings(
        settings,
        EvaluationRagOverrides(reference_expansion_enabled=False, reference_expansion_max_chunks=2),
    )
    assert overridden.rag_reference_expansion_enabled is False
    assert overridden.rag_reference_expansion_max_chunks == 2
