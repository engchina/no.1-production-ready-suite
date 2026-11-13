"""実際に解析した SQL の位置をモデル用の診断へ変換する。SQL 片をログへ出さない。"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from sqlglot.errors import TokenError

from .oracle_dialect import OracleConversion
from .oracle_keyword_metadata import NativeKeywordMetadata

OracleOffsetBasis = Literal["utf8_bytes_zero_based"]
_CONTEXT_CHARACTERS = 80
_TOKEN_CHARACTERS = 160


class SqlTokenLocation(TypedDict):
    kind: str
    lexer_source: Literal["sqlglot.OracleConversion"]
    text: str
    text_truncated: bool
    start_character: int
    end_character_exclusive: int


class SqlErrorLocation(TypedDict):
    character_offset: int
    line_1_based: int
    column_1_based: int
    column_unit: Literal["unicode_codepoints"]
    at_eof: bool
    token: SqlTokenLocation | None
    excerpt: str
    excerpt_start_character: int
    excerpt_end_character_exclusive: int


class OracleSqlDiagnostic(TypedDict):
    code: str
    offset: int
    offset_unit: Literal["utf8_bytes"] | None
    offset_base: Literal[0] | None
    sql_source: Literal["prepared_sql"]
    location: SqlErrorLocation | None
    unavailable_reason: str | None
    native_keyword_metadata: NotRequired[NativeKeywordMetadata]


def oracle_parse_diagnostic(
    prepared_sql: str,
    *,
    code: int,
    offset: int,
    offset_basis: OracleOffsetBasis | None,
) -> OracleSqlDiagnostic:
    """確認できた UTF-8 byte 位置だけを解読する。未知・無効な位置を近傍へ補正しない。

    driver の SQL は UTF-8。offset の基準は driver の契約を確認した場合だけ渡す。
    片と token は制限付きのモデル入力専用で、例外のメッセージには使わない。
    """
    diagnostic: OracleSqlDiagnostic = {
        "code": f"ORA-{code:05d}",
        "offset": offset,
        "offset_unit": "utf8_bytes" if offset_basis == "utf8_bytes_zero_based" else None,
        "offset_base": 0 if offset_basis == "utf8_bytes_zero_based" else None,
        "sql_source": "prepared_sql",
        "location": None,
        "unavailable_reason": None,
    }
    if offset_basis != "utf8_bytes_zero_based":
        diagnostic["unavailable_reason"] = "unknown_offset_basis"
        return diagnostic
    try:
        encoded = prepared_sql.encode("utf-8")
    except UnicodeEncodeError:
        diagnostic["unavailable_reason"] = "sql_not_utf8_encodable"
        return diagnostic
    if isinstance(offset, bool) or not 0 < offset <= len(encoded):
        diagnostic["unavailable_reason"] = "invalid_or_unspecified_offset"
        return diagnostic
    try:
        prefix = encoded[:offset].decode("utf-8")
    except UnicodeDecodeError:
        diagnostic["unavailable_reason"] = "offset_inside_utf8_character"
        return diagnostic
    position = len(prefix)
    token_location: SqlTokenLocation | None = None
    if position < len(prepared_sql):
        try:
            for token in OracleConversion().tokenize(prepared_sql):
                if token.start <= position <= token.end:
                    text = prepared_sql[token.start : token.end + 1]
                    token_location = {
                        "kind": token.token_type.name,
                        "lexer_source": "sqlglot.OracleConversion",
                        "text": text[:_TOKEN_CHARACTERS],
                        "text_truncated": len(text) > _TOKEN_CHARACTERS,
                        "start_character": token.start,
                        "end_character_exclusive": token.end + 1,
                    }
                    break
        except TokenError:
            # 字句解析できなくても native 位置の行列と原文片は保持する。token を推測しない。
            pass
    start = max(0, position - _CONTEXT_CHARACTERS)
    end = min(len(prepared_sql), position + _CONTEXT_CHARACTERS)
    diagnostic["location"] = {
        "character_offset": position,
        "line_1_based": prefix.count("\n") + 1,
        "column_1_based": len(prefix.rsplit("\n", 1)[-1]) + 1,
        "column_unit": "unicode_codepoints",
        "at_eof": position == len(prepared_sql),
        "token": token_location,
        "excerpt": prepared_sql[start:end],
        "excerpt_start_character": start,
        "excerpt_end_character_exclusive": end,
    }
    return diagnostic
