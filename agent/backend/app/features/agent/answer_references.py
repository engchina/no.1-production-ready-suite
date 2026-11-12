"""回答に書かれた根拠の参照と、回答の本文の整形の部品。

根拠の ID の実在の確認（回答が書いた ID を、ツールが返した根拠の ID と照らす）、根拠の位置の表示、
ツールが返した原文の取り出し、調べた経過だけの行の除去を持つ。回答の意味は判定しない（#4714）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.features.agent.answer_passages import (
    is_heading,
    is_lookup_note,
    is_structure,
    list_heading_lines,
    passage_spans,
    table_header_lines,
)
from app.features.agent.support_task import (
    RAG_READ_DOCUMENT,
    RAG_READ_SECTION,
    RAG_READ_SOURCE,
    RAG_RETRIEVE_EVIDENCE,
    RAG_SEARCH,
)
from app.features.agent.tools import mcp_base_tool_name

if TYPE_CHECKING:
    from app.features.agent.runtime import RunStep

JsonObject = dict[str, Any]


# 契約の上限（質問は切り詰めて渡す。回答は切り詰めると別の文の検証になるため、超えたら検証しない）。
MAX_QUERY_CHARS = 8000


MAX_ANSWER_CHARS = 20000


EVIDENCE_TOOLS = frozenset({RAG_SEARCH, RAG_RETRIEVE_EVIDENCE})


def locator_labels(item: JsonObject) -> list[object]:
    """根拠の見出し（章節の列・章節の名前・シート名）。回答と照らすのに使う（#1451）。"""
    labels: list[object] = []
    locator = item.get("locator")
    if isinstance(locator, dict):
        path = locator.get("section_path")
        labels.extend(path if isinstance(path, list) else [])
        labels.append(locator.get("sheet_name"))
    section = item.get("section")
    if isinstance(section, dict):
        labels.append(section.get("title"))
    path = item.get("section_path")
    labels.extend(path if isinstance(path, list) else [])
    # 読み取りのツールの chunk は表計算のシート名を chunk に持つ（#1471）。
    labels.append(item.get("sheet_name"))
    return labels


_EvidenceRef = tuple[tuple[str, str], object, list[object], list[object]]


def evidence_refs(
    tool: str, output: JsonObject, *, original_only: bool = False
) -> list[_EvidenceRef]:
    """根拠を集める・読むツールの出力の（参照, 本文, 見出し, 文書のファイル名）（出てきた順）。"""
    items: list[tuple[object, object, list[object], list[object]]]
    if tool in EVIDENCE_TOOLS:
        evidence = output.get("evidence")
        items = [
            (item, item.get("excerpt"), locator_labels(item), [item.get("file_name")])
            for item in (evidence if isinstance(evidence, list) else [])
            if isinstance(item, dict)
            and (not original_only or item.get("origin", "source") == "source")
        ]
    elif tool == RAG_READ_SOURCE:
        items = [(output, output.get("text"), locator_labels(output), [output.get("file_name")])]
    elif tool in {RAG_READ_DOCUMENT, RAG_READ_SECTION}:
        chunks = output.get("chunks")
        document_id = output.get("document_id")
        body = output.get("text")
        body = body if isinstance(body, str) else ""
        section_labels = locator_labels(output)
        names = [output.get("file_name")]
        items = []
        for chunk in chunks if isinstance(chunks, list) else []:
            if not isinstance(chunk, dict):
                continue
            start, end = chunk.get("start"), chunk.get("end")
            chunk_text = (
                body[start:end]
                if isinstance(start, int) and isinstance(end, int) and start < end
                else ""
            )
            ref = {"document_id": document_id, "chunk_id": chunk.get("chunk_id")}
            items.append((ref, chunk_text, [*locator_labels(chunk), *section_labels], names))
    else:
        return []
    refs: list[_EvidenceRef] = []
    for item, text, labels, file_names in items:
        if not isinstance(item, dict):
            continue
        document_id, chunk_id = item.get("document_id"), item.get("chunk_id")
        if isinstance(document_id, str) and isinstance(chunk_id, str) and document_id and chunk_id:
            refs.append(((document_id, chunk_id), text, labels, file_names))
    return refs


