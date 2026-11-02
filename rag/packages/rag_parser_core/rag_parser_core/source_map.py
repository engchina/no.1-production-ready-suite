"""テキスト系の原本の位置を、解析の座標から記録する（#4100）。

Unstructured は要素に原本の文字位置を持たせない。要素の分け方を変えないために parser は替えず、
同じ原本に対して位置を記録する処理を別に動かす。

- 位置は parser が記録した座標（文字の位置・行・タグの位置・記録の番号・値の範囲）だけから作る。
- 本文の比較は、順番どおりに対応させた結果の検証にだけ使う。読み飛ばして次を探すことはしない。
  検証に通らなければ、その文書には位置を記録しない（呼び出し側で ``unrecorded`` になる）。

形式ごとの座標の作り方はこのファイル、Unstructured に依存する部分は ``unstructured_source_map``。
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from xml.parsers import expat

from charset_normalizer import from_bytes

SOURCE_MAP_KEY = "source_map"
SOURCE_MAP_VERSION = 2
# 左の原本の 1 つの表示範囲の文字数。これ以下の原本は全要素が同じ範囲に入るので位置を記録しない。
SOURCE_WINDOW_CODEPOINTS = 8192
# csv / tsv・JSON の所属の単位の目安の文字数（行・値を 1 つずつ保存すると対応表が大きくなる）。
SOURCE_UNIT_CODEPOINTS = 1024

SCHEME_TEXT_CHAR = "text_char"
SCHEME_MARKDOWN_SOURCE = "markdown_source"
SCHEME_HTML_SOURCE = "html_source"
SCHEME_XML_SOURCE = "xml_source"
SCHEME_DELIMITED_ROW = "delimited_row"
SCHEME_JSONL_LINE = "jsonl_line"
SCHEME_JSON_POINTER = "json_pointer"
# 1 つの要素がファイル全体になる形式。要素の中の単位を所属の単位にする。
FRAGMENT_SCHEMES = frozenset({SCHEME_DELIMITED_ROW, SCHEME_JSON_POINTER})


def decode_source_text(data: bytes) -> str:
    """原本を、左の表示範囲と同じ文字列にする（座標はこの文字列の codepoint）。"""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = from_bytes(data).best()
        return str(decoded) if decoded is not None else data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class SourceRange:
    """要素の原本の範囲（半開区間）。``start`` / ``end`` は要素の本文の中の単位の範囲。"""

    source_start: int
    source_end: int
    locator: str
    start: int | None = None
    end: int | None = None
    row_start: int | None = None
    row_end: int | None = None

    def to_dict(self) -> dict[str, int | str]:
        values: dict[str, int | str | None] = {
            "source_start": self.source_start,
            "source_end": self.source_end,
            "locator": self.locator,
            "start": self.start,
            "end": self.end,
            "row_start": self.row_start,
            "row_end": self.row_end,
        }
        return {key: value for key, value in values.items() if value is not None}


def source_map_artifact(
    data: bytes,
    text: str,
    scheme: str,
    elements: Mapping[str, Sequence[SourceRange]],
) -> dict[str, Any] | None:
    """``parser_artifacts`` に置く形にする。要素が 1 つも無ければ置かない。"""
    if not elements:
        return None
    return {
        "version": SOURCE_MAP_VERSION,
        "coordinate_unit": "unicode_codepoint",
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_length": len(text),
        "scheme": scheme,
        "elements": {
            element_id: [item.to_dict() for item in ranges]
            for element_id, ranges in elements.items()
        },
    }


# ---- csv / tsv ----------------------------------------------------------------------------------


def delimited_record_ranges(text: str, *, delimiter: str) -> list[tuple[int, int]]:
    """引用符を見て記録の範囲を返す（RFC 4180 / 7111 の 1 行。空の行は記録にしない）。"""
    records: list[tuple[int, int]] = []
    start = 0
    position = 0
    length = len(text)
    quoted = False
    field_start = True
    has_content = False
    while position < length:
        char = text[position]
        if quoted:
            if char == '"':
                if text[position + 1 : position + 2] == '"':
                    position += 1
                else:
                    quoted = False
            position += 1
            continue
        if char in "\r\n":
            if has_content:
                records.append((start, position))
            if char == "\r" and text[position + 1 : position + 2] == "\n":
                position += 1
            position += 1
            start = position
            field_start = True
            has_content = False
            continue
        has_content = True
        if char == '"' and field_start:
            quoted = True
            field_start = False
        else:
            field_start = char == delimiter
        position += 1
    if has_content:
        records.append((start, length))
    return records


def delimited_element_ranges(text: str, element_text: str, *, delimiter: str) -> list[SourceRange]:
    """1 つの表の要素（Markdown の 1 行 = 1 記録）の行のまとまりごとの範囲。

    pandas が値を読み替えることがある（型の推定・欠損）ので本文では比べず、記録の数を検証する。
    """
    records = delimited_record_ranges(text, delimiter=delimiter)
    lines = element_text.split("\n")
    if not records or len(records) != len(lines):
        return []
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    groups: list[SourceRange] = []
    first = 0
    for index, (_, record_end) in enumerate(records):
        is_last = index == len(records) - 1
        if not is_last and record_end - records[first][0] < SOURCE_UNIT_CODEPOINTS:
            continue
        groups.append(
            SourceRange(
                source_start=records[first][0],
                source_end=record_end,
                locator=f"row={first + 1}-{index + 1}",
                start=offsets[first],
                end=offsets[index + 1] - 1,
                row_start=first + 1,
                row_end=index + 1,
            )
        )
        first = index + 1
    return groups


# ---- jsonl --------------------------------------------------------------------------------------


def jsonl_line_ranges(text: str) -> list[tuple[int, int, int]]:
    """値のある行の (開始, 終了, 行番号)。Unstructured と同じ ``splitlines`` で行に分ける。"""
    lines: list[tuple[int, int, int]] = []
    position = 0
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        if line.strip():
            lines.append((position, position + len(line.rstrip("\r\n")), number))
        position += len(line)
    return lines


def jsonl_element_ranges(
    text: str,
    element_texts: Sequence[str],
) -> list[SourceRange]:
    """1 行 = 1 要素の対応。行の値を整形した文字が要素の本文と同じことを検証する。"""
    lines = jsonl_line_ranges(text)
    if len(lines) != len(element_texts):
        return []
    ranges: list[SourceRange] = []
    for (start, end, number), element_text in zip(lines, element_texts, strict=True):
        if _json_format(json.loads(text[start:end]), element_text) is None:
            return []
        ranges.append(SourceRange(start, end, f"line={number}"))
    return ranges


def structured_record_ranges(text: str) -> dict[str, SourceRange]:
    """構造化レコード JSONL の ``record_id`` ごとの行（要素の ID と同じ）。"""
    ranges: dict[str, SourceRange] = {}
    for start, end, number in jsonl_line_ranges(text):
        record_id = json.loads(text[start:end]).get("record_id")
        if isinstance(record_id, str):
            ranges[record_id] = SourceRange(start, end, f"line={number}")
    return ranges


# ---- JSON ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _JsonNode:
    pointer: str
    start: int
    end: int
    children: tuple[tuple[str, _JsonNode], ...] = ()
    is_object: bool = False


_JSON_SPACE = re.compile(r"[ \t\r\n]*")


def _pointer_child(pointer: str, key: str | int) -> str:
    return pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _json_tree(text: str) -> _JsonNode:
    """値を構造の順に読み、JSON Pointer と原本の範囲の木にする。同名のキーは拒否する。"""
    decoder = json.JSONDecoder()

    def skip(position: int) -> int:
        match = _JSON_SPACE.match(text, position)
        assert match is not None
        return match.end()

    def read(position: int, pointer: str) -> tuple[_JsonNode, int]:
        position = skip(position)
        token = text[position : position + 1]
        if token not in {"{", "["}:
            _, end = decoder.raw_decode(text, position)
            return _JsonNode(pointer, position, end), end
        closing = "}" if token == "{" else "]"
        is_object = token == "{"
        start = position
        position = skip(position + 1)
        children: list[tuple[str, _JsonNode]] = []
        seen: set[str] = set()
        while text[position : position + 1] != closing:
            key: str | int = len(children)
            if is_object:
                key, position = decoder.raw_decode(text, position)
                if not isinstance(key, str) or key in seen:
                    raise ValueError("invalid object key")
                seen.add(key)
                position = skip(position)
                if text[position : position + 1] != ":":
                    raise ValueError("invalid object separator")
                position += 1
            child, position = read(position, _pointer_child(pointer, key))
            children.append((str(key), child))
            position = skip(position)
            if text[position : position + 1] == ",":
                position = skip(position + 1)
            elif text[position : position + 1] != closing:
                raise ValueError("invalid separator")
        end = position + 1
        return _JsonNode(pointer, start, end, tuple(children), is_object), end

    root, end = read(0, "")
    if skip(end) != len(text):
        raise ValueError("trailing JSON content")
    return root


def _pretty_lines(value: Any, *, ensure_ascii: bool = True, sort_keys: bool = True) -> list[str]:
    """整形した JSON の行（字下げは adapter が除くので比べない）。"""
    dumped = json.dumps(value, indent=2, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    return [line.strip() for line in dumped.splitlines()]


def _json_format(value: Any, text: str) -> bool | None:
    """要素の本文が値の整形と同じなら、キーを並べ替えているかを返す。違えば None。

    Unstructured の版や adapter でエスケープ・キーの順が変わっても、記録を続けられるように比べる。
    """
    lines = _stripped_lines(text)
    for sort_keys in (True, False):
        for ensure_ascii in (True, False):
            if _pretty_lines(value, ensure_ascii=ensure_ascii, sort_keys=sort_keys) == lines:
                return sort_keys
    return None


def _stripped_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n")]


@dataclass(frozen=True)
class _JsonUnit:
    pointer: str
    last_pointer: str
    line_start: int
    line_end: int
    source_start: int
    source_end: int


def _json_units(
    node: _JsonNode, value: Any, line: int, *, sort_keys: bool
) -> tuple[list[_JsonUnit], int]:
    """値の整形後の行の範囲を、整形と同じ順で数えて単位にする。"""
    size = len(_pretty_lines(value, sort_keys=sort_keys))
    small = node.end - node.start <= SOURCE_UNIT_CODEPOINTS
    if small or not node.children or not value:
        unit = _JsonUnit(node.pointer, node.pointer, line, line + size, node.start, node.end)
        return [unit], line + size
    units: list[_JsonUnit] = []
    cursor = line + 1  # 開く括弧の行
    children = dict(node.children)
    if node.is_object:
        keys = sorted(children) if sort_keys else list(children)
    else:
        keys = [str(i) for i in range(len(value))]
    for key in keys:
        child_value = value[key] if node.is_object else value[int(key)]
        child_units, cursor = _json_units(children[key], child_value, cursor, sort_keys=sort_keys)
        units.extend(child_units)
    first, last = units[0], units[-1]
    # 括弧の行は最初と最後の単位に含める（本文の範囲に隙間を作らない）。
    units[0] = _JsonUnit(
        first.pointer,
        first.last_pointer,
        line,
        first.line_end,
        first.source_start,
        first.source_end,
    )
    last = units[-1]
    units[-1] = _JsonUnit(
        last.pointer,
        last.last_pointer,
        last.line_start,
        last.line_end + 1,
        last.source_start,
        last.source_end,
    )
    return units, cursor + 1


def _merged_json_units(units: Iterable[_JsonUnit]) -> list[_JsonUnit]:
    """本文でも原本でも隣り合う単位を、目安の文字数までまとめる。"""
    merged: list[_JsonUnit] = []
    for unit in units:
        if merged:
            previous = merged[-1]
            if (
                previous.line_end == unit.line_start
                and previous.source_end <= unit.source_start
                and unit.source_end - previous.source_start <= SOURCE_UNIT_CODEPOINTS
            ):
                merged[-1] = _JsonUnit(
                    previous.pointer,
                    unit.last_pointer,
                    previous.line_start,
                    unit.line_end,
                    previous.source_start,
                    unit.source_end,
                )
                continue
        merged.append(unit)
    return merged


def json_element_ranges(text: str, element_text: str) -> list[SourceRange]:
    """1 つの JSON の要素の、JSON Pointer の単位ごとの範囲（整形した本文が同じことを検証する）。"""
    value = json.loads(text)
    sort_keys = _json_format(value, element_text)
    if sort_keys is None:
        return []
    units, end = _json_units(_json_tree(text), value, 0, sort_keys=sort_keys)
    if end != len(_stripped_lines(element_text)):
        return []
    offsets = [0]
    for line in element_text.split("\n"):
        offsets.append(offsets[-1] + len(line) + 1)
    return [
        SourceRange(
            source_start=unit.source_start,
            source_end=unit.source_end,
            locator=unit.pointer
            if unit.pointer == unit.last_pointer
            else (f"{unit.pointer}..{unit.last_pointer}"),
            start=offsets[unit.line_start],
            end=offsets[unit.line_end] - 1,
        )
        for unit in _merged_json_units(units)
    ]


# ---- HTML ---------------------------------------------------------------------------------------

_HTML_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
# 中身をそのまま子に分けても読み方が変わらない入れ物（表・リスト・段落は分けない）。
_HTML_CONTAINER_TAGS = frozenset(
    {"html", "body", "main", "div", "section", "article", "aside", "header", "footer", "nav"}
)


@dataclass
class HtmlNode:
    """解析器の ``getpos()`` が記録したタグの範囲。"""

    tag: str
    start: int
    end: int
    line: int
    column: int
    has_text: bool = False
    children: list[HtmlNode] = field(default_factory=list)


class _HtmlTreeRecorder(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self._text = text
        self._line_starts = [0, *(match.end() for match in re.finditer("\n", text))]
        self.root = HtmlNode("", 0, len(text), 1, 0)
        self._open: list[HtmlNode] = [self.root]

    def _position(self) -> tuple[int, int, int]:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column, line, column

    def _add(self, tag: str, *, closed: bool) -> None:
        start, line, column = self._position()
        end = start + len(self.get_starttag_text() or "") if closed else len(self._text)
        node = HtmlNode(tag, start, end, line, column)
        self._open[-1].children.append(node)
        if not closed:
            self._open.append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._add(tag, closed=tag in _HTML_VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._add(tag, closed=True)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, 0, -1):
            if self._open[index].tag == tag:
                start = self._position()[0]
                close = self._text.find(">", start)
                end = close + 1 if close >= 0 else len(self._text)
                for item in self._open[index:]:
                    item.end = end
                del self._open[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._open[-1].has_text = True

    def handle_entityref(self, name: str) -> None:
        self._open[-1].has_text = True

    def handle_charref(self, name: str) -> None:
        self._open[-1].has_text = True


def _html_segments(node: HtmlNode) -> Iterator[HtmlNode]:
    """入れ物を子に分け、隣り合う子を目安の文字数までまとめる。文字のある入れ物は分けない。"""
    pending: HtmlNode | None = None
    for child in node.children:
        splittable = (
            child.tag in _HTML_CONTAINER_TAGS
            and not child.has_text
            and bool(child.children)
            and child.end - child.start > SOURCE_UNIT_CODEPOINTS
        )
        if splittable:
            if pending is not None:
                yield pending
                pending = None
            yield from _html_segments(child)
            continue
        if pending is None:
            pending = HtmlNode(child.tag, child.start, child.end, child.line, child.column)
        else:
            pending.end = child.end
        if pending.end - pending.start >= SOURCE_UNIT_CODEPOINTS:
            yield pending
            pending = None
    if pending is not None:
        yield pending


def html_segments(text: str) -> list[HtmlNode]:
    """要素の順に並んだ、それだけで解析できる原本の区切り。"""
    recorder = _HtmlTreeRecorder(text)
    recorder.feed(text)
    recorder.close()
    if recorder.root.has_text:
        return [HtmlNode("", 0, len(text), 1, 0)]
    return list(_html_segments(recorder.root))


# ---- XML ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class XmlLeaf:
    """文字を持つ XML の要素（lxml の ``element.text`` と同じ、最初の子の前までの文字）。"""

    text: str
    source_range: SourceRange


def xml_leaves(text: str) -> list[XmlLeaf]:
    """expat が記録したタグの位置で、要素が閉じる順（Unstructured の読む順）に並べる。"""
    encoded = text.encode("utf-8")
    # 復号済みの文字列を渡すので、原本の宣言の文字コードを使わない。
    parser = expat.ParserCreate("utf-8")
    parser.buffer_text = True
    parser.UseForeignDTD(False)
    leaves: list[XmlLeaf] = []
    # (タグ名, 開始の文字の位置, 行, 列, 文字の断片, 子やコメントが始まったか)
    stack: list[list[Any]] = []
    last_byte = 0
    last_char = 0

    def char_position() -> int:
        """byte の位置を文字の位置へ。解析の順に増えるので差分だけ復号する。"""
        nonlocal last_byte, last_char
        byte = parser.CurrentByteIndex
        last_char += len(encoded[last_byte:byte].decode("utf-8"))
        last_byte = byte
        return last_char

    def close_text() -> None:
        if stack:
            stack[-1][5] = True

    def start(tag: str, _attrs: Any) -> None:
        close_text()
        stack.append(
            [tag, char_position(), parser.CurrentLineNumber, parser.CurrentColumnNumber, [], False]
        )

    def end(_tag: str) -> None:
        tag, position, line, column, parts, _ = stack.pop()
        value = "".join(parts)
        if value.strip():
            locator = f"line={line};col={column};tag={tag}"
            leaves.append(XmlLeaf(value, SourceRange(position, char_position(), locator)))

    def characters(data: str) -> None:
        if stack and not stack[-1][5]:
            stack[-1][4].append(data)

    def other(*_args: Any) -> None:
        close_text()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.CommentHandler = other
    parser.ProcessingInstructionHandler = other
    parser.Parse(encoded, True)
    return leaves


# ---- 行の位置 -----------------------------------------------------------------------------------


class LineIndex:
    """文字の位置と行（0 始まり。RFC 5147 の line）を換算する。"""

    def __init__(self, text: str) -> None:
        self._starts = [0, *(match.end() for match in re.finditer("\n", text))]
        self._length = len(text)

    def line_of(self, position: int) -> int:
        return bisect.bisect_right(self._starts, position) - 1

    def start_of(self, line: int) -> int:
        return self._starts[line] if line < len(self._starts) else self._length


def physical_line_starts(text: str) -> list[int]:
    """物理行の開始の位置（``csv.reader`` の ``line_num`` と同じ改行の規則で区切る）。"""
    return [0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", text))]


def text_locator(index: LineIndex, start: int, end: int) -> str:
    """RFC 5147 の ``char`` と ``line`` の考え方の位置。"""
    return (
        f"char={start},{end};line={index.line_of(start)},{index.line_of(max(start, end - 1)) + 1}"
    )


def iter_text_blocks(text: str, separator: re.Pattern[str]) -> Iterator[tuple[int, int]]:
    """区切りの正規表現の間の範囲（空白だけの範囲を除く）。"""
    position = 0
    for match in separator.finditer(text):
        if text[position : match.start()].strip():
            yield position, match.start()
        position = match.end()
    if text[position:].strip():
        yield position, len(text)


def nonblank_line_ranges(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """範囲の中の、空白を除いた行の範囲。"""
    ranges: list[tuple[int, int]] = []
    position = start
    while position < end:
        newline = text.find("\n", position, end)
        line_end = end if newline < 0 else newline
        line = text[position:line_end]
        if line.strip():
            leading = len(line) - len(line.lstrip())
            ranges.append((position + leading, position + len(line.rstrip())))
        position = line_end + 1
    return ranges


__all__ = [
    "FRAGMENT_SCHEMES",
    "SCHEME_DELIMITED_ROW",
    "SCHEME_HTML_SOURCE",
    "SCHEME_JSONL_LINE",
    "SCHEME_JSON_POINTER",
    "SCHEME_MARKDOWN_SOURCE",
    "SCHEME_TEXT_CHAR",
    "SCHEME_XML_SOURCE",
    "SOURCE_MAP_KEY",
    "SOURCE_MAP_VERSION",
    "SOURCE_UNIT_CODEPOINTS",
    "SOURCE_WINDOW_CODEPOINTS",
    "HtmlNode",
    "LineIndex",
    "SourceRange",
    "XmlLeaf",
    "decode_source_text",
    "delimited_element_ranges",
    "delimited_record_ranges",
    "html_segments",
    "iter_text_blocks",
    "json_element_ranges",
    "jsonl_element_ranges",
    "jsonl_line_ranges",
    "nonblank_line_ranges",
    "source_map_artifact",
    "structured_record_ranges",
    "text_locator",
    "xml_leaves",
]
