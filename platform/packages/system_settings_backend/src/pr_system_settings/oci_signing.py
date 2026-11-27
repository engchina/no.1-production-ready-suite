"""OCI のリクエスト署名（API キー）を httpx のリクエストに付ける auth（#5932）。

「システム設定 › OCI 認証」の API キー（OCI config の profile）で署名する。OCI SDK の client を
持たない HTTP の endpoint（Oracle AI Data Platform の Agent の A2A の endpoint など）を httpx で
呼ぶときに使う。署名そのものは OCI SDK の `oci.signer.Signer`（`requests` 用の auth）に任せ、
ここでは httpx のリクエストとの受け渡しだけを行う。

- 署名するのは `date`・`(request-target)`・`host` と、本文のあるメソッド（POST / PUT / PATCH）の
  `content-length`・`content-type`・`x-content-sha256`（OCI の署名の版 1）。
- 鍵・署名のヘッダーの値を log・例外の文に出さない（読み込みの失敗は種類だけを残す）。
- instance principal・resource principal・session token は対象外（API キーだけ）。

httpx は `production-ready-backend-core` の依存として入る（3 製品の外向きの HTTP と同じ版）。
"""

from __future__ import annotations

from collections.abc import Generator
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import httpx

from pr_system_settings.oci_auth import (
    OCI_PRIVATE_KEY_PASSPHRASE_REQUIRED_ERROR,
    OciPrivateKeyPassPhraseRequiredError,
    load_oci_config_without_prompt,
)

OCI_REQUEST_SIGNING_NOT_CONFIGURED_ERROR = (
    "OCI 認証（API キー）を読み込めません。"
    "「システム設定 › OCI 認証」で、ユーザー OCID・テナンシ OCID・フィンガープリント・秘密鍵を"
    "設定してください。"
)
# httpx が既に決めているヘッダーのうち、署名の対象になるもの（署名する値と送る値をそろえる）。
_SIGNED_REQUEST_HEADERS = ("host", "content-type", "content-length")


class OciRequestSigningNotConfiguredError(RuntimeError):
    """OCI の API キーの設定が無い・読めないため、署名できない。

    文は利用者に出してよい（鍵・パス・OCID を含めない）。`reason` は元の例外の種類（log 用）。
    """

    safe_for_user = True

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class OciRequestSigningAuth(httpx.Auth):
    """OCI SDK の signer で httpx のリクエストに署名する auth。

    `signer` は `oci.signer.Signer`（`requests` の `PreparedRequest` を受け取る callable）。必要な
    属性（`method`・`url`・`path_url`・`headers`・`body`）だけを持つ写しを渡し、付いたヘッダー
    （`authorization`・`date`・`x-content-sha256` など）を httpx のリクエストへ写す。
    """

    # 本文の hash（`x-content-sha256`）を作るため、送る前に本文を読み切る。
    requires_request_body = True

    def __init__(self, signer: Any) -> None:
        self._signer = signer

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        prepared = SimpleNamespace(
            method=request.method,
            url=str(request.url),
            # path と query（`requests` の `path_url` と同じ。署名の `(request-target)`）。
            path_url=request.url.raw_path.decode("ascii"),
            headers={
                name: request.headers[name]
                for name in _SIGNED_REQUEST_HEADERS
                if name in request.headers
            },
            body=request.content,
        )
        self._signer(prepared)
        for name, value in prepared.headers.items():
            request.headers[name] = value
        yield request


def oci_request_signing_auth(config_file: str, profile: str) -> OciRequestSigningAuth:
    """OCI config の profile の API キーで署名する httpx の auth を作る。

    設定が無い・読めない・秘密鍵が暗号化されていて pass phrase が無いときは
    `OciRequestSigningNotConfiguredError`（対話の入力は求めない）。鍵は呼ぶたびに読む（画面で
    差し替えた鍵をすぐに使う）。
    """
    try:
        oci_config = import_module("oci.config")
        signer_module = import_module("oci.signer")
        config = load_oci_config_without_prompt(oci_config, config_file, profile)
        signer = signer_module.Signer.from_config(config)
    except OciPrivateKeyPassPhraseRequiredError as exc:
        raise OciRequestSigningNotConfiguredError(
            OCI_PRIVATE_KEY_PASSPHRASE_REQUIRED_ERROR, reason=type(exc).__name__
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 設定・鍵の失敗の種類に依らず「未設定」にする
        # OCI SDK の例外の文は設定のパス・profile を含むので、文は固定にして種類だけを残す。
        raise OciRequestSigningNotConfiguredError(
            OCI_REQUEST_SIGNING_NOT_CONFIGURED_ERROR, reason=type(exc).__name__
        ) from exc
    return OciRequestSigningAuth(signer)


def oci_request_signing_configured(config_file: str, profile: str) -> bool:
    """OCI の API キーで署名できる状態か（設定の画面の「設定済み」の判定に使う）。"""
    try:
        oci_request_signing_auth(config_file, profile)
    except OciRequestSigningNotConfiguredError:
        return False
    return True


__all__ = [
    "OCI_REQUEST_SIGNING_NOT_CONFIGURED_ERROR",
    "OciRequestSigningAuth",
    "OciRequestSigningNotConfiguredError",
    "oci_request_signing_auth",
    "oci_request_signing_configured",
]
