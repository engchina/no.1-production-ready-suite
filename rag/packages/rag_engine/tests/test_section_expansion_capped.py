"""文書ごとの上限で起点から外した同じ文書の候補の章節を、章節の拡張で読む（#5209）。

検索が文書を当てても、同じ文書の候補が「文書ごとの上限」を超えると、上位の候補でも起点から外れる。章節の拡張は
起点の所有の章節だけを読むので、外れた候補の章節（別の章節の注意点・現在の情報・未収録の添付の案内）は回答の材料に
入らなかった。所有の章節を読んだ後に、外れた候補の章節（子の章節を持たない章節）を残りの予算で読む。読めなくても
回答に出さず、診断（``unread_omitted``）にだけ残す。合成のスタブで、LLM と DB を呼ばない。
"""

from __future__ import annotations

from dataclasses import replace
from time import monotonic

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import section_expansion as sections
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.generation.answering import _expand_context_by_sections, capped_candidate_ids
from tests.test_section_expansion import (
    CHUNK_SET,
    DOC,
    LEAVES,
    QUESTION,
    SectionStub,
    TwoDocumentStub,
    _child,
    _child_ids,
    _expand,
    _members,
    _other_chunk,
    _seed_context,
    _settings,
)


def test_capped_candidate_section_is_read_after_the_minimum_scope():
    """外れた候補の章節（第4章）を、起点の所有の章節（3.1）の後に読み、回答に未反映の章節にしない。"""
    stub = SectionStub()

    result = _expand(_seed_context("c31a"), _settings(), stub, related=("c4a",))

    assert stub.read_calls == ["s3-1", "s4"]
    assert set(_members("s4")) <= set(_child_ids(result.context))
    assert result.stop_reason == sections.STOP_SCOPE_READ
    assert result.unread == [] and result.omitted == []
    assert [(item["section_id"], item["read"]) for item in result.trace["related"]] == [("s4", "whole")]
    added = [child for parent in result.context.evidence_tree for child in parent.children if child.record.chunk_uid == "c4a"]
    assert [child.role for child in added] == [sections.SECTION_CONTEXT_ROLE]


def test_capped_candidate_in_a_read_section_adds_nothing():
    """外れた候補の章節がすでに読んだ章節なら、もう一度読まない。"""
    stub = SectionStub()

    result = _expand(_seed_context("c31a"), _settings(), stub, related=("c31b",))

    assert stub.read_calls == ["s3-1"]
    assert result.trace["related"] == []


class ParentOwnerStub(SectionStub):
    """指定の chunk の所有の章節を、子の章節を持つ章節・文書全体にするスタブ。"""

    def __init__(self, owners: dict[str, str]) -> None:
        super().__init__()
        self.owners = owners

    def outline(self, *, chunk_uid: str, **options: object) -> sections.SectionOutline | None:
        outline = super().outline(chunk_uid=chunk_uid, **options)
        if outline is None or chunk_uid not in self.owners:
            return outline
        return replace(outline, owner_section_id=self.owners[chunk_uid])


def test_capped_candidate_owned_by_a_parent_section_is_not_read():
    """子の章節を持つ章節・文書全体は読まない（文書の全体を無制限に入れない）。"""
    stub = ParentOwnerStub({"c32a": "s3", "c4a": sections.ROOT_SECTION_ID})

    result = _expand(_seed_context("c2a"), _settings(), stub, related=("c32a", "c4a"))

    assert stub.read_calls == ["s2"]
    assert [(item["section_id"], item["read"]) for item in result.trace["related"]] == [
        ("s3", "not_leaf"),
        (sections.ROOT_SECTION_ID, "not_leaf"),
    ]
    assert result.unread == [] and result.omitted == []


