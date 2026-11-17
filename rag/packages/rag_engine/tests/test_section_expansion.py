"""当たった chunk の章節から親の章節へ段階的に広げる（#1436）。

章節の一覧と章節の chunk はスタブ（``SectionStub``）、足りるかの判定（CRAG の評価器）はスタブの関数で、LLM と DB を
呼ばない。並列でも直列でも通るように、テストごとにスタブを作る（共有の状態を持たない）。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from citation_unit_support import canonical_fixture

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import grounded
from rag_engine.generation import section_expansion as sections
from rag_engine.generation.answer_models import AnswerContext
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.generation.answering import (
    _grounded_spans,
    synthesize_grounded_answer,
)
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import GroundedDraft
from rag_engine.models.storage import StoredChunk
from rag_engine.retrieval.context_builder import (
    ContextChildEvidence,
    ContextParentEvidence,
    context_bundle_from_parent_evidence,
)
from rag_engine.retrieval.evidence_selection import evidence_spans

DOC = "doc-1"
CHUNK_SET = "cs-1"
FILE = "点検要領.pdf"
# 1 chunk の本文（かな・漢字 300 字 = 見積もり 300 token）。
CHUNK_CHARS = 300

# 章節: 文書全体 > 第2章（p.2）・第3章（3.1 p.5 / 3.2 p.6 / 3.3 p.7）・第4章（p.8）。leaf は 2 chunk ずつ。
LEAVES = {
    "s2": ["c2a", "c2b"],
    "s3-1": ["c31a", "c31b"],
    "s3-2": ["c32a", "c32b"],
    "s3-3": ["c33a", "c33b"],
    "s4": ["c4a", "c4b"],
}
PAGES = {"s2": 2, "s3-1": 5, "s3-2": 6, "s3-3": 7, "s4": 8}


def _sections() -> tuple[sections.SectionInfo, ...]:
    def info(section_id, title, level, parent, members, page_start, page_end):
        return sections.SectionInfo(
            section_id=section_id,
            title=title,
            level=level,
            parent_section_id=parent,
            page_start=page_start,
            page_end=page_end,
            chunk_count=len(members),
            chars=len(members) * CHUNK_CHARS,
        )

    return (
        info("document", FILE, 0, None, _members("document"), 1, 8),
        info("s2", "第2章 点検の前の準備", 1, "document", _members("s2"), 2, 2),
        info("s3", "第3章 設備ごとの点検", 1, "document", _members("s3"), 5, 7),
        info("s3-1", "3.1 空調", 2, "s3", _members("s3-1"), 5, 5),
        info("s3-2", "3.2 電源", 2, "s3", _members("s3-2"), 6, 6),
        info("s3-3", "3.3 消火設備", 2, "s3", _members("s3-3"), 7, 7),
        info("s4", "第4章 点検を中止する条件", 1, "document", _members("s4"), 8, 8),
    )


def _members(section_id: str) -> list[str]:
    if section_id == "document":
        return [chunk for leaf in ("s2", "s3-1", "s3-2", "s3-3", "s4") for chunk in LEAVES[leaf]]
    if section_id == "s3":
        return [*LEAVES["s3-1"], *LEAVES["s3-2"], *LEAVES["s3-3"]]
    return list(LEAVES[section_id])


def _owner(chunk_id: str) -> str:
    return next(section for section, members in LEAVES.items() if chunk_id in members)


def _page(chunk_id: str) -> int:
    return PAGES[_owner(chunk_id)]


def _text(chunk_id: str) -> str:
    head = f"本文{chunk_id}："
    return head + "点" * (CHUNK_CHARS - len(head))


def _child(chunk_id: str) -> StoredChunk:
    return StoredChunk(
        chunk_uid=chunk_id,
        chunk_id=chunk_id,
        chunk_level="child",
        chunk_seq=0,
        parent_chunk_uid=f"{DOC}:{chunk_id}",
        parent_chunk_id=f"{DOC}:{chunk_id}",
        child_chunk_ids=(),
        text=_text(chunk_id),
        retrieval_text=_text(chunk_id),
        source_run_id="run",
        source_file_name=FILE,
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=_page(chunk_id),
        page_end=_page(chunk_id),
        source_seq_ranges=(),
        source_record_refs=(),
        metadata={"section_path": [_owner(chunk_id)], "document_id": DOC},
    )


def _parent(chunk_id: str) -> StoredChunk:
    child = _child(chunk_id)
    return replace(
        child,
        chunk_uid=f"{DOC}:{chunk_id}",
        chunk_id=f"{DOC}:{chunk_id}",
        chunk_level="parent",
        parent_chunk_uid="",
        parent_chunk_id="",
        child_chunk_ids=(chunk_id,),
    )


class SectionStub:
    """章節の一覧と章節の chunk のスタブ（呼び出しを記録する）。"""

    def __init__(self, *, mapped: bool = True) -> None:
        self.mapped = mapped
        self.outline_calls: list[str] = []
        self.read_calls: list[str] = []

    def outline(self, *, chunk_uid: str, **_: object) -> sections.SectionOutline | None:
        self.outline_calls.append(chunk_uid)
        if not self.mapped:
            return None
        return sections.SectionOutline(
            document_id=DOC, chunk_set_id=CHUNK_SET, owner_section_id=_owner(chunk_uid), sections=_sections(), file_name=FILE
        )

    def chunks(self, *, document_id: str, chunk_set_id: str, section_id: str, **_: object) -> list[StoredChunk]:
        assert (document_id, chunk_set_id) == (DOC, CHUNK_SET)
        self.read_calls.append(section_id)
        members = _members(section_id)
        return [*(_child(chunk) for chunk in members), *(_parent(chunk) for chunk in members)]


def _seed_context(*chunk_ids: str) -> AnswerContext:
    """検索で当たった chunk（retrieved_anchor）を親ごとに 1 つずつ持つ文脈。"""
    tree = tuple(
        ContextParentEvidence(
            record=_stored_chunk_answer_record(_parent(chunk_id)),
            role="synthesis_parent",
            reason="retrieved",
            children=(
                ContextChildEvidence(
                    record=_stored_chunk_answer_record(_child(chunk_id)), role="retrieved_anchor", reason="retrieved", retrieval_rank=rank
                ),
            ),
        )
        for rank, chunk_id in enumerate(chunk_ids, 1)
    )
    bundle = context_bundle_from_parent_evidence(tree, max_chars=24000)
    return AnswerContext(records=list(bundle.records), text=bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree)


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "section_expansion_enabled": True,
        "section_expansion_max_levels": 4,
        "section_expansion_input_token_percent": 50,
        "evidence_budget_chars": 48000,
        "answer_max_input_tokens": 128_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


QUESTION = "第3章の設備ごとの点検の項目を教えて"
# 読む範囲が文書全体になる列挙の問（親の章節まで上がる段の確認に使う）。


def _expand(
    context: AnswerContext,
    settings: SimpleNamespace,
    stub: SectionStub,
    *,
    question: str = QUESTION,
    **options: object,
) -> sections.SectionExpansionResult:
    return sections.expand_sections(
        question,
        context,
        settings,
        section_outline=stub.outline,
        section_chunks=stub.chunks,
        **options,
    )


def _child_ids(context: AnswerContext) -> list[str]:
    return [child.record.chunk_uid for parent in context.evidence_tree for child in parent.children]


def test_keeps_most_relevant_first_and_reading_order_with_chunk_ids():
    stub = SectionStub()

    result = _expand(_seed_context("c31a"), _settings(), stub)

    # 当たった chunk（最も関連の高い根拠）を先頭に、章節の chunk を章節の中の読み順で後ろに置く。
    assert _child_ids(result.context) == ["c31a", "c31b"]
    # 引用の ID は chunk のまま（章節の id や合成の id を作らない）。
    added = [child for parent in result.context.evidence_tree[1:] for child in parent.children]
    assert {child.role for child in added} == {sections.SECTION_CONTEXT_ROLE}
    assert all(child.record.chunk_id == child.record.chunk_uid for child in added)
    assert [record.chunk_uid for record in result.context.records][0] == f"{DOC}:c31a"


class OwnerStub(SectionStub):
    """当たった chunk の所有の章節を、子の章節を持つ章節にするスタブ（章の前書きに当たった chunk など）。"""

    def __init__(self, owner: str) -> None:
        super().__init__()
        self.owner = owner

    def outline(self, *, chunk_uid: str, **options: object) -> sections.SectionOutline | None:
        outline = super().outline(chunk_uid=chunk_uid, **options)
        return replace(outline, owner_section_id=self.owner) if outline is not None else None


def test_elapsed_time_does_not_impose_a_separate_expansion_deadline():
    stub = SectionStub()
    result = _expand(_seed_context("c31a"), _settings(), stub, started_at=0.0, clock=lambda: 900.0)
    assert result.applied
    assert result.stop_reason != sections.STOP_TIME_LIMIT


def test_answer_deadline_also_limits_time():
    stub = SectionStub()

    result = _expand(_seed_context("c31a"), _settings(), stub, started_at=0.0, clock=lambda: 10.0, deadline_remaining=lambda: 60.0)

    assert result.stop_reason == sections.STOP_TIME_LIMIT and stub.read_calls == []


def _climbs(settings: SimpleNamespace, remaining: float) -> bool:
    """回答の期限の残りが ``remaining`` 秒のとき、1 段目（所有の章節）を読むか。"""
    stub = SectionStub()
    result = _expand(
        _seed_context("c31a"),
        settings,
        stub,
        started_at=0.0,
        clock=lambda: 10.0,
        deadline_remaining=lambda: remaining,
    )
    climbs = stub.read_calls != []
    if not climbs:
        assert result.stop_reason == sections.STOP_TIME_LIMIT and result.levels == 0
    return climbs


def test_answer_deadline_leaves_one_generation_round():
    """回答の期限の残りが、1 段の分（20 秒）と生成の分（1 回の見積もり + 確定の 30 秒）より少なければ上がらない。

    生成の 1 回の見積もりの既定は 120 秒（#5184）なので、境界は 20 + 120 + 30 = 170 秒。
    """
    settings = _settings()
    assert sections.SectionExpansionLimits.from_settings(settings).generation_reserve_seconds == 120.0 + 30.0
    assert sections.DEFAULT_LEVEL_SECONDS == 20.0
    assert not _climbs(settings, 169.0)
    assert _climbs(settings, 170.0)
    # 総期限 600 秒で文書検索に 424 秒・327 秒かかった後の残り（176 秒・273 秒）でも、1 段目を読む。
    # 以前の固定の見積もり 240 秒（境界 290 秒）では、どちらも読まずに止まった。
    assert _climbs(settings, 600.0 - 424.0)
    assert _climbs(settings, 600.0 - 327.0)


def test_generation_round_estimate_comes_from_settings():
    """生成に残す分は設定の answer_generation_round_estimate_seconds で決まる（#5184。固定の値を使わない）。"""
    slow = _settings(answer_generation_round_estimate_seconds=240.0)
    assert sections.SectionExpansionLimits.from_settings(slow).generation_reserve_seconds == 240.0 + 30.0
    assert not _climbs(slow, 289.0)
    assert _climbs(slow, 290.0)
    assert not _climbs(slow, 600.0 - 327.0)
    fast = _settings(answer_generation_round_estimate_seconds=60.0)
    assert not _climbs(fast, 109.0)
    assert _climbs(fast, 110.0)


