"""外部取得は固定 git blob の fixture で検証し、実ネットワーク・配布コードを実行しない。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest

from app.features.agent import marketplace_import as importer
from app.features.agent.builtin_runtime import compose_instructions
from app.features.agent.plugins import (
    MarketplaceEntry,
    MarketplaceListing,
    MarketplaceRegistry,
    MarketplaceSource,
    PluginManifest,
    PluginRegistry,
    plugin_resource_registry,
)
from app.features.agent.skills import skill_registry
from app.features.agent.skills_loader import _split_frontmatter

REVISION = "a" * 40
CATALOG_URL = (
    "https://raw.githubusercontent.com/sample/catalog/main/.claude-plugin/marketplace.json"
)
SKILL = "---\nname: 分析\ndescription: 売上の比較\n---\n比較する範囲を確認してください。"


@pytest.fixture
def distribution(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "files": {
            ".claude-plugin/marketplace.json": json.dumps(
                {
                    "name": "外部",
                    "plugins": [
                        {
                            "name": "analysis",
                            "author": {"name": "著者"},
                            "source": "./plugins/analysis",
                        },
                        {"name": "unsupported", "source": {"source": "npm", "package": "sample"}},
                    ],
                }
            ),
            "plugins/analysis/.claude-plugin/plugin.json": json.dumps(
                {"name": "analysis", "version": "1.2.0"}
            ),
            "plugins/analysis/skills/analyze/SKILL.md": SKILL,
            "plugins/analysis/skills/analyze/REFERENCE.md": "売上の根拠を確認する。",
            "plugins/analysis/skills/analyze/scripts/check.py": "raise RuntimeError('実行しない')",
        },
        "requests": [],
        "fail": False,
        "symlink": False,
        "corrupt": False,
    }
    original_client = httpx.Client

    def handle(request: httpx.Request) -> httpx.Response:
        state["requests"].append(str(request.url))
        if state["fail"]:
            return httpx.Response(503)
        if "/commits/" in request.url.path:
            assert request.headers["accept"] == "application/vnd.github.sha"
            return httpx.Response(200, text=REVISION)
        if "/git/trees/" in request.url.path:
            tree = []
            for path, text in state["files"].items():
                data = text.encode()
                tree.append(
                    {
                        "path": path,
                        "type": "blob",
                        "mode": "120000"
                        if state["symlink"] and path.endswith("SKILL.md")
                        else "100644",
                        "size": len(data),
                        "sha": hashlib.sha1(
                            f"blob {len(data)}\0".encode() + data, usedforsecurity=False
                        ).hexdigest(),
                    }
                )
            return httpx.Response(200, json={"tree": tree, "truncated": False})
        path = request.url.path.split("/", 4)[4]
        if path in state["files"]:
            content = state["files"][path]
            if state["corrupt"] and path.endswith("SKILL.md"):
                content += "変更"
            return httpx.Response(200, text=content)
        return httpx.Response(404)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    return state


def catalog() -> tuple[MarketplaceRegistry, MarketplaceEntry]:
    registry = MarketplaceRegistry()
    registry.add(MarketplaceSource(id="test-import", url=CATALOG_URL))
    source = registry.refresh("test-import")
    assert source.last_error is None
    listing = registry.get_listing(source.id)
    entry = listing.plugins[0]
    assert isinstance(entry, MarketplaceEntry)
    return registry, entry


def test_catalog_does_not_download_plugins_and_preserves_unsupported_entry(
    distribution: dict[str, Any],
) -> None:
    registry, entry = catalog()
    assert entry.source == "./plugins/analysis"
    assert entry.author == "著者"
    assert not any("/plugins/analysis/" in url for url in distribution["requests"])
    listing = registry.get_listing("test-import")
    assert listing.revision == REVISION
    assert len(listing.plugins) == 2
    assert isinstance(listing.plugins[1], MarketplaceEntry)
    assert listing.plugins[1].unavailable_reason
    restored = MarketplaceListing.model_validate(listing.model_dump(mode="json"))
    assert isinstance(restored.plugins[0], MarketplaceEntry)
    with pytest.raises(ValueError, match="導入内容"):
        registry.find_manifest("test-import", entry.id)


def test_prepare_is_deterministic_and_installs_real_skill_reference_and_origin(
    distribution: dict[str, Any],
) -> None:
    marketplace, entry = catalog()
    preview = marketplace.preview("test-import", entry.id)
    assert marketplace.preview("test-import", entry.id).digest == preview.digest
    manifest = preview.manifest
    assert len(manifest.skills) == 1
    assert manifest.skills[0].instructions == "比較する範囲を確認してください。"
    assert manifest.import_metadata["revision"] == REVISION
    assert any("scripts" in warning for warning in preview.warnings)
    registry = PluginRegistry()
    record = registry.install(manifest, marketplace_id="test-import")
    try:
        assert record.skill_count == record.resource_count == 1
        instructions = compose_instructions("", [manifest.skills[0].id])
        assert "REFERENCE.md" in instructions
        assert "売上の根拠を確認する。" not in instructions
        from app.features.agent.skill_resources import reference_tool
        from app.features.agent.tools import ToolInvocationContext

        _, read_reference = reference_tool(manifest.skills[0].resource_ids)
        assert (
            read_reference(
                {"resource_id": manifest.resources[0].id, "offset": 0}, ToolInvocationContext()
            )["content"]
            == "売上の根拠を確認する。"
        )
        with pytest.raises(ValueError, match="割り当て"):
            read_reference(
                {"resource_id": "other-skill-resource", "offset": 0}, ToolInvocationContext()
            )
        restored = PluginManifest.model_validate(manifest.model_dump(mode="json"))
        assert restored.import_metadata == manifest.import_metadata
        registry.set_enabled(record.id, False)
        assert skill_registry.get(manifest.skills[0].id) is None
        registry.set_enabled(record.id, True)
        assert plugin_resource_registry.get(manifest.resources[0].id)
    finally:
        registry.uninstall(record.id)
    assert plugin_resource_registry.get(manifest.resources[0].id) is None


@pytest.mark.parametrize(
    "source",
    [
        {"source": "url", "url": "https://github.com/sample/catalog.git", "ref": "branch/name"},
        {"source": "github", "repo": "sample/catalog", "sha": REVISION},
        {
            "source": "git-subdir",
            "url": "sample/catalog",
            "path": "plugins/analysis",
            "sha": REVISION,
        },
    ],
)
def test_git_sources_and_explicit_skills(
    distribution: dict[str, Any], source: dict[str, Any]
) -> None:
    root = "plugins/analysis" if source["source"] == "git-subdir" else "."
    skill_path = "./skills/analyze" if root != "." else "./plugins/analysis/skills/analyze"
    entry = MarketplaceEntry(
        id="source-test",
        name="analysis",
        source=source,
        upstream={"strict": False, "skills": [skill_path]},
    )
    preview = importer.prepare_import(entry, "test-import")
    assert len(preview.manifest.skills) == 1
    assert preview.manifest.import_metadata["root"] == ("" if root == "." else root)


def test_two_marketplaces_have_distinct_stable_skill_ids(distribution: dict[str, Any]) -> None:
    registry, entry = catalog()
    first = registry.preview("test-import", entry.id).manifest.skills[0]
    second = importer.prepare_import(entry, "another-source").manifest.skills[0]
    assert first.id != second.id


@pytest.mark.parametrize(
    "path", ["../outside", "/outside", "a/../../outside", "a\\outside", "https://elsewhere/path"]
)
def test_path_outside_root_is_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        importer.safe_path(path)


@pytest.mark.parametrize("failure", ["symlink", "corrupt"])
def test_symlink_and_blob_mismatch_are_rejected(distribution: dict[str, Any], failure: str) -> None:
    registry, entry = catalog()
    distribution[failure] = True
    with pytest.raises(ValueError):
        registry.preview("test-import", entry.id)
    assert skill_registry.get(entry.id) is None


def test_failed_refresh_keeps_previous_catalog_and_explicit_status(
    distribution: dict[str, Any],
) -> None:
    registry, entry = catalog()
    distribution["fail"] = True
    source = registry.refresh("test-import")
    assert source.last_error
    assert source.refresh_status == "failed"
    assert source.plugin_count == 2
    assert registry.get_listing("test-import").plugins[0].id == entry.id


def test_empty_external_plugin_is_not_installed(distribution: dict[str, Any]) -> None:
    registry, entry = catalog()
    distribution["files"] = {
        path: text for path, text in distribution["files"].items() if "/skills/" not in path
    }
    with pytest.raises(ValueError, match="対応する Skill"):
        registry.preview("test-import", entry.id)


def test_http_mcp_is_mapped_and_stdio_is_explicitly_unsupported(
    distribution: dict[str, Any],
) -> None:
    registry, entry = catalog()
    distribution["files"]["plugins/analysis/.mcp.json"] = json.dumps(
        {
            "mcpServers": {
                "business": {"type": "http", "url": "https://business.example/api/mcp"},
                "local": {"command": "python", "args": ["server.py"]},
            }
        }
    )
    preview = registry.preview("test-import", entry.id)
    assert len(preview.manifest.mcp_servers) == 1
    assert (
        preview.manifest.skills[0].mcp_requirements[0].server_id
        == preview.manifest.mcp_servers[0].server_id
    )
    assert any("MCP local" in warning for warning in preview.warnings)


def test_native_inline_contract_is_preserved(distribution: dict[str, Any]) -> None:
    distribution["files"][".claude-plugin/marketplace.json"] = json.dumps(
        {"plugins": [{"id": "native", "name": "native", "skills": []}]}
    )
    registry = MarketplaceRegistry()
    registry.add(MarketplaceSource(id="native", url=CATALOG_URL))
    assert registry.refresh("native").refresh_status == "ready"
    assert registry.find_manifest("native", "native") == PluginManifest(id="native", name="native")


def test_catalog_does_not_expose_credentials_or_command_arguments(
    distribution: dict[str, Any],
) -> None:
    payload = {
        "plugins": [
            {
                "name": "secrets",
                "source": {
                    "source": "url",
                    "url": "https://user:private-token@github.com/sample/repo",
                },
                "mcpServers": {
                    "private": {
                        "url": "https://example.test?token=private-token",
                        "headers": {"Authorization": "Bearer private-token"},
                        "command": "private-token",
                        "args": ["private-token"],
                    }
                },
            }
        ]
    }
    distribution["files"][".claude-plugin/marketplace.json"] = json.dumps(payload)
    registry = MarketplaceRegistry()
    registry.add(MarketplaceSource(id="test", url=CATALOG_URL))
    registry.refresh("test")
    response = registry.get_listing("test").model_dump_json()
    assert "private-token" not in response
    assert "導入" not in response or "unavailable_reason" in response


def test_request_and_size_limits_terminate_before_install(
    distribution: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, entry = catalog()
    monkeypatch.setattr(importer, "MAX_REQUESTS", 0)
    with pytest.raises(ValueError, match="上限"):
        registry.preview("test-import", entry.id)


def test_external_preview_and_install_api_require_review(distribution: dict[str, Any]) -> None:
    from fastapi.testclient import TestClient

    from app.features.agent.plugins import marketplace_registry, plugin_registry
    from app.main import app

    with TestClient(app) as client:
        client.post("/api/plugins/marketplaces", json={"id": "api-import", "url": CATALOG_URL})
        client.post("/api/plugins/marketplaces/api-import/refresh")
        entry = marketplace_registry.get_listing("api-import").plugins[0]
        payload = {"marketplace_id": "api-import", "plugin_id": entry.id}
        try:
            assert client.post("/api/plugins", json=payload).status_code == 400
            preview = client.post(
                f"/api/plugins/marketplaces/api-import/plugins/{entry.id}/preview"
            )
            assert preview.status_code == 200, preview.text
            assert (
                client.post(
                    "/api/plugins",
                    json={**payload, "preview_digest": "stale", "accept_limitations": True},
                ).status_code
                == 409
            )
            result = client.post(
                "/api/plugins",
                json={
                    **payload,
                    "preview_digest": preview.json()["data"]["digest"],
                    "accept_limitations": True,
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()["data"]["skill_count"] == 1
        finally:
            if plugin_registry.get(entry.id):
                plugin_registry.uninstall(entry.id)
            marketplace_registry.remove("api-import")


def test_strict_false_does_not_import_unlisted_default_skills(distribution: dict[str, Any]) -> None:
    distribution["files"]["skills/unlisted/SKILL.md"] = SKILL
    entry = MarketplaceEntry(
        id="explicit-only",
        name="explicit-only",
        source={"source": "github", "repo": "sample/catalog", "sha": REVISION},
        upstream={"strict": False, "skills": ["./plugins/analysis/skills/analyze"]},
    )
    preview = importer.prepare_import(entry, "test-import")
    assert len(preview.manifest.skills) == 1
    assert "skills/unlisted/SKILL.md" not in preview.manifest.import_metadata["files_sha256"]


@pytest.mark.parametrize("failure", ["registration", "persistence"])
def test_install_failure_revokes_all_components(
    distribution: dict[str, Any], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from app.features.agent.config import runtime_config_store

    registry, entry = catalog()
    distribution["files"]["plugins/analysis/.mcp.json"] = json.dumps(
        {"mcpServers": {"business": {"url": "https://business.example/api/mcp"}}}
    )
    manifest = registry.preview("test-import", entry.id).manifest
    assert len(manifest.mcp_servers) == 1
    plugins = PluginRegistry()
    original = plugin_resource_registry.set_declared
    if failure == "registration":

        def fail_register(source: str, resources: list[Any]) -> None:
            if resources:
                raise RuntimeError("登録失敗")
            original(source, resources)

        monkeypatch.setattr(plugin_resource_registry, "set_declared", fail_register)

    def persist(_: Any) -> None:
        raise RuntimeError("保存失敗")

    with pytest.raises(RuntimeError):
        plugins.install(manifest, persist=persist if failure == "persistence" else None)
    assert plugins.get(manifest.id) is None
    assert all(skill_registry.get(s.id) is None for s in manifest.skills)
    assert all(plugin_resource_registry.get(r.id) is None for r in manifest.resources)
    assert not any(
        s.source == f"plugin:{manifest.id}" for s in runtime_config_store.list_mcp_servers()
    )


def test_duplicates_and_invalid_entry_do_not_hide_valid_catalog_entries(
    distribution: dict[str, Any],
) -> None:
    distribution["files"][".claude-plugin/marketplace.json"] = json.dumps(
        {
            "plugins": [
                {"name": "duplicate", "source": "./plugins/analysis"},
                {"name": "duplicate", "source": "./plugins/analysis"},
                {"id": "native-duplicate", "name": "native one"},
                {"id": "native-duplicate", "name": "native two"},
                {"name": "bad-url", "source": {"source": "url", "url": "https://[invalid"}},
                {"id": "valid-native", "name": "valid"},
            ]
        }
    )
    registry = MarketplaceRegistry()
    registry.add(MarketplaceSource(id="duplicates", url=CATALOG_URL))
    assert registry.refresh("duplicates").last_error is None
    entries = registry.get_listing("duplicates").plugins
    assert len(entries) == len({entry.id for entry in entries}) == 6
    assert all(
        isinstance(entry, MarketplaceEntry) and entry.unavailable_reason for entry in entries[:5]
    )
    assert isinstance(entries[-1], PluginManifest)
    assert isinstance(entries[4], MarketplaceEntry)
    assert entries[4].unavailable_reason == "配布物の URL の形式が不正です。"


@pytest.mark.parametrize(
    "skill_text",
    ["", "---\nname: partial\n---\nmissing description", "---\nname: [invalid\n---\nbody"],
)
def test_invalid_skill_has_no_partial_install(
    distribution: dict[str, Any], skill_text: str
) -> None:
    registry, entry = catalog()
    distribution["files"]["plugins/analysis/skills/analyze/SKILL.md"] = skill_text
    with pytest.raises(ValueError, match="name・description"):
        registry.preview("test-import", entry.id)
    assert not any(s.source == f"plugin:{entry.id}" for s in skill_registry.list())


@pytest.mark.parametrize(
    "source",
    [
        {"source": "github", "repo": "sample/catalog", "sha": "short"},
        {"source": "git-subdir", "url": "sample/catalog", "path": "../outside"},
        {"source": "url", "url": "ssh://git@github.com/sample/catalog"},
    ],
)
def test_invalid_source_is_rejected_before_download(source: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        importer.entry_repository(MarketplaceEntry(id="invalid", name="invalid", source=source))


@pytest.fixture
def real_distributions(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from pathlib import Path

    base = Path(__file__).parent / "fixtures" / "marketplaces"
    fixtures = {
        label: json.loads((base / f"{label}.distribution.json").read_text())
        for label in ("official", "skills", "superpowers")
    }
    records = {record["repository"]: record for record in fixtures.values()}
    origin = json.loads((base / "superpowers.origin.json").read_text())
    raw = (base / "superpowers.json").read_text()
    data = raw.encode()
    records[origin["repository"]] = {
        "revision": origin["revision"],
        "files": {".claude-plugin/marketplace.json": raw},
        "tree": [
            {
                "path": ".claude-plugin/marketplace.json",
                "type": "blob",
                "mode": "100644",
                "size": len(data),
                "sha": hashlib.sha1(
                    f"blob {len(data)}\0".encode() + data, usedforsecurity=False
                ).hexdigest(),
            }
        ],
    }
    original = httpx.Client

    def handle(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        if request.url.host == "api.github.com":
            record = records["/".join(parts[1:3])]
            if parts[3] == "commits":
                return httpx.Response(200, text=record["revision"])
            assert parts[5] == record["revision"]
            return httpx.Response(200, json={"tree": record["tree"], "truncated": False})
        assert request.url.host == "raw.githubusercontent.com"
        record = records["/".join(parts[:2])]
        assert parts[2] == record["revision"]
        path = "/".join(parts[3:])
        assert path in record["files"], f"未収録の fixture: {path}"
        return httpx.Response(200, text=record["files"][path])

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    )
    return records


@pytest.mark.parametrize(
    "label,repository,count",
    [
        ("official", "anthropics/claude-plugins-official", 315),
        ("skills", "anthropics/skills", 5),
        ("superpowers", "obra/superpowers-marketplace", 10),
    ],
)
def test_actual_catalogs_normalize_all_entries_without_network(
    real_distributions: dict[str, Any], label: str, repository: str, count: int
) -> None:
    revision = real_distributions[repository]["revision"]
    listing = importer.fetch_catalog(
        f"https://raw.githubusercontent.com/{repository}/{revision}/.claude-plugin/marketplace.json",
        f"real-{label}",
        10,
    )
    assert len(listing.plugins) == count
    assert listing.revision == revision
    assert len({entry.id for entry in listing.plugins}) == count


@pytest.mark.parametrize(
    "label,repository,plugin_name,skill_name",
    [
        ("official", "anthropics/claude-plugins-official", "frontend-design", "frontend-design"),
        ("official", "anthropics/claude-plugins-official", "code-review", None),
        ("official", "anthropics/claude-plugins-official", "feature-dev", None),
        ("skills", "anthropics/skills", "document-skills", "pdf"),
        ("skills", "anthropics/skills", "document-skills", "docx"),
        ("skills", "anthropics/skills", "document-skills", "xlsx"),
        ("superpowers", "obra/superpowers-marketplace", "superpowers", "brainstorming"),
        ("superpowers", "obra/superpowers-marketplace", "superpowers", "systematic-debugging"),
        ("superpowers", "obra/superpowers-marketplace", "superpowers", "test-driven-development"),
    ],
)
def test_nine_real_samples_import_body_or_report_specific_incompatibility(
    real_distributions: dict[str, Any],
    label: str,
    repository: str,
    plugin_name: str,
    skill_name: str | None,
) -> None:
    revision = real_distributions[repository]["revision"]
    listing = importer.fetch_catalog(
        f"https://raw.githubusercontent.com/{repository}/{revision}/.claude-plugin/marketplace.json",
        f"real-{label}",
        10,
    )
    entry = next(item for item in listing.plugins if item.name == plugin_name)
    if skill_name is None or label == "skills":
        with pytest.raises(
            ValueError,
            match="サービス外での保持" if label == "skills" else "commands・agents・hooks",
        ):
            importer.prepare_import(entry, f"real-{label}")
        return
    preview = importer.prepare_import(entry, f"real-{label}")
    skill = next(item for item in preview.manifest.skills if item.name == skill_name)
    assert skill.description and skill.instructions
    metadata = preview.manifest.import_metadata
    record = real_distributions[metadata["repository"]]
    raw_skill = next(
        text
        for path, text in record["files"].items()
        if path.endswith("/SKILL.md") and _split_frontmatter(text)[0].get("name") == skill_name
    )
    assert skill.instructions == _split_frontmatter(raw_skill)[1]
    for path, digest in metadata["files_sha256"].items():
        assert hashlib.sha256(record["files"][path].encode()).hexdigest() == digest
    assert preview.warnings
    registry = PluginRegistry()
    installed = registry.install(preview.manifest)
    try:
        assert installed.skill_count > 0
        assert all(plugin_resource_registry.get(rid) for rid in skill.resource_ids)
    finally:
        registry.uninstall(installed.id)


def test_api_persistence_failure_returns_503_without_registered_components(
    distribution: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.features.agent import control_plane_store
    from app.features.agent.plugins import plugin_registry
    from app.main import app

    registry, entry = catalog()
    manifest = registry.preview("test-import", entry.id).manifest

    def fail_save(_: Any) -> None:
        raise control_plane_store.ControlPlaneStoreError("保存先を確認してください。")

    monkeypatch.setattr(control_plane_store, "save_plugin", fail_save)
    with TestClient(app) as client:
        response = client.post("/api/plugins", json={"manifest": manifest.model_dump(mode="json")})
    assert response.status_code == 503
    assert plugin_registry.get(manifest.id) is None
    assert all(skill_registry.get(s.id) is None for s in manifest.skills)
    assert all(plugin_resource_registry.get(r.id) is None for r in manifest.resources)


def test_saved_reference_is_read_in_bounded_chunks_and_validates_offsets() -> None:
    from app.features.agent.plugins import PluginResource
    from app.features.agent.skill_resources import REFERENCE_CHUNK_CHARACTERS, reference_tool
    from app.features.agent.tools import ToolInvocationContext

    resource = PluginResource(
        id="bounded-test-reference",
        kind="prompt",
        name="REFERENCE.md",
        content="文" * (REFERENCE_CHUNK_CHARACTERS + 5),
    )
    plugin_resource_registry.set_declared("test:bounded-reference", [resource])
    try:
        _, handler = reference_tool([resource.id])
        first = handler({"resource_id": resource.id, "offset": 0}, ToolInvocationContext())
        assert len(first["content"]) == REFERENCE_CHUNK_CHARACTERS
        second = handler(
            {"resource_id": resource.id, "offset": first["next_offset"]}, ToolInvocationContext()
        )
        assert second["content"] == "文" * 5 and second["next_offset"] is None
        for invalid in [-1, True, "0"]:
            with pytest.raises(ValueError, match="offset"):
                handler({"resource_id": resource.id, "offset": invalid}, ToolInvocationContext())
    finally:
        plugin_resource_registry.set_declared("test:bounded-reference", [])


def test_missing_and_external_reference_links_are_explicit_without_extra_fetch(
    distribution: dict[str, Any],
) -> None:
    registry, entry = catalog()
    distribution["files"]["plugins/analysis/skills/analyze/SKILL.md"] = (
        SKILL + "\n[必要文書](references/missing.md)\n[外部](https://example.test/guide)"
        "\n[他の Skill](../other/SKILL.md)"
    )
    preview = registry.preview("test-import", entry.id)
    assert any("同梱されていない" in w for w in preview.warnings)
    assert any("外部リンク" in w for w in preview.warnings)
    assert any("範囲外" in w for w in preview.warnings)
    assert not any("example.test" in url for url in distribution["requests"])


def test_refresh_does_not_overwrite_a_source_changed_during_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.agent import plugins

    registry = MarketplaceRegistry()
    registry.add(MarketplaceSource(id="changing", url="https://original.example/catalog"))

    def fetch(_: str, __: float, ___: str) -> tuple[MarketplaceListing, None]:
        registry.add(
            MarketplaceSource(id="changing", url="https://new.example/catalog"),
            MarketplaceListing(plugins=[PluginManifest(id="new", name="new")]),
        )
        return MarketplaceListing(plugins=[PluginManifest(id="old", name="old")]), None

    monkeypatch.setattr(plugins, "_fetch_marketplace_listing", fetch)
    with pytest.raises(ValueError, match="設定が変わり"):
        registry.refresh("changing")
    assert registry.get_listing("changing").plugins[0].id == "new"


@pytest.mark.parametrize("limit_kind", ["file", "total", "reference", "deadline"])
def test_download_caps_reject_before_registration(
    distribution: dict[str, Any], monkeypatch: pytest.MonkeyPatch, limit_kind: str
) -> None:
    registry, entry = catalog()
    if limit_kind == "reference":
        monkeypatch.setattr(importer, "MAX_REFERENCE_BYTES", 1)
        with pytest.raises(ValueError, match="上限"):
            registry.preview("test-import", entry.id)
    else:
        if limit_kind == "total":
            monkeypatch.setattr(importer, "MAX_TOTAL_BYTES", 1)
        with importer.DownloadSession(CATALOG_URL) as session:
            if limit_kind == "deadline":
                session.deadline = 0
            with pytest.raises(ValueError, match="上限"):
                session.read(
                    CATALOG_URL.replace("/main/", f"/{REVISION}/"),
                    limit=1 if limit_kind == "file" else 1024,
                )
    assert not any(s.source == f"plugin:{entry.id}" for s in skill_registry.list())


def test_same_plugin_in_two_catalogs_can_be_installed_independently(
    distribution: dict[str, Any],
) -> None:
    first = importer.fetch_catalog(CATALOG_URL, "independent-one", 10).plugins[0]
    second = importer.fetch_catalog(CATALOG_URL, "independent-two", 10).plugins[0]
    assert first.id != second.id
    registry = PluginRegistry()
    one = registry.install(importer.prepare_import(first, "independent-one").manifest)
    try:
        two = registry.install(importer.prepare_import(second, "independent-two").manifest)
        try:
            assert one.manifest.skills[0].id != two.manifest.skills[0].id
            assert one.manifest.resources[0].id != two.manifest.resources[0].id
            registry.set_enabled(one.id, False)
            assert skill_registry.get(two.manifest.skills[0].id)
        finally:
            registry.uninstall(two.id)
    finally:
        registry.uninstall(one.id)


def test_root_catalog_url_resolves_relative_sources(distribution: dict[str, Any]) -> None:
    distribution["files"]["marketplace.json"] = distribution["files"].pop(
        ".claude-plugin/marketplace.json"
    )
    listing = importer.fetch_catalog(
        "https://raw.githubusercontent.com/sample/catalog/main/marketplace.json", "root-catalog", 10
    )
    entry = listing.plugins[0]
    assert isinstance(entry, MarketplaceEntry) and entry.repository == "sample/catalog"
    assert (
        importer.prepare_import(entry, "root-catalog").manifest.skills[0].instructions
        == "比較する範囲を確認してください。"
    )


def test_root_skill_license_is_checked_before_reading_body(distribution: dict[str, Any]) -> None:
    distribution["files"]["SKILL.md"] = SKILL
    distribution["files"]["LICENSE.txt"] = (
        "Test-only notice: retain copies outside the Services is restricted."
    )
    entry = MarketplaceEntry(
        id="root-skill",
        name="root-skill",
        source={"source": "github", "repo": "sample/catalog", "sha": REVISION},
        upstream={"strict": False, "skills": ["."]},
    )
    with pytest.raises(ValueError, match="サービス外での保持"):
        importer.prepare_import(entry, "root-skill")
    assert not any(url.endswith("/SKILL.md") for url in distribution["requests"])


def test_missing_explicit_default_skill_directory_rejects_the_whole_bundle(
    distribution: dict[str, Any],
) -> None:
    registry, entry = catalog()
    distribution["files"] = {
        path.replace("/skills/analyze", "/custom/analyze"): text
        for path, text in distribution["files"].items()
    }
    entry.upstream = {"strict": False, "skills": ["./custom/analyze", "./skills"]}
    with pytest.raises(ValueError, match="明示的な skills path"):
        importer.prepare_import(entry, "test-import")
    assert not any(s.source == f"plugin:{entry.id}" for s in skill_registry.list())


def test_missing_explicit_mcp_file_does_not_silently_import_skills(
    distribution: dict[str, Any],
) -> None:
    registry, entry = catalog()
    distribution["files"]["plugins/analysis/.claude-plugin/plugin.json"] = json.dumps(
        {"name": "analysis", "mcpServers": "./config/missing.json"}
    )
    with pytest.raises(ValueError, match="明示的な MCP"):
        registry.preview("test-import", entry.id)
    assert not any(s.source == f"plugin:{entry.id}" for s in skill_registry.list())