def test_capped_candidate_over_the_budget_is_omitted_not_unread():
    """残りの予算に入らない章節は読まず、回答に出さない診断（omitted）にだけ残す。"""
    stub = SectionStub()
    # 入力の上限 1,000 token（2,000 の 50%）。起点の章節（2 chunk = 600 token）の後の残りは 400 token。
    settings = _settings(answer_max_input_tokens=2_000)

    result = _expand(_seed_context("c31a"), settings, stub, related=("c4a",))

    assert "s4" not in stub.read_calls or not set(_members("s4")) <= set(_child_ids(result.context))
    assert result.unread == []
    assert [(item.section_id, item.reason) for item in result.omitted] == [("s4", sections.STOP_TOKEN_BUDGET)]
    assert [(item["section_id"], item["read"]) for item in result.trace["related"]] == [("s4", "skipped")]


def test_capped_candidate_is_not_read_without_time_for_generation():
    """回答の生成に残す時間が無ければ読まず、時間の上限として診断に残す（予算の不足と分ける。#5184）。"""
    stub = SectionStub()
    remaining = iter([600.0, 100.0])

    result = _expand(
        _seed_context("c31a"),
        _settings(),
        stub,
        related=("c4a",),
        started_at=0.0,
        clock=lambda: 10.0,
        deadline_remaining=lambda: next(remaining, 100.0),
    )

    assert stub.read_calls == ["s3-1"]
    assert result.stop_reason == sections.STOP_SCOPE_READ
    assert result.unread == []
    assert [(item.section_id, item.reason) for item in result.omitted] == [("s4", sections.STOP_TIME_LIMIT)]


def test_capped_candidate_of_another_document_is_not_read():
    """起点の無い文書の候補は読まない（同じ文書の章節だけを補う）。"""
    stub = TwoDocumentStub()

    result = _expand(_seed_context("c31a"), _settings(), stub, related=("o2a",))

    assert stub.read_calls == ["s3-1"]
    assert result.trace["related"] == []


def _record(chunk_id: str):
    return _stored_chunk_answer_record(_other_chunk(chunk_id) if chunk_id.startswith("o") else _child(chunk_id))


def test_capped_candidate_ids_are_the_top_k_candidates_removed_by_the_per_document_cap():
    """上位 top_k 件のうち、文書ごとの上限で外した候補だけを返す（top_k の外・上限が無いときは返さない）。"""
    ranked = [_record(chunk) for chunk in ("c31a", "c32a", "c4a", "o1a", "c2a", "c33a")]
    context = replace(_seed_context("c31a"), ranked_candidates=tuple(ranked))
    capped = replace(get_settings(environ={}, dotenv_path=None), max_anchors_per_document=2)

    assert capped_candidate_ids(context, capped, top_k=5) == ["c4a", "c2a"]
    assert capped_candidate_ids(context, capped, top_k=3) == ["c4a"]
    assert capped_candidate_ids(context, replace(capped, max_anchors_per_document=0), top_k=5) == []


def test_answer_section_phase_reads_sections_of_capped_candidates():
    """回答の章節の拡張は、主の検索で文書ごとの上限から外した候補の章節を読む。"""
    stub = SectionStub()
    unused = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected I/O"))  # noqa: E731
    settings = replace(get_settings(environ={}, dotenv_path=None), max_anchors_per_document=1)
    context = replace(_seed_context("c31a"), ranked_candidates=(_record("c31a"), _record("c4a")))
    with bind_dependencies(
        AnswerDependencies(unused, unused, unused, unused, unused, section_outline=stub.outline, section_chunks=stub.chunks)
    ):
        expanded, result = _expand_context_by_sections(QUESTION, context, settings, rerank_enabled=False, started_at=monotonic(), top_k=8)

    assert stub.read_calls == ["s3-1", "s4"]
    assert set(LEAVES["s4"]) <= set(_child_ids(expanded))
    assert result.trace["related_seeds"] == ["c4a"]
    assert (result.trace["related"][0]["document_id"], result.trace["related"][0]["chunk_set_id"]) == (DOC, CHUNK_SET)
