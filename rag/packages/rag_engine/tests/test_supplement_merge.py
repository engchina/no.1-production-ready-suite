"""補いの検索（要求別の補完・業務ガイドの手順）の根拠を、主の検索と同じ切りと合わせ込みで足す（#5173）。

補いの検索は検索ごとに「検索 → rerank → 下限 → 主の検索の根拠にある候補を除く（#5175）→ ``top_k`` で切る」だけを
行い、切った根拠を検索拡張と同じ規則（並びにある → 印だけ／同じ文書 → 文書の chunk の順／無い → 最後）で主の検索の
根拠へ合わせ、文脈を補う。主の検索の根拠は外さない。検索・rerank は合成のスタブで、DB と LLM を呼ばない。
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.models.llm import RerankTextRank
from rag_engine.models.storage import StoredChunk
from rag_engine.retrieval.expansion_merge import EXPANSION_EVIDENCE_REASON, GUIDE_STEP_KEY, QUERY_OVERFLOW_KEY
from rag_engine.retrieval.guide_steps import GuidePlan
from rag_engine.retrieval.scope import RETRIEVAL_SCOPE_KNOWLEDGE_BASE
from rag_engine.retrieval.text_search_tokenizer import TextSearchTokenizationResult

# chunk ごとに違う語の本文（MMR が似た本文を後ろへ回さないように）。
_WORDS = ("赤", "青", "黄", "緑", "白", "黒", "茶", "紫", "灰", "桃")


def _chunk(uid: str, seq: int, document: str) -> Any:
    word = _WORDS[(sum(map(ord, uid)) + seq) % len(_WORDS)]
    stored = StoredChunk(
        chunk_uid=uid,
        chunk_id=uid,
        chunk_level="child",
        chunk_seq=seq,
        parent_chunk_uid="",
        parent_chunk_id="",
        child_chunk_ids=(),
        text=f"{uid} {word}{seq} の合成の本文",
        retrieval_text=f"{uid} {word}{seq} の合成の本文",
        source_run_id="run",
        source_file_name=f"{document}.txt",
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=seq,
        page_end=seq,
        source_seq_ranges=(),
        source_record_refs=(),
        metadata={"chunk_strategy": "fixed", "document_id": document, "chunk_set_id": f"set-{document}"},
    )
    return _stored_chunk_answer_record(stored)


def _documents(**counts: int) -> dict[str, Any]:
    return {f"{doc}{seq}": _chunk(f"{doc}{seq}", seq, doc) for doc, count in counts.items() for seq in range(1, count + 1)}


CHUNKS = _documents(A=6, C=3, D=9, E=2, F=3)
QUESTION = "合成Aの手順と設定"
REQUEST = "対象は合成A。\n(1) 合成の手順を教えてください。\n(2) 合成の設定を教えてください。"


class Harness:
    """検索文ごとの候補と、chunk ごとの rerank の点数を返すスタブ。

    pools は検索文ごとの検索の原文（``all_chunks``。既定はすべての chunk）。scoped は頁の範囲を絞った検索（業務ガイドの
    手順の参照）の検索文ごとの候補で、backend と同じに候補へ範囲（``context_page_ranges``）を付け、原文は候補だけにする。
    """

    def __init__(
        self,
        candidates: dict[str, list[str]],
        scores: dict[str, float],
        *,
        chunks: dict[str, Any] | None = None,
        pools: dict[str, list[str]] | None = None,
        scoped: dict[str, list[str]] | None = None,
    ) -> None:
        self.candidates = candidates
        self.scores = scores
        self.chunks = CHUNKS if chunks is None else chunks
        self.pools = pools or {}
        self.scoped = scoped or {}
        self.searches: list[tuple[str, dict[str, Any]]] = []
        self.reranked: list[list[str]] = []

    def search(self, **kwargs: Any) -> Any:
        query = kwargs["retrieval_queries"][0]
        self.searches.append((query, kwargs))
        if kwargs.get("page_ranges"):
            scope = json.dumps(kwargs["page_ranges"], ensure_ascii=False)
            names = next((names for key, names in self.scoped.items() if key in query), [])
            children = [_scoped(self.chunks[name], scope) for name in names]
            return SimpleNamespace(child_chunks=children, all_chunks=list(children), expansion_chunks=[])
        names = next((names for key, names in self.candidates.items() if key in query), [])
        children = [self.chunks[name] for name in names]
        pool = next((names for key, names in self.pools.items() if key in query), None)
        all_chunks = list(self.chunks.values()) if pool is None else [self.chunks[name] for name in pool]
        return SimpleNamespace(child_chunks=children, all_chunks=all_chunks, expansion_chunks=[])

    def rerank(self, question: str, documents: list[str], settings: Any, top_n: int | None = None) -> list[RerankTextRank]:
        names = [next(name for name in self.chunks if document.split("\n")[-1].startswith(f"{name} ")) for document in documents]
        self.reranked.append(names)
        return [RerankTextRank(index, self.scores.get(name, 0.0)) for index, name in enumerate(names)]


def _scoped(record: Any, scope: str) -> Any:
    """頁の範囲で絞った検索・読み取りの原文（backend の ``_with_context_scope`` と同じに範囲の印を付ける）。"""
    return replace(record, metadata={**(record.metadata or {}), "context_page_ranges": scope})


def _run(
    monkeypatch: pytest.MonkeyPatch, harness: Harness, *, guide: GuidePlan | None = None, request: str | None = REQUEST, **changes: Any
) -> Any:
    monkeypatch.setattr(a, "search_adb_hybrid_chunks", harness.search)
    monkeypatch.setattr(a, "_stored_chunk_answer_record", lambda record: record)
    monkeypatch.setattr(a, "_rerank_configured", lambda _: True)
    monkeypatch.setattr(a, "rerank_text_with_scores", harness.rerank)
    monkeypatch.setattr(a, "check_adb_hybrid_search_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        a,
        "tokenize_text_search_query_with_trace",
        lambda text, **kwargs: TextSearchTokenizationResult(tokens=("語",), oracle_text_query="{語}", truncated_count=int(text == REQUEST)),
    )
    settings = replace(
        get_settings(environ={}, dotenv_path=None),
        default_rerank_enabled=True,
        rerank_min_relevance_score=0.3,
        section_expansion_enabled=False,
        **changes,
    )
    return a.answer_question_result(
        QUESTION,
        "synthetic-run",
        ["docling"],
        settings,
        chunk_top_k=2,
        chunk_neighbor_count=1,
        retrieval_scope=RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
        generate_answer=False,
        request_question=request,
        guide_plan=guide,
    )


def _evidence(result: Any) -> list[str]:
    return [item["chunk_uid"] for item in result.evidence_items]


SCORES = {"A2": 0.9, "C2": 0.7, "E1": 0.88, "A5": 0.85, "D2": 0.6, "D9": 0.1, "F1": 0.9, "F2": 0.8, "F3": 0.75}


def _step(result: Any, name: str) -> dict[str, str]:
    """工程の記録の欄（ラベル → 値）。同じ名前の工程が複数あれば最初のもの。"""
    step = next(item for item in result.execution_steps if item["name"] == name)
    return {field["label"]: field["value"] for field in step["details"]["fields"]}


MAIN = ["A1", "A2", "A3", "C1", "C2", "C3"]


def _keeps_main_evidence(evidence: list[str]) -> bool:
    """主の検索の根拠（A2・C2 と前後の文脈）が 1 つも外れず、並びの順も同じか。"""
    return [uid for uid in evidence if uid in MAIN] == MAIN


def test_request_supplements_skip_main_evidence_before_the_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = Harness({QUESTION: ["A2", "C2"], "(1)": ["D9", "D2", "A5", "E1", "A2"]}, SCORES)

    result = _run(monkeypatch, harness, request_coverage_max_chunks=3)

    # 要求の検索も rerank にかける（主の検索 1 回 + 要求 1 回）。
    assert [sorted(names) for names in harness.reranked] == [["A2", "C2"], ["A2", "A5", "D2", "D9", "E1"]]
    trace = result.generation_trace["query_overflow"]
    # 上限 3 件（要求 1 件）で切る: 下限 0.3 で D9 を外し、主の検索の根拠にある A2 を切りの前に除き（#5175）、
    # 残りの rerank の上位 3 件（E1・A5・D2）を採る。枠はすべて新しい根拠に使う。
    assert [row["top_k"] for row in trace["requests"]] == [3]
    assert trace["candidate_chunk_ids"] == ["E1", "A5", "D2"]
    assert trace["requests"][0]["new_candidates"] == 3
    # 除いた件数と理由を記録に残す。
    assert trace["requests"][0]["covered_candidates"] == 1
    assert _step(result, "主の検索の根拠と重複する候補の除外")["結果"] == (
        "主の検索の根拠と重複する 1 件を除きました（主の検索の根拠と同じ chunk 1 件）。残りの候補 3 件"
    )
    assert "主の検索の根拠と重複して除いた候補 1 件" in _step(result, "検索上限の要求別補完")["結果"]
    # 主の検索の根拠（A2・C2 と前後の文脈）は外れない。A5 は同じ文書 A の文書の順の位置に入り（前後の文脈 A4・A6 と
    # 一緒に）、別の文書の E1・D2 は前後の文脈と一緒に最後（合わせ込みの規則は #5173 と同じ）。
    assert _evidence(result) == ["A1", "A2", "A3", "A4", "A5", "A6", "C1", "C2", "C3", "E1", "E2", "D1", "D2", "D3"]
    assert _keeps_main_evidence(_evidence(result))
    # A5 は主の検索の根拠 A2 のかたまり（前後の文脈 A3 と隣り合う）にまとめられ、親の文脈の役割になるが、主の検索の根拠に
    # 無かった本文なので新しく足した根拠に数える（#5185）。
    assert trace["added_chunk_ids"] == ["A5", "E1", "D2"]
    children = {child["chunk_uid"]: child for item in result.evidence_items for child in item.get("children") or ()}
    assert children["A2"]["retrieval_role"] == "retrieved_anchor" and children["A2"]["context_reason"] == "top_retrieved_child"
    assert children["E1"]["retrieval_role"] == "retrieved_anchor" and children["E1"]["context_reason"] == EXPANSION_EVIDENCE_REASON
    assert children["A5"]["retrieval_role"] == "parent_context" and children["A5"]["context_reason"] == EXPANSION_EVIDENCE_REASON
    assert result.generation_trace["supplement_merge"] == {
        "supplement_count": 3,
        "rejected_count": 0,
        "added_chunk_ids": ["A5", "E1", "D2"],
    }
    assert "D9" not in _evidence(result)


def test_request_supplements_fill_the_cut_when_all_top_candidates_are_main_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    # rerank の上位 2 件（A2・C2）がどちらも主の検索の根拠でも、枠 2 件は次の新しい候補（F1・F2）で埋まる。
    harness = Harness({QUESTION: ["A2", "C2"], "(1)": ["F3", "F2", "F1", "C2", "A2"]}, {**SCORES, "A2": 0.99, "C2": 0.98})

    result = _run(monkeypatch, harness, request_coverage_max_chunks=2)

    trace = result.generation_trace["query_overflow"]
    assert trace["candidate_chunk_ids"] == ["F1", "F2"]
    assert trace["requests"][0]["covered_candidates"] == 2
    assert _keeps_main_evidence(_evidence(result))
    assert {"F1", "F2"} <= set(_evidence(result)) and "F3" in _evidence(result)
    assert trace["added_chunk_ids"] == ["F1", "F2"]


def test_guide_step_supplements_use_the_same_cut_and_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    guide = GuidePlan.from_payload("g-1", 1, [{"id": "s1", "title": "合成の工程", "retrieval_hints": ["工程F"]}])
    assert guide is not None
    harness = Harness({"工程F": ["F1", "F2", "F3", "A2"], QUESTION: ["A2", "C2"]}, SCORES)

    result = _run(monkeypatch, harness, guide=guide, request=None, guide_step_chunks_per_step=2)

    step_searches = [kwargs for query, kwargs in harness.searches if "工程F" in query]
    assert step_searches and {kwargs["candidate_limit"] for kwargs in step_searches} == {a._retrieval_candidate_limit(2, None)}
    # 手順の検索も主の検索の根拠にある A2 を切りの前に除き（#5175）、上位 2 件（F1・F2）で切る。F3 は F2 の前後の文脈。
    assert _evidence(result) == ["A1", "A2", "A3", "C1", "C2", "C3", "F1", "F2", "F3"]
    assert _keeps_main_evidence(_evidence(result))
    roles = {child["chunk_uid"]: child for item in result.evidence_items for child in item.get("children") or ()}
    assert roles["F1"]["context_reason"] == EXPANSION_EVIDENCE_REASON and roles["F2"]["context_reason"] == EXPANSION_EVIDENCE_REASON
    guide_trace = result.generation_trace["guide_steps"]
    assert guide_trace["added_chunk_ids"] == ["F1", "F2"]
    search = guide_trace["steps"][0]["searches"][0]
    assert search["candidates"] == 2 and search["covered_candidates"] == 1
    # 除いた A2（主の検索の根拠）も手順の根拠と必須の取得の候補に数える。
    assert guide_trace["steps"][0]["evidence_count"] == 3
    assert "A2" in guide_trace["required_acquisition"]["targets"][0]["candidate_source_ids"]
    assert "主の検索の根拠と重複して除いた候補 1 件" in _step(result, "業務ガイドの手順ごとの検索")["結果"]


def test_request_and_guide_supplements_share_one_exclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """要求別の補完と業務ガイドの手順の検索は、同じ 1 つの除外の判定を使う（#5175）。"""
    guide = GuidePlan.from_payload("g-1", 1, [{"id": "s1", "title": "合成の工程", "retrieval_hints": ["工程F"]}])
    assert guide is not None
    harness = Harness({"工程F": ["F1", "A2"], "(1)": ["E1", "C2"], QUESTION: ["A2", "C2"]}, SCORES)
    calls: list[tuple[str, Any]] = []
    original = a.build_adb_hybrid_answer_context

    def spy(question: str, *args: Any, **kwargs: Any) -> Any:
        calls.append((kwargs.get("rerank_phase", ""), kwargs.get("exclude")))
        return original(question, *args, **kwargs)

    monkeypatch.setattr(a, "build_adb_hybrid_answer_context", spy)
    result = _run(monkeypatch, harness, guide=guide)

    excludes = {phase: exclude for phase, exclude in calls}
    assert excludes["main_search"] is None
    assert excludes["missing_requirement"] is not None and excludes["missing_requirement"] is excludes["guide_step"]
    assert result.generation_trace["query_overflow"]["requests"][0]["covered_candidates"] == 1
    assert result.generation_trace["guide_steps"]["steps"][0]["searches"][0]["covered_candidates"] == 1
    assert _keeps_main_evidence(_evidence(result))