def test_disabled_setting_does_nothing():
    stub = SectionStub()

    result = _expand(_seed_context("c31a"), _settings(section_expansion_enabled=False), stub)

    assert result.stop_reason == sections.STOP_DISABLED and stub.outline_calls == []


def test_estimate_tokens():
    assert sections.estimate_tokens("点検") == 2
    assert sections.estimate_tokens("abcd") == 1
    assert sections.estimate_tokens("UPS の点検") == 1 + 3  # "UPS " は 4 字で 1 token


def test_whole_records_keep_section_text_when_it_fits():
    records = [_stored_chunk_answer_record(_parent(chunk)) for chunk in ("c31a", "c31b", "c32a")]
    records[0] = replace(records[0], text=records[0].text * 4)  # 1,200 字の親（平均より長い）
    cut = evidence_spans("消火設備", records, budget=2400)
    whole = evidence_spans("消火設備", records, budget=2400, whole_records=True)
    assert sum(len(span["text"]) for span in cut) < sum(len(r.text) for r in records)
    assert sum(len(span["text"]) for span in whole) == sum(len(r.text) for r in records)
    # 入らないときは通常の配分（予算を超えない）。
    over = evidence_spans("消火設備", records, budget=1000, whole_records=True)
    assert sum(len(span["text"]) for span in over) <= 1000


