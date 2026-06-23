"""Plugin と Marketplace の registry。

Claude(`plugin.json` + `marketplace.json`)/ Codex / OpenClaw に倣い、
**plugin = skills + MCP server + agents を束ねた宣言データ**(コード実行なし=セキュリティ境界)を、
install で各 registry へ `source="plugin:<id>"` で展開し、uninstall でまとめて外す。
**marketplace = plugin manifest 一覧を返すソース**(リモート HTTP / ローカル inline)で
複数登録できる。

確定スタック不変・中立命名(`.claude`/`.codex` は使わない)。
外部 LLM provider / 外部ベクトル DB は増やさない。
"""

from __future__ import annotations

import json
import logging
from threading import Lock
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.features.agent.config import ExternalMcpRuntimeConfig, runtime_config_store
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
    mcp_servers: list[ExternalMcpRuntimeConfig] = Field(default_factory=list)
    agents: list[AgentProfile] = Field(default_factory=list)


class PluginRecord(BaseModel):
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    enabled: bool = True
    marketplace_id: str | None = None
    skill_count: int = 0
    mcp_count: int = 0
    agent_count: int = 0
    manifest: PluginManifest


class PluginSummary(BaseModel):
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    enabled: bool = True
    marketplace_id: str | None = None
    skill_count: int = 0
    mcp_count: int = 0
    agent_count: int = 0


class PluginListOutput(BaseModel):
    plugins: list[PluginSummary] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class MarketplaceSource(BaseModel):
    id: str
    name: str = ""
    url: str | None = None
    plugin_count: int = 0
    last_error: str | None = None


class MarketplaceListing(BaseModel):
    name: str = ""
    plugins: list[PluginManifest] = Field(default_factory=list)


class MarketplaceSourcesOutput(BaseModel):
    marketplaces: list[MarketplaceSource] = Field(default_factory=list)


# --- Plugin registry ------------------------------------------------------


def _plugin_source(plugin_id: str) -> str:
    return f"plugin:{plugin_id}"


class PluginRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._plugins: dict[str, PluginRecord] = {}

    def _apply(self, manifest: PluginManifest) -> None:
        source = _plugin_source(manifest.id)
        skill_registry.set_declared(source, [s.model_copy(deep=True) for s in manifest.skills])
        runtime_config_store.set_plugin_mcp_servers(source, manifest.mcp_servers)
        runtime_repository.set_plugin_agents(source, manifest.agents)

    def _revoke(self, plugin_id: str) -> None:
        source = _plugin_source(plugin_id)
        skill_registry.set_declared(source, [])
        runtime_config_store.remove_mcp_servers_by_source(source)
        runtime_repository.remove_agents_by_source(source)

    def install(
        self, manifest: PluginManifest, *, marketplace_id: str | None = None
    ) -> PluginRecord:
        with self._lock:
            self._apply(manifest)
            record = PluginRecord(
                id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                description=manifest.description,
                author=manifest.author,
                enabled=True,
                marketplace_id=marketplace_id,
                skill_count=len(manifest.skills),
                mcp_count=len(manifest.mcp_servers),
                agent_count=len(manifest.agents),
                manifest=manifest,
            )
            self._plugins[manifest.id] = record
            return record.model_copy(deep=True)

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord:
        with self._lock:
            record = self._plugins.get(plugin_id)
            if record is None:
                raise KeyError(plugin_id)
            if enabled and not record.enabled:
                self._apply(record.manifest)
            elif not enabled and record.enabled:
                self._revoke(plugin_id)
            record.enabled = enabled
            return record.model_copy(deep=True)

    def uninstall(self, plugin_id: str) -> None:
        with self._lock:
            if plugin_id not in self._plugins:
                raise KeyError(plugin_id)
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


# --- Marketplace registry -------------------------------------------------