def test_supplement_marks_are_kept_on_merged_records() -> None:
    record = CHUNKS["A5"]
    marked = a._with_supplement_mark(record, QUERY_OVERFLOW_KEY, {"request_ids": ["Q1"]})
    marked = a._with_supplement_mark(marked, QUERY_OVERFLOW_KEY, {"request_ids": ["Q2", "Q1"]})
    marked = a._with_supplement_mark(marked, GUIDE_STEP_KEY, {"step_ids": ["s1"]})
    assert marked.metadata[QUERY_OVERFLOW_KEY] == {"request_ids": ["Q1", "Q2"]}
    assert marked.metadata[GUIDE_STEP_KEY] == {"step_ids": ["s1"]}
    assert QUERY_OVERFLOW_KEY not in (record.metadata or {})


# ---- 主の検索の根拠と前後の文脈を外さない（#5185） ----

# 文書 A の要約の chunk（文書の中の位置を持たない派生の chunk。backend は前後を読まない #4793）を末尾の番号で持つ。
SUMMARY_CHUNKS = {**CHUNKS, "AS": _chunk("AS", 7, "A")}


def _reader(chunks: dict[str, Any], *, no_neighbors: frozenset[str] = frozenset({"AS"})) -> Any:
    """backend の ``_context_chunks`` と同じに、起点ごとに起点の範囲で前後 1 件を読むスタブ（派生の chunk は前後を読まない）。"""
    calls: list[list[str]] = []

    def read(*, anchors: list[Any], settings: Any, section_anchor_ids: Any = ()) -> list[Any]:
        calls.append([record.chunk_uid for record in anchors])
        out: list[Any] = []
        for anchor in anchors:
            scope = str((anchor.metadata or {}).get("context_page_ranges") or "")
            document = anchor.metadata["document_id"]
            names = [anchor.chunk_uid]
            if anchor.chunk_uid not in no_neighbors:
                names += [
                    name
                    for name, record in chunks.items()
                    if record.metadata["document_id"] == document
                    and name not in no_neighbors
                    and abs(record.chunk_seq - anchor.chunk_seq) == 1
                ]
            out.extend(_scoped(chunks[name], scope) if scope else chunks[name] for name in names)
        return out

    read.calls = calls  # type: ignore[attr-defined]
    return read


