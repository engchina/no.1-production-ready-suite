"""表・一覧の行と列の対応に反して、別の行・列の値を述べた文を見つける（#2659）。

根拠の表（HTML の表・Markdown の表・「名前：値」の行の並び・1 文の中で並べた「A は x、B は y」）は、値をその行の
区分・名前と、その列の見出しに対応させる。文がある行の区分・名前と、別の行にしか無い値を同じ所で結び付けていれば、
語と値が根拠のどこかにあっても、根拠の対応とは違う。語の一致・話題の一致では見分けられないので、表の構造（行・列）で
確かめる（表の事実の検証で、セルを行と列の見出しに結び付けて照らす考え方）。

値は、英数字だけのセル（コード・番号）と、先頭の列（行の区分・名前の列）以外のセル（日本語の値・単位付きの値）と、
そのセルの中のコード（「A-05-13-1／140 分」の「A-05-13-1」）。他の語の一部に現れやすい短い語（数字の無い 2 文字以下。
「あり」「なし」）は値にしない。

決定的な照合で、意味は見ない。誤って正しい文を外さないように、次のときだけ「対応しない」とする。

- 値のある行のどれにも、文が同じ所で述べた行の手がかり（その表の 1 行にだけあるセル。同じ行のコードなどの値も含む）
  が無く、
- 別の行の区分・名前（値でないセル）を、文が同じ所で述べている。別の行の値を同じ所で述べただけ
  （複数の行の値の列挙・範囲「254・253・300」「A101〜A103」）では疑わない。
- 見出しの行のセルと、表の複数の行にあるセルは、行を見分ける手がかりにしない。
- 列: 行は合っていても、表に見出しの行があり、文がその値の列の見出しを述べず、同じ行に値のある別の列の見出し
  （「改定前」の値に対する「改定後」）を同じ所で述べている。セルの一部に現れた見出しの語（「項目乙」の「項目」）は
  見出しを述べたことにしない。
- 表の本文の行をそのまま写した行（Markdown の表の行）は、行と列の対応が表のとおりなので調べない。

取り違えの直し（`table_corrections`）は、正しいセルが 1 つに決まり、別の列に直すときは 2 つのセルの形が同じときだけ
値を示す。確かめられなければ値を書き換えない。

「同じ所」は文の読点・句点で区切った区間で、値の無い区間（「区分Ⅱの場合、」）は続く区間と合わせて見る。
回答の生成の検査（`generation.grounded`）が使う。
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from pr_backend_core.spreadsheet.models import COLUMN_ROLE_TEXT_LABELS

from rag_engine.parsing.content_labels import content_labels
from rag_engine.retrieval.character_forms import SYMBOL_FOLD, fold_width

# 値とみなすセル（英数字と区切りの「-」「_」「.」だけ。NFKC でローマ数字・全角も英数字にそろえる）。
_VALUE_CELL = re.compile(r"[0-9a-z]+(?:[-_.][0-9a-z]+)*")
_MAX_VALUE_CHARS = 16
# 区分・名前とみなすセルの長さ（短すぎると別の語の一部に当たり、長い説明の文は文が丸ごと写すことが少ない）。
_MIN_LABEL_CHARS = 2
_MAX_LABEL_CHARS = 40
# 先頭の列以外の英数字だけでないセルを値とみなす最小の長さ（数字を含まない語。短い語は別の語の一部に現れやすい）。
_MIN_WORD_VALUE_CHARS = 3
# セルの中のコード（区切りを含む英数字の並びか、英字と数字の両方を含む並び。数字だけの並びは数量・番号と
# 見分けられないので除く）。
_CODE_IN_CELL = re.compile(r"(?<![0-9a-z])(?:[0-9a-z]+(?:[-_.][0-9a-z]+)+|(?=[0-9a-z]*[a-z])(?=[0-9a-z]*[0-9])[0-9a-z]{2,})(?![0-9a-z])")
# 説明の文のセル（読点・句点を含む）。
_SENTENCE_PUNCTUATION = re.compile(r"[、。，．]")
# ローマ数字だけのセル（「Ⅱ」）。NFKC で「ii」になるが、値ではなく区分の名前として扱う。
_ROMAN_CELL = re.compile(r"[\u2160-\u217f]{1,4}")
# セルの照合に使う頭の部分（「01（通常）」「区分Ⅱ (旧)」の括弧・空白の前）。
# 数の桁区切りのカンマ（「12,000円」）は区切りにしない（#4060）。
_DIGIT_GROUP_COMMA = r"(?<![0-9])[,，]|[,，](?![0-9]{3}(?![0-9]))"
_CELL_KEY_END = re.compile(rf"\s*[（(［\[〔【「『<＜]|\s{{2,}}|\s*(?:[、。;；]|{_DIGIT_GROUP_COMMA})")
_COLUMN_ROLE_TEXT = re.compile("|".join(f"［{re.escape(label)}］" for label in COLUMN_ROLE_TEXT_LABELS.values()))
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t[dh]\s*>", re.IGNORECASE | re.DOTALL)
_SPAN = re.compile(r"""(row|col)span\s*=\s*["']?(\d+)""", re.IGNORECASE)
_TAG = re.compile(r"<[^<>]*>")
_TABLE_BLOCK = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
# Markdown の表の区切りの行（| --- | :---: |）。
_PIPE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")