def evidence_source_texts(steps: Sequence[RunStep]) -> list[str]:
    """モデルの生成した回答を含めず、根拠を取得・読むツールが返した原文を集める。"""
    texts: list[str] = []
    for step in steps:
        call, result = step.tool_call, step.tool_result
        if call is None or result is None or not result.success or step.status != "completed":
            continue
        if not isinstance(result.output, dict):
            continue
        for _ref, text, _labels, _names in evidence_refs(
            mcp_base_tool_name(call.name), result.output, original_only=True
        ):
            if isinstance(text, str):
                texts.append(text)
    return texts


def _cited_in(answer: str, chunk_id: str) -> bool:
    """回答が根拠の id（MCP の `evidence_id` は `chunk_id` と同じ）を書いているか。

    `…:1` が `…:19` の先頭に一致しないよう、後ろが数字で続くものは数えない。
    """
    return bool(re.search(re.escape(chunk_id) + r"(?!\d)", answer))


# 回答が書いた根拠の id（途中を「…」で省いた・先頭の部分を落とした・区切りを横線で書いた形）。
# 根拠の id は契約では不透明な文字列（rag-tools.json の `chunk_id`）なので、id の形（区切り・桁）を
# 写さず、渡された根拠の id の値から区切りの文字を決めて照合する（#3240）。
_ID_DASHES = "‐‑‒–—―-"


_ID_ELLIPSIS = re.compile(r"…+|‥+|\.{2,}")


# 省いて書いた ID の残した先頭の最短の長さ（これより短いと別の根拠と区別しにくい）。
_MIN_ABBREVIATED_ID_CHARS = 7


# 候補の語の先頭の英数字の最短の長さ（「第3章」「A-1」のような ID でない語を数えない）。
_MIN_ID_HEAD_CHARS = 6


def _id_separators(chunk_ids: Sequence[str]) -> str:
    """根拠の id の区切りの文字（英数字・省略の印の「.」以外の文字）と横線。"""
    found = {
        char
        for chunk_id in chunk_ids
        for char in chunk_id
        if not (char.isascii() and char.isalnum()) and char != "."
    }
    return "".join(sorted(found | set(_ID_DASHES)))


def _cited_id_pattern(separators: str) -> re.Pattern[str]:
    """回答の中の、根拠の id を書いた候補の語（英数字で始まり、区切りか省略の印を挟んで英数字で
    終わる並び）。"""
    seps = re.escape(separators)
    return re.compile(
        rf"(?<![0-9A-Za-z])[0-9A-Za-z]{{{_MIN_ID_HEAD_CHARS},}}"
        rf"(?:[0-9A-Za-z…‥{seps}]|\.{{2,}})*[{seps}][0-9A-Za-z]+(?![0-9A-Za-z])"
    )


def _abbreviated_part(written: str, actual: str) -> bool:
    """ID の部分を、後ろを切って・途中を「…」で省いて書いたか。"""
    pieces = _ID_ELLIPSIS.split(written)
    if len(pieces[0]) < _MIN_ABBREVIATED_ID_CHARS:
        return False
    if len(pieces) == 1:
        return len(written) < len(actual) and actual.startswith(written)
    return re.fullmatch(".+".join(re.escape(piece) for piece in pieces), actual) is not None


def _written_id_matches(written: str, actual: str, separators: str) -> bool:
    """回答が省いて書いた根拠の id（`written`）が `actual` を指すか（最後の部分はそのまま一致）。"""
    # 区切り（横線を含む）は「:」にそろえ、省略の印の直前の区切りは除く。
    unify = re.compile(f"[{re.escape(separators)}]")
    written = re.sub(r":+(?=…|‥|\.{2,})", "", unify.sub(":", written)).lower()
    actual = unify.sub(":", actual).lower()
    if written == actual or (_ID_ELLIPSIS.search(written) and _abbreviated_part(written, actual)):
        return True
    written_parts, actual_parts = written.split(":"), actual.split(":")
    if 2 <= len(written_parts) < len(actual_parts):
        actual_parts = actual_parts[-len(written_parts) :]
    if len(written_parts) != len(actual_parts) or written_parts[-1] != actual_parts[-1]:
        return False
    return all(
        w == a or _abbreviated_part(w, a)
        for w, a in zip(written_parts[:-1], actual_parts[:-1], strict=True)
    )


def cited_chunk_ids(answer: str, chunk_ids: Sequence[str]) -> set[str]:
    """回答が書いた根拠の id（そのままの形と、省いて書いて 1 つの根拠に決まる形）。"""
    cited = {chunk_id for chunk_id in chunk_ids if _cited_in(answer, chunk_id)}
    if not chunk_ids:
        return cited
    separators = _id_separators(chunk_ids)
    for match in dict.fromkeys(
        item.group(0) for item in _cited_id_pattern(separators).finditer(answer)
    ):
        matches = {
            chunk_id for chunk_id in chunk_ids if _written_id_matches(match, chunk_id, separators)
        }
        if len(matches) == 1:
            cited |= matches
    return cited


# 前の段落を受ける接続の語（#1833）。その前の段落を外すと受ける先が無くなる（「したがって、…」
# から本文が始まる）ので、外した段落の直後の段落では語を外す。意味を変える語（「ただし」
# 「なお」）は残す。
_DANGLING_CONNECTIVE = re.compile(
    r"(?:したがって|従って|そのため|このため|よって|ゆえに|以上から|以上より|以上のことから|このように"
    r"|つまり|すなわち|また|さらに|加えて)[、，,]\s*"
)


# 番号で一覧の項目を指す語（「手順 2 の後に」）。本文に残っていれば、項目を外しても番号を振り
# 直さない（指す先が変わるため。#2692）。
_STEP_REFERENCE = re.compile(r"(?:手順|ステップ|step)\s*[0-9０-９]{1,3}", re.IGNORECASE)


_FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def _with_number(line: str, number: int) -> str:
    """番号つきの項目の行（`_STEP_ITEM`）の番号を `number` にした行（番号の字の種類は保つ）。"""
    match = _STEP_ITEM.match(line)
    if match is None:
        return line
    name = next(key for key in ("dot", "paren", "circled") if match.group(key))
    old = match.group(name)
    if name == "circled":
        new = chr(ord("①") + number - 1) if 1 <= number <= 20 else old
    else:
        new = str(number)
        if any("０" <= char <= "９" for char in old):
            new = new.translate(_FULLWIDTH_DIGITS)
    return line[: match.start(name)] + new + line[match.end(name) :]


def _renumber(original: list[str], kept: dict[int, str]) -> None:
    """外した項目のある番号つきの一覧の、残った項目の番号を詰める（#2692）。

    `kept` は元の行の番号 → 残った行（書き換える）。

    項目の行が消えたか、番号の印ごと外れた（行の初めの段落を外した）項目を外した項目とする。同じ
    番号の繰り返しは保つ。本文に番号で項目を指す語（`_STEP_REFERENCE`）があれば振り直さない。
    """
    if any(_STEP_REFERENCE.search(line) for line in kept.values()):
        return
    for items in _step_ranges(original):
        numbers: list[tuple[int, int, bool]] = []
        for start, _end in items:
            item = _step_item(original[start])
            now = _step_item(kept[start]) if start in kept else None
            if item is not None:
                numbers.append((start, item[2], now is not None and now[:2] == item[:2]))
        if all(survived for _s, _n, survived in numbers):
            continue
        removed = {number for _s, number, survived in numbers if not survived} - {
            number for _s, number, survived in numbers if survived
        }
        for start, number, survived in numbers:
            shift = sum(1 for item in removed if item < number)
            if survived and shift:
                kept[start] = _with_number(kept[start], number - shift)