def _children(result: Any) -> dict[str, dict[str, Any]]:
    return {child["chunk_uid"]: child for item in result.evidence_items for child in item.get("children") or ()}


def _in_order(evidence: list[str], expected: list[str]) -> bool:
    """expected がすべて evidence にあり、同じ順に並ぶか。"""
    return [uid for uid in evidence if uid in expected] == expected


def test_scoped_guide_supplement_keeps_main_anchor_and_its_neighbor(monkeypatch: pytest.MonkeyPatch) -> None:
    """頁の範囲を絞った手順の参照の検索が主の検索の起点と同じ chunk を返しても、主の検索の起点の原文・前後の文脈を変えない。

    主の検索の起点 AS（文書 A の要約の chunk）の前後の文脈は主の検索の原文の A6。手順の参照の検索（文書 A に絞る）は AS と
    A3 を範囲つきで返す。範囲つきの AS が主の検索の AS を置き換えると、AS の前後は範囲つきの原文（A2〜A4）から選ばれて A6
    が外れ、保険の確認で補いの根拠がすべて捨てられていた（#5185）。
    """
    guide = GuidePlan.from_payload(
        "g-1", 1, [{"id": "s1", "title": "合成の参照", "retrieval_hints": ["参照H"], "references": [{"document_id": "A"}]}]
    )
    assert guide is not None
    harness = Harness({QUESTION: ["AS", "C2"]}, {"AS": 0.9, "C2": 0.7, "A3": 0.8}, chunks=SUMMARY_CHUNKS, scoped={"参照H": ["AS", "A3"]})
    reader = _reader(SUMMARY_CHUNKS)
    monkeypatch.setattr(a, "injected_context_chunks", reader)

    result = _run(monkeypatch, harness, guide=guide, request=None, guide_step_chunks_per_step=2)

    evidence = _evidence(result)
    # 主の検索の根拠（AS と前後の文脈 A6、C2 と C1・C3）は 1 つも外れず、並びの順も同じ。
    assert _in_order(evidence, ["A6", "AS", "C1", "C2", "C3"])
    # 補いの根拠 A3 は前後の文脈（範囲の中で読んだ A2・A4）と一緒に入る。範囲つきの AS は主の検索の AS に印だけを付ける。
    assert {"A2", "A3", "A4"} <= set(evidence)
    assert evidence.count("AS") == 1
    children = _children(result)
    assert children["AS"]["retrieval_role"] == "retrieved_anchor" and children["AS"]["context_reason"] == "top_retrieved_child"
    assert children["A3"]["context_reason"] == EXPANSION_EVIDENCE_REASON
    merge = result.generation_trace["supplement_merge"]
    assert merge["rejected_count"] == 0 and merge["added_chunk_ids"] == ["A3"]
    assert result.generation_trace["guide_steps"]["added_chunk_ids"] == ["A3"]
    # 主の検索の起点と補いの起点は分けて読む（補いの読み取りで主の検索の起点の原文を置き換えない）。
    assert ["A3"] in reader.calls


