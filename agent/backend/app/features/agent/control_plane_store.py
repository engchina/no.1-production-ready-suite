"""画面・API で変えた Control Plane の定義の保存と復元（#764）。

対象は、画面・API で追加・変更した Skill（runtime）・プラグイン・マーケットプレイス・MCP 接続・
ツール権限。`.env` の宣言（`AGENT_SKILLS_DIR` / `AGENT_PLUGINS_JSON` など）と組み込みの定義は
起動のたびに読み込むので保存しない。

- 保存先は Run の保存先（`AGENT_RUNTIME_REPOSITORY_BACKEND`）に合わせる。`oracle_*` は
  `AGENT_CONTROL_PLANE_ITEMS`（共通の `PLATFORM_ORACLE_*`。テーブルはシステムテーブルが作る）、
  `file` は snapshot の隣の JSON ファイル、`memory` は保存しない（従来どおりプロセス内だけ）。
- MCP 接続の API キー・OAuth の client secret は `app.secret_box` で暗号化して保存する。
- 起動時（`app.main` の startup）に `restore_control_plane()` で読み込み、宣言の後に重ねる。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol, cast

from app.oracle_connection import connect_platform_oracle
from app.secret_box import SecretBoxError, open_secret, seal_secret
from app.settings import get_settings

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]
ItemKind = Literal[
    "skill",
    "plugin",
    "marketplace",
    "mcp_connection",
    "tool_policy",
    "evaluation_set",
    "evaluation_job",
    "api_key",
    "automation",
]
ITEM_KINDS: tuple[ItemKind, ...] = (
    "mcp_connection",
    "tool_policy",
    "skill",
    "marketplace",
    "plugin",
    # 業務 Agent の自動実行（#784。Webhook の秘密は hash だけ）。
    "automation",
    # 品質評価の評価セットと評価の job（#776）。
    "evaluation_set",
    "evaluation_job",
    # 外部のクライアント向けの API キー（#778。秘密は保存せず hash だけ）。
    "api_key",
)
_EVALUATION_KINDS = {"evaluation_set", "evaluation_job"}
ITEMS_TABLE = "AGENT_CONTROL_PLANE_ITEMS"
_SECRET_FIELDS = ("api_key", "oauth_client_secret")


class ControlPlaneStoreError(RuntimeError):
    """定義を保存できない（画面に出す日本語の文言）。"""


class ControlPlaneItemStore(Protocol):
    @property
    def persistent(self) -> bool: ...
    def load(self) -> dict[str, dict[str, JsonObject]]: ...
    def put(self, kind: ItemKind, item_id: str, document: JsonObject) -> None: ...
    def delete(self, kind: ItemKind, item_id: str) -> None: ...


class MemoryItemStore:
    """保存しない（`memory` の構成。再起動で `.env` の宣言だけに戻る）。"""

    persistent = False

    def load(self) -> dict[str, dict[str, JsonObject]]:
        return {}

    def put(self, kind: ItemKind, item_id: str, document: JsonObject) -> None:
        return None

    def delete(self, kind: ItemKind, item_id: str) -> None:
        return None


class FileItemStore:
    """JSON ファイル（`file` の構成。開発・検証用）。"""

    persistent = True

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()

    def load(self) -> dict[str, dict[str, JsonObject]]:
        with self._lock:
            return self._read_locked()

    def put(self, kind: ItemKind, item_id: str, document: JsonObject) -> None:
        with self._lock:
            items = self._read_locked()
            items.setdefault(kind, {})[item_id] = document
            self._write_locked(items)

    def delete(self, kind: ItemKind, item_id: str) -> None:
        with self._lock:
            items = self._read_locked()
            if items.get(kind, {}).pop(item_id, None) is not None:
                self._write_locked(items)

    def _read_locked(self) -> dict[str, dict[str, JsonObject]]:
        if not self._path.exists():
            return {}
        data = json.loads(self._path.read_text(encoding="utf-8") or "{}")
        return {
            str(kind): {str(item_id): doc for item_id, doc in docs.items()}
            for kind, docs in data.items()
            if isinstance(docs, dict)
        }

    def _write_locked(self, items: dict[str, dict[str, JsonObject]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_name(f".{self._path.name}.tmp")
        temp_path.write_text(
            json.dumps(items, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8"
        )
        os.replace(temp_path, self._path)


class OracleItemStore:
    """`AGENT_CONTROL_PLANE_ITEMS`（共通の `PLATFORM_ORACLE_*`）。"""

    persistent = True

    def __init__(self, connect_factory: Callable[[], Any] | None = None) -> None:
        self._connect = connect_factory or connect_platform_oracle

    def load(self) -> dict[str, dict[str, JsonObject]]:
        items: dict[str, dict[str, JsonObject]] = {}
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT ITEM_KIND, ITEM_ID, ITEM_JSON FROM {ITEMS_TABLE}")
                # CLOB は接続を閉じる前に読む（閉じた後に読むと DPY-1001）。
                rows = [
                    (kind, item_id, _lob_text(item_json))
                    for kind, item_id, item_json in cursor.fetchall()
                ]
        except Exception as exc:
            if _is_table_missing(exc):
                # システムテーブルを作る前（画面はシステムテーブルの作成へ案内する）。
                logger.warning("agent_control_plane_items_missing")
                return {}
            raise
        for kind, item_id, item_json in rows:
            try:
                document = json.loads(item_json)
            except ValueError:
                logger.warning(
                    "agent_control_plane_item_invalid", extra={"kind": kind, "item_id": item_id}
                )
                continue
            if isinstance(document, dict):
                items.setdefault(str(kind), {})[str(item_id)] = document
        return items

    def put(self, kind: ItemKind, item_id: str, document: JsonObject) -> None:
        statement = f"""
        MERGE INTO {ITEMS_TABLE} target
        USING (
            SELECT :item_kind AS item_kind, :item_id AS item_id, :item_json AS item_json
            FROM dual
        ) source
        ON (target.item_kind = source.item_kind AND target.item_id = source.item_id)
        WHEN MATCHED THEN UPDATE SET
            target.item_json = source.item_json,
            target.updated_at = SYSTIMESTAMP
        WHEN NOT MATCHED THEN INSERT (item_kind, item_id, item_json, updated_at)
        VALUES (source.item_kind, source.item_id, source.item_json, SYSTIMESTAMP)
        """  # nosec B608 - テーブル名は固定
        self._execute(
            statement,
            item_kind=kind,
            item_id=item_id,
            item_json=json.dumps(document, ensure_ascii=False, sort_keys=True, default=str),
        )

    def delete(self, kind: ItemKind, item_id: str) -> None:
        self._execute(
            f"DELETE FROM {ITEMS_TABLE} WHERE item_kind = :item_kind AND item_id = :item_id",  # nosec B608
            item_kind=kind,
            item_id=item_id,
        )

    def _execute(self, statement: str, **params: Any) -> None:
        try:
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(statement, **params)
                connection.commit()
        except Exception as exc:
            if _is_table_missing(exc):
                raise ControlPlaneStoreError(
                    "定義を保存するテーブルがありません。"
                    "運用設定 > システムテーブルで作成・更新してください。"
                ) from exc
            raise


def _is_table_missing(exc: BaseException) -> bool:
    text = str(exc)
    return "ORA-00942" in text or "table or view does not exist" in text


def _lob_text(value: object) -> str:
    read = getattr(value, "read", None)
    if callable(read):
        return str(read())
    return str(value)


def build_control_plane_store() -> ControlPlaneItemStore:
    settings = get_settings()
    backend = settings.agent_runtime_repository_backend.strip().lower()
    if backend in {"oracle", "oracle_checkpoint", "oracle_normalized"}:
        return OracleItemStore()
    if backend in {"file", "file_snapshot"} and settings.agent_runtime_snapshot_path:
        snapshot = Path(settings.agent_runtime_snapshot_path)
        return FileItemStore(snapshot.with_name(f"{snapshot.stem}.control-plane.json"))
    return MemoryItemStore()


_store: ControlPlaneItemStore | None = None
_restoring = False


def get_control_plane_store() -> ControlPlaneItemStore:
    global _store
    if _store is None:
        _store = build_control_plane_store()
    return _store


def set_control_plane_store(store: ControlPlaneItemStore | None) -> None:
    """テスト用に保存先を差し替える（None で設定から作り直す）。"""
    global _store
    _store = store


@contextmanager
def _restoring_items() -> Iterator[None]:
    global _restoring
    _restoring = True
    try:
        yield
    finally:
        _restoring = False


def _put(kind: ItemKind, item_id: str, document: JsonObject) -> None:
    if _restoring:
        return
    get_control_plane_store().put(kind, item_id, document)


def _delete(kind: ItemKind, item_id: str) -> None:
    if _restoring:
        return
    get_control_plane_store().delete(kind, item_id)


# ---- 保存（router の変更の後に呼ぶ） ---------------------------------------------


def save_skill(skill: Any) -> None:
    _put("skill", skill.id, skill.model_dump(mode="json"))


def delete_skill(skill_id: str) -> None:
    _delete("skill", skill_id)


def save_plugin(record: Any) -> None:
    _put(
        "plugin",
        record.id,
        {
            "manifest": record.manifest.model_dump(mode="json"),
            "enabled": record.enabled,
            "marketplace_id": record.marketplace_id,
        },
    )


def delete_plugin(plugin_id: str) -> None:
    _delete("plugin", plugin_id)


def save_marketplace(source: Any, listing: Any | None = None) -> None:
    document: JsonObject = {"source": source.model_dump(mode="json")}
    # URL の無いマーケットプレイスは取り直せないため、一覧も保存する。
    if listing is not None and not source.url:
        document["listing"] = listing.model_dump(mode="json")
    _put("marketplace", source.id, document)


def delete_marketplace(marketplace_id: str) -> None:
    _delete("marketplace", marketplace_id)


def save_mcp_connection(config: Any) -> None:
    document = config.model_dump(mode="json")
    for field in _SECRET_FIELDS:
        value = document.get(field)
        if value and get_control_plane_store().persistent:
            try:
                document[field] = seal_secret(str(value))
            except SecretBoxError as exc:
                raise ControlPlaneStoreError(str(exc)) from exc
    _put("mcp_connection", config.server_id, document)


def delete_mcp_connection(server_id: str) -> None:
    _delete("mcp_connection", server_id)


def save_tool_policy(policy: Any) -> None:
    _put(
        "tool_policy",
        "default",
        {
            "default_mode": policy.default_mode,
            "allow": sorted(policy.allow),
            "ask": sorted(policy.ask),
            "deny": sorted(policy.deny),
        },
    )


def save_automation(automation_id: str, document: JsonObject) -> None:
    _put("automation", automation_id, document)


def delete_automation(automation_id: str) -> None:
    _delete("automation", automation_id)


def save_evaluation_item(kind: str, item_id: str, document: JsonObject) -> None:
    """品質評価の評価セット・job を保存する（#776）。"""
    if kind not in _EVALUATION_KINDS:
        raise ValueError(kind)
    _put(cast(ItemKind, kind), item_id, document)


