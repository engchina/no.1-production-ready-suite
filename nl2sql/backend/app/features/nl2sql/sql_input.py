"""利用者の入力が SQL の文そのものかの判定（#1715）。

AI要件確認（#1439 / #1442）は、自然言語の質問から SQL の要件（SELECT・FROM など）を確かめる。
入力が SQL の文そのもの（`select * from employee` など）なら確かめることが無いので、LLM を呼ばずに
SQL の生成へ進む。画面（`frontend/src/features/nl2sql/sqlInput.ts`）と同じ規則にする。

規則:

- SELECT か WITH で始まる 1 文。前後の空白・コメント（`--` と `/* */`）・末尾の `;` は許す。
- 文字列（`'…'`）と引用符付きの名前（`"…"`）の外に、もう 1 つの `;`（2 つ目の文）が無い。
- 文字列と引用符付きの名前の外は ASCII だけ（`select * from employee の件数を出して` のような、
  SQL の語で始まる日本語の質問は SQL の文として扱わない）。
- SELECT は FROM を持つ。WITH は `<名前> AS (` と SELECT を持つ。
"""

from __future__ import annotations

import re

_SELECT_RE = re.compile(r"^select\b", re.IGNORECASE)
_WITH_RE = re.compile(
    r"^with\s+(?:recursive\s+)?[A-Za-z_][\w$#]*(?:\s*\([^()]*\))?\s+as\s*\(", re.IGNORECASE
)
_FROM_RE = re.compile(r"\bfrom\b", re.IGNORECASE)
_SELECT_WORD_RE = re.compile(r"\bselect\b", re.IGNORECASE)


def _code_outside_literals(text: str) -> str | None:
    """コメントを除き、文字列・引用符付きの名前を空にした SQL の骨組み（閉じていなければ None）。"""

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        pair = text[index : index + 2]
        if pair == "--":
            end = text.find("\n", index)
            index = length if end == -1 else end
            out.append(" ")
            continue
        if pair == "/*":
            end = text.find("*/", index + 2)
            if end == -1:
                return None
            index = end + 2
            out.append(" ")
            continue
        if char in ("'", '"'):
            quote = char
            index += 1
            while True:
                end = text.find(quote, index)
                if end == -1:
                    return None
                # '' / "" は引用符そのもの（閉じていない）。
                if text[end + 1 : end + 2] == quote:
                    index = end + 2
                    continue
                index = end + 1
                break
            out.append(f"{quote}{quote}")
            continue
        out.append(char)
        index += 1
    return "".join(out)


def is_sql_statement_input(text: str) -> bool:
    """入力が SQL の文（SELECT / WITH で始まる 1 文）か。"""

    code = _code_outside_literals(text or "")
    if code is None:
        return False
    code = code.strip()
    while code.endswith(";"):
        code = code[:-1].rstrip()
    if not code or ";" in code or not code.isascii():
        return False
    if _SELECT_RE.match(code):
        return bool(_FROM_RE.search(code))
    if _WITH_RE.match(code):
        return bool(_SELECT_WORD_RE.search(code))
    return False


__all__ = ["is_sql_statement_input"]