def _fetch_marketplace_listing(
    url: str, timeout_seconds: float
) -> tuple[MarketplaceListing | None, str | None]:
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(url, headers={"accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("marketplace の取得に失敗: %s (%s)", url, exc)
        return None, str(exc)
    try:
        return MarketplaceListing.model_validate(payload), None
    except ValidationError as exc:
        logger.warning("marketplace manifest が不正: %s", url)
        return None, str(exc)


class MarketplaceRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sources: dict[str, MarketplaceSource] = {}
        self._listings: dict[str, MarketplaceListing] = {}

    def add(
        self, source: MarketplaceSource, listing: MarketplaceListing | None = None
    ) -> MarketplaceSource:
        sid = source.id.strip()
        if not sid:
            raise ValueError("marketplace id is required")
        with self._lock:
            stored = source.model_copy(deep=True, update={"id": sid})
            if listing is not None:
                self._listings[sid] = listing.model_copy(deep=True)
                stored.plugin_count = len(listing.plugins)
            self._sources[sid] = stored
            return stored.model_copy(deep=True)

    def remove(self, marketplace_id: str) -> None:
        with self._lock:
            if marketplace_id not in self._sources:
                raise KeyError(marketplace_id)
            del self._sources[marketplace_id]
            self._listings.pop(marketplace_id, None)

    def list(self) -> list[MarketplaceSource]:
        with self._lock:
            return [
                source.model_copy(deep=True)
                for source in sorted(self._sources.values(), key=lambda item: item.id)
            ]

    def get_listing(self, marketplace_id: str) -> MarketplaceListing:
        with self._lock:
            if marketplace_id not in self._sources:
                raise KeyError(marketplace_id)
            return self._listings.get(marketplace_id, MarketplaceListing()).model_copy(deep=True)

    def find_manifest(self, marketplace_id: str, plugin_id: str) -> PluginManifest | None:
        with self._lock:
            listing = self._listings.get(marketplace_id)
            if listing is None:
                return None
            for manifest in listing.plugins:
                if manifest.id == plugin_id:
                    return manifest.model_copy(deep=True)
            return None

    def refresh(self, marketplace_id: str, *, timeout_seconds: float = 10.0) -> MarketplaceSource:
        with self._lock:
            source = self._sources.get(marketplace_id)
            if source is None:
                raise KeyError(marketplace_id)
            url = source.url
        if not url:
            with self._lock:
                return self._sources[marketplace_id].model_copy(deep=True)
        listing, error = _fetch_marketplace_listing(url, timeout_seconds)
        with self._lock:
            source = self._sources.get(marketplace_id)
            if source is None:
                raise KeyError(marketplace_id)
            if listing is not None:
                self._listings[marketplace_id] = listing
                source.plugin_count = len(listing.plugins)
                source.last_error = None
            else:
                source.last_error = error
            return source.model_copy(deep=True)


plugin_registry = PluginRegistry()
marketplace_registry = MarketplaceRegistry()


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
            logger.warning("宣言 plugin が不正: %s", exc)
    return manifests


def _marketplaces_from_json(
    raw: str | None,
) -> list[tuple[MarketplaceSource, MarketplaceListing | None]]:
    result: list[tuple[MarketplaceSource, MarketplaceListing | None]] = []
    for item in _json_items(raw, "marketplaces"):
        if not isinstance(item, dict):
            continue
        marketplace_id = str(item.get("id") or "").strip()
        if not marketplace_id:
            continue
        source = MarketplaceSource(
            id=marketplace_id,
            name=str(item.get("name") or marketplace_id),
            url=item.get("url"),
        )
        listing: MarketplaceListing | None = None
        plugins_raw = item.get("plugins")
        if isinstance(plugins_raw, list):
            manifests: list[PluginManifest] = []
            for plugin_item in plugins_raw:
                try:
                    manifests.append(PluginManifest.model_validate(plugin_item))
                except ValidationError:
                    continue
            listing = MarketplaceListing(name=source.name, plugins=manifests)
        result.append((source, listing))
    return result


def reload_declared_plugins() -> dict[str, int]:
    """env 宣言の marketplace を登録し、宣言 plugin を install する(起動時/明示 reload)。"""
    from app.settings import get_settings

    settings = get_settings()
    marketplaces = _marketplaces_from_json(settings.agent_plugin_marketplaces_json)
    manifests = _plugins_from_json(settings.agent_plugins_json)
    for source, listing in marketplaces:
        marketplace_registry.add(source, listing)
    installed = 0
    for manifest in manifests:
        try:
            plugin_registry.install(manifest)
            installed += 1
        except (ValueError, KeyError) as exc:
            logger.warning("宣言 plugin の install に失敗: %s (%s)", manifest.id, exc)
    return {"plugins": installed, "marketplaces": len(marketplaces)}


# 起動時に宣言層を読み込む(未設定なら何もしない)。
reload_declared_plugins()
