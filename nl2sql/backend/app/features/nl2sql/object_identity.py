"""NL2SQL 查询链路使用的 Oracle 对象限定名。

管理型 DDL 仍使用 service.py 既有的简单 identifier helper；本模块只负责
只读 NL2SQL scope，避免跨 schema 支持意外扩散到写操作。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIMPLE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")


def normalize_object_part(value: str) -> str:
    """规范化一个 Oracle owner/object 标识符。"""

    normalized = str(value or "").strip().strip('"').upper()
    if not normalized or not _SIMPLE_IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{value}: Oracle 对象标识符不合法。")
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