def test_unread_sections_make_the_answer_conditional():
    context = _seed_context("c31a")
    spans = _grounded_spans("質問", context, (), None)
    evidence_id = spans[0]["evidence_id"]
    quote = spans[0]["text"][:20]
    draft = {
        "summary": "空調の点検の項目です。",
        "confidence": "high",
        "items": [{"kind": "rule", "text": quote, "citations": [{"evidence_id": evidence_id, "quote": quote}]}],
    }
    audit = {
        "goal_alignment": "aligned",
        "summary_supported": True,
        "reviews": [{"index": 0, "support": "supported", "applicability": "matched", "condition": "", "reason": "r"}],
        "request_reviews": [{"request_id": "Q1", "status": "addressed", "reason": "合成の要求に回答済み"}],
        "unused_evidence_ids": [],
    }

    def model(system, prompt, settings, schema, **options):
        if getattr(schema, "_uses_full_answer", False) or issubclass(schema, (GroundedDraft, generation_draft_wire_model())):
            # 既存quote fixtureのcanonical再生。wire出力の検証とは別。
            return canonical_fixture(draft)
        return schema.model_validate(audit)

    unread = [f"「{FILE}」の「第3章 設備ごとの点検」（p.5〜7）は、読み込み量の上限に達したため未反映です。"]
    unused = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected I/O"))  # noqa: E731
    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        plain = synthesize_grounded_answer("質問", context, get_settings(), image_prompt_mode="text_only")
        limited = synthesize_grounded_answer("質問", context, get_settings(), image_prompt_mode="text_only", unread_sections=unread)
    assert plain.response.envelope["outcome"] == "answered"
    assert limited.response.envelope["outcome"] == "conditional"
    assert unread[0] in limited.response.envelope["gaps"]
    assert f"{grounded.answer_labels().unverified_section}\n\n・{unread[0]}" in limited.response.answer_text
    assert limited.response.needs_human_review is True


