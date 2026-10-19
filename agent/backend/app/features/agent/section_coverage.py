"""回答に使った章節の未読の続き（#2492）。

`rag_read_section` は章節を `max_chars` までしか返さず、続きがあれば `section_complete=false` と
`next_cursor` を返す。手順・一覧・条件のまとまりを求める質問で、回答に使った章節の続きを読まずに
答えると、後半の手順・前提を欠いた回答になる。最終の検証（`rag_validate_answer`）は回答の主張が
根拠に合うかを確かめるだけなので、書かれていない手順の不足は見つからない。

ここは Run の step から章節ごとの読み取りの状態を決定的に追跡し（モデルは呼ばない）、回答に使った
章節のうち終わりまで読めていないものを返す。

- 同じ章節: 同じ MCP 接続・文書で、`section_id` が同じもの。文書が取り込み直されて続きの位置が
  古くなった（`source_stale`）後に読み直した章節は、題名が同じなら同じ章節とみなす。
- 読み切った: その章節か、その上の章節（読み取りの結果の `parent_section_id` をたどる）を
  `section_complete=true` まで読んだ。
- 続きの読み取り: `cursor` を渡した呼び出しを、その `next_cursor` を返した章節に対応付ける。
  読めなかった理由は、文書の版が変わった（`stale`）・ツールの呼び出しの上限（`budget`）・
  そのほかの失敗（`failed`）。続きを呼んでいなければ `unread`。
- 回答に使った章節: 回答が章節の chunk の id を書いたか、回答の本文が章節の本文・題名と当たる
  （`evidence_match`）。探索で読んだだけで回答に使っていない章節は数えない。
- 質問が手順・一覧・条件のまとまりを求めるとき（`asks_for_grouped_content`）だけ判定する。

呼ぶ側（組み込み Runtime）は、未読の続きがあり予算が残っていれば答える前に 1 回だけ差し戻し、
それでも残れば回答に「読み切れていない章節」を示し、最終の検証の成果物に主張の照合とは別の
`coverage` として残す。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.features.agent.answer_validation import cited_chunk_ids
from app.features.agent.evidence_match import EvidenceText, answer_features, matched_features
from app.features.agent.support_task import (
    BUDGET_EXCEEDED_CODE,
    RAG_READ_SECTION,
    asks_for_grouped_content,
)
from app.features.agent.tools import MCP_TOOL_SEPARATOR, mcp_base_tool_name

if TYPE_CHECKING:
    from app.features.agent.runtime import RunStep

JsonObject = dict[str, Any]

# 続きを読めていない理由。
REASON_UNREAD = "unread"
REASON_STALE = "stale"
REASON_BUDGET = "budget"
REASON_FAILED = "failed"
# 差し戻せば読める理由（予算の上限は差し戻しても読めない）。
RETRYABLE_REASONS = frozenset({REASON_UNREAD, REASON_STALE, REASON_FAILED})
# RAG が続きの位置を古い版のもの・形の違うものとして拒んだときの code。
_STALE_CODES = frozenset({"source_stale", "cursor_invalid"})

COVERAGE_COMPLETE = "complete"
COVERAGE_INCOMPLETE = "incomplete"
# 差し戻し・回答の注記に挙げる章節の上限。
MAX_UNREAD_SECTIONS = 5

UNREAD_SECTIONS_RETRY_HINT = (
    "直前の回答は、根拠にした次の章節を終わりまで読まずに作られています。質問は手順・一覧・条件の"
    "まとまりを求めているので、章節ごとに続きを読んでから改めて回答してください。読み切れない"
    "章節は、確かめていない範囲として回答に示してください。"
)
_CONTINUE_LINE = "next_cursor を rag_read_section の cursor（document_id も渡す）に渡して続きを読む"
_STALE_LINE = (
    "文書が更新され続きの位置が古い。rag_outline で章節を確かめ、section_id を "
    "rag_read_section に渡して最初から読み直す"
)
UNREAD_SECTIONS_HEADING = "読み切れていない章節"
UNREAD_SECTIONS_INTRO = (
    "次の章節は終わりまで読めていないため、手順・条件の全体は確かめられていません。"
)
_REASON_LABELS = {
    REASON_UNREAD: "続きを読んでいません",
    REASON_STALE: "文書が更新され、続きの位置が古くなりました",
    REASON_BUDGET: "この実行のツールの呼び出しの上限に達しました",
    REASON_FAILED: "続きの読み取りに失敗しました",
}


@dataclass
class _Section:
    """1 つの章節の読み取りの状態。"""

    connection: str
    document_id: str
    section_id: str
    title: str = ""
    file_name: str | None = None
    parent_section_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    complete: bool = False
    next_cursor: str | None = None
    reason: str = REASON_UNREAD
    chunk_ids: list[str] = field(default_factory=list)
    evidence: EvidenceText = field(default_factory=EvidenceText)


def _connection(function_name: str) -> str:
    """function tool の名前の接続の部分（`rag__rag_read_section` → `rag`。無ければ空）。"""
    if MCP_TOOL_SEPARATOR not in function_name:
        return ""
    return function_name.split(MCP_TOOL_SEPARATOR, 1)[0]


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _failure_reason(step: RunStep) -> str:
    result = step.tool_result
    if result is None:
        return REASON_FAILED
    if result.error_code == BUDGET_EXCEEDED_CODE:
        return REASON_BUDGET
    details = result.error_details if isinstance(result.error_details, dict) else {}
    if result.error_code in _STALE_CODES or details.get("error_code") in _STALE_CODES:
        return REASON_STALE
    return REASON_FAILED


def _track(steps: Sequence[RunStep]) -> list[_Section]:
    """`rag_read_section` の step から、章節ごとの読み取りの状態を作る（読んだ順）。"""
    sections: dict[tuple[str, str, str], _Section] = {}
    by_cursor: dict[tuple[str, str], _Section] = {}
    parents: dict[tuple[str, str, str], str] = {}
    for step in steps:
        call = step.tool_call
        if call is None or mcp_base_tool_name(call.name) != RAG_READ_SECTION:
            continue
        connection = _connection(call.name)
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        cursor = _text(arguments.get("cursor"))
        result = step.tool_result
        output = result.output if result is not None else None
        if (
            result is None
            or not result.success
            or step.status != "completed"
            or not isinstance(output, dict)
        ):
            entry = by_cursor.get((connection, cursor)) if cursor else None
            if entry is not None and not entry.complete:
                entry.reason = _failure_reason(step)
            continue
        section = output.get("section")
        document_id = _text(output.get("document_id"))
        section_id = _text(section.get("section_id")) if isinstance(section, dict) else ""
        if not isinstance(section, dict) or not document_id or not section_id:
            continue
        key = (connection, document_id, section_id)
        entry = sections.get(key)
        if entry is None:
            entry = sections[key] = _Section(connection, document_id, section_id)
        entry.title = _text(section.get("title")) or entry.title
        # 古い版の位置で続きを読めなかった同じ題名の章節は、取り込み直した後のこの章節にまとめる。
        for other_key, other in list(sections.items()):
            if (
                other is not entry
                and other.reason == REASON_STALE
                and not other.complete
                and other.connection == connection
                and other.document_id == document_id
                and entry.title
                and other.title == entry.title
            ):
                entry.chunk_ids += [item for item in other.chunk_ids if item not in entry.chunk_ids]
                entry.evidence.add("\n".join(other.evidence.texts), [])
                del sections[other_key]
        entry.file_name = _text(output.get("file_name")) or entry.file_name
        entry.parent_section_id = _text(section.get("parent_section_id")) or None
        entry.page_start = _int(section.get("page_start"))
        entry.page_end = _int(section.get("page_end"))
        if entry.parent_section_id:
            parents[key] = entry.parent_section_id
        for child in output.get("child_sections") or []:
            child_id = _text(child.get("section_id")) if isinstance(child, dict) else ""
            if child_id:
                parents[(connection, document_id, child_id)] = section_id
        for chunk in output.get("chunks") or []:
            chunk_id = _text(chunk.get("chunk_id")) if isinstance(chunk, dict) else ""
            if chunk_id and chunk_id not in entry.chunk_ids:
                entry.chunk_ids.append(chunk_id)
        entry.evidence.add(output.get("text"), [entry.title])
        if output.get("section_complete") is True:
            entry.complete = True
            entry.next_cursor = None
        elif not entry.complete:
            entry.next_cursor = _text(output.get("next_cursor")) or None
            entry.reason = REASON_UNREAD
            if entry.next_cursor:
                by_cursor[(connection, entry.next_cursor)] = entry
    complete = {key for key, entry in sections.items() if entry.complete}
    unread: list[_Section] = []
    for key, entry in sections.items():
        if entry.complete:
            continue
        # 上の章節を読み切っていれば、その中の章節も読み切っている。
        connection, document_id, section_id = key
        seen = {section_id}
        parent = parents.get(key)
        covered = False
        while parent and parent not in seen:
            if (connection, document_id, parent) in complete:
                covered = True
                break
            seen.add(parent)
            parent = parents.get((connection, document_id, parent))
        if not covered:
            unread.append(entry)
    return unread


def _used(entry: _Section, answer: str) -> bool:
    """回答がその章節を使ったか（chunk の id を書いた・本文・題名と当たる）。"""
    if cited_chunk_ids(answer, entry.chunk_ids):
        return True
    return bool(matched_features(answer_features(answer), entry.evidence))


def _record(entry: _Section, *, budget_exhausted: bool) -> JsonObject:
    reason = entry.reason
    if budget_exhausted and reason == REASON_UNREAD:
        reason = REASON_BUDGET
    return {
        "connection": entry.connection,
        "document_id": entry.document_id,
        "file_name": entry.file_name,
        "section_id": entry.section_id,
        "title": entry.title,
        "page_start": entry.page_start,
        "page_end": entry.page_end,
        "next_cursor": entry.next_cursor,
        "reason": reason,
    }


def section_coverage(
    steps: Sequence[RunStep],
    answer: str,
    question_texts: Sequence[object],
    *,
    budget_exhausted: bool = False,
) -> JsonObject | None:
    """回答に使った章節を終わりまで読んだか（判定しなければ None）。

    質問が手順・一覧・条件のまとまりを求め、`rag_read_section` で読んだ章節がある Run だけ判定する。
    返すのは `status`（`complete` / `incomplete`）と、回答に使ったのに終わりまで読めていない章節
    （`sections`。理由 `reason` つき、最大 `MAX_UNREAD_SECTIONS` 件）。`budget_exhausted` は、
    この実行のツールの呼び出しの上限に達したか（続きを読んでいない章節の理由を `budget` にする）。
    """
    if not answer.strip() or not asks_for_grouped_content(question_texts):
        return None
    if not any(
        step.tool_call is not None and mcp_base_tool_name(step.tool_call.name) == RAG_READ_SECTION
        for step in steps
    ):
        return None
    unread = [
        _record(entry, budget_exhausted=budget_exhausted)
        for entry in _track(steps)
        if _used(entry, answer)
    ][:MAX_UNREAD_SECTIONS]
    return {
        "status": COVERAGE_INCOMPLETE if unread else COVERAGE_COMPLETE,
        "sections": unread,
    }


def _label(section: JsonObject) -> str:
    start, end = section.get("page_start"), section.get("page_end")
    pages = ""
    if isinstance(start, int):
        pages = f"p.{start}" + (f"-{end}" if isinstance(end, int) and end != start else "")
    name = section.get("file_name") or section.get("document_id")
    place = "、".join(str(item) for item in (name, pages) if item)
    return f"「{section.get('title') or section.get('section_id')}」（{place}）"


def unread_sections_message(sections: Sequence[JsonObject]) -> str:
    """差し戻しの文（章節ごとに 1 行。続きの cursor か読み直し方）。"""
    lines = [UNREAD_SECTIONS_RETRY_HINT, ""]
    for section in sections:
        head = (
            f"- {_label(section)}（document_id: {section['document_id']}、"
            f"section_id: {section['section_id']}）: "
        )
        if section.get("reason") == REASON_STALE or not section.get("next_cursor"):
            lines.append(head + _STALE_LINE + "。")
        else:
            lines.append(head + f"{_CONTINUE_LINE}（next_cursor: {section['next_cursor']}）。")
    return "\n".join(lines)


def with_unread_sections_notice(answer: str, sections: Sequence[JsonObject]) -> str:
    """回答の末尾に、終わりまで読めていない章節と理由を足す（利用者に見せる回答）。"""
    lines = [UNREAD_SECTIONS_INTRO]
    for section in sections:
        reason = _REASON_LABELS.get(str(section.get("reason")), _REASON_LABELS[REASON_FAILED])
        lines.append(f"- {_label(section)}: {reason}")
    return f"{answer.rstrip()}\n\n**{UNREAD_SECTIONS_HEADING}**\n\n" + "\n".join(lines)


__all__ = [
    "COVERAGE_COMPLETE",
    "COVERAGE_INCOMPLETE",
    "RETRYABLE_REASONS",
    "UNREAD_SECTIONS_HEADING",
    "UNREAD_SECTIONS_RETRY_HINT",
    "section_coverage",
    "unread_sections_message",
    "with_unread_sections_notice",
]