def withhold_paragraphs(
    answer: str, quotes: set[str], positions: frozenset[tuple[int, int]] = frozenset()
) -> str | None:
    """回答から `quotes` の段落を外した本文（位置を決められない段落があれば None）。

    `positions` は、原文ではなく位置（行の番号・行の中の開始の位置）で外す段落（`withheld_labels`
    の項目の名前。同じ原文の段落がほかにあっても、その項目だけを外す。#2692）。

    段落の位置は Agent の段落の分け方（`passage_spans`）で決める（文字列の置換では、同じ文を含む
    別の段落まで削ってしまうため。RAG の判定は `align_claims` でこの段落に結び付けてある）。
    段落が無くなった行は消し、続く空行は 1 つにする。段落を外した行に見出し・
    出典だけが残ればその行も消し、中身をすべて外した節の見出しも消す（#1306。番号つきの見出しも
    消す: 中身の無い操作の名前を実行の順の 1 つとして残さない。#2692）。番号つきの一覧の項目を
    外したら、残った項目の番号を詰める（`_renumber`。#1898 の番号の飛びを残さない）。見出し・出典の
    ほかに何も残らなければ空文字を返す（本文を載せない）。外した段落の直後（見出し・出典を挟んでも）の
    段落が前の段落を受ける接続の語（「したがって、」など）で始まれば、その語を外す（#1833）。
    """
    found: set[str] = set()
    headers = table_header_lines(answer)
    # 次の行で決まる番号つき・箇条書きの見出しの行（#1481 / #1486）。
    listed = list_heading_lines(answer)

    def structure(text: str) -> bool:
        return is_structure(text) or text in headers or text in listed

    # （元の行の番号, 行, 見出しの行か, 中身のある行か）。消した見出しは None にする。
    kept: list[tuple[int, str, bool, bool] | None] = []
    heading: int | None = None
    section_content = False
    section_removed = False
    # 直前の（主張の）段落を外したか（次の段落の先頭の接続の語を外す。#1833）。
    dangling = False

    def close_section() -> None:
        if heading is not None and section_removed and not section_content:
            kept[heading] = None

    original = answer.split("\n")
    for index, line in enumerate(original):
        spans = passage_spans(line)

        def removed(span: tuple[int, int, str], index: int = index) -> bool:
            return span[2] in quotes or (index, span[0]) in positions

        withheld = [span for span in spans if removed(span)]
        if not withheld and len(spans) == 1 and (is_heading(spans[0][2]) or spans[0][2] in listed):
            close_section()
            heading, section_content, section_removed = len(kept), False, False
            kept.append((index, line, True, False))
            continue
        # 行の中の消す範囲（外す段落と、外した段落の直後の段落の先頭の接続の語）。
        cuts: list[tuple[int, int]] = []
        for start, end, text in spans:
            if removed((start, end, text)):
                cuts.append((start, end))
                dangling = True
            elif not structure(text):
                connective = _DANGLING_CONNECTIVE.match(text) if dangling else None
                if connective and connective.end() < len(text):
                    cuts.append((start, start + connective.end()))
                dangling = False
        for start, end in reversed(cuts):
            line = line[:start] + line[end:]
        if not withheld:
            content = any(not structure(text) for _s, _e, text in spans)
            section_content = section_content or content
            kept.append((index, line, False, content))
            continue
        found.update(span[2] for span in withheld)
        section_removed = True
        rest = [text for _s, _e, text in passage_spans(line)]
        if line.strip() and any(not structure(text) for text in rest):
            section_content = True
            kept.append((index, line.rstrip(), False, True))
    close_section()
    if quotes - found:
        return None
    lines = [item for item in kept if item is not None]
    if not any(content for _index, _line, _heading, content in lines):
        return ""
    texts = {index: line for index, line, _h, _c in lines}
    _renumber(original, texts)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(texts[index] for index, *_ in lines)).strip()


# 番号つきの一覧の項目の行（「1. 」「2）」「（3）」「④」。字下げ・強調の印を許す。#2604）。
# 「1.5 倍」のような数は項目にしないため、半角の「.」の後は空白を求める。
_STEP_ITEM = re.compile(
    r"(?P<indent>[ \t\u3000]*)(?:\*\*|__)?"
    r"(?:(?P<dot>[0-9０-９]{1,3})(?:\.\s|[)．）]\s*)|[（(](?P<paren>[0-9０-９]{1,3})[）)]\s*"
    r"|(?P<circled>[①-⑳])\s*)\S"
)


