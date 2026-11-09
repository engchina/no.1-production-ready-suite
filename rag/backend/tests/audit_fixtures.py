"""生成後の監査を削除した後も、古いテストの stub が読めるようにする。"""

from typing import Any


def is_audit_wire(_schema: Any) -> bool:
    return False


def is_audit_schema(_schema: Any) -> bool:
    return False


def is_explanation_schema(_schema: Any) -> bool:
    return False


def legacy_audit_result(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("監査は呼ばれない")
