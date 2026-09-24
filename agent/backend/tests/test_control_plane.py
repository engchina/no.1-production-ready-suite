"""Control Plane v2 の契約テスト。legacy v1 経路には依存しない。"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

import httpx
import pytest

import app.features.agent.control_plane as control_plane
from app.features.agent.control_plane import (
    RUNTIME_ADAPTERS,
    DeerFlowAdapter,
    HermesAdapter,
    OpenClawAdapter,
    RuntimeBinding,
    RuntimeBindingRegistry,
    RuntimeDefinition,
    RuntimePatch,
    RuntimeSubmission,
    runtime_binding_registry,
    runtime_registry,
)
from app.features.agent.plugins import PluginManifest, PluginRegistry
from app.features.agent.router import _binding_token_env_name
from app.features.agent.runtime import (
    AgentProfile,
    AgentRuntimeRepository,
    RunCreateRequest,
    RunEventType,
    RunStatus,
    _migrate_legacy_agent,
    runtime_repository,
)
from app.features.agent.skills import AgentSkillDefinition, skill_registry
from app.main import app


class _FakeHermesAdapter:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def sync_binding(self, *_: Any, **__: Any) -> None:
        return None

    async def submit_run(self, *_: Any, **__: Any) -> RuntimeSubmission:
        return RuntimeSubmission(external_run_id="hermes-run-1", status="running")

    async def cancel(self, _runtime: Any, external_run_id: str) -> None:
        self.cancelled.append(external_run_id)


@pytest.mark.asyncio
async def test_v2_run_requires_binding_and_rejects_tool_calls() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.post(
            "/api/runs",
            headers={"X-Agent-API-Version": "2"},
            json={"goal": "Binding が必要"},
        )
        legacy = await client.post(
            "/api/runs",
            headers={"X-Agent-API-Version": "2"},
            json={
                "goal": "tool_calls は拒否",
                "tool_calls": [{"name": "echo", "arguments": {}}],
            },
        )

    assert missing.status_code == 409
    assert missing.json()["error_code"] == "runtime_binding_required"
    assert legacy.status_code == 422
    assert legacy.json()["error_code"] == "legacy_tool_calls_not_supported"


@pytest.mark.asyncio
async def test_binding_mcp_closure_and_external_run(monkeypatch: pytest.MonkeyPatch) -> None:
    agent_id = "control_plane_test_agent"
    binding_id = "binding_control_plane_test"
    adapter = _FakeHermesAdapter()
    monkeypatch.setitem(RUNTIME_ADAPTERS, "hermes", adapter)
    monkeypatch.setenv(
        _binding_token_env_name(binding_id),
        "binding-secret",
    )
    runtime_registry.patch("hermes-default", RuntimePatch(enabled=True))
    runtime_repository.create_agent(
        AgentProfile(
            id=agent_id,
            name="Control Plane test",
            instructions="Skill だけを利用する。",
            skill_ids=["business_rag_research"],
        )
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            created_binding = await client.post(
                "/api/runtime-bindings",
                json={
                    "id": binding_id,
                    "agent_id": agent_id,
                    "runtime_id": "hermes-default",
                    "native_agent_ref": "control-plane-test",
                    "is_default": True,
                },
            )
            tools = await client.post(
                f"/api/mcp/{binding_id}",
                headers={"Authorization": "Bearer binding-secret"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            created_run = await client.post(
                "/api/runs",
                headers={"X-Agent-API-Version": "2"},
                json={"goal": "業務文書を調べる", "agent_id": agent_id},
            )
            run_id = created_run.json()["data"]["id"]
            fetched_run = await client.get(f"/api/runs/{run_id}")
            cancelled = await client.post(f"/api/runs/{run_id}/cancel")
            cancelled_again = await client.post(f"/api/runs/{run_id}/cancel")

        assert created_binding.status_code == 200
        assert created_binding.json()["data"]["sync_status"] == "ready"
        assert [item["name"] for item in tools.json()["result"]["tools"]] == ["external_rag_search"]
        assert created_run.status_code == 200
        assert fetched_run.json()["data"]["external_run_id"] == "hermes-run-1"
        assert fetched_run.json()["data"]["runtime_id"] == "hermes-default"
        assert cancelled.status_code == 200
        assert cancelled_again.status_code == 200
        assert adapter.cancelled == ["hermes-run-1"]
    finally:
        if runtime_binding_registry.get(binding_id):
            runtime_binding_registry.delete(binding_id)
        with suppress(KeyError):
            runtime_repository.delete_agent(agent_id)
        runtime_registry.patch("hermes-default", RuntimePatch(enabled=False))


def test_binding_registry_keeps_one_default_per_agent() -> None:
    registry = RuntimeBindingRegistry()
    first = registry.create(
        RuntimeBinding(
            id="binding_default_first",
            agent_id="agent_default_test",
            runtime_id="openclaw-default",
            native_agent_ref="first",
            is_default=True,
        )
    )
    second = registry.create(
        RuntimeBinding(
            id="binding_default_second",
            agent_id="agent_default_test",
            runtime_id="hermes-default",
            native_agent_ref="second",
            is_default=True,
        )
    )

    assert first.is_default is True
    defaults = [item for item in registry.list() if item.is_default]
    assert [item.id for item in defaults] == [second.id]


def test_plugin_duplicate_ids_fail_before_registry_mutation() -> None:
    registry = PluginRegistry()
    skill_id = "duplicate_control_plane_skill"
    manifest = PluginManifest(
        id="duplicate_control_plane_package",
        name="duplicate",
        skills=[
            AgentSkillDefinition(id=skill_id, name="first"),
            AgentSkillDefinition(id=skill_id, name="second"),
        ],
    )

    with pytest.raises(ValueError, match="duplicate skill id"):
        registry.install(manifest)

    assert skill_registry.get(skill_id) is None


def test_plugin_in_use_cannot_be_disabled_or_uninstalled() -> None:
    registry = PluginRegistry()
    plugin_id = "referenced_control_plane_package"
    skill_id = "referenced_control_plane_skill"
    agent_id = "referenced_control_plane_agent"
    registry.install(
        PluginManifest(
            id=plugin_id,
            name="referenced",
            skills=[AgentSkillDefinition(id=skill_id, name="referenced")],
        )
    )
    runtime_repository.create_agent(
        AgentProfile(id=agent_id, name="referenced", skill_ids=[skill_id])
    )
    try:
        with pytest.raises(ValueError, match="referenced by agents"):
            registry.set_enabled(plugin_id, False)
        with pytest.raises(ValueError, match="referenced by agents"):
            registry.uninstall(plugin_id)
    finally:
        runtime_repository.delete_agent(agent_id)
        registry.uninstall(plugin_id)

    assert skill_registry.get(skill_id) is None


def test_legacy_tool_migration_disables_unmappable_agent() -> None:
    migrated = _migrate_legacy_agent(
        AgentProfile(
            id="legacy_agent",
            name="legacy",
            tool_names=["external_rag_search", "echo"],
        )
    )

    assert migrated.skill_ids == ["business_rag_research"]
    assert migrated.migration_required is True
    assert migrated.enabled is False


def test_runtime_events_status_and_artifacts_are_normalized_and_deduplicated() -> None:
    repository = AgentRuntimeRepository()
    repository.create_agent(
        AgentProfile(
            id="agent_runtime_reconcile",
            name="Runtime reconcile",
            skill_ids=["business_rag_research"],
        )
    )
    run = repository.create_control_plane_run(
        RunCreateRequest(goal="同期する", agent_id="agent_runtime_reconcile"),
        runtime_id="hermes-fixture",
        binding_id="binding-runtime-reconcile",
        capabilities={"stream_events": True, "artifacts": True},
    )
    repository.mark_runtime_submitted(
        run.id,
        external_run_id="external-run",
        external_cursor=None,
        external_status="queued",
    )
    event = {
        "id": "runtime-event-1",
        "status": "running",
        "message": "処理中",
        "api_token": "must-not-leak",
    }
    repository.record_runtime_event(run.id, event, cursor="cursor-1")
    repository.record_runtime_event(run.id, event, cursor="cursor-1")
    repository.reconcile_runtime_status(
        run.id,
        external_status="succeeded",
        payload={"status": "succeeded"},
    )
    repository.replace_runtime_artifacts(
        run.id,
        [{"id": "report", "name": "report.md", "url": "https://example/a?token=secret"}],
    )

    reconciled = repository.get_run(run.id)
    runtime_events = [item for item in reconciled.events if item.type == RunEventType.RUNTIME_EVENT]
    assert reconciled.status == RunStatus.COMPLETED
    assert reconciled.external_cursor == "cursor-1"
    assert len(runtime_events) == 1
    assert runtime_events[0].payload["api_token"] == "[REDACTED]"
    assert reconciled.artifacts[0].content["url"] == "https://example/a"


@pytest.mark.asyncio
async def test_hermes_adapter_uses_capability_runs_status_and_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/capabilities":
            return httpx.Response(
                200,
                json={
                    "features": {
                        "run_events_sse": True,
                        "run_stop": True,
                        "run_approval": True,
                    }
                },
            )
        if request.url.path == "/v1/runs" and request.method == "POST":
            return httpx.Response(200, json={"run_id": "hermes-fixture", "status": "started"})
        if request.url.path == "/v1/runs/hermes-fixture" and request.method == "GET":
            return httpx.Response(200, json={"run_id": "hermes-fixture", "status": "running"})
        if request.url.path.endswith("/stop"):
            return httpx.Response(200, json={"status": "stopping"})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def fixture_client(*_: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fixture_client)
    adapter = HermesAdapter()
    runtime = RuntimeDefinition(
        id="hermes-fixture",
        name="Hermes fixture",
        kind="hermes",
        base_url="http://hermes.test",
        enabled=True,
    )
    binding = RuntimeBinding(
        id="binding-hermes-fixture",
        agent_id="agent-hermes-fixture",
        runtime_id=runtime.id,
        native_agent_ref="fixture-profile",
    )

    capabilities = await adapter.probe_capabilities(runtime)
    submission = await adapter.submit_run(
        runtime,
        binding,
        goal="fixture",
        instructions="日本語で回答",
        control_plane_run_id="control-run",
    )
    status = await adapter.get_status(runtime, submission.external_run_id)
    await adapter.cancel(runtime, submission.external_run_id)

    assert capabilities.cancel is True
    assert capabilities.approvals is True
    assert submission.external_run_id == "hermes-fixture"
    assert status["status"] == "running"
    assert calls == [
        ("GET", "/v1/capabilities"),
        ("POST", "/v1/runs"),
        ("GET", "/v1/runs/hermes-fixture"),
        ("POST", "/v1/runs/hermes-fixture/stop"),
    ]


@pytest.mark.asyncio
async def test_deerflow_adapter_normalizes_thread_run_and_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/langgraph/threads":
            return httpx.Response(200, json={"thread_id": "thread-fixture"})
        if request.url.path.endswith("/runs") and request.method == "POST":
            return httpx.Response(200, json={"run_id": "run-fixture", "status": "queued"})
        if request.url.path.endswith("/state"):
            return httpx.Response(
                200,
                json={"values": {"artifacts": [{"name": "report.md"}]}},
            )
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def fixture_client(*_: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fixture_client)
    runtime = RuntimeDefinition(
        id="deerflow-fixture",
        name="DeerFlow fixture",
        kind="deerflow",
        base_url="http://deerflow.test",
        enabled=True,
    )
    binding = RuntimeBinding(
        id="binding-deerflow-fixture",
        agent_id="agent-deerflow-fixture",
        runtime_id=runtime.id,
        native_agent_ref="fixture-agent",
    )
    adapter = DeerFlowAdapter()

    submission = await adapter.submit_run(
        runtime,
        binding,
        goal="fixture",
        instructions="日本語で回答",
        control_plane_run_id="control-run",
    )
    artifacts = await adapter.list_artifacts(runtime, submission.external_cursor or "")

    assert submission.external_run_id == "run-fixture"
    assert submission.external_cursor == "thread-fixture"
    assert artifacts == [{"name": "report.md"}]


class _OpenClawFixtureSocket:
    def __init__(self) -> None:
        self.frames = [
            json.dumps(
                {
                    "type": "event",
                    "event": "connect.challenge",
                    "payload": {"nonce": "fixture"},
                }
            )
        ]
        self.requests: list[dict[str, Any]] = []

    async def __aenter__(self) -> _OpenClawFixtureSocket:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def recv(self) -> str:
        return self.frames.pop(0)

    async def send(self, value: str) -> None:
        request = json.loads(value)
        self.requests.append(request)
        if request["method"] == "connect":
            payload = {"type": "hello-ok"}
        else:
            payload = {"runId": "openclaw-fixture", "status": "queued"}
        self.frames.append(
            json.dumps(
                {
                    "type": "res",
                    "id": request["id"],
                    "ok": True,
                    "payload": payload,
                }
            )
        )


@pytest.mark.asyncio
async def test_openclaw_adapter_uses_gateway_websocket_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _OpenClawFixtureSocket()
    monkeypatch.setattr(control_plane, "websocket_connect", lambda *_args, **_kwargs: socket)
    monkeypatch.setenv("OPENCLAW_FIXTURE_TOKEN", "fixture-token")
    runtime = RuntimeDefinition(
        id="openclaw-fixture",
        name="OpenClaw fixture",
        kind="openclaw",
        base_url="http://openclaw.test:18789",
        auth_secret_ref="OPENCLAW_FIXTURE_TOKEN",
        enabled=True,
    )
    binding = RuntimeBinding(
        id="binding-openclaw-fixture",
        agent_id="agent-openclaw-fixture",
        runtime_id=runtime.id,
        native_agent_ref="fixture-agent",
    )

    submission = await OpenClawAdapter().submit_run(
        runtime,
        binding,
        goal="fixture goal",
        instructions="日本語で回答",
        control_plane_run_id="control-run",
    )

    assert submission.external_run_id == "openclaw-fixture"
    assert socket.requests[0]["method"] == "connect"
    assert socket.requests[0]["params"]["auth"] == {"token": "fixture-token"}
    assert socket.requests[1]["method"] == "chat.send"
    assert socket.requests[1]["params"]["agentId"] == "fixture-agent"
