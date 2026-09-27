"""共通認証のエラー型（#212）。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

_SECURITY_ERROR_CODES = {
    400: "SECURITY_REQUEST_INVALID",
    401: "SECURITY_AUTHENTICATION_REQUIRED",
    403: "SECURITY_PERMISSION_DENIED",
    404: "SECURITY_RESOURCE_NOT_FOUND",
    409: "SECURITY_STATE_CONFLICT",
    429: "SECURITY_RATE_LIMITED",
    500: "SECURITY_OPERATION_FAILED",
    503: "SECURITY_SERVICE_UNAVAILABLE",
}


class SecurityApiError(RuntimeError):
    """利用者に見せてよい認証・認可のエラー。router は problem 形式で返す。"""

    def __init__(
        self,
        status_code: int,
        public_message: str,
        *,
        code: str | None = None,
        title: str | None = None,
        retryable: bool = False,
        field_errors: Sequence[Mapping[str, str]] = (),
    ) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message
        self.code = code or _SECURITY_ERROR_CODES.get(status_code, "SECURITY_API_ERROR")
        self.title = title
        self.retryable = retryable
        self.field_errors = tuple(dict(item) for item in field_errors)


class LoginFailed(SecurityApiError):
    def __init__(self) -> None:
        super().__init__(401, "ログインユーザーIDまたはパスワードを確認してください。")


class SecurityStoreError(RuntimeError):
    """認証 store の基底例外。"""


class SecurityNotFound(SecurityStoreError):
    pass


class SecurityConflict(SecurityStoreError):
    """競合の機械判定情報を store から service へ安全に伝える。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "SECURITY_STATE_CONFLICT",
        pointer: str | None = None,
        field_code: str = "conflict",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer
        self.field_code = field_code


class SecurityMigrationRequired(SecurityStoreError):
    """security migration が必要な DB object に到達した。"""

    def __init__(self, object_name: str) -> None:
        self.object_name = object_name
        super().__init__(f"{object_name} security migration is required.")


def missing_security_migration_object(exc: Exception, object_names: Iterable[str]) -> str | None:
    """ORA-00942 のうち、認証の object が原因のものだけを特定する。"""
    if isinstance(exc, SecurityMigrationRequired):
        return exc.object_name
    message = str(exc).upper()
    if "ORA-00942" not in message:
        return None
    return next((name for name in object_names if name in message), None)
