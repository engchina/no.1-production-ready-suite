"""Oracle の接続ごとの初期化（3 製品共通）。

python-oracledb に依存しない（接続オブジェクトを受け取るだけ）。
"""

from __future__ import annotations

from typing import Any

# ADB の既定は result_cache_mode=FORCE。行の外にある大きな JSON(OSON) 列を読む SELECT が
# result cache の内部エラー(ORA-700 [qesrcCopyQBufferToOpn:large loc])を起こし、heap の破損
# (ORA-600 [17114] / [kghfrh:ds] など)と接続断が連鎖したため、アプリの接続では使わない(#333)。
DISABLE_RESULT_CACHE_SQL = "ALTER SESSION SET RESULT_CACHE_MODE = MANUAL"


def init_oracle_session(connection: Any, requested_tag: str | None = None) -> None:
    """新しい接続の初期化。pool の ``session_callback`` にも、単発の接続にも使う。"""
    del requested_tag
    with connection.cursor() as cursor:
        cursor.execute(DISABLE_RESULT_CACHE_SQL)
