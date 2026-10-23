"""利用状況と自動実行で共通のブラウザのタイムゾーン。"""

from __future__ import annotations

import re
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Tokyo"
_OFFSET = re.compile(r"([+-])([01][0-9]|2[0-3]):([0-5][0-9])")


def resolve_timezone(name: str | None) -> tzinfo:
    """IANA 名と ±HH:MM の固定時差を解決し、不正な値は拒否する。"""
    value = (name or "").strip() or DEFAULT_TIMEZONE
    match = _OFFSET.fullmatch(value)
    if match:
        sign, hours, minutes = match.groups()
        delta = timedelta(hours=int(hours), minutes=int(minutes))
        return timezone(-delta if sign == "-" else delta, name=value)
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("タイムゾーンが正しくありません。") from exc


def timezone_name(tz: tzinfo) -> str:
    """地域名を保持し、固定時差はブラウザと同じ表記で返す。"""
    return tz.key if isinstance(tz, ZoneInfo) else str(tz.tzname(None))
