"""当たった chunk の章節から、親の章節へ段階的に広げて回答の文脈に入れる（#1436）。

章節の正本は文書の「章節ナビゲーション」で、章節と chunk の対応（#1438）は呼び出し元（backend）が持つ。この module は
対応を ``AnswerDependencies.section_outline`` / ``section_chunks`` から読み、広げる順・予算・止める条件だけを決める
（I/O は注入された関数を通す。決定論のテストはスタブで行う）。

広げる順は、当たった chunk → 所有の章節（leaf）→ 親の章節 → … → 最上位の章節 → 文書全体（根の章節）。

- 読む範囲（当たった章節 = leaf）までを読む。足りるかの判定はせず、上限（段・token・時間）で止める。
- 上の段の章節は、予算に入れば全体を読む。入らなければ、子の章節の見出し（目次）を質問との関連度（rerank。使えなければ
  語の重なり）で並べ、関連の高い子の章節から、予算に入る章節を全体で読む。入らない子の章節は読まない。
- 章節が予算に入るかは、章節の chunk のうち、まだ文脈に無いものの token で決める（最初の根拠・前の段で読んだ
  chunk は数えない。#1504）。章節の chunk がすべて文脈にあれば、読むものは無い（診断の ``read: "in_context"``）。
- 子の章節が無い（または子の章節も入らない）章節は、見積もりで入るなら先頭から入る分だけ読み、入らなければ読まない。
- 上限（入力 token・段の数・時間）で止めたときは、読めなかった章節を返し、呼び出し元は回答を条件つき
  （``conditional``）にして「回答に未反映の関連章節」に出す。ただし、当たった章節より上の段で子の章節を読むときに、
  関連度の順でより上の兄弟の章節を全体で読んだ後に予算で読めなかった章節のうち、どの起点の chunk も含まないものは、
  回答に出さず、診断（``unread_omitted``）にだけ残す（#2816。質問に合わせて広げた参考の
  範囲で、読めなくても当たった章節の読了は崩れない）。読取の失敗は、どの章節でも回答に出す。
- 1 回の回答の中で後に別の起点から広げる段（業務ガイドの手順ごとの検索の後など）があるときは、呼び出し元が
  ``reserve_tokens`` で残す token を渡し、先の段はその分を使わずに止める（#2816）。後の段は、前の段で起点に
  しなかった起点の章節を先に広げる。
- 呼び出し元が先に読む範囲（``priority``。業務ガイドの手順の参照の文書と頁）を渡したら、参照の頁に重なる起点 →
  参照の文書の起点 → その他の起点の順に広げる（#2828）。参照の文書の起点があれば、その他の文書の起点は段の外に
  置き（親へ上がらない）、所有の章節だけを止めた後に残りの予算で読む。読めなくても回答に出さず、診断
  （``unread_omitted``）にだけ残す（質問と関係の弱い別の文書の章節が予算と「回答に未反映の関連章節」を占めない）。
- 「回答に未反映の関連章節」に出す行は、回答の生成の後に ``answer_unread_view`` で、回答が根拠にした文書と先に読む
  範囲の文書の章節に絞り、``MAX_UNREAD_LINES`` 行を超えた分は件数の 1 行にする（#2828）。

入力 token の上限は、回答のモデルの入力の上限（共通のモデル設定の登録モデルの「入力の上限（tokens）」。#1437。
backend が platform の ``enterprise_ai_max_input_tokens`` で決めて ``answer_max_input_tokens`` で渡す）に対する割合
（既定 50%）。根拠の本文の token は ``estimate_tokens``（platform の ``estimate_input_tokens`` と同じ見積もり）で
数える。文脈の並びは、もとの根拠（関連度の順）を先頭に、広げた章節をその後ろに章節の中の読み順で置く。引用の ID は
chunk のまま（章節の chunk は検索の chunk と同じ親子の record にする）。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any

from rag_engine.dependencies import report_degraded
from rag_engine.generation.answer_models import AnswerContext
from rag_engine.generation.answer_records import AnswerRecord, _stored_chunk_answer_record
from rag_engine.resources.deadline import ANSWER_FINALIZE_RESERVE_SECONDS, DEFAULT_GENERATION_ROUND_SECONDS
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
# 1 つの章節で読む chunk の上限（文書全体でも、これより多い章節は予算に関係なく読まない）。
MAX_SECTION_CHUNKS = 400
# モデルの入力の上限の既定（tokens。platform の DEFAULT_ENTERPRISE_AI_MAX_INPUT_TOKENS と同じ。#1437）。
DEFAULT_MODEL_INPUT_TOKENS = 128_000
# 回答の生成に残す秒数。これより残りが少なければ上がらない。生成と監査の 1 組の見積もりの既定と回答の確定の分の和
# （#1516。150 秒は、長い根拠で 1 組 125〜344 秒かかるモデルの 1 組目に足りなかった）。是正の round は、残りの時間と
# 直前の組の実測から grounded の `RoundBudget` が決める。
GENERATION_RESERVE_SECONDS = DEFAULT_GENERATION_ROUND_SECONDS + ANSWER_FINALIZE_RESERVE_SECONDS
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
STOP_SECTION_CHUNKS = "section_chunks"
LIMIT_STOPS = frozenset({STOP_MAX_LEVELS, STOP_TOKEN_BUDGET, STOP_TIME_LIMIT})
# 「回答に未反映の関連章節」に出す行の上限（超えた分は件数の 1 行にする。#2828）。
MAX_UNREAD_LINES = 10
# 「回答に未反映の関連章節」の各行の理由（利用者の言葉。#2040）。段・入力・chunk の上限は
# 利用者から見て同じ意味なので「読み込み量の上限」にまとめる。上限の区別は工程の記録（``_SECTION_STOP_LABELS``）と
# 回答の診断の ``section_expansion.unread[].reason`` に残す。
_READ_LIMIT_LABEL = "読み込み量の上限"
_LIMIT_LABELS = {
    STOP_MAX_LEVELS: _READ_LIMIT_LABEL,
    STOP_TOKEN_BUDGET: _READ_LIMIT_LABEL,
    STOP_TIME_LIMIT: "回答時間の上限",
    STOP_SECTION_CHUNKS: _READ_LIMIT_LABEL,
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
    """上限で読めなかった章節（「回答に未反映の関連章節」に出す）。"""

    document_id: str
    section_id: str
    label: str
    reason: str
    # 章節（範囲）の始まりの頁。画面が各行から原文を開くのに使う（#2040。頁の無い文書は None）。
    page: int | None = None

    def gap_line(self) -> str:
        """本文の行。影響（回答に未反映）と理由を書き言葉で述べる（#2040）。"""
        if self.reason == STOP_READ_ERROR:
            return f"{self.label}は、原文を取得できなかったため未反映です。"
        if self.reason == STOP_PARTIAL:
            return f"{self.label}は、原文の一部だけを取得したため未確認の範囲が残っています。"
        return f"{self.label}は、{_LIMIT_LABELS.get(self.reason, _READ_LIMIT_LABEL)}に達したため未反映です。"

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
        lines = [*lines[:limit], f"ほか {overflow} 件の関連章節も未反映です。"]
    return UnreadView(lines=tuple(lines), hidden=tuple(hidden), overflow=overflow)


