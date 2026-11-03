"""DB の読み取りを時間の上限つきで実行し、失敗を画面が見分けられる API の失敗にする（#4217）。

読み取りの失敗を 200 + 空のデータで返さない。画面は「0 件」と「読み取れなかった」を応答の
status と `error_code` で見分ける（文言は照合しない）。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from pr_backend_core.oracle_errors import is_oracle_connection_error, is_oracle_pool_wait_timeout
from pydantic import ValidationError

from app.api.errors import (
    DatabaseReadFailedError,
    DatabaseReadTimeoutError,
    DatabaseUnavailableError,
)

logger = logging.getLogger(__name__)

# そのまま投げる例外。意図した HTTP 応答と、DB の障害ではなくプログラムの誤り（#476）。
_NOT_READ_FAILURES: tuple[type[BaseException], ...] = (
    HTTPException,
    ValidationError,
    TypeError,
    AttributeError,
    NameError,
    AssertionError,
)


async def read_or_raise[T](
    loader: Callable[[], Awaitable[T]],
    *,
    timeout_seconds: float,
    log_label: str,
) -> T:
    """loader を時間の上限つきで実行する。失敗は原因ごとの `CodedApiError` にして投げる。

    - 時間の上限・pool の待ちの timeout: `DatabaseReadTimeoutError`（混雑の可能性）
    - 接続の失敗: `DatabaseUnavailableError`
    - それ以外: `DatabaseReadFailedError`（接続できないと言い切らない）
    """
    try:
        return await asyncio.wait_for(loader(), timeout=timeout_seconds)
    except TimeoutError as exc:
        logger.warning("%s_timeout", log_label, extra={"timeout_seconds": timeout_seconds})
        raise DatabaseReadTimeoutError() from exc
    except _NOT_READ_FAILURES:
        raise
    except Exception as exc:  # noqa: BLE001 - DB の読み取りの失敗を API の失敗に正規化する境界
        logger.exception(
            "%s_failed",
            log_label,
            extra={"exception_type": type(exc).__name__},
        )
        if is_oracle_pool_wait_timeout(exc):
            raise DatabaseReadTimeoutError() from exc
        if is_oracle_connection_error(exc):
            raise DatabaseUnavailableError() from exc
        raise DatabaseReadFailedError() from exc
