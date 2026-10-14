"""Plugin（配布 package）の registry。

Plugin は実行概念ではなく Skill / MCP / 非実行 resource の原子的な配布単位。
入れる手段は API・画面の manifest の登録と `AGENT_PLUGINS_JSON` の宣言。
install 後の Agent は Plugin ではなく Skill だけを参照する。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from threading import Lock
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.features.agent.config import McpConnectionConfig, runtime_config_store
from app.features.agent.runtime import AgentProfile, runtime_repository
from app.features.agent.skills import AgentSkillDefinition, skill_registry

logger = logging.getLogger(__name__)
JsonObject = dict[str, Any]


# --- モデル ---------------------------------------------------------------


class PluginManifest(BaseModel):
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    skills: list[AgentSkillDefinition] = Field(default_factory=list)
    mcp_servers: list[McpConnectionConfig] = Field(default_factory=list)
    resources: list[PluginResource] = Field(default_factory=list)
    # Deprecated input compatibility。install 時に template resource へ変換し、Agent は作成しない。
    agents: list[AgentProfile] = Field(default_factory=list)


class PluginResource(BaseModel):
    id: str
    kind: Literal["prompt", "workflow", "template"]
    name: str
    version: str = "0.0.0"
    media_type: str = "application/json"
    content: str | JsonObject
    metadata: JsonObject = Field(default_factory=dict)
    source: str = "runtime"


class PluginRecord(BaseModel):
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    enabled: bool = True
    skill_count: int = 0
    mcp_count: int = 0
    resource_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    # Deprecated response compatibility。常に 0。
    agent_count: int = 0
    manifest: PluginManifest


class PluginSummary(BaseModel):
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    enabled: bool = True
    skill_count: int = 0
    mcp_count: int = 0
    resource_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    agent_count: int = 0


class PluginListOutput(BaseModel):
    plugins: list[PluginSummary] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


# --- Plugin registry ------------------------------------------------------


def _plugin_source(plugin_id: str) -> str:
    return f"plugin:{plugin_id}"


class PluginResourceRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._resources: dict[str, PluginResource] = {}

    def set_declared(self, source: str, resources: list[PluginResource]) -> None:
        with self._lock:
            for resource_id in [
                rid for rid, resource in self._resources.items() if resource.source == source
            ]:
                del self._resources[resource_id]
            for resource in resources:
                existing = self._resources.get(resource.id)
                if existing is not None and existing.source != source:
                    raise ValueError(f"同じ ID の参照文書が既にあります: {resource.id}")
                self._resources[resource.id] = resource.model_copy(
                    deep=True, update={"source": source}
                )

    def get(self, resource_id: str) -> PluginResource | None:
        with self._lock:
            resource = self._resources.get(resource_id)
            return resource.model_copy(deep=True) if resource else None

    def list(self) -> list[PluginResource]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._resources.values()]


plugin_resource_registry = PluginResourceRegistry()


def _normalize_manifest(manifest: PluginManifest) -> tuple[PluginManifest, list[str]]:
    if not manifest.agents:
        return manifest.model_copy(deep=True), []
    resources = [resource.model_copy(deep=True) for resource in manifest.resources]
    existing_ids = {resource.id for resource in resources}
    for agent in manifest.agents:
        resource_id = f"{manifest.id}.agent-template.{agent.id}"
        if resource_id in existing_ids:
            raise ValueError(f"同じ ID の参照文書が既にあります: {resource_id}")
        existing_ids.add(resource_id)
        resources.append(
            PluginResource(
                id=resource_id,
                kind="template",
                name=f"{agent.name} template",
                version=manifest.version,
                content=agent.model_dump(mode="json"),
                metadata={"deprecated_source": "agents[]"},
            )
        )
    return (
        manifest.model_copy(deep=True, update={"resources": resources, "agents": []}),
        ["plugin.agents_deprecated_converted_to_templates"],
    )


class PluginRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._plugins: dict[str, PluginRecord] = {}

    def _apply(self, manifest: PluginManifest) -> None:
        source = _plugin_source(manifest.id)
        skill_registry.set_declared(source, [s.model_copy(deep=True) for s in manifest.skills])
        runtime_config_store.set_plugin_mcp_servers(source, manifest.mcp_servers)
        plugin_resource_registry.set_declared(
            source, [resource.model_copy(deep=True) for resource in manifest.resources]
        )

    def _revoke(self, plugin_id: str) -> None:
        source = _plugin_source(plugin_id)
        skill_registry.set_declared(source, [])
        runtime_config_store.remove_mcp_servers_by_source(source)
        plugin_resource_registry.set_declared(source, [])

    def install(
        self,
        manifest: PluginManifest,
        *,
        persist: Callable[[PluginRecord], None] | None = None,
    ) -> PluginRecord:
        with self._lock:
            normalized, warnings = _normalize_manifest(manifest)
            self._validate_install(normalized)
            record = PluginRecord(
                id=normalized.id,
                name=normalized.name,
                version=normalized.version,
                description=normalized.description,
                author=normalized.author,
                enabled=True,
                skill_count=len(normalized.skills),
                mcp_count=len(normalized.mcp_servers),
                resource_count=len(normalized.resources),
                agent_count=0,
                warnings=warnings,
                manifest=normalized,
            )
            try:
                self._apply(normalized)
                if persist is not None:
                    persist(record)
            except Exception:
                # 登録途中・保存失敗のどちらでも、成功していない配布物を残さない。
                self._revoke(normalized.id)
                raise
            self._plugins[normalized.id] = record
            return record.model_copy(deep=True)

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord:
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is None:
                raise KeyError(plugin_id)
            if not enabled:
                self._ensure_not_referenced(record)
            if enabled and not record.enabled:
                self._validate_reenable(record.manifest)
                try:
                    self._apply(record.manifest)
                except Exception:
                    self._revoke(plugin_id)
                    raise
            elif not enabled and record.enabled:
                self._revoke(plugin_id)
            record.enabled = enabled
            return record.model_copy(deep=True)

    def uninstall(self, plugin_id: str) -> None:
        with self._lock:
            if plugin_id not in self._plugins:
                raise KeyError(plugin_id)
            self._ensure_not_referenced(self._plugins[plugin_id])
            self._revoke(plugin_id)
            del self._plugins[plugin_id]

    def list(self) -> list[PluginRecord]:
        with self._lock:
            return [
                record.model_copy(deep=True)
                for record in sorted(self._plugins.values(), key=lambda item: item.id)
            ]

    def get(self, plugin_id: str) -> PluginRecord | None:
        with self._lock:
            record = self._plugins.get(plugin_id)
            return record.model_copy(deep=True) if record is not None else None

    def _validate_install(self, manifest: PluginManifest) -> None:
        if manifest.id in self._plugins:
            raise ValueError("このプラグインは導入済みです。")
        skill_ids = [item.id for item in manifest.skills]
        mcp_ids = [item.server_id for item in manifest.mcp_servers]
        resource_ids = [item.id for item in manifest.resources]
        for label, values in (
            ("skill", skill_ids),
            ("MCP server", mcp_ids),
            ("resource", resource_ids),
        ):
            duplicate = next((value for value in values if values.count(value) > 1), None)
            if duplicate is not None:
                raise ValueError(f"duplicate {label} id: {duplicate}")
        manifest_resource_ids = set(resource_ids)
        for resource_id in resource_ids:
            if plugin_resource_registry.get(resource_id) is not None:
                raise ValueError(f"同じ ID の参照文書が既にあります: {resource_id}")
        for skill in manifest.skills:
            existing = skill_registry.get(skill.id)
            if existing is not None:
                raise ValueError(f"同じ ID のスキルが既にあります: {skill.id}")
            for resource_id in skill.resource_ids:
                if resource_id not in manifest_resource_ids and (
                    plugin_resource_registry.get(resource_id) is None
                ):
                    raise ValueError(f"プラグインに無い参照文書を指しています: {resource_id}")
        existing_mcp = {item.server_id for item in runtime_config_store.list_mcp_servers()}
        for server in manifest.mcp_servers:
            if server.server_id in existing_mcp:
                raise ValueError(f"同じ ID の MCP 接続が既にあります: {server.server_id}")

    @staticmethod
    def _validate_reenable(manifest: PluginManifest) -> None:
        for skill in manifest.skills:
            if skill_registry.get(skill.id) is not None:
                raise ValueError(f"同じ ID のスキルが既にあります: {skill.id}")
        existing_mcp = {item.server_id for item in runtime_config_store.list_mcp_servers()}
        for server in manifest.mcp_servers:
            if server.server_id in existing_mcp:
                raise ValueError(f"同じ ID の MCP 接続が既にあります: {server.server_id}")
        for resource in manifest.resources:
            if plugin_resource_registry.get(resource.id) is not None:
                raise ValueError(f"同じ ID の参照文書が既にあります: {resource.id}")

    @staticmethod
    def _ensure_not_referenced(record: PluginRecord) -> None:
        """業務 Agent が下書きか公開中の版で使うスキルを含むプラグインは、無効化・削除しない。

        公開中の版だけが使う場合も断る（スキルを欠いた版が利用者の Run で動くため。#1032）。
        画面はこの文をそのまま出すため、業務 Agent の名前を日本語の文で返す。
        """
        skill_ids = {skill.id for skill in record.manifest.skills}
        referenced: list[str] = []
        for agent in runtime_repository.list_agents():
            published = agent.published()
            used = set(agent.skill_ids) | set(published.skill_ids if published else [])
            if skill_ids & used:
                referenced.append(agent.name or agent.id)
        if referenced:
            raise ValueError(
                f"このプラグインのスキルは業務 Agent（{'、'.join(referenced)}）が使っています。"
                "業務 Agent のスキルから外して公開してから、無効化・削除してください。"
            )


plugin_registry = PluginRegistry()


# --- 宣言ロード(env) ------------------------------------------------------


def _json_items(raw: str | None, key: str) -> list[Any]:
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("plugin 宣言 env が不正な JSON です")
        return []
    if isinstance(data, list):
        items: object = data
    elif isinstance(data, dict):
        items = data.get(key)
    else:
        items = None
    return items if isinstance(items, list) else []


def _plugins_from_json(raw: str | None) -> list[PluginManifest]:
    manifests: list[PluginManifest] = []
    for item in _json_items(raw, "plugins"):
        try:
            manifests.append(PluginManifest.model_validate(item))
        except ValidationError as exc:
            # 例外の文は入力（MCP の API キー・URL など）を含むため、位置と種類だけを出す（#1081）。
            problems = ", ".join(
                f"{'.'.join(str(part) for part in error['loc'])} ({error['type']})"
                for error in exc.errors(include_input=False, include_url=False)
            )
            logger.warning("宣言 plugin が不正: %s", problems)
    return manifests


def reload_declared_plugins() -> dict[str, int]:
    """env 宣言の plugin を install する(起動時/明示 reload)。"""
    from app.settings import get_settings

    manifests = _plugins_from_json(get_settings().agent_plugins_json)
    installed = 0
    for manifest in manifests:
        try:
            plugin_registry.install(manifest)
            installed += 1
        except (ValueError, KeyError) as exc:
            logger.warning("宣言 plugin の install に失敗: %s (%s)", manifest.id, exc)
    return {"plugins": installed}


# 起動時に宣言層を読み込む(未設定なら何もしない)。
reload_declared_plugins()