@pytest.mark.parametrize(
    "percent, model, expected",
    [(50, 128_000, 64_000), (25, 128_000, 32_000), (90, 200_000, 180_000), (50, None, 64_000)],
    ids=["50", "25", "90-large-model", "default-model"],
)
def test_input_token_limit_is_share_of_model_input(percent, model, expected):
    """根拠の入力の上限は、回答のモデルの入力の上限（共通のモデル設定。#1437）に対する割合。"""
    overrides = {"section_expansion_input_token_percent": percent}
    if model is not None:
        overrides["answer_max_input_tokens"] = model
    settings = _settings(**overrides)
    if model is None:
        del settings.answer_max_input_tokens
    limits = sections.SectionExpansionLimits.from_settings(settings)
    assert limits.input_tokens == expected
    # 根拠の文字数の予算（evidence_budget_chars）には依らない（#1436 の暫定の推定を置き換えた）。
    assert sections.SectionExpansionLimits.from_settings(_settings(**overrides, evidence_budget_chars=1000)).input_tokens == (
        expected if model else 64_000
    )


def test_engine_settings_read_section_expansion_environment():
    settings = get_settings(
        environ={
            "RAG_ENGINE_SECTION_EXPANSION": "0",
            "RAG_ENGINE_SECTION_EXPANSION_MAX_LEVELS": "2",
            "RAG_ENGINE_SECTION_EXPANSION_INPUT_TOKEN_PERCENT": "30",
        },
        dotenv_path=None,
    )
    assert (
        settings.section_expansion_enabled,
        settings.section_expansion_max_levels,
        settings.section_expansion_input_token_percent,
    ) == (False, 2, 30)
    defaults = get_settings(environ={}, dotenv_path=None)
    assert defaults.section_expansion_enabled and defaults.section_expansion_max_levels == 4


def test_engine_settings_read_generation_round_estimate_environment():
    """生成の 1 回の見積もりは backend が RAG_ENGINE_ANSWER_GENERATION_ROUND_ESTIMATE_SECONDS で渡す（#5184。既定 120）。"""
    assert get_settings(environ={}, dotenv_path=None).answer_generation_round_estimate_seconds == 120.0
    settings = get_settings(environ={"RAG_ENGINE_ANSWER_GENERATION_ROUND_ESTIMATE_SECONDS": "200"}, dotenv_path=None)
    assert settings.answer_generation_round_estimate_seconds == 200.0
    assert sections.SectionExpansionLimits.from_settings(settings).generation_reserve_seconds == 200.0 + 30.0