def test_scoped_guide_candidates_of_main_evidence_do_not_take_the_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """頁の範囲を絞った手順の参照の検索の候補が主の検索の根拠と同じ chunk・前後の文脈なら、切りの前に除く（#5197）。

    範囲つきの候補は ``record_context_key`` が別の key になり、除外に掛からず切りの枠（1 件）を占めて、新しい根拠 A5 が
    入らなかった。
    """
    guide = GuidePlan.from_payload(
        "g-1", 1, [{"id": "s1", "title": "合成の参照", "retrieval_hints": ["参照H"], "references": [{"document_id": "A"}]}]
    )
    assert guide is not None
    # A2 は主の検索の起点、A3 はその前後の文脈、A5 は新しい根拠。
    harness = Harness({QUESTION: ["A2", "C2"]}, {**SCORES, "A2": 0.95, "A3": 0.9, "A5": 0.8}, scoped={"参照H": ["A2", "A3", "A5"]})
    monkeypatch.setattr(a, "injected_context_chunks", _reader(CHUNKS, no_neighbors=frozenset()))

    result = _run(monkeypatch, harness, guide=guide, request=None, guide_step_chunks_per_step=1)

    search = result.generation_trace["guide_steps"]["steps"][0]["searches"][0]
    assert search["covered_candidates"] == 2 and search["candidates"] == 1
    assert _step(result, "主の検索の根拠と重複する候補の除外")["結果"] == (
        "主の検索の根拠と重複する 2 件を除きました（主の検索の根拠と同じ chunk 1 件・主の検索の根拠の前後の文脈に含まれる 1 件）。"
        "残りの候補 1 件"
    )
    assert result.generation_trace["guide_steps"]["added_chunk_ids"] == ["A5"]
    evidence = _evidence(result)
    assert _keeps_main_evidence(evidence) and "A5" in evidence
    assert evidence.count("A2") == 1 and evidence.count("A3") == 1


