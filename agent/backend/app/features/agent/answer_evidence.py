"""回答の根拠（成果物 kind=`answer_evidence`・「回答の根拠」。#2328）。

チャットの回答の下の「AI に渡した N 件 ／ 回答で引用 M 件」（RAG のチャットと同じ
共有の部品）と、外部の MCP クライアントへの `sources` の正本。組み込み Runtime は
回答を確定するときに 1 回作る。

- AI に渡した根拠: その Run でモデルに渡したツールの結果のうち、根拠を返すツール
  （`rag_search` / `rag_retrieve_evidence`）の `evidence`。同じ chunk
  （`document_id`・`chunk_id`）は 1 回だけ、出てきた順。
- 回答で引用した根拠: 利用者に見せる回答（最終の検証の後）が書いた根拠の ID
  （省いて書いた形を含む。最終の検証が根拠を並べるときと同じ照合
  `answer_validation.cited_chunk_ids`）と、最終の検証が回答の段落を裏付けた
  （`supported`）根拠。
- 回答での使われ方（`answer_use`。該当確認済み / 参考）は、回答で引用した根拠に、
  RAG が同じ根拠に付けた値があれば残す。

内容は画面と MCP が読む形（RAG の MCP の根拠の項目の一部と `cited`）。本文の抜粋は
RAG が返した長さのまま。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from app.features.agent.answer_passages import passage_spans
from app.features.agent.answer_validation import STATUS_COMPLETED, cited_chunk_ids
from app.features.agent.support_task import RAG_RETRIEVE_EVIDENCE, RAG_SEARCH
from app.features.agent.tools import mcp_base_tool_name

if TYPE_CHECKING:
    from app.features.agent.runtime import RunStep

JsonObject = dict[str, Any]

ANSWER_EVIDENCE_KIND = "answer_evidence"
ANSWER_EVIDENCE_NAME = "回答の根拠"
# 根拠を返すツール（AI に渡した根拠として数える）。本文を読むツールは根拠の一覧を
# 返さないので数えない。
EVIDENCE_LIST_TOOLS = frozenset({RAG_SEARCH, RAG_RETRIEVE_EVIDENCE})
# 1 つの Run の根拠の上限（画面の一覧と成果物の大きさ）。
MAX_ANSWER_EVIDENCE = 200
_ANSWER_USES = frozenset({"confirmed", "reference"})
# RAG の根拠の項目のうち、そのまま写すもの。
_COPIED_FIELDS = (
    "document_id",
    "chunk_id",
    "file_name",
    "excerpt",
    "truncated",
    "locator",
    "role",
    "content_kind",
    "evidence_type",
    "image_ref",
    "score",
    "rerank_score",
    "superseded",
)


def _passed_evidence(steps: Sequence[RunStep]) -> list[JsonObject]:
    """その Run でモデルに渡した根拠（chunk で重複を除き、出てきた順）。

    同じ chunk を後の呼び出しが返し、RAG がそこで回答での使われ方（`answer_use`）を付けていれば、
    その値を足す。
    """
    items: dict[tuple[str, str], JsonObject] = {}
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if mcp_base_tool_name(call.name) not in EVIDENCE_LIST_TOOLS:
            continue
        output = result.output
        evidence = output.get("evidence") if isinstance(output, Mapping) else None
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, Mapping):
                continue
            document_id, chunk_id = item.get("document_id"), item.get("chunk_id")
            if not isinstance(document_id, str) or not isinstance(chunk_id, str):
                continue
            if not document_id or not chunk_id:
                continue
            known = items.get((document_id, chunk_id))
            if known is not None:
                if known.get("answer_use") not in _ANSWER_USES:
                    known["answer_use"] = item.get("answer_use")
                continue
            if len(items) < MAX_ANSWER_EVIDENCE:
                items[(document_id, chunk_id)] = dict(item)
    return list(items.values())


def _supported_claims(validation: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    """最終の検証が裏付けた（`supported`）段落と根拠の chunk（段落の原文, chunk）。"""
    if not isinstance(validation, Mapping) or validation.get("status") != STATUS_COMPLETED:
        return []
    result = validation.get("result")
    claims = result.get("claims") if isinstance(result, Mapping) else None
    found: list[tuple[str, str]] = []
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, Mapping) or claim.get("status") != "supported":
            continue
        quote, chunk_id = claim.get("answer_quote"), claim.get("chunk_id")
        if isinstance(chunk_id, str) and chunk_id:
            found.append((quote if isinstance(quote, str) else "", chunk_id))
    return found


# 回答が本文に書いた根拠の ID の印（「【証拠 ID: 0f3a…:7】」「〔根拠: …〕」「[evidence_id: …]」）。
# 中の ID を根拠に引き当てられた印だけを数える（Markdown のリンク `[文](URL)` は除く）。
_MARKER = re.compile(r"【[^】\n]{1,200}】|〔[^〕\n]{1,200}〕|\[[^\]\n]{1,200}\](?!\()")


def _ordered_ids(ids: set[str], order: Sequence[str]) -> list[str]:
    return [chunk_id for chunk_id in order if chunk_id in ids]


def _markers(answer: str, chunk_ids: Sequence[str]) -> list[JsonObject]:
    """回答の中の根拠の ID の印と、引き当てた根拠（印の原文, chunk の一覧）。"""
    markers: list[JsonObject] = []
    for text in dict.fromkeys(match.group(0) for match in _MARKER.finditer(answer)):
        ids = cited_chunk_ids(text, chunk_ids)
        if ids:
            markers.append({"text": text, "chunk_ids": _ordered_ids(ids, chunk_ids)})
    return markers


def _passages(
    answer: str,
    chunk_ids: Sequence[str],
    supported: list[tuple[str, str]],
    markers: list[JsonObject],
) -> list[JsonObject]:
    """回答の段落ごとの根拠（段落の原文, chunk の一覧）。

    根拠は、最終の検証がその段落を裏付けた根拠と、段落が書いた根拠の ID の印の根拠。画面は
    段落の下に「根拠：文書 p.N」を出す。根拠の無い段落は入れない。
    """
    known = set(chunk_ids)
    passages: list[JsonObject] = []
    seen: set[str] = set()
    for line in answer.split("\n"):
        for _start, _end, text in passage_spans(line):
            if not text or text in seen:
                continue
            seen.add(text)
            ids = {chunk_id for quote, chunk_id in supported if quote == text and chunk_id in known}
            for marker in markers:
                if marker["text"] in text:
                    ids.update(marker["chunk_ids"])
            if ids:
                passages.append({"text": text, "chunk_ids": _ordered_ids(ids, chunk_ids)})
    return passages


def answer_evidence(
    steps: Sequence[RunStep], answer: str, validation: Mapping[str, Any] | None
) -> JsonObject:
    """成果物「回答の根拠」の内容（`items`・`passed`・`cited`・`markers`・`passages`）。

    `markers` は回答の中の根拠の ID の印と引き当てた根拠（画面は印を文字で出さず、根拠の行にする）、
    `passages` は段落ごとの根拠（画面は段落の下に「根拠：文書 p.N」を出す）。

    `answer` は利用者に見せる回答（最終の検証の後の本文）、`validation` は最終の検証の成果物の内容
    （無ければ None）。
    """
    passed = _passed_evidence(steps)
    chunk_ids = [str(item["chunk_id"]) for item in passed]
    supported = _supported_claims(validation)
    cited = cited_chunk_ids(answer, chunk_ids) | {chunk_id for _quote, chunk_id in supported}
    markers = _markers(answer, chunk_ids)
    items: list[JsonObject] = []
    for item in passed:
        view: JsonObject = {key: item.get(key) for key in _COPIED_FIELDS if key in item}
        section = item.get("section")
        if isinstance(section, Mapping) and isinstance(section.get("title"), str):
            view["section_title"] = section["title"]
        used = item["chunk_id"] in cited
        view["cited"] = used
        answer_use = item.get("answer_use")
        view["answer_use"] = answer_use if used and answer_use in _ANSWER_USES else None
        items.append(view)
    return {
        "items": items,
        "passed": len(items),
        "cited": sum(1 for item in items if item["cited"]),
        "markers": markers,
        "passages": _passages(answer, chunk_ids, supported, markers),
    }
