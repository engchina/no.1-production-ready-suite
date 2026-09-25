"""設定 API の安全な構造化エラー。"""

from __future__ import annotations


class DatabaseWalletOperationError(RuntimeError):
    """Wallet の server-side install が失敗した段階を API 境界へ伝える。"""

    def __init__(
        self,
        *,
        code: str,
        public_message: str,
        stage: str,
        retryable: bool = True,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.stage = stage
        self.retryable = retryable