def test_supplement_records_do_not_change_main_neighbors(monkeypatch: pytest.MonkeyPatch) -> None:
    """補いの検索の原文が増えても、主の検索の根拠の前後の文脈は主の検索の原文だけから選ぶ。

    前後は読んだ chunk の並びの位置で選ぶ。主の検索の原文に A5・A6 が無いと AS の前は A4。補いの検索の原文に A6 が入ると、
    合わせ直しで AS の前が A6 に変わり A4 が外れていた（#5185）。
    """
    harness = Harness(
        {QUESTION: ["AS", "C2"], "(1)": ["E1"]},
        {"AS": 0.9, "C2": 0.7, "E1": 0.8},
        chunks=SUMMARY_CHUNKS,
        pools={QUESTION: [name for name in SUMMARY_CHUNKS if name not in {"A5", "A6"}], "(1)": ["E1", "E2", "A6"]},
    )

    result = _run(monkeypatch, harness, request_coverage_max_chunks=1)

    evidence = _evidence(result)
    assert _in_order(evidence, ["A4", "AS", "C1", "C2", "C3"])
    assert "A6" not in evidence and {"E1", "E2"} <= set(evidence)
    assert result.generation_trace["supplement_merge"]["rejected_count"] == 0
    assert result.generation_trace["query_overflow"]["added_chunk_ids"] == ["E1"]