def has_table_structure(text: str) -> bool:
    """本文に表の形（HTML の表・Markdown の表の行・列の種類の表示）があるか。語の意味は読まない。"""
    return (
        "<table" in text.lower()
        or _COLUMN_ROLE_TEXT.search(text) is not None
        or any(_PIPE_RULE.match(line) or line.count("|") >= 2 for line in text.splitlines())
    )


# 「名前：値」の行の区切り（全角のコロン・縦棒・タブ・等号。半角のコロンは時刻「9:00」と区別する）。
_PAIR_SEPARATOR = re.compile(r"[：｜|\t＝]|(?<![0-9]):(?![0-9])|\s=\s")
# 「名前：値」の行の先頭の箇条書きの印（「- 」「・」「1. 」）。
_PAIR_MARKER = re.compile(r"^\s*(?:[-*]\s+|[・•]\s*|[0-9]{1,3}[.)．]\s+)")
# 1 文の中で並べた「A は x、B は y」の区間（名前と値の区切りは「は」・コロン・等号。値は短い語・数・コード）。
_PROSE_PAIR = re.compile(
    r"^\s*(?P<label>[^、。，,；;：:＝=]{2,30}?)\s*(?:は|：|:|＝|=)\s*(?P<value>[^、。，,；;]{1,20}?)\s*"
    r"(?:です|である|であり|とする|とします|とし|となる|となります|となり|で)?\s*$"
)
# 文の区間の区切り（読点・句点・改行・セミコロン）。
_SENTENCE_END = re.compile(r"[。！？!?\n]+")
_CLAUSE_END = re.compile(rf"[、；;]|{_DIGIT_GROUP_COMMA}")


def _fold(text: str) -> str:
    """照合の形（NFKC・小文字・空白をまとめる）。ローマ数字「Ⅱ」は「ii」、全角の英数字は半角になる。"""
    return " ".join(unicodedata.normalize("NFKC", fold_width(text)).lower().split())


def _cell_key(cell: str) -> str:
    """セルの照合に使う頭の部分（括弧の補足・続く説明を除く）。"""
    folded = _fold(cell)
    match = _CELL_KEY_END.search(folded)
    return (folded[: match.start()] if match else folded).strip(" ・:：")


def _is_value(key: str) -> bool:
    # 種類を決めるときだけ横線をそろえる。セルの値と照合のキーは元の表記のまま残す。
    return 0 < len(key) <= _MAX_VALUE_CHARS and _VALUE_CELL.fullmatch(key.translate(SYMBOL_FOLD)) is not None


def _is_label(key: str) -> bool:
    return _MIN_LABEL_CHARS <= len(key) <= _MAX_LABEL_CHARS and not _is_value(key)


@dataclass(frozen=True)
class Table:
    """表の本文の行（見出しの行を除く）。各行はセルの照合の形の集合。``cells`` は照合の形 → 原文のセル。

    ``values`` は先頭の列以外の値のセルとセルの中のコード（英数字だけの値は ``_is_value`` で見る）、``labels`` は
    値の形でも区分の名前として扱うセル（ローマ数字だけの先頭の列）。``header`` は列の見出し（無ければ空）、``grid``
    は本文の行の列ごとのセル（照合の形。空欄は空文字）。
    """

    rows: tuple[frozenset[str], ...]
    cells: dict[str, str]
    values: frozenset[str] = frozenset()
    labels: frozenset[str] = frozenset()
    header: tuple[str, ...] = ()
    grid: tuple[tuple[str, ...], ...] = ()
    # セル（とセルの中のコード）の照合の形 → そのセルがある列。
    columns: dict[str, frozenset[int]] = field(default_factory=dict)

    def is_value(self, key: str) -> bool:
        return key not in self.labels and (_is_value(key) or key in self.values)

    def is_label(self, key: str) -> bool:
        return key in self.labels or _is_label(key)


def _take_carried(carried: dict[int, tuple[int, str]], column: int, cells: list[str]) -> int:
    """上の行の rowspan のセルを、その列から続く分だけ行に写し、次の列の位置を返す。"""
    while column in carried:
        remaining, text = carried[column]
        cells.append(text)
        if remaining <= 1:
            del carried[column]
        else:
            carried[column] = (remaining - 1, text)
        column += 1
    return column


