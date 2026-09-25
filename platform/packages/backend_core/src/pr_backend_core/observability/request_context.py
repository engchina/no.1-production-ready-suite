"""リクエストコンテキスト（request id）。"""

import re
from contextvars import ContextVar
from uuid import uuid4

# 受信ヘッダ x-request-id の許容文字（過剰な label cardinality / 注入を防ぐ）。
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# ログ等から参照できる現在リクエストの id。
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def generate_request_id(incoming: str | None) -> str:
    """受信 x-request-id を検証し、妥当ならそれを、無ければ新規発行する。"""
    candidate = (incoming or "").strip()
    if REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex
