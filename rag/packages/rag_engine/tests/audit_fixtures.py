"""生成後の監査を削除した後も、古いテストの stub が読めるようにする。"""

from typing import Any


def is_audit_wire(_schema: Any) -> bool:
    return False


def is_explanation_schema(_schema: Any) -> bool:
    return False


def legacy_audit_result(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("監査は呼ばれない")


def synthetic_explanation(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("説明の生成は呼ばれない")


def legacy_application_audit_result(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("結論の判定は呼ばれない")


def legacy_audit_to_wire_fixture(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("監査は呼ばれない")
