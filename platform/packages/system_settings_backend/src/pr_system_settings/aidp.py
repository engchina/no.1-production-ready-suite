"""Oracle AI Data Platform（AIDP）の連携の情報（3 製品共通。読み取り専用。#5931）。

システム設定 › データベースの「Oracle AI Data Platform」の区画が読む。

- 出すのは共通の設定（`PLATFORM_AIDP_*`）と、External Catalog の登録に要る値（ADB の DB 名・
  サービス名・AIDP 用の DB ユーザーの名前）、そのユーザーが ADB に在るか。
- AIDP の API・OCI の API は呼ばない。DB は、製品が渡す接続で dictionary view（`ALL_USERS`）を
  1 回引くだけ。
- パスワードは持たない・返さない（AIDP 用の DB ユーザーのパスワードは 3 製品の設定に無い）。
- `PLATFORM_AIDP_OCID` が空なら「未設定」だけを返す（画面は区画を出さない）。DB に接続できない
  ときはユーザーの状態を「確かめられない」にし、応答は失敗にしない。

実際の DB 接続は製品ごとに違うため、`run_on_connection` の hook で受け取る（共有パッケージは
oracledb に依存しない）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import urlsplit

from pr_backend_core.oracle_errors import oracle_error_codes
from pydantic import BaseModel

logger = logging.getLogger(__name__)

AidpDbUserStatus = Literal["exists", "missing", "unknown"]

# 製品の接続を借りて `operation(connection)` を実行し、その戻り値を返す hook（接続は製品が返す）。
RunOnConnection = Callable[[Any, Callable[[Any], Any]], Awaitable[Any]]

# ADB のサービス名の末尾（`<DB 名>_<サービス>`）。DSN から DB 名を読むための形式の解析に使う。
_ADB_SERVICE_SUFFIXES = frozenset({"high", "medium", "low", "tp", "tpurgent"})
_SERVICE_NAME_RE = re.compile(r"\(\s*service_name\s*=\s*([^)\s]+)\s*\)", re.IGNORECASE)
_DEFAULT_CHECK_TIMEOUT_SECONDS = 15.0


class AidpIntegrationData(BaseModel):
    """Oracle AI Data Platform の連携の情報（表示用。パスワードの項目は無い）。"""

    configured: bool
    url: str = ""
    ocid: str = ""
    region: str = ""
    # External Catalog の登録に要る値。DB 名は DSN から読めたときだけ入る（読めなければ空）。
    adb_db_name: str = ""
    adb_service_name: str = ""
    db_user: str = ""
    db_user_status: AidpDbUserStatus = "unknown"


def _text(settings: Any, name: str) -> str:
    return str(getattr(settings, name, "") or "").strip()


def aidp_configured(settings: Any) -> bool:
    """AIDP の OCID（`PLATFORM_AIDP_OCID`）が設定されているか。"""
    return bool(_text(settings, "aidp_ocid"))


def _https_url(value: str) -> str:
    """画面がリンクにする URL。https の絶対 URL だけを返す（それ以外は出さない）。"""
    parsed = urlsplit(value)
    return value if parsed.scheme == "https" and parsed.netloc else ""


def adb_name_from_dsn(dsn: str) -> str:
    """ADB の DSN から DB 名を読む。読めない形なら空を返す（推測で埋めない）。

    対応する形: Wallet の別名（`<DB 名>_high`）・接続記述子
    （`(service_name=<接頭辞>_<DB 名>_high.adb…)`）・Easy Connect（`host:port/<service name>`）。
    ADB の DB 名は英数字だけなので、サービス名を `_` で分けた末尾から 2 番目が DB 名になる。
    """
    text = dsn.strip()
    match = _SERVICE_NAME_RE.search(text)
    if match:
        service = match.group(1)
    elif "(" in text:
        return ""
    else:
        service = text.rsplit("/", 1)[-1].split("?", 1)[0]
    parts = service.split(".", 1)[0].split("_")
    if len(parts) < 2 or parts[-1].lower() not in _ADB_SERVICE_SUFFIXES:
        return ""
    return parts[-2]


def database_user_exists(connection: Any, user_name: str) -> bool:
    """DB ユーザーが在るかを `ALL_USERS` で確かめる（アプリ用の DB ユーザーの権限で読める）。"""
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT 1 FROM ALL_USERS WHERE USERNAME = :user_name", {"user_name": user_name}
        )
        return cursor.fetchone() is not None
    finally:
        cursor.close()


async def _db_user_status(
    settings: Any,
    user_name: str,
    *,
    run_on_connection: RunOnConnection | None,
    database_ready: Callable[[], bool],
) -> AidpDbUserStatus:
    """AIDP 用の DB ユーザーの状態。未設定・接続できない・時間切れは unknown（確かめられない）。"""
    if not user_name or run_on_connection is None or not database_ready():
        return "unknown"
    timeout = float(
        getattr(settings, "oracle_db_test_timeout_seconds", _DEFAULT_CHECK_TIMEOUT_SECONDS)
        or _DEFAULT_CHECK_TIMEOUT_SECONDS
    )
    try:
        exists = await asyncio.wait_for(
            run_on_connection(
                settings, lambda connection: database_user_exists(connection, user_name)
            ),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - 接続・SQL・時間切れは「確かめられない」として返す
        # 例外の文には接続先が入り得るため、種類と ORA / DPY のコードだけを残す。
        logger.warning(
            "aidp_db_user_check_failed",
            extra={
                "exception_type": type(exc).__name__,
                "oracle_error_codes": ", ".join(oracle_error_codes(exc)) or None,
            },
        )
        return "unknown"
    return "exists" if exists else "missing"


async def load_aidp_integration(
    settings: Any,
    *,
    run_on_connection: RunOnConnection | None,
    database_ready: Callable[[], bool],
) -> AidpIntegrationData:
    """AIDP の連携の情報を作る。未設定なら `configured=False` だけを返す（DB に触らない）。"""
    if not aidp_configured(settings):
        return AidpIntegrationData(configured=False)
    db_name = adb_name_from_dsn(_text(settings, "oracle_dsn"))
    # 引用符なしで作る DB ユーザーの名前は大文字で保存される（stack・`suite-db-user.py` と同じ）。
    user_name = _text(settings, "aidp_db_user").upper()
    return AidpIntegrationData(
        configured=True,
        url=_https_url(_text(settings, "aidp_url")),
        ocid=_text(settings, "aidp_ocid"),
        region=_text(settings, "aidp_region"),
        adb_db_name=db_name,
        # External Catalog は Wallet の別名で登録し、少数の重い分析の読み取りなので HIGH の
        # サービスにする（3 製品の接続先の TP とは分ける。#6018）。DB 名が読めなければ空。
        adb_service_name=f"{db_name}_high" if db_name else "",
        db_user=user_name,
        db_user_status=await _db_user_status(
            settings,
            user_name,
            run_on_connection=run_on_connection,
            database_ready=database_ready,
        ),
    )
