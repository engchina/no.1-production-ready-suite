"""外部のクライアント（業務システム・MCP クライアント）向けの API キー（#778）。

- 形式は `prak_<id>_<秘密>`。秘密は作成時に 1 回だけ返し、保存するのは全体の SHA-256 だけ。
- キーは「実行する利用者」（既定は作った利用者。システム管理者は連携用の専用の利用者を選べる）
  として動き、キーに付けた業務 Agent に絞る（`agent_ids` が None なら、その利用者が使える業務
  Agent すべて）。権限は利用者の現在のロールから毎回計算し直す。
- 保存先は Control Plane の定義と同じ `AGENT_CONTROL_PLANE_ITEMS`（kind `api_key`。#764）。
- 最後に使った日時は、保存の負荷を抑えるため 10 分に 1 回だけ保存する。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

API_KEY_PREFIX = "prak_"
_TOKEN_PATTERN = re.compile(r"^prak_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$")
_LAST_USED_PERSIST_INTERVAL = timedelta(minutes=10)
ApiKeyExpiryDays = Literal[30, 90, 365]


def _now() -> datetime:
    return datetime.now(UTC)


def hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_api_key(token: str) -> bool:
    return token.startswith(API_KEY_PREFIX)


class ApiKeyRecord(BaseModel):
    """保存する API キー（秘密は持たない）。"""

    id: str
    name: str
    # 実行する利用者（キーはこの利用者として動く）。
    owner_user_uuid: str
    # 作った利用者（#778 の当初のキーは無い = 実行する利用者と同じ）。
    created_by_user_uuid: str | None = None
    # None は「実行する利用者が使える業務 Agent すべて」。
    agent_ids: list[str] | None = None
    # 一覧で見分けるための先頭（`prak_<id>_` + 秘密の先頭 4 文字）。
    token_prefix: str
    token_hash: str
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    def expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or _now())


class ApiKeyView(BaseModel):
    """画面に返す API キー（hash を含めない）。"""

    id: str
    name: str
    owner_user_uuid: str
    owner_display_name: str = ""
    created_by_user_uuid: str
    created_by_display_name: str = ""
    agent_ids: list[str] | None
    token_prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    expired: bool


class ApiKeysListData(BaseModel):
    keys: list[ApiKeyView] = Field(default_factory=list)
    # False はキーの保存先（Oracle）が無い（再起動で消える）。
    persistent: bool = True


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # 省く・null は「作った利用者が使える業務 Agent すべて」。空の配列は受け付けない。
    agent_ids: list[str] | None = Field(default=None, max_length=200)
    # 省く・null は無期限。
    expires_in_days: ApiKeyExpiryDays | None = 90
    # 実行する利用者。省く・null は作った利用者（ほかの利用者はシステム管理者だけが選べる）。
    run_as_user_uuid: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名前を入力してください。")
        return value.strip()

    @field_validator("agent_ids")
    @classmethod
    def _agent_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = sorted({item.strip() for item in value if item.strip()})
        if not cleaned:
            raise ValueError("業務 Agent を 1 つ以上選ぶか、すべてにしてください。")
        return cleaned


class ApiKeyCreated(BaseModel):
    """作成の応答。`token` はこの 1 回だけ返す。"""

    key: ApiKeyView
    token: str


class ApiKeyRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._keys: dict[str, ApiKeyRecord] = {}
        self._persisted_last_used: dict[str, datetime] = {}

    def create(
        self, request: ApiKeyCreateRequest, *, owner_user_uuid: str, created_by_user_uuid: str
    ) -> tuple[ApiKeyRecord, str]:
        key_id = uuid4().hex[:16]
        secret = secrets.token_urlsafe(32)
        token = f"{API_KEY_PREFIX}{key_id}_{secret}"
        now = _now()
        record = ApiKeyRecord(
            id=key_id,
            name=request.name,
            owner_user_uuid=owner_user_uuid,
            created_by_user_uuid=created_by_user_uuid,
            agent_ids=request.agent_ids,
            token_prefix=f"{API_KEY_PREFIX}{key_id}_{secret[:4]}",
            token_hash=hash_api_key(token),
            created_at=now,
            expires_at=(
                now + timedelta(days=request.expires_in_days) if request.expires_in_days else None
            ),
        )
        with self._lock:
            self._keys[key_id] = record
        return record.model_copy(), token

    def restore(self, record: ApiKeyRecord) -> None:
        with self._lock:
            self._keys[record.id] = record
            if record.last_used_at is not None:
                self._persisted_last_used[record.id] = record.last_used_at

    def list(self) -> list[ApiKeyRecord]:
        with self._lock:
            return sorted(
                (record.model_copy() for record in self._keys.values()),
                key=lambda record: record.created_at,
                reverse=True,
            )

    def get(self, key_id: str) -> ApiKeyRecord:
        with self._lock:
            record = self._keys.get(key_id)
            if record is None:
                raise KeyError(key_id)
            return record.model_copy()

    def delete(self, key_id: str) -> None:
        with self._lock:
            if self._keys.pop(key_id, None) is None:
                raise KeyError(key_id)
            self._persisted_last_used.pop(key_id, None)

    def authenticate(self, token: str) -> tuple[ApiKeyRecord, bool] | None:
        """キーを確かめる。戻り値は (キー, 最後に使った日時を保存するか)。無効なら None。"""
        match = _TOKEN_PATTERN.match(token)
        if match is None:
            return None
        now = _now()
        with self._lock:
            record = self._keys.get(match.group(1))
            if record is None or record.expired(now):
                return None
            if not hmac.compare_digest(record.token_hash, hash_api_key(token)):
                return None
            record.last_used_at = now
            persisted = self._persisted_last_used.get(record.id)
            persist = persisted is None or now - persisted >= _LAST_USED_PERSIST_INTERVAL
            if persist:
                self._persisted_last_used[record.id] = now
            return record.model_copy(), persist

    def clear(self) -> None:
        with self._lock:
            self._keys.clear()
            self._persisted_last_used.clear()


api_key_registry = ApiKeyRegistry()


def narrow_agent_ids(
    allowed: frozenset[str] | None, key_agent_ids: list[str] | None
) -> frozenset[str] | None:
    """利用者が使える業務 Agent を、キーに付けた業務 Agent に絞る（None は制限なし）。"""
    if key_agent_ids is None:
        return allowed
    keyed = frozenset(key_agent_ids)
    return keyed if allowed is None else allowed & keyed


def key_view(record: ApiKeyRecord, names: dict[str, str] | None = None) -> ApiKeyView:
    names = names or {}
    created_by = record.created_by_user_uuid or record.owner_user_uuid
    return ApiKeyView(
        id=record.id,
        name=record.name,
        owner_user_uuid=record.owner_user_uuid,
        owner_display_name=names.get(record.owner_user_uuid, ""),
        created_by_user_uuid=created_by,
        created_by_display_name=names.get(created_by, ""),
        agent_ids=record.agent_ids,
        token_prefix=record.token_prefix,
        created_at=record.created_at,
        expires_at=record.expires_at,
        last_used_at=record.last_used_at,
        expired=record.expired(),
    )