OTHER_DOC = "doc-2"
OTHER_FILE = "別の文書.pdf"
OTHER_LEAVES = {"o1": ["o1a", "o1b"], "o2": ["o2a", "o2b"]}


def _other_sections() -> tuple[sections.SectionInfo, ...]:
    def info(section_id, title, level, parent, members, page):
        return sections.SectionInfo(
            section_id=section_id,
            title=title,
            level=level,
            parent_section_id=parent,
            page_start=page,
            page_end=page if section_id != "document" else 2,
            chunk_count=len(members),
            chars=len(members) * CHUNK_CHARS,
        )

    every = [chunk for members in OTHER_LEAVES.values() for chunk in members]
    return (
        info("document", OTHER_FILE, 0, None, every, 1),
        info("o1", "1章 別の操作", 1, "document", OTHER_LEAVES["o1"], 1),
        info("o2", "2章 別の設定", 1, "document", OTHER_LEAVES["o2"], 2),
    )


def _other_chunk(chunk_id: str, *, parent: bool = False) -> StoredChunk:
    owner = next(section for section, members in OTHER_LEAVES.items() if chunk_id in members)
    child = replace(
        _child("c31a"),
        chunk_uid=chunk_id,
        chunk_id=chunk_id,
        parent_chunk_uid=f"{OTHER_DOC}:{chunk_id}",
        parent_chunk_id=f"{OTHER_DOC}:{chunk_id}",
        text=_text(chunk_id),
        retrieval_text=_text(chunk_id),
        source_file_name=OTHER_FILE,
        page_start=int(owner[1:]),
        page_end=int(owner[1:]),
        metadata={"section_path": [owner], "document_id": OTHER_DOC},
    )
    if not parent:
        return child
    return replace(
        child,
        chunk_uid=f"{OTHER_DOC}:{chunk_id}",
        chunk_id=f"{OTHER_DOC}:{chunk_id}",
        chunk_level="parent",
        parent_chunk_uid="",
        parent_chunk_id="",
        child_chunk_ids=(chunk_id,),
    )


class TwoDocumentStub(SectionStub):
    """業務ガイドが参照する文書（DOC）と別の文書（OTHER_DOC）の 2 つの文書のスタブ。"""

    def outline(self, *, chunk_uid: str, **options: object) -> sections.SectionOutline | None:
        if chunk_uid not in {chunk for members in OTHER_LEAVES.values() for chunk in members}:
            return super().outline(chunk_uid=chunk_uid, **options)
        self.outline_calls.append(chunk_uid)
        owner = next(section for section, members in OTHER_LEAVES.items() if chunk_uid in members)
        return sections.SectionOutline(
            document_id=OTHER_DOC, chunk_set_id=CHUNK_SET, owner_section_id=owner, sections=_other_sections(), file_name=OTHER_FILE
        )

    def chunks(self, *, document_id: str, chunk_set_id: str, section_id: str, **options: object) -> list[StoredChunk]:
        if document_id != OTHER_DOC:
            return super().chunks(document_id=document_id, chunk_set_id=chunk_set_id, section_id=section_id, **options)
        self.read_calls.append(f"{OTHER_DOC}/{section_id}")
        members = (
            [chunk for members in OTHER_LEAVES.values() for chunk in members] if section_id == "document" else OTHER_LEAVES[section_id]
        )
        return [*(_other_chunk(chunk) for chunk in members), *(_other_chunk(chunk, parent=True) for chunk in members)]


def _two_document_context(*chunk_ids: str) -> AnswerContext:
    """2 つの文書の当たった chunk を、渡した順（関連度の順）に持つ文脈。"""
    tree = tuple(
        ContextParentEvidence(
            record=_stored_chunk_answer_record(_other_chunk(chunk_id, parent=True) if chunk_id.startswith("o") else _parent(chunk_id)),
            role="synthesis_parent",
            reason="retrieved",
            children=(
                ContextChildEvidence(
                    record=_stored_chunk_answer_record(_other_chunk(chunk_id) if chunk_id.startswith("o") else _child(chunk_id)),
                    role="retrieved_anchor",
                    reason="retrieved",
                    retrieval_rank=rank,
                ),
            ),
        )
        for rank, chunk_id in enumerate(chunk_ids, 1)
    )
    bundle = context_bundle_from_parent_evidence(tree, max_chars=24000)
    return AnswerContext(records=list(bundle.records), text=bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree)


