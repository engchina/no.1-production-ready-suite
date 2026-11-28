"""当たった chunk を含む章節の全体を、設定の段まで広げて回答の文脈に入れる（#1436・#5812）。

章節の正本は文書の「章節ナビゲーション」で、章節と chunk の対応（#1438）は呼び出し元（backend）が持つ。この module は
対応を ``AnswerDependencies.section_outline`` / ``section_chunks`` から読み、読む章節と止める条件だけを決める
（I/O は注入された関数を通す。決定論のテストはスタブで行う）。

- 段は、当たった chunk を含む章節（所有の章節）を 1 段目、その親の章節を 2 段目、さらに上を 3 段目と数える
  （「広げる段の上限」1〜3、既定 1）。文書全体（根の章節）も段の 1 つで、上限の段に入れば読む候補になる。
- 起点ごとに、上限の段から下へ見て、文字数（1 文字 = 1 token。``SectionInfo.chars``）が回答のモデルの入力の上限の
  1/2 以下の最も上の章節の全体を読む。問題・プロンプト・ほかの根拠は数えない。合わせて入らない分は分割読取に任せる。
- 所有の章節も 1/2 を超える起点は読まない。呼び出し元（``_fill_unread_section_anchors``）が親 → 前後の chunk で補う。
  縮めた・読まなかったことは診断にだけ残し、回答の「回答に未反映の関連章節」には出さない。読取の失敗と時間の上限で
  読めなかった章節は回答に出す。
- 回答の流れは、補いの検索（要求別の補完・業務ガイドの手順）の根拠を主の検索の根拠に合わせた後に 1 回だけ広げる（#5173）。
- 呼び出し元が先に読む範囲（``priority``。業務ガイドの手順の参照の文書と頁）を渡したら、参照の文書の起点を段で広げ、
  その他の文書の起点は所有の章節だけを最後に読む（#2828）。読めなくても回答に出さず、診断（``unread_omitted``）にだけ残す。
- 呼び出し元が「文書ごとの上限」で起点から外した同じ文書の候補（``related``。#5209）を渡したら、その候補の所有の章節
  （子の章節を持たない章節だけ）を、1/2 以下なら読む。読めなくても回答に出さず、診断にだけ残す。回答の生成に残す
  時間が無ければ読まない。
- 「回答に未反映の関連章節」に出す行は、回答の生成の後に ``answer_unread_view`` で、回答が根拠にした文書と先に読む
  範囲の文書の章節に絞り、``MAX_UNREAD_LINES`` 行を超えた分は件数の 1 行にする（#2828）。

入力の上限は、回答のモデルの入力の上限（共通のモデル設定の登録モデルの「入力の上限（tokens）」。#1437。backend が
platform の ``enterprise_ai_max_input_tokens`` で決めて ``answer_max_input_tokens`` で渡す）。この module は、もとの根拠の
後ろに広げた章節を章節の中の読み順で足し、回答の流れはその後に文脈の補完のまとめ（``rag_engine.retrieval.context_fill``。
#5168）で章節の本文を最も近い根拠のかたまりへ置く。引用の ID は chunk のまま。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any

from rag_parser_core.language import current_pack

from rag_engine.generation.answer_models import AnswerContext
from rag_engine.generation.answer_records import AnswerRecord, _stored_chunk_answer_record
from rag_engine.resources.deadline import ANSWER_FINALIZE_RESERVE_SECONDS, DEFAULT_GENERATION_ROUND_SECONDS
from rag_engine.retrieval.context_builder import (
    ChildEvidenceRole,
    ContextChildEvidence,
    ContextParentEvidence,
    ParentEvidenceRole,
    context_bundle_from_parent_evidence,
)

# 根の章節（文書全体）の id。backend の ``section_chunk_map.ROOT_SECTION_ID`` と同じ。
ROOT_SECTION_ID = "document"
# 広げた章節の根拠の役割（context の parent の理由と child の役割）。
SECTION_EXPANSION_REASON = "section_expansion"
SECTION_CONTEXT_ROLE: ChildEvidenceRole = "section_context"
# モデルの入力の上限の既定（tokens。platform の DEFAULT_ENTERPRISE_AI_MAX_INPUT_TOKENS と同じ。#1437）。
DEFAULT_MODEL_INPUT_TOKENS = 128_000
# 広げる段の上限の最大（1 = 所有の章節。#5812）。
MAX_LEVELS = 3
# 次の段を読む処理の見積もりの秒数。
DEFAULT_LEVEL_SECONDS = 20.0
_ASCII = re.compile(r"[\x00-\x7f]")

# 止めた理由。LIMIT_STOPS は上限で止めた（読めなかった章節があれば条件つきの回答にする）。
STOP_DOCUMENT = "document_read"
STOP_MAX_LEVELS = "max_levels"
STOP_TOKEN_BUDGET = "token_budget"
STOP_TIME_LIMIT = "time_limit"
STOP_NO_SECTIONS = "no_sections"
STOP_READ_ERROR = "read_error"
STOP_PARTIAL = "partial_read"
STOP_DISABLED = "disabled"
# 読む範囲まで読んで止めた。
STOP_SCOPE_READ = "scope_read"
LIMIT_STOPS = frozenset({STOP_MAX_LEVELS, STOP_TOKEN_BUDGET, STOP_TIME_LIMIT})
# 「回答に未反映の関連章節」に出す行の上限（超えた分は件数の 1 行にする。#2828）。
MAX_UNREAD_LINES = 10


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
    """上限で読めなかった章節（「回答に未反映の関連章節」に出す）。"""

    document_id: str
    section_id: str
    label: str
    reason: str
    # 章節（範囲）の始まりの頁。画面が各行から原文を開くのに使う（#2040。頁の無い文書は None）。
    page: int | None = None

    def gap_line(self) -> str:
        """本文の行。影響（回答に未反映）と理由を書き言葉で述べる（#2040）。"""
        pack = current_pack()
        if self.reason == STOP_TIME_LIMIT:
            key = "section_skip_time"
        elif self.reason == STOP_READ_ERROR:
            key = "section_read_error"
        elif self.reason == STOP_PARTIAL:
            key = "section_read_partial"
        else:
            key = "section_skip_volume"
        return pack.text("answer_labels", key).format(label=self.label)

    def to_payload(self) -> dict[str, Any]:
        # line は本文の行と同じ文。画面は本文の行をこれで引き当て、その行に原文を開く操作を付ける（#2040）。
        return {
            "document_id": self.document_id,
            "section_id": self.section_id,
            "label": self.label,
            "reason": self.reason,
            "page": self.page,
            "line": self.gap_line(),
        }