def _html_rows(block: str) -> list[tuple[bool, list[str]]]:
    """HTML の表の行（見出しの行か、セルの本文）。rowspan / colspan は覆う行・列にセルを写す。"""
    carried: dict[int, tuple[int, str]] = {}  # 列 → (残りの行の数, 本文)
    rows: list[tuple[bool, list[str]]] = []
    for row_match in _ROW.finditer(block):
        cells: list[str] = []
        kinds: list[str] = []
        column = 0
        for kind, attributes, body in _CELL.findall(row_match.group(1)):
            column = _take_carried(carried, column, cells)
            text = html.unescape(_TAG.sub(" ", body)).strip()
            spans = {name.lower(): int(value) for name, value in _SPAN.findall(attributes)}
            for _ in range(max(1, min(spans.get("col", 1), 50))):
                cells.append(text)
                kinds.append(kind.lower())
                if spans.get("row", 1) > 1:
                    carried[column] = (min(spans["row"], 500) - 1, text)
                column += 1
        _take_carried(carried, column, cells)
        if cells:
            rows.append((bool(kinds) and all(kind == "th" for kind in kinds), cells))
    return rows


def _pipe_rows(lines: Sequence[str]) -> list[tuple[bool, list[str]]]:
    """Markdown の表の行（区切りの行の直前の行は見出し）。"""
    rows: list[tuple[bool, list[str]]] = []
    for index, line in enumerate(lines):
        if _PIPE_RULE.match(line) or line.count("|") < 2:
            continue
        header = index + 1 < len(lines) and _PIPE_RULE.match(lines[index + 1]) is not None
        rows.append((header, [cell.strip() for cell in line.strip().strip("|").split("|")]))
    return rows


def _pair_runs(lines: Sequence[str]) -> list[list[tuple[bool, list[str]]]]:
    """「名前：値」の行（区切りで 2 つ以上のセルに分かれる行）の、続いて並ぶ行のまとまり。見出しの行は持たない。"""
    runs: list[list[tuple[bool, list[str]]]] = [[]]
    for line in lines:
        body = _PAIR_MARKER.sub("", line, count=1)
        cells = [cell.strip() for cell in _PAIR_SEPARATOR.split(body) if cell.strip()] if line.count("|") < 2 else []
        if len(cells) >= 2:
            runs[-1].append((False, cells))
        elif runs[-1]:
            runs.append([])
    return [run for run in runs if run]


def _prose_runs(lines: Sequence[str]) -> list[list[tuple[bool, list[str]]]]:
    """1 文の中で読点で並べた「A は x、B は y」（名前と値の組が 2 つ以上・名前がすべて違う）を表の行にする。"""
    runs: list[list[tuple[bool, list[str]]]] = []
    for line in lines:
        for sentence in _SENTENCE_END.split(line):
            pairs = [match for clause in _CLAUSE_END.split(sentence) if (match := _PROSE_PAIR.match(clause))]
            labels = [_cell_key(match["label"]) for match in pairs]
            if len(pairs) >= 2 and all(labels) and len(set(labels)) == len(labels):
                runs.append([(False, [match["label"].strip(), match["value"].strip()]) for match in pairs])
    return runs


def _value_cell(key: str, cell: str) -> bool:
    """先頭の列以外のセルを値とみなすか（英数字だけ・数字を含む・数字の無い 3 文字以上の語）。

    読点・句点で区切った文のセルは説明で、値ではない（頭の部分だけが文に現れても対応の手がかりにならない）。
    """
    if _is_value(key):
        return True
    if not key or len(key) > _MAX_LABEL_CHARS or _SENTENCE_PUNCTUATION.search(cell):
        return False
    return any(char.isdigit() for char in key) or len(key) >= _MIN_WORD_VALUE_CHARS


_INDEX_CELL = re.compile(r"[0-9]{1,6}")


def index_column(body: Sequence[Sequence[str]]) -> bool:
    """先頭の列が行の番号（整数が上の行から増える）で、ほかに 2 列以上ある表か。

    行の番号の列を区分・名前の列とみなすと、次の列の区分・名前（「部品 1」）を値として扱い、正しい文を別の列の値の
    取り違えと疑ってしまう。行の番号は飛んでもよい（根拠に一部の行だけがあるとき）。
    """
    if len(body) < 2 or any(len(cells) < 3 for cells in body):
        return False
    numbers: list[int] = []
    for cells in body:
        key = _fold(cells[0])
        if not _INDEX_CELL.fullmatch(key):
            return False
        numbers.append(int(key))
    return all(later > earlier for earlier, later in zip(numbers, numbers[1:], strict=False))