def _dropping_main_evidence(monkeypatch: pytest.MonkeyPatch, *, when: str | None, drop: str = "A1") -> None:
    """合わせ直しで、補いの根拠 when（None ならいつも）があると主の検索の根拠 drop を外す（保険の確認のためのスタブ）。"""
    original = a.fill_answer_context

    def fill(*args: Any, **kwargs: Any) -> Any:
        merged = original(*args, **kwargs)
        supplements = [record.chunk_uid for record in kwargs.get("supplements") or ()]
        if supplements and (when is None or when in supplements):
            tree = tuple(parent for parent in merged.evidence_tree if parent.record.chunk_uid != drop)
            merged = replace(merged, evidence_tree=tree, records=[record for record in merged.records if record.chunk_uid != drop])
        return merged

    monkeypatch.setattr(a, "fill_answer_context", fill)


def test_supplements_that_would_drop_main_evidence_are_left_out_one_document_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """保険の確認で外れたときは、外れた主の検索の根拠と同じ文書の補いの根拠だけを除き、ほかは合わせる（#5185）。"""
    _dropping_main_evidence(monkeypatch, when="A5")
    harness = Harness({QUESTION: ["A2", "C2"], "(1)": ["A5", "E1"]}, SCORES)

    result = _run(monkeypatch, harness, request_coverage_max_chunks=2)

    evidence = _evidence(result)
    assert _keeps_main_evidence(evidence)
    assert "E1" in evidence and "A5" not in evidence
    merge = result.generation_trace["supplement_merge"]
    assert merge["supplement_count"] == 2 and merge["rejected_count"] == 1
    assert merge["rejected"] == [{"chunk_id": "A5", "kinds": [QUERY_OVERFLOW_KEY], "reason": a.SUPPLEMENT_REJECTED_DROPS_MAIN}]
    assert merge["missing_main_chunk_ids"] == ["A1"]
    assert merge["added_chunk_ids"] == ["E1"]
    assert _step(result, "補いの検索の根拠の合わせ込み")["結果"] == (
        "補いの根拠 2 件を合わせ、新しく足した根拠 1 件（主の検索の根拠が外れるため 1 件を使いませんでした）"
    )