def delete_evaluation_item(kind: str, item_id: str) -> None:
    if kind not in _EVALUATION_KINDS:
        raise ValueError(kind)
    _delete(cast(ItemKind, kind), item_id)


def save_api_key(record: Any) -> None:
    _put("api_key", record.id, record.model_dump(mode="json"))


def delete_api_key(key_id: str) -> None:
    _delete("api_key", key_id)


# ---- 復元（起動時） ---------------------------------------------------------------


def restore_control_plane() -> dict[str, int]:
    """保存した定義を読み込み、宣言の後に重ねる（読めない項目は飛ばして記録する）。"""
    items = get_control_plane_store().load()
    restored: dict[str, int] = dict.fromkeys(ITEM_KINDS, 0)
    with _restoring_items():
        for kind in ITEM_KINDS:
            for item_id, document in sorted(items.get(kind, {}).items()):
                try:
                    _restore_item(kind, document)
                except Exception as exc:  # noqa: BLE001 - 1 件の不正で起動を止めない
                    logger.warning(
                        "agent_control_plane_restore_failed",
                        extra={"kind": kind, "item_id": item_id, "reason": str(exc)},
                    )
                    continue
                restored[kind] += 1
    return restored


def _restore_item(kind: ItemKind, document: JsonObject) -> None:
    # 登録先は遅延 import（このモジュールは config / skills / plugins から import されない）。
    from app.features.agent.config import McpConnectionConfig, runtime_config_store
    from app.features.agent.plugins import (
        MarketplaceListing,
        MarketplaceSource,
        PluginManifest,
        marketplace_registry,
        plugin_registry,
    )
    from app.features.agent.skills import AgentSkillDefinition, skill_registry

    if kind == "mcp_connection":
        stored = McpConnectionConfig.model_validate(_opened_secrets(document))
        runtime_config_store.restore_mcp_server(stored)
    elif kind == "tool_policy":
        runtime_config_store.patch_tool_policy(
            default_mode=str(document.get("default_mode") or "approval"),
            allow=list(document.get("allow") or []),
            ask=list(document.get("ask") or []),
            deny=list(document.get("deny") or []),
        )
    elif kind == "skill":
        skill_registry.upsert_custom(AgentSkillDefinition.model_validate(document))
    elif kind == "marketplace":
        listing_raw = document.get("listing")
        marketplace_registry.add(
            MarketplaceSource.model_validate(document.get("source") or {}),
            MarketplaceListing.model_validate(listing_raw) if listing_raw else None,
        )
    elif kind == "automation":
        from app.features.agent.automations import Automation, automation_store

        automation_store.restore(Automation.model_validate(document))
    elif kind == "evaluation_set":
        from app.features.agent.evaluation import EvaluationSet, evaluation_set_store

        evaluation_set_store.restore(EvaluationSet.model_validate(document))
    elif kind == "evaluation_job":
        from app.features.agent.evaluation import EvaluationJob, evaluation_store

        evaluation_store.restore(EvaluationJob.model_validate(document))
    elif kind == "api_key":
        from app.features.agent.api_keys import ApiKeyRecord, api_key_registry

        api_key_registry.restore(ApiKeyRecord.model_validate(document))
    elif kind == "plugin":
        manifest = PluginManifest.model_validate(document.get("manifest") or {})
        record = plugin_registry.install(
            manifest, marketplace_id=document.get("marketplace_id") or None
        )
        if document.get("enabled") is False:
            plugin_registry.set_enabled(record.id, False)


def _opened_secrets(document: JsonObject) -> JsonObject:
    opened = dict(document)
    for field in _SECRET_FIELDS:
        value = opened.get(field)
        if isinstance(value, str) and value:
            opened[field] = open_secret(value)
    return opened
