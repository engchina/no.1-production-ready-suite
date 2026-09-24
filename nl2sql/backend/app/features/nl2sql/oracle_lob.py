"""python-oracledb の JSON CLOB を array fetch で文字列化する補助。"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def configure_clob_fetch_as_text(cursor: Any) -> None:
    """CLOB locator の行単位 read を避け、DB_TYPE_LONG として一括取得する。"""

    try:
        oracledb = import_module("oracledb")
    except Exception:  # pragma: no cover - optional driver boundary
        return

    def output_type_handler(inner_cursor: Any, metadata: Any) -> Any:
        if metadata.type_code is not oracledb.DB_TYPE_CLOB:
            return None
        return inner_cursor.var(
            oracledb.DB_TYPE_LONG,
            arraysize=inner_cursor.arraysize,
        )

    cursor.outputtypehandler = output_type_handler