def _table(rows: Sequence[tuple[bool, list[str]]]) -> Table | None:
    body = [cells for header, cells in rows if not header]
    headers = [cells for header, cells in rows if header]
    # 見出しの印（<th>・区切りの行）が無い HTML の表は、値のセルが無い最初の行を見出しとみなす。
    if body and len(body) == len(rows) and len(body) >= 3 and not any(_is_value(_cell_key(cell)) for cell in body[0]):
        headers = [body[0]]
        body = body[1:]
    if index_column(body):
        # 先頭の行の番号の列は、行の区分・名前の列でも値でもない（次の列を区分・名前の列にする。#3564）。
        body = [cells[1:] for cells in body]
        headers = [cells[1:] for cells in headers]
    grid = tuple(tuple(_cell_key(cell) for cell in cells) for cells in body)
    values: set[str] = set()
    labels: set[str] = set()
    keyed: list[frozenset[str]] = []
    originals: dict[str, str] = {}
    columns: dict[str, set[int]] = {}
    for cells, keys in zip(body, grid, strict=True):
        row = {key for key in keys if key}
        for column, (cell, key) in enumerate(zip(cells, keys, strict=True)):
            if not key:
                continue
            originals.setdefault(key, " ".join(cell.split())[:_MAX_LABEL_CHARS])
            columns.setdefault(key, set()).add(column)
            if column == 0:
                if _ROMAN_CELL.fullmatch(cell.strip()):
                    labels.add(key)
                continue
            if _value_cell(key, cell):
                values.add(key)
            # セルの中のコード（文がコードだけを述べたときも、その行の値として照合する）。
            for code in _CODE_IN_CELL.findall(_fold(cell)):
                if code != key and _is_value(code):
                    row.add(code)
                    values.add(code)
                    originals.setdefault(code, code)
                    columns.setdefault(code, set()).add(column)
        keyed.append(frozenset(row))
    header: tuple[str, ...] = ()
    if headers:
        header = tuple(_cell_key(cell) for cell in headers[-1])
        for cell, key in zip(headers[-1], header, strict=True):
            if key:
                originals.setdefault(key, " ".join(cell.split())[:_MAX_LABEL_CHARS])
    if len(keyed) < 2:
        return None
    return Table(
        tuple(keyed),
        originals,
        frozenset(values),
        frozenset(labels),
        header,
        grid,
        {key: frozenset(found) for key, found in columns.items()},
    )


def _generated_block_start(line: str) -> bool:
    """画像の生成説明の欄の見出し（「■ グループ」）か、図の中の文字（OCR）の欄の行か（言語資源のラベル）。"""
    labels = content_labels()
    stripped = line.strip()
    prefix = labels.group_prefix.strip()
    if prefix and stripped.startswith(prefix) and stripped[len(prefix) :].strip() in set(labels.groups.values()):
        return True
    ocr = labels.text("picture_ocr")
    return bool(ocr) and re.match(rf"{re.escape(ocr)}\s*[:：]", stripped) is not None


def _document_lines(lines: Sequence[str]) -> list[str]:
    """本文の行から、画像の生成説明の欄と図の中の文字（OCR）の欄（見出しの行から次の空行まで）を除いた行。

    生成説明は 1 つの図の属性を「ラベル: 値」で並べた形で、行は同じ図の別の属性（区分・名前ごとに値を対応させた
    表の行ではない）。欄の値と別の欄の名前を同じ所で述べても、表の対応の取り違えではない（#3482）。
    """
    kept: list[str] = []
    generated = False
    for line in lines:
        if not line.strip():
            generated = False
            kept.append(line)
        elif generated or _generated_block_start(line):
            generated = True
        else:
            kept.append(line)
    return kept


def evidence_tables(text: str, *, line_pairs: bool = True) -> list[Table]:
    """根拠の本文の表（HTML の表・Markdown の表・「名前：値」の行の並び）。行が 2 つ以上のものだけ。

    「名前：値」の行と 1 文の並びは資料の本文からだけ取る（画像の生成説明・図の中の文字の欄は除く）。
    ``line_pairs`` が偽なら、本当の表の構造（HTML・Markdown の表）だけを返す（画像の生成説明の抜粋の根拠）。
    """
    source = str(text or "")
    tables: list[Table] = []
    for block in _TABLE_BLOCK.findall(source):
        table = _table(_html_rows(block))
        if table is not None:
            tables.append(table)
    rest = _TABLE_BLOCK.sub("\n", source)
    lines = [line for line in rest.split("\n") if line.strip()]
    document = [line for line in _document_lines(rest.split("\n")) if line.strip()] if line_pairs else []
    for rows in (_pipe_rows(lines), *_pair_runs(document), *_prose_runs(document)):
        table = _table(rows)
        if table is not None:
            tables.append(table)
    return tables


__all__ = ["Table", "evidence_tables", "has_table_structure"]
