"""Oracle の接続ごとの初期化（3 製品共通）。

python-oracledb に依存しない（接続オブジェクトを受け取るだけ）。
"""

from __future__ import annotations

from typing import Any

# 新しい接続ごとに 1 回の ALTER SESSION で次の 2 つを設定する（往復を増やさない）。
#
# - RESULT_CACHE_MODE = MANUAL: ADB の既定は result_cache_mode=FORCE。行の外にある大きな
#   JSON(OSON) 列を読む SELECT が result cache の内部エラー
#   (ORA-700 [qesrcCopyQBufferToOpn:large loc])を起こし、heap の破損
#   (ORA-600 [17114] / [kghfrh:ds] など)と接続断が連鎖したため、アプリの接続では使わない(#333)。
# - TIME_ZONE = '+00:00': 時刻は UTC で保存する(#3255)。python-oracledb は接続のときに、
#   process の地方時の offset を session の時間帯にする(thin mode は ORA_SDTZ が無いとき)。
#   また datetime の tzinfo を無視して TIMESTAMP として bind するため、Compute の OS を
#   Asia/Tokyo にすると、UTC の時刻を TIMESTAMP WITH TIME ZONE の列に書く・比べる箇所が
#   9 時間ずれる。OS の時間帯に左右されないよう UTC に固定する。地域名('UTC')ではなく
#   offset にする(名前の時間帯の値は thin mode で読めない: DPY-3022。#3241)。
SESSION_INIT_SQL = "ALTER SESSION SET RESULT_CACHE_MODE = MANUAL TIME_ZONE = '+00:00'"


def init_oracle_session(connection: Any, requested_tag: str | None = None) -> None:
    """新しい接続の初期化。pool の ``session_callback`` にも、単発の接続にも使う。"""
    del requested_tag
    with connection.cursor() as cursor:
        cursor.execute(SESSION_INIT_SQL)
