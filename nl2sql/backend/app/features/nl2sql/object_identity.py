"""NL2SQL / 管理画面で共有する Oracle owner-qualified object identity。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIMPLE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")


def normalize_object_part(value: str) -> str:
    """规范化一个 Oracle owner/object 标识符。"""

    raw = str(value or "").strip()
    if '"' in raw:
        if len(raw) >= 2 and raw[0] == raw[-1] == '"' and raw.count('"') == 2:
            raw = raw[1:-1]
        else:
            raise ValueError(f"{value}: Oracle 識別子が不正です。")
    normalized = raw.upper()
    if not normalized or not _SIMPLE_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{value}: Oracle 識別子が不正です。")
    return normalized


@dataclass(frozen=True, slots=True)
class OracleObjectIdentity:
    """Owner-aware 的只读对象身份。"""

    owner: str
    object_name: str

    @property
    def qualified_name(self) -> str:
        return f"{self.owner}.{self.object_name}"

    @property
    def quoted_name(self) -> str:
        return f'"{self.owner}"."{self.object_name}"'


def parse_object_identity(
    value: str,
    *,
    default_owner: str = "",
) -> OracleObjectIdentity:
    """`OBJECT` 或 `OWNER.OBJECT` を解析し、必ず owner 付きで返す。"""

    parts = [part.strip() for part in str(value or "").split(".") if part.strip()]
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
    raise ValueError(f"{value}: OWNER.OBJECT 形式で指定してください。")


def qualified_object_name(owner: str, object_name: str) -> str:
    """Catalog metadata から canonical `OWNER.OBJECT` key を作る。"""

    return OracleObjectIdentity(
        owner=normalize_object_part(owner),
        object_name=normalize_object_part(object_name),
    ).qualified_name


__all__ = [
    "OracleObjectIdentity",
    "normalize_object_part",
    "parse_object_identity",
    "qualified_object_name",
]
