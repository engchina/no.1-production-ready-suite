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
- 会話の添付の根拠（#2554）: 添付を読むツール（`chat_attachment_read_section` /
  `chat_attachment_read_rows` / `chat_attachment_find`）が返した節・行・一致した箇所。
  `document_id` は `attachment:<添付の ID>`（RAG の文書ではないので、根拠の原文・図を
  開く確かめ（`passed_evidence`）には入れない）。添付は最終の検証の対象外で根拠の ID の
  印も無いため、回答で引用は回答がその添付のファイル名を書いたときに付ける。

内容は画面と MCP が読む形（RAG の MCP の根拠の項目の一部と `cited`）。本文の抜粋は
RAG が返した長さのまま（添付は `ATTACHMENT_EXCERPT_CHARACTERS` まで）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pr_system_settings.chat_attachments.tools import (
    FIND_TOOL_NAME,
    LIST_TOOL_NAME,
    READ_ROWS_TOOL_NAME,
    READ_SECTION_TOOL_NAME,
    VIEW_IMAGE_TOOL_NAME,
)

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
# 会話の添付の根拠（#2554）。
ATTACHMENT_DOCUMENT_PREFIX = "attachment:"
ATTACHMENT_EVIDENCE_TYPE = "chat_attachment"
# 添付の根拠の抜粋の上限（節は 1 回に 8000 文字まで読めるので、画面と成果物では縮める）。
ATTACHMENT_EXCERPT_CHARACTERS = 2_000
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
    "chat_attachment_id",
)


