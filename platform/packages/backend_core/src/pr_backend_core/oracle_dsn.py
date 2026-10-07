"""Wallet の tnsnames.ora の別名（alias）から、再試行の設定を外した接続記述子を作る（#1212）。

ADB の Wallet の `tnsnames.ora` は、別名ごとに `(retry_count=20)(retry_delay=3)` を持つ。
python-oracledb は接続記述子の値を `connect()` / `create_pool()` の `retry_count` / `retry_delay`
より優先するため、DB の停止中（`ORA-12514` / `DPY-6001`）は 1 回の接続に 1 分以上かかる。接続を
待たずに失敗を返したいところ（接続確認・接続 pool）は、別名を記述子に置き換えて再試行の設定を外す。

- 別名が `tnsnames.ora` に無い・Wallet が無い・読めないときは、元の DSN をそのまま返す（簡易接続の
  文字列・記述子の DSN もそのまま）。
- 接続先・資格情報はログに出さない。
"""

from __future__ import annotations

import re
from pathlib import Path

_ALIAS_RE = re.compile(r"(?im)^\s*([A-Za-z0-9_.-]+)\s*=\s*")
_RETRY_COUNT_RE = re.compile(r"\(\s*retry_count\s*=\s*\d+\s*\)", re.IGNORECASE)
_RETRY_DELAY_RE = re.compile(r"\(\s*retry_delay\s*=\s*\d+\s*\)", re.IGNORECASE)


def tns_alias_descriptor(wallet_dir: str | Path, alias: str) -> str | None:
    """`tnsnames.ora` から別名の接続記述子を抜き出す（見つからなければ None）。"""
    tnsnames = Path(wallet_dir).expanduser() / "tnsnames.ora"
    if not alias.strip() or not tnsnames.is_file():
        return None
    try:
        content = tnsnames.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for match in _ALIAS_RE.finditer(content):
        if match.group(1).lower() != alias.strip().lower():
            continue
        start = content.find("(", match.end())
        if start < 0:
            return None
        return _balanced_parenthesized_text(content, start)
    return None


def strip_tns_retry_settings(descriptor: str) -> str:
    """接続記述子から `(retry_count=N)` と `(retry_delay=N)` を取り除く。"""
    return _RETRY_DELAY_RE.sub("", _RETRY_COUNT_RE.sub("", descriptor))


def dsn_without_tns_retry(dsn: str, wallet_dir: str | Path | None) -> str:
    """Wallet の別名なら、再試行の設定を外した接続記述子にする（それ以外は元の DSN）。"""
    if not wallet_dir or not str(wallet_dir).strip():
        return dsn
    descriptor = tns_alias_descriptor(wallet_dir, dsn)
    if descriptor is None:
        return dsn
    return strip_tns_retry_settings(descriptor)


def _balanced_parenthesized_text(content: str, start: int) -> str | None:
    """`start` の `(` から、対応する `)` までを返す（対応しなければ None）。"""
    depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
    return None


__all__ = ["dsn_without_tns_retry", "strip_tns_retry_settings", "tns_alias_descriptor"]