# 業務ガイドの手順が参照する頁（第3章 p.5〜7）。
GUIDE_PAGES = (sections.PriorityRange(DOC, 5, 7),)


def test_priority_rank_prefers_referenced_pages_then_the_document():
    outline = SectionStub().outline(chunk_uid="c4a")
    assert outline is not None
    # 第4章（p.8）は参照の頁（p.5〜7）の外だが、参照の文書の起点。頁の無い参照は文書の全体。
    assert sections._priority_rank(outline, GUIDE_PAGES) == sections._RANK_DOCUMENT
    assert sections._priority_rank(replace(outline, owner_section_id="s3-2"), GUIDE_PAGES) == sections._RANK_PAGES
    assert sections._priority_rank(outline, (sections.PriorityRange(DOC),)) == sections._RANK_PAGES
    assert sections._priority_rank(outline, (sections.PriorityRange(OTHER_DOC, 1, 2),)) == sections._RANK_OTHER


def _unread(document_id: str, section_id: str) -> sections.UnreadSection:
    return sections.UnreadSection(document_id, section_id, f"「{document_id}」の「{section_id}」", sections.STOP_TOKEN_BUDGET, 1)


def test_answer_unread_view_keeps_cited_and_guide_documents_only():
    unread = [_unread(DOC, "s3-2"), _unread(OTHER_DOC, "o1"), _unread("doc-3", "x1"), "文の行。"]

    view = sections.answer_unread_view(unread, {DOC}, {"doc-3"})

    assert view.lines == (unread[0].gap_line(), unread[2].gap_line(), "文の行。")
    assert [item.section_id for item in view.hidden] == ["o1"] and view.overflow == 0
    # 回答が根拠にした文書も参照の文書も無ければ（根拠を示せなかった回答）絞らない。
    assert len(sections.answer_unread_view(unread, set()).lines) == 4


def test_answer_unread_view_caps_the_lines():
    unread = [_unread(DOC, f"s{index}") for index in range(sections.MAX_UNREAD_LINES + 3)]

    view = sections.answer_unread_view(unread, {DOC})

    assert len(view.lines) == sections.MAX_UNREAD_LINES + 1 and view.overflow == 3
    assert view.lines[-1] == "ほか 3 件の関連章節も未反映です。"
    assert view.to_payload() == {"shown": sections.MAX_UNREAD_LINES + 1, "hidden": [], "overflow": 3}


def test_guide_plan_reference_ranges():
    from rag_engine.retrieval.guide_steps import GuidePageRange, GuidePlan, GuideStep, GuideStepReference

    plan = GuidePlan(
        guide_id="g",
        revision=1,
        steps=(
            GuideStep("1", "手順1", references=(GuideStepReference(DOC, page_ranges=(GuidePageRange(5, 7),)),)),
            GuideStep(
                "2", "手順2", references=(GuideStepReference(OTHER_DOC), GuideStepReference(DOC, page_ranges=(GuidePageRange(5, 7),)))
            ),
        ),
    )

    assert plan.reference_ranges == ((DOC, 5, 7), (OTHER_DOC, None, None))


def test_answer_section_phase_passes_the_guide_priority():
    from time import monotonic

    from rag_engine.generation.answering import _expand_context_by_sections

    stub = TwoDocumentStub()
    unused = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected I/O"))  # noqa: E731
    settings = get_settings(environ={}, dotenv_path=None)
    with bind_dependencies(
        AnswerDependencies(unused, unused, unused, unused, unused, section_outline=stub.outline, section_chunks=stub.chunks)
    ):
        _context, result = _expand_context_by_sections(
            QUESTION,
            _two_document_context("o1a", "c31a"),
            settings,
            rerank_enabled=False,
            started_at=monotonic(),
            priority=((DOC, 5, 7),),
        )

    assert [seed["document_id"] for seed in result.trace["deferred_seeds"]] == [OTHER_DOC]
    assert stub.read_calls[0] == "s3-1"


