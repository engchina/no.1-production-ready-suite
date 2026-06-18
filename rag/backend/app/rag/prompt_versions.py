"""回答生成 system prompt の版管理(PoweRAG の Prompt versioning 由来)。

PoweRAG は Langfuse 連携で「prompt template を保存・版管理・取得・rollback」する。本モジュールは
外部 SaaS を導入せず、確定スタック内で **JSON 永続の prompt version store** として再実装する:
- 版の作成・一覧・有効化(rollback = 旧版を再有効化)・有効版の取得。
- Generation アダプターの `custom` profile が有効版の system_prompt を解決する。

永続先は `model-settings.json` と同じ backend ディレクトリ基準の `prompt-versions.json`
(env `RAG_PROMPT_VERSIONS_FILE` で上書き可)。config.py / .env には依存しない。
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import BACKEND_ROOT

PROMPT_VERSIONS_FILE_ENV = "RAG_PROMPT_VERSIONS_FILE"
DEFAULT_PROMPT_VERSIONS_FILE = "prompt-versions.json"
MAX_PROMPT_VERSIONS = 100


class PromptVersion(BaseModel):
    """1 つの回答生成 system prompt の版。"""

    version_id: str
    name: str = Field(max_length=120)
    system_prompt: str = Field(max_length=20000)
    note: str = Field(default="", max_length=2000)
    created_at: datetime
    created_by: str = Field(default="", max_length=120)

    @field_validator("name", "system_prompt")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name と system_prompt は空にできません。")
        return cleaned


class PromptVersionStore(BaseModel):
    """版管理ファイルの schema。"""

    version: Literal[1] = 1
    active_version_id: str | None = None
    versions: list[PromptVersion] = Field(default_factory=list)


def _prompt_versions_path() -> Path:
    """`prompt-versions.json` を backend ディレクトリ基準で解決する。"""
    raw = os.environ.get(PROMPT_VERSIONS_FILE_ENV, "").strip() or DEFAULT_PROMPT_VERSIONS_FILE
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def load_prompt_version_store() -> PromptVersionStore:
    """版管理ファイルを読む。無ければ空 store、壊れていても安全に空 store を返す。"""
    path = _prompt_versions_path()
    try:
        data = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return PromptVersionStore()
    try:
        return PromptVersionStore.model_validate_json(data)
    except ValueError:
        return PromptVersionStore()


def _save_prompt_version_store(store: PromptVersionStore) -> None:
    path = _prompt_versions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(store.model_dump_json(indent=2), encoding="utf-8")


def list_prompt_versions() -> PromptVersionStore:
    """全版と有効版を返す(新しい順)。"""
    store = load_prompt_version_store()
    store.versions = sorted(store.versions, key=lambda v: v.created_at, reverse=True)
    return store


def create_prompt_version(
    *,
    name: str,
    system_prompt: str,
    note: str = "",
    created_by: str = "",
    activate: bool = True,
) -> PromptVersion:
    """新しい版を作成して保存する。`activate` または初版なら有効版にする。"""
    store = load_prompt_version_store()
    version = PromptVersion(
        version_id=uuid.uuid4().hex,
        name=name,
        system_prompt=system_prompt,
        note=note,
        created_at=datetime.now(UTC),
        created_by=created_by,
    )
    store.versions.append(version)
    if len(store.versions) > MAX_PROMPT_VERSIONS:
        store.versions = store.versions[-MAX_PROMPT_VERSIONS:]
    if activate or store.active_version_id is None:
        store.active_version_id = version.version_id
    _save_prompt_version_store(store)
    return version


def activate_prompt_version(version_id: str) -> PromptVersionStore:
    """指定版を有効化する(rollback = 旧版を再有効化)。未知 id は KeyError。"""
    store = load_prompt_version_store()
    if not any(version.version_id == version_id for version in store.versions):
        raise KeyError(version_id)
    store.active_version_id = version_id
    _save_prompt_version_store(store)
    return store


def get_active_prompt_version() -> PromptVersion | None:
    """現在有効な版を返す。無ければ None。"""
    store = load_prompt_version_store()
    if store.active_version_id is None:
        return None
    for version in store.versions:
        if version.version_id == store.active_version_id:
            return version
    return None


def active_custom_system_prompt() -> str | None:
    """Generation アダプターの custom profile が使う有効版 system_prompt。"""
    version = get_active_prompt_version()
    return version.system_prompt if version is not None else None