def passed_evidence(steps: Sequence[RunStep]) -> list[JsonObject]:
    """その Run でモデルに渡した根拠（chunk で重複を除き、出てきた順）。

    画面の根拠の一覧（`items`）と、画面から根拠の原文・図を開くときの確かめ（`rag_figures`）が
    同じこの関数を使う（2 つの集合をずらさない。#2427）。

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


def _completed_outputs(steps: Sequence[RunStep], names: frozenset[str]) -> list[tuple[str, Any]]:
    outputs: list[tuple[str, Any]] = []
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if call.name in names:
            outputs.append((call.name, result.output))
    return outputs


def _excerpt(text: str) -> tuple[str, bool]:
    if len(text) <= ATTACHMENT_EXCERPT_CHARACTERS:
        return text, False
    return text[:ATTACHMENT_EXCERPT_CHARACTERS], True


def _row_line(values: Any) -> str:
    if not isinstance(values, Mapping):
        return ""
    return " / ".join(f"{column}: {value}" for column, value in values.items() if value is not None)


def _attachment_item(
    attachment_id: str,
    file_name: str,
    chunk_id: str,
    text: str,
    *,
    locator: JsonObject,
    section_title: str | None = None,
    content_kind: str,
) -> JsonObject:
    excerpt, truncated = _excerpt(text)
    item: JsonObject = {
        "document_id": f"{ATTACHMENT_DOCUMENT_PREFIX}{attachment_id}",
        "chunk_id": chunk_id,
        "file_name": file_name,
        "excerpt": excerpt,
        "truncated": truncated,
        "locator": locator,
        "content_kind": content_kind,
        "evidence_type": ATTACHMENT_EVIDENCE_TYPE,
        "chat_attachment_id": attachment_id,
    }
    # PDF の節の見出しは「p.N」（頁と同じ）なので、頁があれば見出しに重ねない。
    if section_title and section_title != f"p.{locator.get('page_start')}":
        item["section"] = {"title": section_title}
    return item


def _section_locator(page: Any) -> JsonObject:
    if isinstance(page, int) and not isinstance(page, bool) and page > 0:
        return {"page_start": page, "page_end": page}
    return {}


def _rows_locator(sheet: str, start: int, end: int) -> JsonObject:
    return {"sheet_name": sheet, "row_start": start, "row_end": end}


def attachment_evidence(steps: Sequence[RunStep]) -> list[JsonObject]:
    """その Run で添付を読むツールが返した根拠（#2554。節・行の範囲で重複なし、出てきた順）。

    `chat_attachment_find` の結果は頁・見出しを返さないので、同じ Run の
    `chat_attachment_list` の節から引く。
    """
    sections: dict[tuple[str, str], tuple[str | None, Any]] = {}
    for _name, output in _completed_outputs(steps, frozenset({LIST_TOOL_NAME})):
        attachments = output.get("attachments") if isinstance(output, Mapping) else None
        for attachment in attachments if isinstance(attachments, list) else []:
            if not isinstance(attachment, Mapping):
                continue
            for section in attachment.get("sections") or []:
                if isinstance(section, Mapping):
                    sections[
                        (str(attachment.get("attachment_id")), str(section.get("section_id")))
                    ] = (
                        section.get("title") if isinstance(section.get("title"), str) else None,
                        section.get("page"),
                    )
    items: dict[str, JsonObject] = {}

    def add(item: JsonObject) -> None:
        if item["chunk_id"] not in items and len(items) < MAX_ANSWER_EVIDENCE:
            items[item["chunk_id"]] = item

    read_tools = frozenset(
        {READ_SECTION_TOOL_NAME, READ_ROWS_TOOL_NAME, FIND_TOOL_NAME, VIEW_IMAGE_TOOL_NAME}
    )
    for name, output in _completed_outputs(steps, read_tools):
        if not isinstance(output, Mapping):
            continue
        if name == VIEW_IMAGE_TOOL_NAME:
            image = _image_evidence(output)
            if image is not None:
                add(image)
        elif name == READ_SECTION_TOOL_NAME:
            attachment_id, section_id = output.get("attachment_id"), output.get("section_id")
            content = output.get("content")
            if not isinstance(attachment_id, str) or not isinstance(section_id, str):
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            add(
                _attachment_item(
                    attachment_id,
                    str(output.get("file_name") or attachment_id),
                    f"{ATTACHMENT_DOCUMENT_PREFIX}{attachment_id}:{section_id}",
                    content,
                    locator=_section_locator(output.get("page")),
                    section_title=output.get("title")
                    if isinstance(output.get("title"), str)
                    else None,
                    content_kind="text",
                )
            )
        elif name == READ_ROWS_TOOL_NAME:
            attachment_id, sheet = output.get("attachment_id"), output.get("sheet")
            rows = [row for row in output.get("rows") or [] if isinstance(row, Mapping)]
            numbers: list[int] = [
                number for row in rows if isinstance(number := row.get("row_number"), int)
            ]
            if not isinstance(attachment_id, str) or not isinstance(sheet, str) or not numbers:
                continue
            start, end = min(numbers), max(numbers)
            add(
                _attachment_item(
                    attachment_id,
                    str(output.get("file_name") or attachment_id),
                    f"{ATTACHMENT_DOCUMENT_PREFIX}{attachment_id}:{sheet}:{start}-{end}",
                    "\n".join(_row_line(row.get("values")) for row in rows),
                    locator=_rows_locator(sheet, start, end),
                    content_kind="sheet",
                )
            )
        else:
            for match in output.get("matches") or []:
                if not isinstance(match, Mapping):
                    continue
                attachment_id, snippet = match.get("attachment_id"), match.get("snippet")
                if not isinstance(attachment_id, str) or not isinstance(snippet, str):
                    continue
                file_name = str(match.get("file_name") or attachment_id)
                section_id, sheet, row = (
                    match.get("section_id"),
                    match.get("sheet"),
                    match.get("row_number"),
                )
                if isinstance(section_id, str):
                    title, page = sections.get((attachment_id, section_id), (None, None))
                    add(
                        _attachment_item(
                            attachment_id,
                            file_name,
                            f"{ATTACHMENT_DOCUMENT_PREFIX}{attachment_id}:{section_id}",
                            snippet,
                            locator=_section_locator(page),
                            section_title=title,
                            content_kind="text",
                        )
                    )
                elif isinstance(sheet, str) and isinstance(row, int):
                    add(
                        _attachment_item(
                            attachment_id,
                            file_name,
                            f"{ATTACHMENT_DOCUMENT_PREFIX}{attachment_id}:{sheet}:{row}-{row}",
                            snippet,
                            locator=_rows_locator(sheet, row, row),
                            content_kind="sheet",
                        )
                    )
    return list(items.values())


def _image_evidence(output: Mapping[str, Any]) -> JsonObject | None:
    """画像の原本を読んだ根拠（#2652）。モデルへ届いた画像か、Vision モデルが読んだ文だけ。

    モデルに渡らなかった画像（非対応・上限）は読んだ扱いにしない。
    """
    attachment_id, sha = output.get("attachment_id"), output.get("content_sha256")
    if not isinstance(attachment_id, str) or not isinstance(sha, str):
        return None
    focus = str(output.get("focus") or "")
    refs = [ref for ref in output.get("content_images") or [] if isinstance(ref, Mapping)]
    delivered = [ref for ref in refs if ref.get("model_input") == "attached"]
    reading = output.get("content")
    if delivered:
        text = f"原画像（観点: {focus}）"
        provenance: JsonObject = {
            "image_id": delivered[0].get("image_id"),
            "model_input": "attached",
        }
    elif isinstance(reading, str) and reading.strip():
        text = reading
        provenance = {
            "read_by": output.get("read_by"),
            "content_source": output.get("content_source"),
        }
    else:
        return None
    item = _attachment_item(
        attachment_id,
        str(output.get("file_name") or attachment_id),
        f"{ATTACHMENT_DOCUMENT_PREFIX}{attachment_id}:image:{sha[:16]}",
        text,
        locator={},
        content_kind="image",
    )
    item["image_reading"] = {**provenance, "focus": focus, "content_sha256": sha}
    return item


# ファイル名の照合で同じとみなすダッシュ（モデルは「-」を「‑」などに書き換えることがある）。
_DASHES = str.maketrans(dict.fromkeys("‐‑‒–—―−－", "-"))


def _comparable(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DASHES).casefold()


def _attachment_cited(answer: str, file_name: str) -> bool:
    """回答が添付のファイル名（拡張子つき、または拡張子なし）を書いたか。"""
    name = _comparable(file_name).strip()
    if not name:
        return False
    text = _comparable(answer)
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return name in text or (len(stem) >= 3 and stem in text)


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
    passed = passed_evidence(steps)
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
    for item in attachment_evidence(steps)[: max(MAX_ANSWER_EVIDENCE - len(items), 0)]:
        view = {key: item.get(key) for key in _COPIED_FIELDS if key in item}
        section = item.get("section")
        if isinstance(section, Mapping) and isinstance(section.get("title"), str):
            view["section_title"] = section["title"]
        if isinstance(item.get("image_reading"), Mapping):
            # 画像を読んだ由来（モデルへ届いた画像か、読んだ Vision モデルと観点。#2652）。
            view["image_reading"] = dict(item["image_reading"])
        view["cited"] = _attachment_cited(answer, str(item.get("file_name") or ""))
        view["answer_use"] = None
        items.append(view)
    return {
        "items": items,
        "passed": len(items),
        "cited": sum(1 for item in items if item["cited"]),
        "markers": markers,
        "passages": _passages(answer, chunk_ids, supported, markers),
    }