def test_section_over_the_input_budget_is_reported_as_unread():
    from time import monotonic

    from rag_engine.generation.answering import _expand_context_by_sections

    stub = SectionStub()
    unused = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected I/O"))  # noqa: E731
    settings = replace(
        get_settings(environ={}, dotenv_path=None),
        section_expansion_max_levels=4,
        section_expansion_input_token_percent=50,
        answer_max_input_tokens=800,
    )
    with bind_dependencies(
        AnswerDependencies(unused, unused, unused, unused, unused, section_outline=stub.outline, section_chunks=stub.chunks)
    ):
        _, result = _expand_context_by_sections(
            QUESTION,
            _seed_context("c31a"),
            settings,
            rerank_enabled=False,
            started_at=monotonic(),
        )
    assert result.unread or result.omitted


def _reader_for(
    stub: SectionStub, empty: sections.SectionInfo, section: str = "s2"
) -> tuple[sections.SectionOutline, sections.SectionReader]:
    outline = stub.outline(chunk_uid=LEAVES[section][0])
    assert outline is not None
    outline = replace(outline, sections=(*outline.sections, empty))
    reader = sections.SectionReader(stub.chunks, set())
    return outline, reader


def _heading_only(section_id: str = "s2-h", *, chunk_count: int = 0) -> sections.SectionInfo:
    return sections.SectionInfo(
        section_id=section_id,
        title="見出しだけの節",
        level=2,
        parent_section_id="s2",
        page_start=2,
        page_end=4,
        chunk_count=chunk_count,
        chars=0,
    )


def test_a_section_without_chunks_has_nothing_to_read_and_is_not_unread():
    """#4138: chunk を持たない章節（見出しだけ）は読むものが無い。読めなかった章節にしない。"""

    class Stub(SectionStub):
        def chunks(self, *, section_id: str, **options: object) -> list[StoredChunk]:
            if section_id == "s2-h":
                return sections.SectionRead(complete=True)
            return super().chunks(section_id=section_id, **options)

    empty = _heading_only()
    outline, reader = _reader_for(Stub(), empty)
    assert reader.read(outline, empty) == []
    assert reader.section_key(outline, empty) not in reader.failures


@pytest.mark.parametrize(
    "response",
    [sections.SectionRead(complete=False, reason="partial_read"), sections.SectionRead(complete=True)],
    ids=["not_readable", "empty_response"],
)
def test_a_section_with_chunks_that_returns_nothing_stays_unread(response: sections.SectionRead):
    """#4138: chunk があるのに読めない章節は、未確認の範囲として残す（案内を消さない）。"""

    class Stub(SectionStub):
        def chunks(self, *, section_id: str, **options: object) -> list[StoredChunk]:
            if section_id == "s2-h":
                return response
            return super().chunks(section_id=section_id, **options)

    section = _heading_only(chunk_count=2)
    outline, reader = _reader_for(Stub(), section)
    assert reader.read(outline, section) == []
    assert reader.failures[reader.section_key(outline, section)] == sections.STOP_PARTIAL


def test_reference_full_read_evidence_is_not_a_section_seed():
    """参照先の文書を全体で読む退避で足した根拠（参照の印の section_seed が偽。#5263）は起点にしない。"""
    context = _seed_context("c31a", "c32a")
    tree = list(context.evidence_tree)
    child = tree[1].children[0]
    record = replace(
        child.record,
        metadata={**child.record.metadata, "reference_expansion": {"from_chunk_uid": "x", "section_seed": False}},
    )
    tree[1] = replace(tree[1], children=(replace(child, record=record),))
    context = replace(context, evidence_tree=tuple(tree))
    stub = SectionStub()

    assert sections.seed_chunk_ids(context) == ["c31a"]
    _expand(context, _settings(), stub)
    assert stub.outline_calls == ["c31a"]
    # 印の無い参照の根拠（目次から選んだ章節など）は今どおり起点にする。
    record = replace(child.record, metadata={**child.record.metadata, "reference_expansion": {"from_chunk_uid": "x"}})
    tree[1] = replace(tree[1], children=(replace(child, record=record),))
    assert sections.seed_chunk_ids(replace(context, evidence_tree=tuple(tree))) == ["c31a", "c32a"]