@dataclass(frozen=True)
class PriorityRange:
    """先に読む範囲（業務ガイドの手順の参照の文書と頁。#2828）。頁が無ければ文書の全体。"""

    document_id: str
    page_start: int | None = None
    page_end: int | None = None


# 起点の順位（小さいほど先に読む）。参照の頁に重なる・参照の文書・その他。
_RANK_PAGES, _RANK_DOCUMENT, _RANK_OTHER = 0, 1, 2


def _priority_rank(outline: SectionOutline, priority: Sequence[PriorityRange]) -> int:
    """起点の所有の章節が、先に読む範囲のどれに当たるか（``_RANK_*``）。"""
    owner = outline.get(outline.owner_section_id)
    ranges = [item for item in priority if item.document_id == outline.document_id]
    if not ranges:
        return _RANK_OTHER
    start = owner.page_start if owner is not None else None
    end = (owner.page_end or start) if owner is not None else None
    for item in ranges:
        if item.page_start is None:
            return _RANK_PAGES
        if start is not None and end is not None and start <= (item.page_end or item.page_start) and item.page_start <= end:
            return _RANK_PAGES
    return _RANK_DOCUMENT


@dataclass(frozen=True)
class UnreadView:
    """回答の本文に出す「回答に未反映の関連章節」の行と、出さなかった章節（#2828）。"""

    lines: tuple[str, ...] = ()
    # 回答が根拠にした文書でも先に読む範囲の文書でもないため出さなかった章節。
    hidden: tuple[UnreadSection, ...] = ()
    # 行の上限を超え、件数の 1 行にまとめた章節の数。
    overflow: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {"shown": len(self.lines), "hidden": [item.to_payload() for item in self.hidden], "overflow": self.overflow}


def answer_unread_view(
    unread: Sequence[UnreadSection | str],
    cited_documents: Collection[str],
    kept_documents: Collection[str] = (),
    *,
    max_lines: int = MAX_UNREAD_LINES,
) -> UnreadView:
    """読めなかった章節から、回答の本文に出す行を決める（#2828）。

    回答が根拠にした文書（``cited_documents``）か先に読む範囲の文書（``kept_documents``）の章節だけを出す。どちらも
    無ければ（根拠を示せなかった回答）絞らない。文の行（文書の分からない行）はそのまま出す。``max_lines`` を超えた
    分は「ほか N 件」の 1 行にする。
    """
    focus = {str(item) for item in (*cited_documents, *kept_documents) if item}
    lines: list[str] = []
    hidden: list[UnreadSection] = []
    for item in unread:
        if isinstance(item, UnreadSection):
            if focus and item.document_id not in focus:
                hidden.append(item)
                continue
            line = item.gap_line()
        else:
            line = str(item or "")
        if line and line not in lines:
            lines.append(line)
    limit = max(1, int(max_lines))
    overflow = max(0, len(lines) - limit)
    if overflow:
        lines = [*lines[:limit], current_pack().text("answer_labels", "section_overflow").format(count=overflow)]
    return UnreadView(lines=tuple(lines), hidden=tuple(hidden), overflow=overflow)


