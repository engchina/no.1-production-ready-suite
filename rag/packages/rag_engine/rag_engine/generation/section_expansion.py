"""当たった chunk の章節から、親の章節へ段階的に広げて回答の文脈に入れる（#1436）。

章節の正本は文書の「章節ナビゲーション」で、章節と chunk の対応（#1438）は呼び出し元（backend）が持つ。この module は
対応を ``AnswerDependencies.section_outline`` / ``section_chunks`` から読み、広げる順・予算・止める条件だけを決める
（I/O と LLM は注入された関数と ``grade`` を通す。決定論のテストはスタブで行う）。

広げる順は、当たった chunk → 所有の章節（leaf）→ 親の章節 → … → 最上位の章節 → 文書全体（根の章節）。

- 1 段上げる前に、既存の CRAG の十分性の判定（``grade``）で足りるかを確かめ、足りれば止める。最初の段の前は、CRAG の
  最後の判定を使う（評価器の呼び出しを増やさない）。足りるは「十分と判定され、内容の観点（``CONTENT_ASPECTS``）に
  確かめられていないものが無い」。適用の観点（applicability）は利用者の状況の確認で、章節を読んでも埋まらないため
  広げる理由にしない。
- 列挙・網羅の問（#1503。``retrieval.enumeration``）は、件数・章の範囲の欠けを評価器の項目ごとの根拠から数え、
  欠ければ内容の観点 ``enumeration_coverage`` が確かめられていないとして広げる。件数も範囲も無い網羅の問
  （「〜要領で、…をすべて挙げて」）は根拠の数から足りるかを決められないため、評価器が足りると判定しても、
  読む範囲（``minimum_scope``。当たった章節 = leaf、文書全体 = 根の章節）まで読んでから判定する。読む範囲に
  届くまでの段は評価器を呼ばない（段ごとの LLM の呼び出しを増やさない）。上限（段・token・時間）は変えない。
- 上の段の章節は、予算に入れば全体を読む。入らなければ、子の章節の見出し（目次）を質問との関連度（rerank。使えなければ
  語の重なり）で並べ、関連の高い子の章節から、予算に入る章節を全体で読む。入らない子の章節は読まない。
- 全体を読む必要があるのに予算に入らない章節は、第 3 段階（#1437。抽出してから統合）の差し込み口
  （``large_sections``。``section_extraction.SectionExtractor``）に渡し、質問に関係する原文だけを抜き出して文脈に
  入れる。差し込み口が無ければ予算で打ち切る。
- 上限（入力 token・段の数・時間）で止めたときは、読めなかった章節を返し、呼び出し元は回答を条件つき
  （``conditional``）にして「確かめられていない点」に出す。

入力 token の上限は、回答のモデルの入力の上限（共通のモデル設定の登録モデルの「入力の上限（tokens）」。#1437。
backend が platform の ``enterprise_ai_max_input_tokens`` で決めて ``answer_max_input_tokens`` で渡す）に対する割合
（既定 50%）。根拠の本文の token は ``estimate_tokens``（platform の ``estimate_input_tokens`` と同じ見積もり）で
数える。文脈の並びは、もとの根拠（関連度の順）を先頭に、広げた章節をその後ろに章節の中の読み順で置く。引用の ID は
chunk のまま（章節の chunk は検索の chunk と同じ親子の record にする）。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any

from rag_engine.generation.answer_models import AnswerContext, CragRetrievalAttempt
from rag_engine.generation.answer_records import AnswerRecord, _stored_chunk_answer_record
from rag_engine.retrieval.enumeration import ENUMERATION_ASPECT, minimum_scope
from rag_engine.retrieval.context_builder import (
    ContextChildEvidence,
    ContextParentEvidence,
    context_bundle_from_parent_evidence,
)

# 根の章節（文書全体）の id。backend の ``section_chunk_map.ROOT_SECTION_ID`` と同じ。
ROOT_SECTION_ID = "document"
# 広げた章節の根拠の役割（context の parent の理由と child の役割）。
SECTION_EXPANSION_REASON = "section_expansion"
SECTION_CONTEXT_ROLE = "section_context"
# 「足りる」の判定で、確かめられていないと広げる内容の観点（``task_contract`` の required_aspects のうち）。
CONTENT_ASPECTS = frozenset({"requested_result", "procedure", "member_details", "population_membership",
                             ENUMERATION_ASPECT})
# 起点にする章節の数（関連の高い当たった chunk の所有の章節から、重ならないもの）。
MAX_SEED_SECTIONS = 2
# 1 つの章節で読む chunk の上限（文書全体でも、これより多い章節は予算に関係なく読まない）。
MAX_SECTION_CHUNKS = 400
# モデルの入力の上限の既定（tokens。platform の DEFAULT_ENTERPRISE_AI_MAX_INPUT_TOKENS と同じ。#1437）。
DEFAULT_MODEL_INPUT_TOKENS = 128_000
# 回答の生成（生成・監査・是正）に残す秒数。これより残りが少なければ上がらない。
GENERATION_RESERVE_SECONDS = 150.0
# 次の段の見積もりの秒数（評価器の 1 回の実測が無いとき）。
DEFAULT_LEVEL_SECONDS = 20.0
_ASCII = re.compile(r"[\x00-\x7f]")

# 止めた理由。LIMIT_STOPS は上限で止めた（読めなかった章節があれば条件つきの回答にする）。
STOP_SUFFICIENT = "sufficient"
STOP_DOCUMENT = "document_read"
STOP_MAX_LEVELS = "max_levels"
STOP_TOKEN_BUDGET = "token_budget"
STOP_TIME_LIMIT = "time_limit"
STOP_NO_SECTIONS = "no_sections"
STOP_GRADE_ERROR = "grade_error"
STOP_DISABLED = "disabled"
# 抽出してから統合（#1437）で読めなかった範囲の理由（章節の拡張の止めた理由には使わない）。
STOP_EXTRACTION_CALLS = "extraction_calls"
STOP_EXTRACTION_ERROR = "extraction_error"
STOP_SECTION_CHUNKS = "section_chunks"
LIMIT_STOPS = frozenset({STOP_MAX_LEVELS, STOP_TOKEN_BUDGET, STOP_TIME_LIMIT})
_LIMIT_LABELS = {
    STOP_MAX_LEVELS: "広げる段の上限",
    STOP_TOKEN_BUDGET: "根拠の入力の上限",
    STOP_TIME_LIMIT: "回答の時間の上限",
    STOP_EXTRACTION_CALLS: "原文を抜き出す呼び出しの上限",
    STOP_SECTION_CHUNKS: "1 つの章節で読む chunk の上限",
}


@dataclass(frozen=True)
class SectionInfo:
    """章節の一覧の 1 つ（根の章節を含む）。``chars`` は章節に入る chunk の本文の文字数（子の章節の分を含む）。"""

    section_id: str
    title: str
    level: int
    parent_section_id: str | None
    page_start: int | None = None
    page_end: int | None = None
    chunk_count: int = 0
    chars: int = 0


@dataclass(frozen=True)
class SectionOutline:
    """chunk の文書（chunk_set）の章節の一覧と、その chunk の所有の章節。"""

    document_id: str
    chunk_set_id: str
    owner_section_id: str
    sections: tuple[SectionInfo, ...]
    file_name: str = ""

    def get(self, section_id: str | None) -> SectionInfo | None:
        return next((item for item in self.sections if item.section_id == section_id), None) if section_id else None

    def parent(self, section: SectionInfo) -> SectionInfo | None:
        return self.get(section.parent_section_id)

    def children(self, section_id: str) -> list[SectionInfo]:
        return [item for item in self.sections if item.parent_section_id == section_id]

    def chain(self) -> list[SectionInfo]:
        """所有の章節から根の章節までの列（所有の章節が無ければ空）。"""
        chain: list[SectionInfo] = []
        current = self.get(self.owner_section_id)
        while current is not None and current not in chain:
            chain.append(current)
            current = self.parent(current)
        return chain

    def path(self, section: SectionInfo) -> list[str]:
        """根の下から章節までの名前の列（根の章節は空）。"""
        titles: list[str] = []
        current: SectionInfo | None = section
        while current is not None and current.section_id != ROOT_SECTION_ID and len(titles) < 32:
            titles.append(current.title)
            current = self.parent(current)
        return list(reversed(titles))


@dataclass(frozen=True)
class UnreadSection:
    """上限で読めなかった章節（「確かめられていない点」に出す）。"""

    document_id: str
    section_id: str
    label: str
    reason: str

    def gap_line(self) -> str:
        if self.reason == STOP_EXTRACTION_ERROR:
            return f"{self.label}は、原文を抜き出せなかったため読んでいません。"
        return f"{self.label}は、{_LIMIT_LABELS.get(self.reason, '上限')}に達したため読んでいません。"

    def to_payload(self) -> dict[str, Any]:
        return {"document_id": self.document_id, "section_id": self.section_id, "label": self.label,
                "reason": self.reason}


@dataclass(frozen=True)
class SectionExpansionLimits:
    """拡張の上限（検索・回答プロファイルで変えられる）。``input_tokens`` は根拠の本文の入力 token の上限。"""

    max_levels: int
    input_tokens: int
    time_limit_seconds: float

    @classmethod
    def from_settings(cls, settings: Any) -> SectionExpansionLimits:
        percent = max(1, min(100, int(getattr(settings, "section_expansion_input_token_percent", 50) or 50)))
        return cls(
            max_levels=max(0, int(getattr(settings, "section_expansion_max_levels", 4))),
            input_tokens=model_input_tokens(settings) * percent // 100,
            time_limit_seconds=max(0.0, float(getattr(settings, "section_expansion_time_limit_seconds", 600.0))),
        )


@dataclass
class SectionExpansionResult:
    """拡張の結果。``unread`` は上限で読めなかった章節（空なら条件つきにしない）。"""

    context: AnswerContext
    applied: bool = False
    stop_reason: str = ""
    levels: int = 0
    used_tokens: int = 0
    unread: list[UnreadSection] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)

    @property
    def gap_lines(self) -> list[str]:
        return [item.gap_line() for item in self.unread]


@dataclass(frozen=True)
class LargeSectionRequest:
    """全体を読む必要があるのに予算に入らない章節（第 3 段階 #1437 の抽出してから統合に渡す）。

    ``tree`` はまだ文脈に無い章節の親（章節の中の読み順）、``remaining_tokens`` は文脈に足せる残りの入力 token、
    ``seed_positions`` は同じ文書の当たった chunk の位置（``seq_no``。近い分から先に読む）、``seconds_left`` は抽出に使える残りの秒数
    （回答の時間の上限から、生成と次の判定の分を引いた値）、``truncated`` は章節の chunk が読む上限を超えて
    先頭だけを読んだこと。
    """

    question: str
    outline: SectionOutline
    section: SectionInfo
    tree: tuple[ContextParentEvidence, ...]
    remaining_tokens: int
    seed_positions: tuple[int, ...] = ()
    seconds_left: Callable[[], float] = lambda: math.inf
    truncated: bool = False


@dataclass
class LargeSectionOutcome:
    """抽出してから統合の結果。``tree`` は文脈に足す根拠、``unread`` は上限で読めなかった範囲。"""

    tree: tuple[ContextParentEvidence, ...] = ()
    unread: list[UnreadSection] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


# (章節の要求) を受け取り、抜き出した根拠（読めなかった範囲つき）を返す。None は読まない（予算で打ち切る）。
LargeSectionHandler = Callable[[LargeSectionRequest], LargeSectionOutcome | None]


def no_large_section_handler(request: LargeSectionRequest) -> LargeSectionOutcome | None:
    """抽出してから統合を使わない差し込み口。読まずに None を返し、呼び出し元は予算で打ち切る。"""
    del request
    return None


def estimate_tokens(text: str) -> int:
    """本文の入力 token の見積もり（platform の ``estimate_input_tokens`` と同じ。#1437）。

    モデルごとの tokenizer を持たないため多めに見積もる: ASCII は 4 字で 1 token、それ以外（かな・漢字など）は
    1 字で 1 token。見積もりは文字数を超えない。
    """
    ascii_count = len(_ASCII.findall(text))
    return (len(text) - ascii_count) + math.ceil(ascii_count / 4)


def model_input_tokens(settings: Any) -> int:
    """回答のモデルの入力の上限（tokens。#1437。共通のモデル設定の登録モデルの値。無ければ既定の 128000）。"""
    try:
        value = int(getattr(settings, "answer_max_input_tokens", DEFAULT_MODEL_INPUT_TOKENS) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_MODEL_INPUT_TOKENS


def context_tokens(context: AnswerContext) -> int:
    """文脈の根拠の本文（親の本文）の見積もりの token。"""
    return sum(estimate_tokens(str(getattr(record, "text", "") or "")) for record in context.records)


def is_enough(attempt: CragRetrievalAttempt | None) -> bool:
    """CRAG の判定で足りるか（十分で、内容の観点に確かめられていないものが無い）。"""
    if attempt is None or attempt.grade_error:
        return False
    return bool(attempt.sufficient) and not (set(attempt.missing_aspects) & CONTENT_ASPECTS)


def section_label(outline: SectionOutline, section: SectionInfo) -> str:
    """利用者向けの章節の表記（例: 「点検要領.pdf」の「第3章 設備ごとの点検」（p.5〜7））。"""
    document = f"「{outline.file_name}」" if outline.file_name else "文書"
    path = outline.path(section)
    name = f"の「{' > '.join(path)}」" if path else "の全体"
    pages = ""
    if section.page_start:
        end = section.page_end or section.page_start
        pages = f"（p.{section.page_start}）" if end == section.page_start else f"（p.{section.page_start}〜{end}）"
    return f"{document}{name}{pages}"


def _record_key(record: Any) -> str:
    return str(getattr(record, "chunk_uid", "") or getattr(record, "id", "") or "")


def _context_chunk_ids(context: AnswerContext) -> set[str]:
    ids: set[str] = set()
    for parent in context.evidence_tree:
        ids.add(_record_key(parent.record))
        ids.update(str(cid) for cid in getattr(parent.record, "child_chunk_ids", ()) or ())
        ids.update(_record_key(child.record) for child in parent.children)
    for record in context.records:
        ids.add(_record_key(record))
        ids.update(str(cid) for cid in getattr(record, "child_chunk_ids", ()) or ())
    ids.discard("")
    return ids


def seed_chunk_ids(context: AnswerContext, relevant_ids: Sequence[str] = ()) -> list[str]:
    """起点にする当たった chunk（関連度の順。CRAG が関連とした child を先に）。"""
    relevant = [uid for uid in dict.fromkeys(relevant_ids) if uid]
    anchors: list[str] = []
    for parent in context.evidence_tree:
        for child in parent.children:
            uid = _record_key(child.record)
            if uid and child.role == "retrieved_anchor":
                anchors.append(uid)
    known = {_record_key(child.record) for parent in context.evidence_tree for child in parent.children}
    ordered = [uid for uid in relevant if uid in known] + anchors
    return list(dict.fromkeys(ordered))


@dataclass
class _Chain:
    """1 つの起点の章節から根までの列と、読んだ段。"""

    outline: SectionOutline
    sections: list[SectionInfo]
    position: int = -1  # 読み終えた段（-1 は当たった chunk だけ）
    seed_uid: str = ""  # 起点の当たった chunk

    @property
    def next_section(self) -> SectionInfo | None:
        return self.sections[self.position + 1] if self.position + 1 < len(self.sections) else None


@dataclass
class _Reader:
    """章節を読み、文脈に足す record を作る（読んだ chunk・章節を覚える）。"""

    read_chunks: Callable[..., Sequence[Any]]
    known_ids: set[str]
    read_sections: set[tuple[str, str, str]] = field(default_factory=set)

    def section_key(self, outline: SectionOutline, section: SectionInfo) -> tuple[str, str, str]:
        return outline.document_id, outline.chunk_set_id, section.section_id

    def unread_chars(self, outline: SectionOutline, section: SectionInfo) -> int:
        """読む前の見積もり（章節の文字数から、読み終えた子孫の章節の分を引く）。"""
        done = [item for item in outline.sections
                if self.section_key(outline, item) in self.read_sections and _is_descendant(outline, item, section)]
        top = [item for item in done if not any(other is not item and _is_descendant(outline, item, other)
                                                for other in done)]
        return max(0, section.chars - sum(item.chars for item in top))

    def read(self, outline: SectionOutline, section: SectionInfo) -> list[ContextParentEvidence]:
        """章節の chunk を読み、まだ文脈に無い親を章節の中の読み順で返す（child は章節の中の順）。"""
        chunks = list(self.read_chunks(document_id=outline.document_id, chunk_set_id=outline.chunk_set_id,
                                       section_id=section.section_id))
        records = [_stored_chunk_answer_record(chunk) for chunk in chunks]
        parents = {_record_key(r): r for r in records if getattr(r, "chunk_level", "") == "parent"}
        groups: dict[str, list[AnswerRecord]] = {}
        for record in records:
            if getattr(record, "chunk_level", "") == "parent":
                continue
            key = str(getattr(record, "parent_chunk_uid", "") or "") or _record_key(record)
            groups.setdefault(key, []).append(record)
        tree: list[ContextParentEvidence] = []
        for key, children in groups.items():
            parent = parents.get(key)
            if _record_key(parent or children[0]) in self.known_ids:
                continue
            tree.append(ContextParentEvidence(
                record=parent or children[0],
                role="synthesis_parent" if parent is not None else "child_fallback",
                reason=SECTION_EXPANSION_REASON,
                children=tuple(
                    ContextChildEvidence(record=child, role=SECTION_CONTEXT_ROLE,  # type: ignore[arg-type]
                                         reason=SECTION_EXPANSION_REASON)
                    for child in children),
            ))
        return tree

    def mark(self, outline: SectionOutline, section: SectionInfo, tree: Sequence[ContextParentEvidence]) -> None:
        self.read_sections.add(self.section_key(outline, section))
        for item in tree:
            self.known_ids.add(_record_key(item.record))
            self.known_ids.update(_record_key(child.record) for child in item.children)


def _is_descendant(outline: SectionOutline, item: SectionInfo, ancestor: SectionInfo) -> bool:
    current = outline.parent(item)
    while current is not None:
        if current.section_id == ancestor.section_id:
            return True
        current = outline.parent(current)
    return False


def _tree_tokens(tree: Sequence[ContextParentEvidence]) -> int:
    return sum(estimate_tokens(str(getattr(item.record, "text", "") or "")) for item in tree)


def _fit_tree(tree: Sequence[ContextParentEvidence], remaining: int) -> tuple[list[ContextParentEvidence], bool]:
    """読み順の先頭から予算に入る親だけを残す（全部入れば True）。"""
    kept: list[ContextParentEvidence] = []
    used = 0
    for item in tree:
        cost = estimate_tokens(str(getattr(item.record, "text", "") or ""))
        if used + cost > remaining:
            return kept, False
        kept.append(item)
        used += cost
    return kept, True


def _rank_children(question: str, outline: SectionOutline, children: Sequence[SectionInfo],
                   rerank: Callable[..., Any] | None, settings: Any) -> tuple[list[SectionInfo], str]:
    """子の章節を見出しと質問の関連度で並べる（rerank。使えなければ語の重なり）。"""
    if len(children) <= 1:
        return list(children), "single"
    documents = [" > ".join(outline.path(child)) or child.title for child in children]
    if rerank is not None:
        try:
            ranked = rerank(question, documents, settings, top_n=len(documents))
            order = [int(getattr(item, "index", item)) for item in ranked or ()]
            order = [index for index in dict.fromkeys(order) if 0 <= index < len(children)]
            if order:
                rest = [index for index in range(len(children)) if index not in order]
                return [children[index] for index in [*order, *rest]], "rerank"
        except Exception:  # noqa: BLE001 - 並びは補助。語の重なりで並べる
            pass
    from rag_engine.retrieval.evidence_selection import evidence_relevance
    scored = sorted(range(len(children)), key=lambda index: (-evidence_relevance(question, documents[index]), index))
    return [children[index] for index in scored], "lexical"


def _merge(context: AnswerContext, tree: Sequence[ContextParentEvidence]) -> AnswerContext:
    """もとの根拠の後ろに、章節の根拠を読み順で足す。"""
    if not tree:
        return context
    bundle = context_bundle_from_parent_evidence(tuple(tree), max_chars=max(
        [len(str(getattr(item.record, "text", "") or "")) for item in tree] + [1]))
    if not bundle.records:
        return context
    return replace(
        context,
        records=[*context.records, *bundle.records],
        text="\n\n".join(part for part in (context.text, bundle.text) if part.strip()),
        evidence=(*context.evidence, *bundle.evidence),
        evidence_tree=(*context.evidence_tree, *bundle.evidence_tree),
    )


def expand_sections(
    question: str,
    context: AnswerContext,
    settings: Any,
    *,
    last_attempt: CragRetrievalAttempt | None,
    grade: Callable[[AnswerContext, int], CragRetrievalAttempt],
    section_outline: Callable[..., Any],
    section_chunks: Callable[..., Sequence[Any]],
    rerank: Callable[..., Any] | None = None,
    large_sections: LargeSectionHandler = no_large_section_handler,
    started_at: float | None = None,
    deadline_remaining: Callable[[], float | None] = lambda: None,
    clock: Callable[[], float] = monotonic,
) -> SectionExpansionResult:
    """当たった chunk の章節から段階的に広げる（上の module の説明）。

    ``grade(context, level)`` は既存の CRAG の十分性の判定、``started_at`` は回答の開始の時刻（``clock`` の値）、
    ``deadline_remaining`` は回答の期限までの残りの秒数（無ければ None）。
    """
    limits = SectionExpansionLimits.from_settings(settings)
    start = clock() if started_at is None else started_at
    trace: dict[str, Any] = {"enabled": True, "max_levels": limits.max_levels, "input_token_limit": limits.input_tokens,
                             "time_limit_seconds": limits.time_limit_seconds, "levels": []}
    result = SectionExpansionResult(context=context, trace=trace)

    def finish(reason: str) -> SectionExpansionResult:
        result.stop_reason = reason
        result.used_tokens = context_tokens(result.context)
        trace.update(stop_reason=reason, applied=result.applied, levels_read=result.levels,
                     used_tokens=result.used_tokens, unread=[item.to_payload() for item in result.unread])
        return result

    if not getattr(settings, "section_expansion_enabled", True) or limits.max_levels <= 0:
        trace["enabled"] = False
        return finish(STOP_DISABLED)
    # 列挙・網羅の問で、足りると判定する前に読む範囲（#1503。None は範囲の規則なし）。
    need = minimum_scope(question)
    if need is not None:
        trace["minimum_scope"] = need
    if need is None and is_enough(last_attempt):
        return finish(STOP_SUFFICIENT)
    seeds = seed_chunk_ids(context, last_attempt.relevant_chunk_ids if last_attempt is not None else ())
    chains: list[_Chain] = []
    owners: set[tuple[str, str, str]] = set()
    for uid in seeds:
        if len(chains) >= MAX_SEED_SECTIONS:
            break
        outline = section_outline(chunk_uid=uid)
        if not isinstance(outline, SectionOutline):
            continue
        key = (outline.document_id, outline.chunk_set_id, outline.owner_section_id)
        sections = outline.chain()
        if key in owners or not sections:
            continue
        owners.add(key)
        chains.append(_Chain(outline=outline, sections=sections, seed_uid=uid))
    trace["seeds"] = [{"document_id": c.outline.document_id, "chunk_set_id": c.outline.chunk_set_id,
                       "section_id": c.outline.owner_section_id} for c in chains]
    if not chains:
        return finish(STOP_NO_SECTIONS)

    reader = _Reader(read_chunks=section_chunks, known_ids=_context_chunk_ids(context))
    level_seconds = DEFAULT_LEVEL_SECONDS
    latest = last_attempt  # 最後の判定（読む範囲に届くまでの段は判定しない）

    def scope_reached() -> bool:
        """読む範囲まで読んだか（leaf: いずれかの起点の所有の章節、document: 起点の列の最上位の章節）。"""
        if need is None:
            return True
        if need == "leaf":
            return any(chain.position >= 0 for chain in chains)
        return any(chain.position >= len(chain.sections) - 1 for chain in chains)
    current = context
    extracting = large_sections is not no_large_section_handler

    def seconds_left() -> float:
        """抽出に使える残りの秒数（回答の開始からの上限と回答の期限の早いほうから、生成と次の判定の分を引く）。"""
        left = limits.time_limit_seconds - (clock() - start)
        remaining_deadline = deadline_remaining()
        if remaining_deadline is not None:
            left = min(left, remaining_deadline)
        return left - GENERATION_RESERVE_SECONDS - level_seconds

    def hand_off(chain: _Chain, section: SectionInfo, remaining: int, entry: dict[str, Any],
                 added: list[ContextParentEvidence]) -> bool:
        """予算に入らない章節を、抽出してから統合（#1437）に渡す。読めなかった範囲があれば True。

        差し込み口が無い（None を返す）ときは、章節を読まずに「根拠の入力の上限」で読めなかった章節にする。
        抜き出した根拠は ``added`` に足し、章節は読んだことにする（抜き出す原文が無くても読んだ）。
        """
        outcome = None
        if extracting:
            tree = reader.read(chain.outline, section)
            outcome = large_sections(LargeSectionRequest(
                question=question, outline=chain.outline, section=section, tree=tuple(tree),
                remaining_tokens=max(0, remaining), seconds_left=seconds_left,
                seed_positions=_seed_positions(context, chains, chain),
                truncated=section.chunk_count > MAX_SECTION_CHUNKS))
        if outcome is None:
            entry["read"] = "skipped"
            result.unread.append(UnreadSection(chain.outline.document_id, section.section_id,
                                               section_label(chain.outline, section), STOP_TOKEN_BUDGET))
            return True
        reader.mark(chain.outline, section, outcome.tree)
        added.extend(outcome.tree)
        result.unread.extend(outcome.unread)
        entry.update(read="extracted", added_parents=len(outcome.tree), extraction=outcome.trace)
        return bool(outcome.unread)

    for level in range(1, limits.max_levels + 1):
        targets = [chain for chain in chains if chain.next_section is not None]
        if not targets:
            return finish(STOP_DOCUMENT)
        # 時間: 回答の開始からの上限と回答の期限の早いほうまでに、この段と生成の分が残るか。
        elapsed = clock() - start
        left = limits.time_limit_seconds - elapsed
        remaining_deadline = deadline_remaining()
        if remaining_deadline is not None:
            left = min(left, remaining_deadline)
        if left < level_seconds + GENERATION_RESERVE_SECONDS:
            _mark_unread(result, reader, targets, STOP_TIME_LIMIT)
            return finish(STOP_TIME_LIMIT)
        level_trace: dict[str, Any] = {"level": level, "sections": []}
        trace["levels"].append(level_trace)
        added: list[ContextParentEvidence] = []
        budget_hit = False
        labels: list[str] = []
        seen_targets: set[tuple[str, str, str]] = set()
        for chain in targets:
            section = chain.next_section
            assert section is not None
            chain.position += 1
            key = reader.section_key(chain.outline, section)
            if key in seen_targets or key in reader.read_sections:
                continue
            seen_targets.add(key)
            labels.append(section_label(chain.outline, section))
            remaining = limits.input_tokens - context_tokens(current) - _tree_tokens(added)
            entry: dict[str, Any] = {"section_id": section.section_id, "title": section.title,
                                     "is_document": section.section_id == ROOT_SECTION_ID}
            level_trace["sections"].append(entry)
            if reader.unread_chars(chain.outline, section) <= remaining and section.chunk_count <= MAX_SECTION_CHUNKS:
                tree = reader.read(chain.outline, section)
                kept, whole = _fit_tree(tree, remaining)
                reader.mark(chain.outline, section, kept)
                added.extend(kept)
                entry.update(read="whole" if whole else "partial", added_parents=len(kept))
                if not whole:
                    budget_hit = True
                    result.unread.append(UnreadSection(chain.outline.document_id, section.section_id,
                                                       section_label(chain.outline, section) + "の残り",
                                                       STOP_TOKEN_BUDGET))
                continue
            # 予算に入らない: 子の章節を関連度の順に、入るものから全体で読む。
            done = chain.sections[chain.position - 1] if chain.position > 0 else None
            children = [child for child in chain.outline.children(section.section_id)
                        if done is None or child.section_id != done.section_id]
            if not children:
                # 子の章節が無い章節（大きな leaf・子をすべて読んだ章節）は、抽出してから統合（#1437）に渡す。
                if hand_off(chain, section, remaining, entry, added):
                    budget_hit = True
                continue
            ranked, method = _rank_children(question, chain.outline, children, rerank, settings)
            entry.update(read="children", ranking=method, children=[])
            for child in ranked:
                child_key = reader.section_key(chain.outline, child)
                if child_key in reader.read_sections:
                    continue
                remaining = limits.input_tokens - context_tokens(current) - _tree_tokens(added)
                child_entry = {"section_id": child.section_id, "title": child.title}
                entry["children"].append(child_entry)
                if reader.unread_chars(chain.outline, child) > remaining or child.chunk_count > MAX_SECTION_CHUNKS:
                    # 入らない子の章節も、抽出してから統合（#1437）に渡す。
                    if hand_off(chain, child, remaining, child_entry, added):
                        budget_hit = True
                    continue
                tree = reader.read(chain.outline, child)
                kept, whole = _fit_tree(tree, remaining)
                reader.mark(chain.outline, child, kept)
                added.extend(kept)
                child_entry.update(read="whole" if whole else "partial", added_parents=len(kept))
                if not whole:
                    budget_hit = True
                    result.unread.append(UnreadSection(chain.outline.document_id, child.section_id,
                                                       section_label(chain.outline, child) + "の残り",
                                                       STOP_TOKEN_BUDGET))
        if budget_hit or any(item.get("read") == "extracted" for item in _entries(level_trace)):
            # 全体を読む必要のある章節は、抽出してから統合（#1437）に渡す（差し込み口が無ければ予算で打ち切る）。
            trace["large_section_handoff"] = (
                "extract_then_integrate" if extracting else "extract_then_integrate_unavailable")
        result.levels = level
        level_trace["labels"] = labels
        if added:
            current = _merge(current, added)
            result.context = current
            result.applied = True
            level_trace["added_parents"] = len(added)
        if not added:
            if budget_hit:
                return finish(STOP_TOKEN_BUDGET)
            if need is not None and scope_reached() and is_enough(latest):
                # 読む範囲の章節はすでに文脈にあった（足す根拠が無い）。最後の判定のまま止める。
                return finish(STOP_SUFFICIENT)
            continue
        if not scope_reached() and not budget_hit:
            # 読む範囲に届くまでは判定しない（網羅の問で、見えている数件で「足りる」としない。#1503）。
            level_trace["grade_skipped"] = "minimum_scope"
            continue
        before = clock()
        try:
            attempt = grade(current, level)
        except Exception as exc:  # noqa: BLE001 - 判定の失敗は、読んだ章節のまま回答へ進む
            if type(exc).__name__ == "DeadlineExceeded":
                raise
            level_trace["grade_error"] = f"{type(exc).__name__}: {exc}"[:240]
            return finish(STOP_GRADE_ERROR)
        level_seconds = max(1.0, clock() - before)
        latest = attempt
        level_trace.update(sufficient=attempt.sufficient, missing_aspects=list(attempt.missing_aspects))
        if is_enough(attempt):
            # 読めなかった章節があっても、読んだ範囲で足りるなら条件つきにしない。
            result.unread.clear()
            return finish(STOP_SUFFICIENT)
        if budget_hit:
            return finish(STOP_TOKEN_BUDGET)
    remaining_targets = [chain for chain in chains if chain.next_section is not None]
    if not remaining_targets:
        return finish(STOP_DOCUMENT)
    _mark_unread(result, reader, remaining_targets, STOP_MAX_LEVELS)
    return finish(STOP_MAX_LEVELS)


def _seed_positions(context: AnswerContext, chains: Sequence[_Chain], chain: _Chain) -> tuple[int, ...]:
    """同じ文書（chunk_set）の起点の当たった chunk の、文書の中の位置（``seq_no``）。"""
    same = {item.seed_uid for item in chains
            if (item.outline.document_id, item.outline.chunk_set_id)
            == (chain.outline.document_id, chain.outline.chunk_set_id)}
    positions = [int(getattr(child.record, "seq_no", 0) or 0)
                 for parent in context.evidence_tree for child in parent.children
                 if _record_key(child.record) in same]
    return tuple(dict.fromkeys(positions))


def _entries(level_trace: dict[str, Any]) -> list[dict[str, Any]]:
    """段の記録の章節と子の章節の記録（読み方を数えるため）。"""
    entries: list[dict[str, Any]] = []
    for entry in level_trace.get("sections", []):
        entries.append(entry)
        entries.extend(entry.get("children", []) or [])
    return entries


def _mark_unread(result: SectionExpansionResult, reader: _Reader, chains: Sequence[_Chain], reason: str) -> None:
    """次に読むはずだった章節を、読めなかった章節にする（同じ章節は 1 回）。"""
    seen = {(item.document_id, item.section_id) for item in result.unread}
    for chain in chains:
        section = chain.next_section
        if section is None or reader.section_key(chain.outline, section) in reader.read_sections:
            continue
        key = (chain.outline.document_id, section.section_id)
        if key in seen:
            continue
        seen.add(key)
        result.unread.append(UnreadSection(chain.outline.document_id, section.section_id,
                                           section_label(chain.outline, section), reason))


__all__ = [
    "CONTENT_ASPECTS",
    "GENERATION_RESERVE_SECONDS",
    "DEFAULT_MODEL_INPUT_TOKENS",
    "LIMIT_STOPS",
    "MAX_SECTION_CHUNKS",
    "STOP_EXTRACTION_CALLS",
    "STOP_EXTRACTION_ERROR",
    "STOP_SECTION_CHUNKS",
    "ROOT_SECTION_ID",
    "SECTION_CONTEXT_ROLE",
    "SECTION_EXPANSION_REASON",
    "LargeSectionHandler",
    "LargeSectionOutcome",
    "LargeSectionRequest",
    "SectionExpansionLimits",
    "SectionExpansionResult",
    "SectionInfo",
    "SectionOutline",
    "UnreadSection",
    "context_tokens",
    "estimate_tokens",
    "expand_sections",
    "is_enough",
    "no_large_section_handler",
    "model_input_tokens",
    "section_label",
    "seed_chunk_ids",
]