def test_all_supplements_are_left_out_when_main_evidence_still_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ文書の補いの根拠を除いても外れるときだけ、補いの根拠をすべて使わず、件数と理由を残す（#5185）。"""
    _dropping_main_evidence(monkeypatch, when=None)
    harness = Harness({QUESTION: ["A2", "C2"], "(1)": ["A5", "E1"]}, SCORES)

    result = _run(monkeypatch, harness, request_coverage_max_chunks=2)

    assert _evidence(result) == MAIN
    merge = result.generation_trace["supplement_merge"]
    assert merge["rejected_count"] == 2 and [row["chunk_id"] for row in merge["rejected"]] == ["E1", "A5"]
    assert merge["added_chunk_ids"] == []
    assert result.generation_trace["query_overflow"]["added_chunk_ids"] == []
    assert "主の検索の根拠が外れるため 2 件を使いませんでした" in _step(result, "補いの検索の根拠の合わせ込み")["結果"]


def test_supplement_folded_into_main_block_keeps_mark_and_counts_as_added(monkeypatch: pytest.MonkeyPatch) -> None:
    """主の検索の根拠のかたまりにまとめられて親の文脈の役割になった補いの根拠も、印と added_chunk_ids を残す（#5185）。"""
    guide = GuidePlan.from_payload("g-1", 1, [{"id": "s1", "title": "合成の工程", "retrieval_hints": ["工程A"]}])
    assert guide is not None
    # A5 は主の検索の根拠 A2（前後の文脈 A1・A3）と、前後の文脈 A4 でつながるかたまりにまとめられる。
    harness = Harness({"工程A": ["A5"], QUESTION: ["A2", "C2"]}, SCORES)
    trees: list[Any] = []
    original = a.fill_answer_context

    def spy(*args: Any, **kwargs: Any) -> Any:
        merged = original(*args, **kwargs)
        if kwargs.get("supplements"):
            trees.append(merged.evidence_tree)
        return merged

    monkeypatch.setattr(a, "fill_answer_context", spy)
    result = _run(monkeypatch, harness, guide=guide, request=None, guide_step_chunks_per_step=1)

    assert _keeps_main_evidence(_evidence(result)) and "A5" in _evidence(result)
    children = _children(result)
    assert children["A5"]["retrieval_role"] == "parent_context" and children["A5"]["context_reason"] == EXPANSION_EVIDENCE_REASON
    # 根拠の木の A5 の record に手順の検索の印が残る。
    (tree,) = trees
    a5 = next(child.record for parent in tree for child in parent.children if child.record.chunk_uid == "A5")
    assert a5.metadata[GUIDE_STEP_KEY] == {"step_ids": ["s1"], "kinds": ["step"]}
    guide_trace = result.generation_trace["guide_steps"]
    assert guide_trace["added_chunk_ids"] == ["A5"]
    assert guide_trace["steps"][0]["searches"][0]["added_chunk_ids"] == ["A5"]