class SectionRead(list[Any]):
    """章節の原文と読取結果。空・権限による欠落・途中までの取得を全量と区別する。"""

    def __init__(self, chunks: Sequence[Any] = (), *, complete: bool, reason: str = "") -> None:
        super().__init__(chunks)
        self.complete = complete
        self.reason = reason


@dataclass(frozen=True)
class SectionExpansionLimits:
    """拡張の上限（検索方法の全体設定で決める）。``input_tokens`` は根拠の本文の入力 token の上限。

    ``generation_reserve_seconds`` は回答の生成に残す秒数で、回答の期限の残りが次の段の見積もりとこの値の和より
    少なければ上がらない。生成の 1 回の見積もり（設定の ``answer_generation_round_estimate_seconds``。#5184）と
    回答の確定の分（``ANSWER_FINALIZE_RESERVE_SECONDS``）の和。
    """

    max_levels: int
    input_tokens: int
    generation_reserve_seconds: float = DEFAULT_GENERATION_ROUND_SECONDS + ANSWER_FINALIZE_RESERVE_SECONDS

    @classmethod
    def from_settings(cls, settings: Any) -> SectionExpansionLimits:
        round_seconds = getattr(settings, "answer_generation_round_estimate_seconds", None)
        round_seconds = DEFAULT_GENERATION_ROUND_SECONDS if round_seconds is None else max(0.0, float(round_seconds))
        return cls(
            max_levels=max(1, min(MAX_LEVELS, int(getattr(settings, "section_expansion_max_levels", 1) or 1))),
            # 1 つの章節が入ってよい大きさは、回答のモデルの入力の上限の 1/2（#5812）。
            input_tokens=model_input_tokens(settings) // 2,
            generation_reserve_seconds=round_seconds + ANSWER_FINALIZE_RESERVE_SECONDS,
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
    # 予算で読めなかった、起点を含まない兄弟の章節（回答に出さず診断にだけ残す。#2816）。
    omitted: list[UnreadSection] = field(default_factory=list)

    @property
    def gap_lines(self) -> list[str]:
        return [item.gap_line() for item in self.unread]


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


def seed_chunk_ids(context: AnswerContext) -> list[str]:
    """起点にする当たった chunk（関連度の順）。"""
    anchors: list[str] = []
    for parent in context.evidence_tree:
        for child in parent.children:
            uid = _record_key(child.record)
            if uid and child.role == "retrieved_anchor":
                anchors.append(uid)
    return list(dict.fromkeys(anchors))


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
class SectionReader:
    """章節を読み、文脈に足す record を作る（読んだ chunk・章節を覚える）。"""

    read_chunks: Callable[..., Sequence[Any]]
    known_ids: set[str]
    read_sections: set[tuple[str, str, str]] = field(default_factory=set)
    known_texts: dict[str, str] = field(default_factory=dict)
    # 章節ごとの親（読み順。文脈にあるかに関係なく全部）。同じ章節を 2 回読まない。
    _groups: dict[tuple[str, str, str], list[ContextParentEvidence]] = field(default_factory=dict)
    failures: dict[tuple[str, str, str], str] = field(default_factory=dict)
    read_events: list[dict[str, Any]] = field(default_factory=list)

    def section_key(self, outline: SectionOutline, section: SectionInfo) -> tuple[str, str, str]:
        return outline.document_id, outline.chunk_set_id, section.section_id

    def groups(self, outline: SectionOutline, section: SectionInfo) -> list[ContextParentEvidence]:
        """章節の chunk を読み、親ごとにまとめて章節の中の読み順で返す（文脈にある親も含む。child は章節の中の順。
        親の record が無い chunk は chunk ごとに返す）。"""
        key = self.section_key(outline, section)
        if key in self._groups:
            self.read_events.append(
                {
                    "document_id": key[0],
                    "chunk_set_id": key[1],
                    "section_id": key[2],
                    "origin_section_id": outline.owner_section_id,
                    "state": "reused",
                    "child_ids": sorted({_record_key(child.record) for group in self._groups[key] for child in group.children}),
                }
            )
            return self._groups[key]
        try:
            response = self.read_chunks(document_id=outline.document_id, chunk_set_id=outline.chunk_set_id, section_id=section.section_id)
            chunks = list(response)
            count = sum(getattr(chunk, "chunk_level", "") != "parent" for chunk in chunks)
            # chunk を 1 つも持たない章節（見出しだけ）は読むものが無い。未読にしない（#4138）。
            missing = not chunks and section.chunk_count > 0
            if missing or not getattr(response, "complete", count >= section.chunk_count):
                self.failures[key] = getattr(response, "reason", "") or STOP_PARTIAL
        except Exception as exc:  # noqa: BLE001 - 読取失敗を未確認として残して回答を続ける
            if type(exc).__name__ == "DeadlineExceeded":
                raise
            chunks = []
            self.failures[key] = STOP_READ_ERROR
        records = [_stored_chunk_answer_record(chunk) for chunk in chunks]
        parents = {_record_key(r): r for r in records if getattr(r, "chunk_level", "") == "parent"}
        grouped: dict[str, list[AnswerRecord]] = {}
        for record in records:
            if getattr(record, "chunk_level", "") == "parent":
                continue
            group_key = str(getattr(record, "parent_chunk_uid", "") or "") or _record_key(record)
            grouped.setdefault(group_key, []).append(record)

        def evidence(record: AnswerRecord, members: Sequence[AnswerRecord], role: ParentEvidenceRole) -> ContextParentEvidence:
            return ContextParentEvidence(
                record=record,
                role=role,
                reason=SECTION_EXPANSION_REASON,
                children=tuple(
                    ContextChildEvidence(
                        record=child,
                        role=SECTION_CONTEXT_ROLE,
                        reason=SECTION_EXPANSION_REASON,
                    )
                    for child in members
                ),
            )

        # 親の record を読めなければ chunk ごとに根拠にする。先頭の chunk の本文で章節の残りを代表させると、
        # 残りの本文が回答の入力から消える（#5810）。
        tree = [
            item
            for group_key, children in grouped.items()
            for item in (
                [evidence(parents[group_key], children, "synthesis_parent")]
                if group_key in parents
                else [evidence(child, [child], "child_fallback") for child in children]
            )
        ]
        self.read_events.append(
            {
                "document_id": key[0],
                "chunk_set_id": key[1],
                "section_id": key[2],
                "origin_section_id": outline.owner_section_id,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "state": "read",
                "child_ids": sorted({_record_key(child.record) for group in tree for child in group.children}),
                "reason": self.failures.get(key),
                "expected_chunk_count": section.chunk_count,
            }
        )
        self._groups[key] = tree
        return tree

    def read(self, outline: SectionOutline, section: SectionInfo) -> list[ContextParentEvidence]:
        """章節の親のうち、まだ文脈に無いものを章節の中の読み順で返す。"""
        return [
            item
            for item in self.groups(outline, section)
            if str(getattr(item.record, "text", "") or "") not in self.known_texts.get(_record_key(item.record), "")
        ]

    def mark(self, outline: SectionOutline, section: SectionInfo, tree: Sequence[ContextParentEvidence]) -> None:
        self.read_sections.add(self.section_key(outline, section))
        for item in tree:
            self.known_ids.add(_record_key(item.record))
            self.known_texts[_record_key(item.record)] = str(getattr(item.record, "text", "") or "")
            self.known_ids.update(_record_key(child.record) for child in item.children)


def _is_descendant(outline: SectionOutline, item: SectionInfo, ancestor: SectionInfo) -> bool:
    current = outline.parent(item)
    while current is not None:
        if current.section_id == ancestor.section_id:
            return True
        current = outline.parent(current)
    return False


def _merge(context: AnswerContext, tree: Sequence[ContextParentEvidence]) -> AnswerContext:
    """もとの根拠の後ろに、章節の根拠を読み順で足す。"""
    if not tree:
        return context
    replacements = {
        _record_key(item.record): item.record
        for item in tree
        if any(_record_key(record) == _record_key(item.record) for record in context.records)
    }
    if replacements:
        context = _replace_records(context, replacements)
        by_key = {_record_key(item.record): item for item in tree}
        merged_tree = []
        for parent in context.evidence_tree:
            extra = by_key.get(_record_key(parent.record))
            children = {_record_key(child.record): child for child in extra.children} if extra else {}
            children.update({_record_key(child.record): child for child in parent.children})
            merged_tree.append(replace(parent, children=tuple(children.values())))
        context = replace(context, evidence_tree=tuple(merged_tree))
        existing = {_record_key(record) for record in context.records}
        tree = tuple(item for item in tree if _record_key(item.record) not in existing)
    bundle = context_bundle_from_parent_evidence(
        tuple(tree), max_chars=max([len(str(getattr(item.record, "text", "") or "")) for item in tree] + [1])
    )
    if not bundle.records:
        return context
    return replace(
        context,
        records=[*context.records, *bundle.records],
        text="\n\n".join(part for part in (context.text, bundle.text) if part.strip()),
        evidence=(*context.evidence, *bundle.evidence),
        evidence_tree=(*context.evidence_tree, *bundle.evidence_tree),
    )


def _replace_records(context: AnswerContext, replacements: dict[str, AnswerRecord | None]) -> AnswerContext:
    """文脈の親を、渡した record に置き換える（None は外す）。位置・役割・child はそのまま。"""
    if not replacements:
        return context

    def swap(record: Any) -> Any:
        key = _record_key(record)
        return replacements.get(key, record)

    tree = tuple(replace(item, record=new) for item in context.evidence_tree if (new := swap(item.record)) is not None)
    records = [new for record in context.records if (new := swap(record)) is not None]
    evidence = tuple(replace(item, record=new) for item in context.evidence if (new := swap(item.record)) is not None)
    text = (
        context_bundle_from_parent_evidence(
            tree, max_chars=max([len(str(getattr(item.record, "text", "") or "")) for item in tree] + [1])
        ).text
        if tree
        else ""
    )
    return replace(
        context,
        records=records,
        evidence=evidence,
        evidence_tree=tree,
        text=text or "\n\n".join(str(getattr(record, "text", "") or "") for record in records),
    )


def expand_sections(
    question: str,
    context: AnswerContext,
    settings: Any,
    *,
    section_outline: Callable[..., Any],
    section_chunks: Callable[..., Sequence[Any]],
    started_at: float | None = None,
    deadline_remaining: Callable[[], float | None] = lambda: None,
    clock: Callable[[], float] = monotonic,
    reader: SectionReader | None = None,
    priority: Sequence[PriorityRange] = (),
    related: Sequence[str] = (),
    block_chars: Callable[[AnswerContext], int] | None = None,
) -> SectionExpansionResult:
    """当たった chunk の章節から段階的に広げ、読む範囲までの原文を文脈に足す。

    ``started_at`` は回答の開始の時刻（``clock`` の値）、
    ``deadline_remaining`` は回答の期限までの残りの秒数（無ければ None）。``priority`` は先に読む範囲
    （業務ガイドの手順の参照。#2828）。``related`` は文書ごとの上限で起点から外した候補の chunk の ID（上位の順。
    #5209）で、起点のある文書のものだけ、所有の章節を読んだ後に残りの予算で読む。

    ``block_chars`` は文脈の根拠を回答の入力の形にしたときの字数（分割読取に回すかの判定と同じ数え方。#5820）。
    渡されたとき、章節は起点の順に、足した後の根拠の全体が 1 回の生成の原文の予算（``evidence_budget_chars``）に
    入るものだけを足す。入らない章節は 1 段ずつ下げ、所有の章節も入らなければ読まない（回答に出さない）。
    """
    limits = SectionExpansionLimits.from_settings(settings)
    start = clock() if started_at is None else started_at
    trace: dict[str, Any] = {
        "enabled": True,
        "max_levels": limits.max_levels,
        "input_token_limit": limits.input_tokens,
        "levels": [],
    }
    chains: list[_Chain] = []
    # 先に読む範囲があるとき、範囲の外の文書の起点（段の外。所有の章節だけを最後に読む。#2828）。
    deferred: list[_Chain] = []
    result = SectionExpansionResult(context=context, trace=trace)
    current = context  # 読んだ章節を足した文脈（判定を飛ばした段の分を含む）

    def finish(reason: str) -> SectionExpansionResult:
        if deferred and reason not in {STOP_TIME_LIMIT, STOP_DISABLED, STOP_NO_SECTIONS}:
            read_deferred()
        if related and chains and reason not in {STOP_TIME_LIMIT, STOP_DISABLED, STOP_NO_SECTIONS}:
            read_related()
        if current is not result.context:
            # 判定を飛ばした段（読む範囲に届く前。#1503）で足した根拠も、止めた理由によらず返す（#1526）。
            result.context = current
            result.applied = True
        result.stop_reason = reason
        result.used_tokens = context_tokens(result.context)
        trace.update(
            stop_reason=reason,
            elapsed_seconds=max(0.0, clock() - start),
            applied=result.applied,
            levels_read=result.levels,
            used_tokens=result.used_tokens,
            unread=[item.to_payload() for item in result.unread],
            unread_omitted=[item.to_payload() for item in result.omitted],
            minimum_scope_complete=bool(chains) and scope_reached() if reader is not None and chains else False,
            completed_sections=[
                {"document_id": key[0], "chunk_set_id": key[1], "section_id": key[2]} for key in sorted(reader.read_sections)
            ]
            if reader is not None
            else [],
        )
        return result

    if not getattr(settings, "section_expansion_enabled", True) or limits.max_levels <= 0:
        trace["enabled"] = False
        return finish(STOP_DISABLED)
    # 最低でも所有の章節を読む。
    seeds = seed_chunk_ids(context)
    owners: set[tuple[str, str, str]] = set()
    for uid in seeds:
        try:
            outline = section_outline(chunk_uid=uid)
        except Exception:  # noqa: BLE001 - 対応を取得できなかったことを診断へ残す
            trace.setdefault("unmapped_seeds", []).append(uid)
            continue
        if not isinstance(outline, SectionOutline):
            trace.setdefault("unmapped_seeds", []).append(uid)
            continue
        key = (outline.document_id, outline.chunk_set_id, outline.owner_section_id)
        sections = outline.chain()
        trace.setdefault("expansion_routes", []).append(
            {
                "seed_id": uid,
                "document_id": outline.document_id,
                "chunk_set_id": outline.chunk_set_id,
                "origin_section_id": outline.owner_section_id,
                "sections": [
                    {"section_id": item.section_id, "page_start": item.page_start, "page_end": item.page_end} for item in sections
                ],
            }
        )
        if key in owners or not sections:
            continue
        owners.add(key)
        chains.append(_Chain(outline=outline, sections=sections, seed_uid=uid))
    trace["seeds"] = [
        {"document_id": c.outline.document_id, "chunk_set_id": c.outline.chunk_set_id, "section_id": c.outline.owner_section_id}
        for c in chains
    ]
    if not chains:
        return finish(STOP_NO_SECTIONS)

    reader = reader or SectionReader(read_chunks=section_chunks, known_ids=set())
    # 先に読む範囲（業務ガイドの手順の参照）に当たる起点を先に広げる（#2828。同じ段の中の並び）。
    ranks = {id(chain): _priority_rank(chain.outline, priority) for chain in chains}
    chains.sort(key=lambda c: ranks[id(c)])
    if any(rank < _RANK_OTHER for rank in ranks.values()):
        # 範囲の外の文書の起点は段の外に置き、範囲の起点の章節を先に読む（#2828）。
        deferred = [chain for chain in chains if ranks[id(chain)] == _RANK_OTHER]
        chains = [chain for chain in chains if ranks[id(chain)] < _RANK_OTHER]
        if deferred:
            trace["deferred_seeds"] = [
                {"document_id": c.outline.document_id, "chunk_set_id": c.outline.chunk_set_id, "section_id": c.outline.owner_section_id}
                for c in deferred
            ]
    reader.known_ids = _context_chunk_ids(context)
    reader.known_texts = {_record_key(record): str(getattr(record, "text", "") or "") for record in context.records}
    # 再検索の組立で本文が短縮された場合、前の段の読了をそのまま再利用しない。
    reader.read_sections = {
        key
        for key in reader.read_sections
        if all(
            str(getattr(item.record, "text", "") or "") in reader.known_texts.get(_record_key(item.record), "")
            for item in reader._groups.get(key, ())
        )
    }
    level_seconds = DEFAULT_LEVEL_SECONDS
    # 1 つの章節が入ってよい大きさ（文字数。1 文字 = 1 token。問題・プロンプト・ほかの根拠は数えない。#5812）。
    section_limit = limits.input_tokens
    # 根拠の全体（検索の根拠と足した章節）が入ってよい大きさ（回答の入力の形の字数。1 回の生成の原文の予算。#5820）。
    # これを超えると分割読取に回り、同じ入力でも回答の要点が回ごとに変わる。
    total_limit = int(getattr(settings, "evidence_budget_chars", 0) or 0) or limits.input_tokens
    room: int | None = None  # 残り（最初に章節を足すときに測る）
    over_total: set[tuple[str, str, str]] = set()  # 入らなかった章節（残りは減るだけなので測り直さない）
    if block_chars is not None:
        trace["total_limit_chars"] = total_limit

    def admit(outline: SectionOutline, section: SectionInfo, tree: Sequence[ContextParentEvidence]) -> bool:
        """章節の根拠を足しても、根拠の全体が 1 回の生成の原文の予算に入るか（入れば残りを減らす）。"""
        nonlocal room
        if block_chars is None or not tree:
            return True
        key = reader.section_key(outline, section)
        if key in over_total:
            return False
        if room is None:
            room = total_limit - block_chars(context)
        size = block_chars(_merge(replace(context, records=[], text="", evidence=(), evidence_tree=()), tree))
        if size > room:
            over_total.add(key)
            return False
        room -= size
        trace["total_room_chars"] = room
        return True

    def scope_reached() -> bool:
        """全起点で、起点から根までのどれかの章節を読んだか。"""
        return all(any(reader.section_key(chain.outline, item) in reader.read_sections for item in chain.sections) for chain in chains)

    def read_whole(outline: SectionOutline, section: SectionInfo, entry: dict[str, Any], added: list[ContextParentEvidence]) -> bool:
        """章節の全体を読む（読めた・読むものが無ければ True。読取の失敗は ``failure`` に、合計の上限は ``entry`` に残す）。"""
        tree = reader.read(outline, section)
        failure = reader.failures.get(reader.section_key(outline, section))
        if failure:
            entry.update(read="partial" if tree else "failed", added_parents=0)
            return False
        if not admit(outline, section, tree):
            entry.update(read="over_total", added_parents=0)
            return False
        reader.mark(outline, section, tree)
        added.extend(tree)
        entry.update(read="whole" if tree else "in_context", added_parents=len(tree))
        return True

    def read_owners(targets: Sequence[tuple[SectionOutline, SectionInfo, dict[str, Any]]]) -> None:
        """所有の章節だけを、大きさが上限以下で合計にも入るなら読む（読めなかったものは回答に出さず診断にだけ残す）。"""
        nonlocal current
        assert reader is not None
        added: list[ContextParentEvidence] = []
        for outline, section, entry in targets:
            failure = None
            if section.chars <= section_limit and read_whole(outline, section, entry, added):
                continue
            failure = reader.failures.get(reader.section_key(outline, section))
            if not failure:
                entry.setdefault("read", "over_limit")
            result.omitted.append(
                UnreadSection(
                    outline.document_id,
                    section.section_id,
                    section_label(outline, section),
                    failure or STOP_TOKEN_BUDGET,
                    section.page_start,
                )
            )
        if added:
            current = _merge(current, added)

    def read_deferred() -> None:
        """範囲の外の文書の起点の所有の章節を読む（#2828。親の章節へは上がらない）。"""
        entries: list[dict[str, Any]] = []
        targets = []
        for chain in deferred:
            section = chain.sections[0]
            key = reader.section_key(chain.outline, section)
            if key in reader.read_sections or any(entry["key"] == key for entry in entries):
                continue
            entry: dict[str, Any] = {"key": key, "section_id": section.section_id, "title": section.title}
            entries.append(entry)
            targets.append((chain.outline, section, entry))
        read_owners(targets)
        trace["deferred"] = [{k: v for k, v in entry.items() if k != "key"} for entry in entries]

    def read_related() -> None:
        """文書ごとの上限で起点から外した同じ文書の候補の所有の章節を読む（#5209）。

        子の章節を持たない章節だけを読む（文書全体・子を持つ章節は読まない）。回答の生成に残す時間が無ければ読まない。
        """
        documents = {(chain.outline.document_id, chain.outline.chunk_set_id) for chain in [*chains, *deferred]}
        entries: list[dict[str, Any]] = []
        targets: list[tuple[SectionOutline, SectionInfo, dict[str, Any]]] = []
        seen: set[tuple[str, str, str]] = set()
        for uid in dict.fromkeys(related):
            try:
                outline = section_outline(chunk_uid=uid)
            except Exception:  # noqa: BLE001 - 対応を取得できなかった候補は読まない
                continue
            if not isinstance(outline, SectionOutline) or (outline.document_id, outline.chunk_set_id) not in documents:
                continue
            section = outline.get(outline.owner_section_id)
            if section is None:
                continue
            key = reader.section_key(outline, section)
            if key in seen or key in reader.read_sections:
                continue
            seen.add(key)
            entry: dict[str, Any] = {
                "seed_id": uid,
                "document_id": outline.document_id,
                "chunk_set_id": outline.chunk_set_id,
                "section_id": section.section_id,
                "title": section.title,
            }
            entries.append(entry)
            if section.section_id == ROOT_SECTION_ID or outline.children(section.section_id):
                entry["read"] = "not_leaf"
                continue
            targets.append((outline, section, entry))
        trace["related"] = entries
        if not targets:
            return
        remaining_deadline = deadline_remaining()
        if remaining_deadline is not None and remaining_deadline < level_seconds + limits.generation_reserve_seconds:
            for outline, section, entry in targets:
                entry["read"] = "time_limit"
                result.omitted.append(
                    UnreadSection(
                        outline.document_id, section.section_id, section_label(outline, section), STOP_TIME_LIMIT, section.page_start
                    )
                )
            return
        read_owners(targets)

    # 起点ごとに、設定の段（1 = 所有の章節）までの章節のうち、大きさが上限以下の最も上の章節の全体を読む（#5812）。
    # 文書全体（根）も上限以下のときだけ読む。所有の章節も上限を超える起点は読まず、呼び出し元が親 → 前後で補う。
    # 足すと根拠の全体が 1 回の生成の原文の予算を超える章節は 1 段ずつ下げ、所有の章節も入らなければ同じく読まない（#5820）。
    level_trace: dict[str, Any] = {"level": limits.max_levels, "sections": []}
    trace["levels"].append(level_trace)
    added: list[ContextParentEvidence] = []
    for chain in chains:
        candidates = chain.sections[: limits.max_levels]
        within = [index for index in range(len(candidates) - 1, -1, -1) if candidates[index].chars <= section_limit]
        entry: dict[str, Any] = {"seed_id": chain.seed_uid}
        level_trace["sections"].append(entry)
        if not within:
            owner = candidates[0]
            entry.update(section_id=owner.section_id, title=owner.title, read="over_limit")
            continue
        for chosen in within:
            section = candidates[chosen]
            entry.update(
                section_id=section.section_id, title=section.title, level=chosen + 1, is_document=section.section_id == ROOT_SECTION_ID
            )
            if chosen + 1 < len(candidates):
                entry["shrunk_from_level"] = len(candidates)
            if any(reader.section_key(chain.outline, item) in reader.read_sections for item in chain.sections[chosen:]):
                entry["read"] = "in_context"
                break
            remaining_deadline = deadline_remaining()
            left = math.inf if remaining_deadline is None else remaining_deadline
            if left < level_seconds + limits.generation_reserve_seconds:
                _mark_unread(result, reader, [replace(chain, position=chosen - 1)], STOP_TIME_LIMIT)
                entry["read"] = "time_limit"
                break
            if read_whole(chain.outline, section, entry, added):
                result.levels = max(result.levels, chosen + 1)
                break
            if entry["read"] == "over_total":
                continue  # 1 段下の章節で確かめる
            result.unread.append(
                UnreadSection(
                    chain.outline.document_id,
                    section.section_id,
                    section_label(chain.outline, section),
                    reader.failures[reader.section_key(chain.outline, section)],
                    section.page_start,
                )
            )
            result.levels = max(result.levels, chosen + 1)
            break
    if added:
        current = _merge(current, added)
        level_trace["added_parents"] = len(added)
    if any(item.reason == STOP_TIME_LIMIT for item in result.unread):
        return finish(STOP_TIME_LIMIT)
    if result.unread:
        return finish(STOP_PARTIAL)
    # 所有の章節も 1/2 を超えた起点・合計に入らなかった起点は、章節ではなく親 → 前後で補う（回答に出さない）。
    over = any(entry.get("read") in {"over_limit", "over_total"} for entry in level_trace["sections"])
    return finish(STOP_TOKEN_BUDGET if over else STOP_SCOPE_READ)


def _mark_unread(result: SectionExpansionResult, reader: SectionReader, chains: Sequence[_Chain], reason: str) -> None:
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
        result.unread.append(
            UnreadSection(chain.outline.document_id, section.section_id, section_label(chain.outline, section), reason, section.page_start)
        )


__all__ = [
    "DEFAULT_MODEL_INPUT_TOKENS",
    "LIMIT_STOPS",
    "MAX_LEVELS",
    "STOP_SCOPE_READ",
    "ROOT_SECTION_ID",
    "SECTION_CONTEXT_ROLE",
    "SECTION_EXPANSION_REASON",
    "SectionExpansionLimits",
    "SectionExpansionResult",
    "SectionInfo",
    "SectionRead",
    "SectionReader",
    "SectionOutline",
    "UnreadSection",
    "context_tokens",
    "estimate_tokens",
    "expand_sections",
    "model_input_tokens",
    "section_label",
    "seed_chunk_ids",
]