class SectionRead(list[Any]):
    """章節の原文と読取結果。空・権限による欠落・途中までの取得を全量と区別する。"""

    def __init__(self, chunks: Sequence[Any] = (), *, complete: bool, reason: str = "") -> None:
        super().__init__(chunks)
        self.complete = complete
        self.reason = reason


@dataclass(frozen=True)
class SectionExpansionLimits:
    """拡張の上限（検索方法の全体設定で決める）。``input_tokens`` は根拠の本文の入力 token の上限。"""

    max_levels: int
    input_tokens: int
    # 後に別の起点から広げる段があるとき、先の段で使わずに残す token（``input_tokens`` に対する割合から。#2816）。
    followup_reserve_tokens: int = 0

    @classmethod
    def from_settings(cls, settings: Any) -> SectionExpansionLimits:
        percent = max(1, min(100, int(getattr(settings, "section_expansion_input_token_percent", 50) or 50)))
        input_tokens = model_input_tokens(settings) * percent // 100
        reserve = max(0, min(50, int(getattr(settings, "section_expansion_followup_reserve_percent", 20) or 0)))
        return cls(
            max_levels=max(0, int(getattr(settings, "section_expansion_max_levels", 4))),
            input_tokens=input_tokens,
            followup_reserve_tokens=input_tokens * reserve // 100,
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
    # 前の呼び出しで起点にした章節（文書・chunk_set・所有の章節）。後の呼び出しは新しい起点を先に広げる（#2816）。
    seeded_owners: set[tuple[str, str, str]] = field(default_factory=set)

    def section_key(self, outline: SectionOutline, section: SectionInfo) -> tuple[str, str, str]:
        return outline.document_id, outline.chunk_set_id, section.section_id

    def unread_chars(self, outline: SectionOutline, section: SectionInfo) -> int:
        """読む前の見積もり（章節の文字数から、読み終えた子孫の章節の分を引く）。"""
        done = [
            item
            for item in outline.sections
            if self.section_key(outline, item) in self.read_sections and _is_descendant(outline, item, section)
        ]
        top = [item for item in done if not any(other is not item and _is_descendant(outline, item, other) for other in done)]
        return max(0, section.chars - sum(item.chars for item in top))

    def groups(self, outline: SectionOutline, section: SectionInfo) -> list[ContextParentEvidence]:
        """章節の chunk を読み、親ごとにまとめて章節の中の読み順で返す（文脈にある親も含む。child は章節の中の順）。"""
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
        tree = [
            ContextParentEvidence(
                record=parents.get(group_key) or children[0],
                role="synthesis_parent" if group_key in parents else "child_fallback",
                reason=SECTION_EXPANSION_REASON,
                children=tuple(
                    ContextChildEvidence(
                        record=child,
                        role=SECTION_CONTEXT_ROLE,  # type: ignore[arg-type]
                        reason=SECTION_EXPANSION_REASON,
                    )
                    for child in children
                ),
            )
            for group_key, children in grouped.items()
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


def _rank_children(
    question: str, outline: SectionOutline, children: Sequence[SectionInfo], rerank: Callable[..., Any] | None, settings: Any
) -> tuple[list[SectionInfo], str]:
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
        except Exception as exc:  # noqa: BLE001 - 並びは補助。語の重なりで並べる
            # ``ranking: lexical`` は rerank が無いときと同じなので、失敗を呼び出し元の診断に残す（#3286）。
            report_degraded("section_child_rerank", exc)
    from rag_engine.retrieval.evidence_selection import evidence_relevance

    scored = sorted(range(len(children)), key=lambda index: (-evidence_relevance(question, documents[index]), index))
    return [children[index] for index in scored], "lexical"


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
    rerank: Callable[..., Any] | None = None,
    started_at: float | None = None,
    deadline_remaining: Callable[[], float | None] = lambda: None,
    clock: Callable[[], float] = monotonic,
    reader: SectionReader | None = None,
    reserve_tokens: int = 0,
    priority: Sequence[PriorityRange] = (),
) -> SectionExpansionResult:
    """当たった chunk の章節から段階的に広げ、読む範囲までの原文を文脈に足す。

    ``started_at`` は回答の開始の時刻（``clock`` の値）、
    ``deadline_remaining`` は回答の期限までの残りの秒数（無ければ None）。``reserve_tokens`` は、後の段のために
    この呼び出しで使わずに残す入力 token（#2816）。``priority`` は先に読む範囲（業務ガイドの手順の参照。#2828）。
    """
    limits = SectionExpansionLimits.from_settings(settings)
    reserved = max(0, min(int(reserve_tokens or 0), limits.input_tokens))
    budget = limits.input_tokens - reserved
    start = clock() if started_at is None else started_at
    trace: dict[str, Any] = {
        "enabled": True,
        "max_levels": limits.max_levels,
        "input_token_limit": limits.input_tokens,
        "levels": [],
    }
    if reserved:
        trace["reserved_tokens"] = reserved
    chains: list[_Chain] = []
    # 先に読む範囲があるとき、範囲の外の文書の起点（段の外。所有の章節だけを最後に読む。#2828）。
    deferred: list[_Chain] = []
    result = SectionExpansionResult(context=context, trace=trace)
    current = context  # 読んだ章節を足した文脈（判定を飛ばした段の分を含む）

    def finish(reason: str) -> SectionExpansionResult:
        if deferred and reason not in {STOP_TIME_LIMIT, STOP_DISABLED, STOP_NO_SECTIONS}:
            read_deferred()
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
    # 先に読む範囲（業務ガイドの手順の参照）に当たる起点を先に、前の呼び出しで起点にしなかった起点（後の段の新しい
    # 根拠）をその次に広げる（#2828・#2816。同じ段の中の並び）。
    ranks = {id(chain): _priority_rank(chain.outline, priority) for chain in chains}
    chains.sort(
        key=lambda c: (
            ranks[id(c)],
            (c.outline.document_id, c.outline.chunk_set_id, c.outline.owner_section_id) in reader.seeded_owners,
        )
    )
    if any(rank < _RANK_OTHER for rank in ranks.values()):
        # 範囲の外の文書の起点は段の外に置き、範囲の起点の章節を先に読む（#2828）。
        deferred = [chain for chain in chains if ranks[id(chain)] == _RANK_OTHER]
        chains = [chain for chain in chains if ranks[id(chain)] < _RANK_OTHER]
        if deferred:
            trace["deferred_seeds"] = [
                {"document_id": c.outline.document_id, "chunk_set_id": c.outline.chunk_set_id, "section_id": c.outline.owner_section_id}
                for c in deferred
            ]
    reader.seeded_owners.update(owners)
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

    def scope_reached() -> bool:
        """読む範囲（全起点の所有の章節）まで読んだか。"""
        return all(reader.section_key(chain.outline, chain.sections[0]) in reader.read_sections for chain in chains)

    def remaining_now(added: Sequence[ContextParentEvidence]) -> int:
        return budget - context_tokens(current) - _tree_tokens(added)

    def read_deferred() -> None:
        """範囲の外の文書の起点の所有の章節を、残りの予算に全体が入るものだけ読む（#2828）。

        読めなかった章節は回答に出さず、診断（``unread_omitted``）にだけ残す。親の章節へは上がらない。
        """
        nonlocal current
        assert reader is not None
        added: list[ContextParentEvidence] = []
        entries: list[dict[str, Any]] = []
        for chain in deferred:
            section = chain.sections[0]
            key = reader.section_key(chain.outline, section)
            if key in reader.read_sections or any(entry["key"] == key for entry in entries):
                continue
            entry: dict[str, Any] = {"key": key, "section_id": section.section_id, "title": section.title}
            entries.append(entry)
            tree = reader.read(chain.outline, section) if section.chunk_count <= MAX_SECTION_CHUNKS else None
            failure = reader.failures.get(key)
            if tree is not None and not failure and _tree_tokens(tree) <= remaining_now(added):
                reader.mark(chain.outline, section, tree)
                added.extend(tree)
                entry.update(read="whole" if tree else "in_context", added_parents=len(tree))
                continue
            entry["read"] = "failed" if failure else "skipped"
            result.omitted.append(
                UnreadSection(
                    chain.outline.document_id,
                    section.section_id,
                    section_label(chain.outline, section),
                    failure or STOP_TOKEN_BUDGET,
                    section.page_start,
                )
            )
        if added:
            current = _merge(current, added)
        trace["deferred"] = [{k: v for k, v in entry.items() if k != "key"} for entry in entries]

    def optional(chain: _Chain, section: SectionInfo, after_read: bool) -> bool:
        """予算で読めなくても回答に出さない章節か（#2816）。

        当たった章節（読む範囲が leaf のとき）より上の段で読む子の章節のうち、関連度の順でより上の兄弟の章節を
        全体で読んだ後に読めなかったもので（``after_read``）、どの起点の chunk も含まず、読取に失敗していないもの。
        """
        if (
            not after_read
            or chain.position <= 0
            or section.section_id == chain.sections[chain.position].section_id
            or reader.section_key(chain.outline, section) in reader.failures
        ):
            return False
        same = (chain.outline.document_id, chain.outline.chunk_set_id)
        for other in chains:
            if (other.outline.document_id, other.outline.chunk_set_id) != same:
                continue
            owner = other.outline.get(other.outline.owner_section_id)
            if owner is not None and (owner.section_id == section.section_id or _is_descendant(other.outline, owner, section)):
                return False
        return True

    def report(chain: _Chain, item: UnreadSection, section: SectionInfo, after_read: bool) -> None:
        """予算で読めなかった章節を、回答に出すもの（``unread``）と診断だけのもの（``omitted``）に分ける。"""
        (result.omitted if optional(chain, section, after_read) else result.unread).append(item)

    def try_whole(chain: _Chain, section: SectionInfo, entry: dict[str, Any], added: list[ContextParentEvidence]) -> bool:
        """章節の、まだ文脈に無い親が予算に入れば全体を読む（読んだ・読む必要が無ければ True）。

        文脈にすでにある chunk（最初の根拠・前の段で読んだ章節）は数えない（#1504。二重に数えない）。章節の chunk が
        すべて文脈にあれば、読むものが無い（``in_context``）。
        """
        if section.chunk_count > MAX_SECTION_CHUNKS:
            return False
        tree = reader.read(chain.outline, section)
        failure = reader.failures.get(reader.section_key(chain.outline, section))
        if failure:
            result.unread.append(
                UnreadSection(
                    chain.outline.document_id, section.section_id, section_label(chain.outline, section), failure, section.page_start
                )
            )
            entry.update(read="partial" if tree else "failed", added_parents=0)
            return False
        if _tree_tokens(tree) > remaining_now(added):
            return False
        if not tree:
            reader.mark(chain.outline, section, ())
            entry.update(read="in_context", added_parents=0)
            return True
        reader.mark(chain.outline, section, tree)
        added.extend(tree)
        entry.update(read="whole", added_parents=len(tree))
        return True

    def fit_or_skip(
        chain: _Chain, section: SectionInfo, entry: dict[str, Any], added: list[ContextParentEvidence], after_read: bool = False
    ) -> None:
        """予算に入らない章節: 見積もりで入るなら先頭から入る分だけ読み、入らなければ読まない。"""
        remaining = remaining_now(added)
        label = section_label(chain.outline, section)
        if section.chunk_count <= MAX_SECTION_CHUNKS and reader.unread_chars(chain.outline, section) <= remaining:
            kept, _whole = _fit_tree(reader.read(chain.outline, section), remaining)
            reader.known_ids.update(_record_key(item.record) for item in kept)
            added.extend(kept)
            entry.update(read="partial", added_parents=len(kept))
            label += "の残り"
        else:
            entry["read"] = "skipped"
        report(
            chain,
            UnreadSection(chain.outline.document_id, section.section_id, label, STOP_TOKEN_BUDGET, section.page_start),
            section,
            after_read,
        )

    for level in range(1, limits.max_levels + 1):
        targets = [chain for chain in chains if chain.next_section is not None]
        if not targets:
            return finish(STOP_DOCUMENT)
        # 時間: 回答全体の期限までに、この段と生成の分が残るか。
        remaining_deadline = deadline_remaining()
        left = math.inf if remaining_deadline is None else remaining_deadline
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
            entry: dict[str, Any] = {
                "section_id": section.section_id,
                "title": section.title,
                "is_document": section.section_id == ROOT_SECTION_ID,
            }
            level_trace["sections"].append(entry)
            if try_whole(chain, section, entry, added):
                continue
            if reader.section_key(chain.outline, section) in reader.failures:
                budget_hit = True
                continue
            # 予算に入らない: 子の章節を関連度の順に、入るものから全体で読む。
            done = chain.sections[chain.position - 1] if chain.position > 0 else None
            children = [
                child for child in chain.outline.children(section.section_id) if done is None or child.section_id != done.section_id
            ]
            if not children:
                # 子の章節が無い章節（大きな leaf・子をすべて読んだ章節）。
                fit_or_skip(chain, section, entry, added)
                budget_hit = True
                continue
            ranked, method = _rank_children(question, chain.outline, children, rerank, settings)
            entry.update(read="children", ranking=method, children=[])
            read_above = False  # 関連度の順でより上の子の章節を全体で読んだ（#2816）
            for child in ranked:
                child_key = reader.section_key(chain.outline, child)
                if child_key in reader.read_sections:
                    continue
                child_entry: dict[str, Any] = {"section_id": child.section_id, "title": child.title}
                entry["children"].append(child_entry)
                if try_whole(chain, child, child_entry, added):
                    read_above = True
                    continue
                if child_key in reader.failures:
                    # 読取に失敗した子の章節は、失敗として出す（予算で読めなかった章節にしない）。
                    budget_hit = True
                    continue
                fit_or_skip(chain, child, child_entry, added, read_above)
                budget_hit = True
        result.levels = level
        level_trace["labels"] = labels
        if added:
            current = _merge(current, added)
            level_trace["added_parents"] = len(added)
        if scope_reached() or budget_hit:
            return finish(STOP_PARTIAL if result.unread else STOP_SCOPE_READ)
    remaining_targets = [chain for chain in chains if chain.next_section is not None]
    if not remaining_targets:
        return finish(STOP_DOCUMENT)
    _mark_unread(result, reader, remaining_targets, STOP_MAX_LEVELS)
    return finish(STOP_MAX_LEVELS)


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
    "GENERATION_RESERVE_SECONDS",
    "DEFAULT_MODEL_INPUT_TOKENS",
    "LIMIT_STOPS",
    "MAX_SECTION_CHUNKS",
    "STOP_SCOPE_READ",
    "STOP_SECTION_CHUNKS",
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
