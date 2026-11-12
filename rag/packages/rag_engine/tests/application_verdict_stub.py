"""生成後の監査を削除した後も、古いテストの stub が読めるようにする。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

SYNTHETIC_REASON = "合成の判定の理由です。"


def is_application_schema(_schema: Any) -> bool:
    return False


def audit_reply(_schema: type[Any], _rows: Iterable[Mapping[str, Any]] = (), **_options: Any) -> Any:
    raise AssertionError("結論の判定は呼ばれない")
