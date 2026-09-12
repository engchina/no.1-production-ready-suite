"""Oracle の引用・コメントを保全した SELECT 実行/解析用の字句互換処理。"""

from __future__ import annotations

from collections.abc import Iterator

_Q_CLOSERS = {"[": "]", "(": ")", "{": "}", "<": ">"}


def _tokens(sql: str) -> Iterator[tuple[str, str]]:
    index = 0
    while index < len(sql):
        start = index
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end
            yield "comment", sql[start:index]
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                yield "invalid", sql[index:]
                return
            index = end + 2
            yield "comment", sql[start:index]
            continue
        boundary = index == 0 or not (sql[index - 1].isalnum() or sql[index - 1] in "_$#")
        prefix = 0
        if boundary:
            if sql[index : index + 2].lower() == "q'":
                prefix = 2
            elif sql[index : index + 3].lower() == "nq'":
                prefix = 3
        if prefix and index + prefix < len(sql):
            opener = sql[index + prefix]
            end = sql.find(_Q_CLOSERS.get(opener, opener) + "'", index + prefix + 1)
            if opener.isspace() or end < 0:
                yield "invalid", sql[index:]
                return
            index = end + 2
            yield "nq" if prefix == 3 else "q", sql[start:index]
            continue
        quote = sql[index]
        if quote in {"'", '"'}:
            index += 1
            while index < len(sql):
                if sql[index] != quote:
                    index += 1
                elif sql[index : index + 2] == quote * 2:
                    index += 2
                else:
                    index += 1
                    yield "quoted", sql[start:index]
                    break
            else:
                yield "invalid", sql[start:]
                return
            continue
        index += 1
        yield "code", sql[start:index]


def prepare_oracle_query(sql: str, *, for_parser: bool = True) -> str:
    """終端の分号だけを除き、parser 用には q 引用を同値の通常引用へ変換する。

    コメント/通常引用内の q や分号は触らない。複数文の区切りは残す。
    不正な引用や閉じていないコメントは parser に拒否させる。
    """
    tokens = list(_tokens(sql))
    # 末尾の空 statement は一度で除去し、実行用/解析用の反復処理を冪等にする。
    # 実際の SQL token に到達したら停止し、文と文の間の分号は残す。
    for index in range(len(tokens) - 1, -1, -1):
        kind, text = tokens[index]
        if kind == "comment" or not text.strip():
            continue
        if (kind, text) != ("code", ";"):
            break
        tokens[index] = ("code", "")
    result: list[str] = []
    for kind, text in tokens:
        if for_parser and kind in {"q", "nq"}:
            prefix = 4 if kind == "nq" else 3
            value = text[prefix:-2].replace("'", "''")
            text = ("N" if kind == "nq" else "") + "'" + value + "'"
        result.append(text)
    return "".join(result).strip()
