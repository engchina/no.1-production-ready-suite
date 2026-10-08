"""図の根拠の元の画像をブラウザで開く、短命・対象に縛った署名つきの URL（#1311）。

Agent の画面（ブラウザ）は RAG の Cookie を持たないため、Agent の backend が MCP の
``rag_read_source``（``include_image_url``）で URL を受け取り、画面はその URL で図を開く。

- トークンは HMAC-SHA256 の署名（stateless）。プロセスのメモリに置かないので、複数の worker・
  再起動でも検証できる。中身は ``sub``（利用者）・``document_id``・``chunk_id``・
  ``chunk_set_id``（版）・``iat``・``exp``（発行から 5 分以内）。
- 署名の鍵は共通 ``.env`` の ``PLATFORM_SERVICE_TOKEN_SECRET`` から HKDF-SHA256 で用途を区別して
  導く（用途の印 ``rag-figure-url-v1``）。サービストークンの鍵そのものでは署名しないので、図の
  トークンをサービストークンとして使うこと（またはその逆）はできない。鍵が無い・短いときは URL を
  作らず、読み取りも拒否する（fail closed）。
- トークンは URL の path に置く（query に載せない）。読み取りのたびに、署名に加えて今の権限・版を
  確かめ直す（``app.api.routes.figures``）。トークンは「読んでよい対象」を縛るだけで、権限は持たない。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

# URL の有効期限（秒）。画面が開く直前に作るので短くてよい。上限も同じ 5 分。
FIGURE_URL_TTL_SECONDS = 300
FIGURE_URL_MAX_TTL_SECONDS = 300
# 発行時刻が未来に寄っているときに許す秒数（同じ RAG の worker 間の時計のずれ）。
FIGURE_URL_LEEWAY_SECONDS = 30
FIGURE_URL_MIN_SECRET_LENGTH = 32
_TOKEN_VERSION = "f1"  # nosec B105 - トークンの形式の版で秘密ではない
_HKDF_SALT = b"production-ready-rag"
_HKDF_INFO = b"rag-figure-url-v1"


class FigureUrlUnavailableError(RuntimeError):
    """署名の鍵が設定されていない（URL を作らない・読ませない）。"""


class FigureTokenError(ValueError):
    """トークンが無効（改ざん・形式の誤り）か、期限切れ。"""

    def __init__(self, *, expired: bool = False) -> None:
        super().__init__("figure token expired" if expired else "figure token invalid")
        self.expired = expired


@dataclass(frozen=True, slots=True)
class FigureClaims:
    """署名を確かめたトークンの中身。"""

    subject: str
    document_id: str
    chunk_id: str
    chunk_set_id: str | None
    issued_at: int
    expires_at: int


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _hkdf_sha256(secret: bytes, *, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 の HKDF-SHA256（標準ライブラリだけで書く。長さは 1 ブロック分まで）。"""
    if not 0 < length <= hashlib.sha256().digest_size:
        raise ValueError("length は 1〜32 にしてください。")
    pseudo_random_key = hmac.new(salt, secret, hashlib.sha256).digest()
    return hmac.new(pseudo_random_key, info + b"\x01", hashlib.sha256).digest()[:length]


def figure_signing_key(secret: str) -> bytes:
    """図の URL の署名の鍵（サービストークンの鍵から用途を区別して導く）。"""
    secret = secret.strip()
    if len(secret) < FIGURE_URL_MIN_SECRET_LENGTH:
        raise FigureUrlUnavailableError(
            "図を開く URL を作れません。共通 .env の PLATFORM_SERVICE_TOKEN_SECRET に"
            f" {FIGURE_URL_MIN_SECRET_LENGTH} 文字以上のランダムな値を設定してください。"
        )
    return _hkdf_sha256(secret.encode("utf-8"), salt=_HKDF_SALT, info=_HKDF_INFO)


def _signature(key: bytes, signing_input: str) -> str:
    return _b64url_encode(hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest())


def issue_figure_token(
    secret: str,
    *,
    subject: str,
    document_id: str,
    chunk_id: str,
    chunk_set_id: str | None,
    ttl_seconds: int = FIGURE_URL_TTL_SECONDS,
    now: float | None = None,
) -> tuple[str, int]:
    """利用者・根拠・版に縛ったトークンと、期限（epoch 秒）を返す。"""
    key = figure_signing_key(secret)
    if not subject or not document_id or not chunk_id:
        raise ValueError("subject・document_id・chunk_id は必須です。")
    if not 0 < ttl_seconds <= FIGURE_URL_MAX_TTL_SECONDS:
        raise ValueError(f"ttl_seconds は 1〜{FIGURE_URL_MAX_TTL_SECONDS} 秒にしてください。")
    issued_at = int(now if now is not None else time.time())
    expires_at = issued_at + ttl_seconds
    payload = {
        "sub": subject,
        "doc": document_id,
        "chunk": chunk_id,
        "cs": chunk_set_id,
        "iat": issued_at,
        "exp": expires_at,
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{_TOKEN_VERSION}.{body}"
    return f"{signing_input}.{_signature(key, signing_input)}", expires_at


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise FigureTokenError()
    return value


def verify_figure_token(secret: str, token: str, *, now: float | None = None) -> FigureClaims:
    """署名・形式・期限を確かめて中身を返す。鍵が無ければ ``FigureUrlUnavailableError``。"""
    key = figure_signing_key(secret)
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        raise FigureTokenError()
    version, body, signature = parts
    expected = _signature(key, f"{version}.{body}")
    if not hmac.compare_digest(signature.encode("ascii", "ignore"), expected.encode("ascii")):
        raise FigureTokenError()
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise FigureTokenError() from None
    if not isinstance(payload, dict):
        raise FigureTokenError()
    issued_at, expires_at = payload.get("iat"), payload.get("exp")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise FigureTokenError()
    if isinstance(issued_at, bool) or isinstance(expires_at, bool):
        raise FigureTokenError()
    current = now if now is not None else time.time()
    if expires_at - issued_at > FIGURE_URL_MAX_TTL_SECONDS or expires_at <= issued_at:
        raise FigureTokenError()
    if issued_at > current + FIGURE_URL_LEEWAY_SECONDS:
        raise FigureTokenError()
    if expires_at <= current:
        raise FigureTokenError(expired=True)
    chunk_set_id = payload.get("cs")
    if chunk_set_id is not None and not isinstance(chunk_set_id, str):
        raise FigureTokenError()
    return FigureClaims(
        subject=_required_text(payload, "sub"),
        document_id=_required_text(payload, "doc"),
        chunk_id=_required_text(payload, "chunk"),
        chunk_set_id=chunk_set_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )
