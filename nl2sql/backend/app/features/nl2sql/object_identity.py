"""NL2SQL / 管理画面で共有する Oracle owner-qualified object identity。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIMPLE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")


def _split_identifier_parts(value: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    in_double = False
    index = 0
    raw = str(value or "").strip()
    while index < len(raw):
        char = raw[index]
        if char == '"':
            buffer.append(char)
            if in_double and index + 1 < len(raw) and raw[index + 1] == '"':
                buffer.append(raw[index + 1])
                index += 2
                continue
            in_double = not in_double
            index += 1
            continue
        if char == "." and not in_double:
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    if in_double:
        raise ValueError(f"{value}: Oracle 識別子が不正です。")
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


def _unquote_identifier_part(value: str, original: str) -> str:
    if not (len(value) >= 2 and value[0] == value[-1] == '"'):
        raise ValueError(f"{original}: Oracle 識別子が不正です。")
    inner = value[1:-1]
    chars: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == '"':
            if index + 1 < len(inner) and inner[index + 1] == '"':
                chars.append('"')
                index += 2
                continue
            raise ValueError(f"{original}: Oracle 識別子が不正です。")
        chars.append(char)
        index += 1
    normalized = "".join(chars)
    if not normalized or "\x00" in normalized:
        raise ValueError(f"{original}: Oracle 識別子が不正です。")
    return normalized


def normalize_object_part(value: str) -> str:
    """规范化一个 Oracle owner/object 标识符。"""

    raw = str(value or "").strip()
    if '"' in raw:
        return _unquote_identifier_part(raw, str(value))
    if not raw or "\x00" in raw:
        raise ValueError(f"{value}: Oracle 識別子が不正です。")
    normalized = raw.upper()
    if _SIMPLE_IDENTIFIER.fullmatch(normalized):
        return normalized
    return raw


def format_object_part(value: str) -> str:
    """Catalog/API 表示用に、必要な部分だけ二重引用符で囲む。"""

    raw = str(value or "").strip()
    normalized = normalize_object_part(raw) if raw.startswith('"') else raw
    if not normalized or "\x00" in normalized:
        raise ValueError(f"{value}: Oracle 識別子が不正です。")
    if _SIMPLE_IDENTIFIER.fullmatch(normalized):
        return normalized
    return '"' + normalized.replace('"', '""') + '"'


@dataclass(frozen=True, slots=True)
class OracleObjectIdentity:
    """Owner-aware 的只读对象身份。"""

    owner: str
    object_name: str

    @property
    def qualified_name(self) -> str:
        return qualified_object_name(self.owner, self.object_name)

    @property
    def quoted_name(self) -> str:
        return f'"{self.owner}"."{self.object_name}"'


def parse_object_identity(
    value: str,
    *,
    default_owner: str = "",
) -> OracleObjectIdentity:
    """`OBJECT` 或 `OWNER.OBJECT` を解析し、必ず owner 付きで返す。"""

    parts = _split_identifier_parts(value)
    if len(parts) == 1 and default_owner:
        return OracleObjectIdentity(
            owner=normalize_object_part(default_owner),
            object_name=normalize_object_part(parts[0]),
        )
    if len(parts) == 2:
        return OracleObjectIdentity(
            owner=normalize_object_part(parts[0]),
            object_name=normalize_object_part(parts[1]),
        )
    if len(parts) == 1 and '"' in parts[0]:
        normalize_object_part(parts[0])
    raise ValueError(f"{value}: OWNER.OBJECT 形式で指定してください。")


def qualified_object_name(owner: str, object_name: str) -> str:
    """Catalog metadata から canonical `OWNER.OBJECT` key を作る。"""

    return f"{format_object_part(owner)}.{format_object_part(object_name)}"


__all__ = [
    "OracleObjectIdentity",
    "format_object_part",
    "normalize_object_part",
    "parse_object_identity",
    "qualified_object_name",
]