# 番号つきの一覧を切る行（Markdown の見出し）。
_SECTION_BREAK = re.compile(r"\s*#{1,6}\s")


@dataclass
class _StepRun:
    """番号つきの一覧の 1 つの並び（外側の一覧。字下げの深い番号は項目の中の行）。"""

    indent: int
    style: str
    last: int
    start: int
    # 最後の項目の行と、その項目が見出しの行（下の行に中身がある形）か。
    last_item: int
    heading_item: bool
    # 項目の行（並びの中の各項目の最初の行）。
    items: list[int]


def _step_item(line: str) -> tuple[int, str, int] | None:
    """番号つきの項目の行なら（字下げの幅・番号の種類・番号）。"""
    match = _STEP_ITEM.match(line)
    if not match:
        return None
    indent = len(match.group("indent").expandtabs(4))
    if match.group("circled"):
        return indent, "circled", ord(match.group("circled")) - ord("①") + 1
    style = "dot" if match.group("dot") else "paren"
    return indent, style, int(match.group("dot") or match.group("paren"))


def _step_ranges(lines: list[str]) -> list[list[tuple[int, int]]]:
    """回答の行のうち、外側の番号つきの一覧ごとの、項目の範囲（[開始の行, 終わりの行)）の並び。

    番号が 1 つずつ増える（同じ番号の繰り返しも許す）同じ字下げ・同じ種類の項目を 1 つの並びにし、
    項目と項目の間の行は前の項目の中身にする。最後の項目の中身は、字下げした行と、空行を挟まずに
    続く行（項目が見出しの行なら、次の見出しの行まで）。Markdown の見出しで並びを切る。
    """
    ranges: list[list[tuple[int, int]]] = []
    run: _StepRun | None = None

    def close(at: int) -> None:
        nonlocal run
        if run is None:
            return
        end = run.last_item + 1
        blank = False
        while end < at:
            line = lines[end]
            if not line.strip():
                blank = True
            elif run.heading_item:
                spans = passage_spans(line)
                if len(spans) == 1 and is_heading(spans[0][2]):
                    break
            elif blank and len(line) - len(line.lstrip()) <= run.indent:
                break
            end += 1
        while end > run.start and not lines[end - 1].strip():
            end -= 1
        bounds = [*run.items[1:], end]
        ranges.append(list(zip(run.items, bounds, strict=True)))
        run = None

    def heading_item(line: str) -> bool:
        spans = passage_spans(line)
        return len(spans) == 1 and (is_heading(spans[0][2]) or spans[0][2] in listed)

    listed = list_heading_lines("\n".join(lines))
    for index, line in enumerate(lines):
        if _SECTION_BREAK.match(line):
            close(index)
            continue
        item = _step_item(line)
        if item is None:
            continue
        indent, style, number = item
        if run is not None and indent > run.indent:
            continue
        if (
            run is not None
            and indent == run.indent
            and style == run.style
            and number in {run.last, run.last + 1}
        ):
            run.last, run.last_item, run.heading_item = number, index, heading_item(line)
            run.items.append(index)
            continue
        close(index)
        run = _StepRun(indent, style, number, index, index, heading_item(line), [index])
    close(len(lines))
    return ranges


def without_lookup_notes(answer: str) -> str:
    """回答から、調べるのに使ったツール・プロファイル・内部の ID だけを述べる行を外す（#2328）。

    組み込み Runtime が回答を確定する前に呼ぶ（内部の ID を利用者に見せない）。外すと本文が
    残らない・位置を決められないときはそのまま返す。
    """
    quotes = {
        text
        for line in answer.split("\n")
        for _start, _end, text in passage_spans(line)
        if is_lookup_note(text)
    }
    if not quotes:
        return answer
    body = withhold_paragraphs(answer, quotes)
    return body or answer
