"""文脈の補完: 起点ごとに補い方を 1 つ選び、補った後に重なる根拠を 1 回でまとめる（#5168）。

章節の一覧と章節の chunk はスタブで、DB と LLM を呼ばない。テストごとにスタブを作る（共有の状態を持たない）。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering
from rag_engine.generation import section_expansion as sections
from rag_engine.generation.answer_models import AnswerContext
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.models.storage import StoredChunk
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence
from rag_engine.retrieval.context_fill import (
    FILL_NEIGHBORS,
    FILL_PARENT,
    FILL_SECTION,
    consolidate_evidence_tree,
    context_fill_mode,
)
from rag_engine.retrieval.expansion_merge import ENTITY_EXPANSION_KEY, EXPANSION_EVIDENCE_REASON


def _stored(uid: str, seq: int, document: str, *, strategy: str = "fixed", parent: str = "", level: str = "child") -> StoredChunk:
    return StoredChunk(
        chunk_uid=uid,
        chunk_id=uid,
        chunk_level=level,
        chunk_seq=seq,
        parent_chunk_uid=parent,
        parent_chunk_id=parent,
        child_chunk_ids=(),
        text=f"合成の本文 {uid}",
        retrieval_text=f"合成の本文 {uid}",
        source_run_id="run",
        source_file_name=f"{document}.txt",
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=1,
        page_end=1,
        source_seq_ranges=(),
        source_record_refs=(),
        metadata={"chunk_strategy": strategy, "document_id": document, "chunk_set_id": f"set-{document}"},
    )


def _record(uid: str, seq: int, document: str, **options: Any):
    return _stored_chunk_answer_record(_stored(uid, seq, document, **options))


def _flat(document: str, count: int) -> dict[str, StoredChunk]:
    return {f"{document}{seq}": _stored(f"{document}{seq}", seq, document) for seq in range(1, count + 1)}


class Sections:
    """文書 A だけが章節の対応（1 つの章節に全 chunk）を持つスタブ。"""

    def __init__(self, chunks: dict[str, StoredChunk], document: str = "A") -> None:
        self.chunks = chunks
        self.document = document
        self.reads: list[str] = []

    def _info(self) -> tuple[sections.SectionInfo, ...]:
        members = [chunk for chunk in self.chunks.values() if chunk.metadata["document_id"] == self.document]
        return (
            sections.SectionInfo("document", "文書", 0, None, 1, 1, len(members), len(members) * 10),
            sections.SectionInfo("s1", "章節 1", 1, "document", 1, 1, len(members), len(members) * 10),
        )

    def outline(self, *, chunk_uid: str, **_: object) -> sections.SectionOutline | None:
        chunk = self.chunks.get(chunk_uid)
        if chunk is None or chunk.metadata["document_id"] != self.document:
            return None
        return sections.SectionOutline(self.document, f"set-{self.document}", "s1", self._info(), file_name="A.txt")

    def read(self, *, document_id: str, chunk_set_id: str, section_id: str, **_: object) -> list[StoredChunk]:
        self.reads.append(section_id)
        return [chunk for chunk in self.chunks.values() if chunk.metadata["document_id"] == document_id]


def unused(*args, **kwargs):
    raise AssertionError("unexpected I/O")


@dataclass
class Observation:
    name: str
    inputs: Any = None
    outputs: Any = None
    params: dict = field(default_factory=dict)

    def request(self, value, **kwargs):
        self.inputs = value

    def response(self, value):
        self.outputs = value

    def usage(self, value):
        pass

    def display(self, kind=None, params=None, details=None):
        self.params.update(params or {})


@contextmanager
def recorded():
    observations: list[Observation] = []

    @contextmanager
    def observer(operation, name):
        entry = Observation(name)
        observations.append(entry)
        yield entry

    with bind_trace_observer(observer):
        yield observations


def _settings():
    return replace(get_settings(environ={}, dotenv_path=None), section_expansion_enabled=True, section_expansion_max_levels=1)


def _ids(context) -> list[str]:
    return [record.chunk_uid for record in context.records]


def _build(stub: Sections, ranked, active, *, expansions=(), section_fill=True, neighbors=1):
    calls: list[dict[str, Any]] = []

    def load(**kwargs):
        calls.append(kwargs)
        return []

    dependencies = AnswerDependencies(
        unused, unused, unused, unused, unused, context_chunks=load, section_outline=stub.outline, section_chunks=stub.read
    )
    with bind_dependencies(dependencies), recorded() as observed:
        bundle = answering._bundle_context(
            "合成の質問",
            ranked,
            active,
            _settings(),
            top_k=len(ranked),
            neighbor_child_count=neighbors,
            max_records=20,
            max_chars=100_000,
            expansions=expansions,
            section_fill=section_fill,
        )
    step = next(item for item in observed if item.name == "文脈の補完")
    context = AnswerContext(records=list(bundle.records), text=bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree)
    return context, step, calls


def _expand(stub: Sections, context: AnswerContext) -> AnswerContext:
    dependencies = AnswerDependencies(unused, unused, unused, unused, unused, section_outline=stub.outline, section_chunks=stub.read)
    with bind_dependencies(dependencies), recorded():
        expanded, _result = answering._expand_context_by_sections(
            "合成の質問", context, _settings(), rerank_enabled=False, started_at=0, state={}
        )
    return expanded


def test_fill_mode_prefers_section_then_parent_then_neighbors():
    assert context_fill_mode(sectioned=True, parent_child=True) == FILL_SECTION
    assert context_fill_mode(sectioned=True, parent_child=False) == FILL_SECTION
    assert context_fill_mode(sectioned=False, parent_child=True) == FILL_PARENT
    assert context_fill_mode(sectioned=False, parent_child=False) == FILL_NEIGHBORS


def _three_documents() -> tuple[dict[str, StoredChunk], list[Any], list[Any]]:
    """A: 章節の対応がある固定長の分割 / B: 章節の無い親子分割 / C: どちらも無い固定長の分割。"""
    chunks = {**_flat("A", 3), **_flat("C", 3)}
    chunks["B2"] = _stored("B2", 2, "B", strategy="small_to_big", parent="PB")
    chunks["PB"] = _stored("PB", 1, "B", strategy="small_to_big", level="parent")
    records = {uid: _stored_chunk_answer_record(chunk) for uid, chunk in chunks.items()}
    return chunks, [records["A2"], records["B2"], records["C2"]], list(records.values())


def test_each_anchor_gets_exactly_one_fill_mode():
    chunks, ranked, active = _three_documents()
    context, step, calls = _build(Sections(chunks), ranked, active)
    assert step.inputs["起点ごとの補い方"] == {"A2": FILL_SECTION, "B2": FILL_PARENT, "C2": FILL_NEIGHBORS}
    # 章節で補う A2 は前後を読まない（backend へ渡す）。B2 は親、C2 は前後 1 件ずつ。
    assert calls[0]["section_anchor_ids"] == ["A2"]
    assert _ids(context) == ["A2", "PB", "C1", "C2", "C3"]


def test_without_section_fill_every_flat_anchor_reads_neighbors():
    chunks, ranked, active = _three_documents()
    context, step, calls = _build(Sections(chunks), ranked, active, section_fill=False)
    assert step.inputs["起点ごとの補い方"] == {"A2": FILL_NEIGHBORS, "B2": FILL_PARENT, "C2": FILL_NEIGHBORS}
    assert "section_anchor_ids" not in calls[0]
    assert _ids(context) == ["A1", "A2", "A3", "PB", "C1", "C2", "C3"]


def test_neighbor_count_reads_n_chunks_on_each_side():
    chunks = _flat("C", 7)
    records = [_stored_chunk_answer_record(chunk) for chunk in chunks.values()]
    context, _step, _calls = _build(Sections(chunks, document="none"), [records[3]], records, neighbors=2)
    assert _ids(context) == ["C2", "C3", "C4", "C5", "C6"]


def test_section_fill_places_the_section_next_to_its_anchor_in_rerank_order():
    chunks, ranked, active = _three_documents()
    stub = Sections(chunks)
    context, _step, _calls = _build(stub, ranked, active)
    expanded = _expand(stub, context)
    assert stub.reads == ["s1"]
    # 章節の本文は最後に足さず、起点 A2 のかたまり（文書の順）に置く。かたまりの並びは rerank の順。
    assert _ids(expanded) == ["A1", "A2", "A3", "PB", "C1", "C2", "C3"]
    roles = {child.record.chunk_uid: child.role for parent in expanded.evidence_tree for child in parent.children}
    assert roles["A2"] == "retrieved_anchor"
    assert roles["A1"] == roles["A3"] == sections.SECTION_CONTEXT_ROLE


def test_expansion_inside_an_anchor_section_is_only_marked():
    chunks = {**_flat("A", 6), **_flat("C", 3)}
    records = {uid: _stored_chunk_answer_record(chunk) for uid, chunk in chunks.items()}
    expansion = replace(records["A5"], metadata={**records["A5"].metadata, ENTITY_EXPANSION_KEY: {"hop": 1}})
    stub = Sections(chunks)
    context, step, _calls = _build(stub, [records["A2"], records["C2"]], list(records.values()), expansions=[expansion])
    # 拡張の根拠も同じ規則で補う（章節の対応があるので前後を読まない）。
    assert step.inputs["起点ごとの補い方"]["A5"] == FILL_SECTION
    expanded = _expand(stub, context)
    assert _ids(expanded) == ["A1", "A2", "A3", "A4", "A5", "A6", "C1", "C2", "C3"]
    a5 = next(child for parent in expanded.evidence_tree for child in parent.children if child.record.chunk_uid == "A5")
    # 起点 A2 の章節に含まれるので、重ねて置かずに印だけを残す。
    assert a5.role == "parent_context"
    assert a5.reason == EXPANSION_EVIDENCE_REASON
    assert ENTITY_EXPANSION_KEY in a5.record.metadata
    assert [record.chunk_uid for record in expanded.records].count("A5") == 1


def _group(record, *children, reason="retrieved_chunk", role="child_fallback"):
    return ContextParentEvidence(record=record, role=role, reason=reason, children=tuple(children))


def _anchor(record, rank):
    return ContextChildEvidence(record=record, role="retrieved_anchor", reason="top_retrieved_child", retrieval_rank=rank)


def _fill(record, role="neighbor_context", reason="adjacent_child"):
    return ContextChildEvidence(record=record, role=role, reason=reason)


def test_contained_anchor_merges_into_the_section_parent_at_the_anchor_position():
    x = _record("x", 1, "D", strategy="small_to_big", parent="P")
    z = _record("z", 2, "D", strategy="small_to_big", parent="P")
    y = _record("y", 1, "E")
    p = _record("P", 1, "D", strategy="small_to_big", level="parent")
    tree = (
        _group(x, _anchor(x, 1), reason="parent_unavailable_child_fallback"),
        _group(y, _anchor(y, 2)),
        _group(
            p,
            _fill(x, sections.SECTION_CONTEXT_ROLE, "section_expansion"),
            _fill(z, sections.SECTION_CONTEXT_ROLE, "section_expansion"),
            reason="section_expansion",
            role="synthesis_parent",
        ),
    )
    merged = consolidate_evidence_tree(tree)
    assert [item.record.chunk_uid for item in merged] == ["P", "y"]
    assert {child.record.chunk_uid: child.role for child in merged[0].children} == {
        "x": "retrieved_anchor",
        "z": sections.SECTION_CONTEXT_ROLE,
    }
    # 起点の理由を引き継ぐ（補った文脈の理由のままにしない）。
    assert merged[0].reason == "parent_unavailable_child_fallback"


def test_largely_overlapping_evidence_is_merged():
    a, b, c = (_record(name, seq, "D", strategy="small_to_big", parent="P") for seq, name in enumerate("abc", 1))
    p = _record("P", 1, "D", strategy="small_to_big", level="parent")
    q = _record("Q", 2, "D", strategy="small_to_big", level="parent")
    tree = (
        _group(p, _anchor(a, 1), _fill(b, "parent_context"), _fill(c, "parent_context"), role="synthesis_parent"),
        _group(q, _anchor(b, 2), _fill(c, "parent_context"), role="synthesis_parent"),
    )
    merged = consolidate_evidence_tree(tree)
    assert [item.record.chunk_uid for item in merged] == ["P"]
    assert {child.record.chunk_uid: child.role for child in merged[0].children}["b"] == "retrieved_anchor"


def test_fill_joins_the_nearest_anchor_block_and_adjacent_anchors_form_one_block():
    d = {seq: _record(f"d{seq}", seq, "D") for seq in (1, 2, 8, 10, 11)}
    other = _record("e1", 1, "E")
    tree = (
        _group(d[10], _anchor(d[10], 1)),
        _group(other, _anchor(other, 2)),
        _group(d[1], _anchor(d[1], 3)),
        _group(d[11], _anchor(d[11], 4)),
        _group(d[8], _fill(d[8]), reason="adjacent_child"),
        _group(d[2], _fill(d[2]), reason="adjacent_child"),
    )
    merged = consolidate_evidence_tree(tree)
    # d8 は最も近い d10 のかたまりへ、d2 は d1 のかたまりへ。隣り合う d10・d11 は 1 つのかたまり（文書の順）。
    assert [item.record.chunk_uid for item in merged] == ["d8", "d10", "d11", "e1", "d1", "d2"]


def test_fill_without_an_anchor_in_the_document_keeps_its_position():
    a = _record("a1", 1, "D")
    lone = _record("f5", 5, "F")
    merged = consolidate_evidence_tree((_group(lone, _fill(lone), reason="adjacent_child"), _group(a, _anchor(a, 1))))
    assert [item.record.chunk_uid for item in merged] == ["f5", "a1"]
