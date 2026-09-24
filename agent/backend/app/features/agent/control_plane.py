"""Business Agent を外部 Runtime へ接続する Control Plane 契約。

Runtime 固有 API はこの境界で吸収し、Agent は Skill だけを参照する。Registry は
既存 snapshot repository へ同梱できるよう JSON export/import を持つ。
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import re
import shlex
import shutil
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field, field_validator
from websockets.asyncio.client import connect as websocket_connect

from app.settings import get_settings

JsonObject = dict[str, Any]
RuntimeKind = Literal["openclaw", "hermes", "deerflow", "legacy_native"]
RuntimeStatus = Literal["unknown", "running", "degraded", "stopped", "disabled", "legacy"]
BindingSyncStatus = Literal["pending", "ready", "error"]
RuntimeServiceAction = Literal["pull", "start", "stop", "restart", "remove"]
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _now() -> datetime:
    return datetime.now(UTC)


class RuntimeCapabilities(BaseModel):
    stream_events: bool = False
    cancel: bool = False
    artifacts: bool = False
    approvals: bool = False
    skill_sync: bool = True
    mcp_sync: bool = True


class RuntimeDefinition(BaseModel):
    id: str
    name: str
    kind: RuntimeKind
    base_url: str = ""
    auth_secret_ref: str | None = None
    managed_service_id: str | None = None
    capabilities: RuntimeCapabilities = Field(default_factory=RuntimeCapabilities)
    enabled: bool = False
    status: RuntimeStatus = "unknown"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        _validate_id(value, "runtime id")
        return value


class RuntimePatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    auth_secret_ref: str | None = None
    managed_service_id: str | None = None
    enabled: bool | None = None


class RuntimeBinding(BaseModel):
    id: str = Field(default_factory=lambda: f"binding_{uuid4().hex}")
    agent_id: str
    runtime_id: str
    native_agent_ref: str
    is_default: bool = False
    enabled: bool = True
    policy: JsonObject = Field(default_factory=dict)
    sync_status: BindingSyncStatus = "pending"
    sync_error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("id", "agent_id", "native_agent_ref")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        _validate_id(value, "binding identifier")
        return value


class RuntimeBindingPatch(BaseModel):
    runtime_id: str | None = None
    native_agent_ref: str | None = None
    is_default: bool | None = None
    enabled: bool | None = None
    policy: JsonObject | None = None


class RuntimeListData(BaseModel):
    runtimes: list[RuntimeDefinition]


class RuntimeBindingListData(BaseModel):
    bindings: list[RuntimeBinding]


class RuntimeSubmission(BaseModel):
    external_run_id: str
    status: str = "queued"
    external_cursor: str | None = None
    output: JsonObject = Field(default_factory=dict)


class RuntimeAdapterError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class RuntimeCapabilityUnsupported(RuntimeAdapterError):
    def __init__(self, capability: str) -> None:
        super().__init__(
            "runtime_capability_unsupported",
            f"Runtime は {capability} に対応していません。",
        )


class RuntimeAdapter(Protocol):
    async def probe_capabilities(self, runtime: RuntimeDefinition) -> RuntimeCapabilities: ...

    async def sync_binding(
        self,
        runtime: RuntimeDefinition,
        binding: RuntimeBinding,
        *,
        agent: JsonObject,
        skills: Sequence[JsonObject],
        mcp_servers: Sequence[JsonObject],
    ) -> None: ...

    async def submit_run(
        self,
        runtime: RuntimeDefinition,
        binding: RuntimeBinding,
        *,
        goal: str,
        instructions: str,
        control_plane_run_id: str,
    ) -> RuntimeSubmission: ...

    def follow_events(
        self, runtime: RuntimeDefinition, external_run_id: str, cursor: str | None = None
    ) -> AsyncIterator[JsonObject]: ...

    async def get_status(self, runtime: RuntimeDefinition, external_run_id: str) -> JsonObject: ...

    async def cancel(self, runtime: RuntimeDefinition, external_run_id: str) -> None: ...

    async def list_artifacts(
        self, runtime: RuntimeDefinition, external_run_id: str
    ) -> list[JsonObject]: ...


class RuntimeRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._runtimes = {runtime.id: runtime for runtime in _builtin_runtimes()}

    def list(self) -> list[RuntimeDefinition]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._runtimes.values()]

    def get(self, runtime_id: str) -> RuntimeDefinition | None:
        with self._lock:
            item = self._runtimes.get(runtime_id)
            return item.model_copy(deep=True) if item else None

    def create(self, runtime: RuntimeDefinition) -> RuntimeDefinition:
        _validate_id(runtime.id, "runtime id")
        if runtime.kind == "legacy_native":
            raise ValueError("legacy runtime cannot be created")
        with self._lock:
            if runtime.id in self._runtimes:
                raise ValueError("runtime already exists")
            stored = runtime.model_copy(
                deep=True,
                update={"created_at": _now(), "updated_at": _now()},
            )
            self._runtimes[stored.id] = stored
            return stored.model_copy(deep=True)

    def patch(self, runtime_id: str, patch: RuntimePatch) -> RuntimeDefinition:
        with self._lock:
            runtime = self._runtimes.get(runtime_id)
            if runtime is None:
                raise KeyError(runtime_id)
            if runtime.kind == "legacy_native":
                raise ValueError("legacy runtime is read-only")
            for key, value in patch.model_dump(exclude_unset=True).items():
                setattr(runtime, key, value)
            runtime.updated_at = _now()
            runtime.status = "unknown" if runtime.enabled else "disabled"
            return runtime.model_copy(deep=True)

    def set_status(
        self,
        runtime_id: str,
        status: RuntimeStatus,
        capabilities: RuntimeCapabilities | None = None,
    ) -> RuntimeDefinition:
        with self._lock:
            runtime = self._runtimes[runtime_id]
            runtime.status = status
            if capabilities is not None:
                runtime.capabilities = capabilities
            runtime.updated_at = _now()
            return runtime.model_copy(deep=True)

    def delete(self, runtime_id: str) -> None:
        with self._lock:
            runtime = self._runtimes.get(runtime_id)
            if runtime is None:
                raise KeyError(runtime_id)
            if runtime.kind == "legacy_native" or runtime.managed_service_id:
                raise ValueError("built-in runtime cannot be removed")
            del self._runtimes[runtime_id]

    def export(self) -> builtins.list[JsonObject]:
        return [item.model_dump(mode="json") for item in self.list()]

    def replace(self, payload: Sequence[JsonObject]) -> None:
        restored = {item.id: item for item in _builtin_runtimes()}
        for raw in payload:
            item = RuntimeDefinition.model_validate(raw)
            if item.kind != "legacy_native":
                restored[item.id] = item
        with self._lock:
            self._runtimes = restored


class RuntimeBindingRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._bindings: dict[str, RuntimeBinding] = {}

    def list(self, *, agent_id: str | None = None) -> list[RuntimeBinding]:
        with self._lock:
            values = self._bindings.values()
            return [
                item.model_copy(deep=True)
                for item in values
                if agent_id is None or item.agent_id == agent_id
            ]

    def get(self, binding_id: str) -> RuntimeBinding | None:
        with self._lock:
            item = self._bindings.get(binding_id)
            return item.model_copy(deep=True) if item else None

    def default_for_agent(self, agent_id: str) -> RuntimeBinding | None:
        with self._lock:
            item = next(
                (
                    binding
                    for binding in self._bindings.values()
                    if binding.agent_id == agent_id and binding.is_default and binding.enabled
                ),
                None,
            )
            return item.model_copy(deep=True) if item else None

    def create(self, binding: RuntimeBinding) -> RuntimeBinding:
        _validate_id(binding.id, "binding id")
        _validate_id(binding.native_agent_ref, "native agent ref")
        with self._lock:
            if binding.id in self._bindings:
                raise ValueError("binding already exists")
            if any(
                item.runtime_id == binding.runtime_id
                and item.native_agent_ref == binding.native_agent_ref
                for item in self._bindings.values()
            ):
                raise ValueError("native agent ref already exists in runtime")
            self._clear_default_locked(binding.agent_id, binding.is_default)
            stored = binding.model_copy(
                deep=True,
                update={"created_at": _now(), "updated_at": _now()},
            )
            self._bindings[stored.id] = stored
            return stored.model_copy(deep=True)

    def patch(self, binding_id: str, patch: RuntimeBindingPatch) -> RuntimeBinding:
        with self._lock:
            binding = self._bindings.get(binding_id)
            if binding is None:
                raise KeyError(binding_id)
            data = patch.model_dump(exclude_unset=True)
            native_ref = data.get("native_agent_ref")
            if isinstance(native_ref, str):
                _validate_id(native_ref, "native agent ref")
            next_runtime_id = str(data.get("runtime_id", binding.runtime_id))
            next_native_ref = str(data.get("native_agent_ref", binding.native_agent_ref))
            if any(
                item.id != binding_id
                and item.runtime_id == next_runtime_id
                and item.native_agent_ref == next_native_ref
                for item in self._bindings.values()
            ):
                raise ValueError("native agent ref already exists in runtime")
            self._clear_default_locked(binding.agent_id, bool(data.get("is_default")))
            for key, value in data.items():
                setattr(binding, key, value)
            binding.sync_status = "pending"
            binding.sync_error = None
            binding.updated_at = _now()
            return binding.model_copy(deep=True)

    def set_sync_result(
        self, binding_id: str, status: BindingSyncStatus, error: str | None = None
    ) -> RuntimeBinding:
        with self._lock:
            binding = self._bindings[binding_id]
            binding.sync_status = status
            binding.sync_error = error
            binding.updated_at = _now()
            return binding.model_copy(deep=True)

    def delete(self, binding_id: str) -> None:
        with self._lock:
            binding = self._bindings.get(binding_id)
            if binding is None:
                raise KeyError(binding_id)
            del self._bindings[binding_id]
        _remove_binding_materialization(binding)

    def delete_for_agent(self, agent_id: str) -> None:
        with self._lock:
            removed = [item for item in self._bindings.values() if item.agent_id == agent_id]
            for binding_id in [item.id for item in removed]:
                del self._bindings[binding_id]
        for binding in removed:
            _remove_binding_materialization(binding)

    def references_runtime(self, runtime_id: str) -> bool:
        with self._lock:
            return any(item.runtime_id == runtime_id for item in self._bindings.values())

    def export(self) -> builtins.list[JsonObject]:
        return [item.model_dump(mode="json") for item in self.list()]

    def replace(self, payload: Sequence[JsonObject]) -> None:
        items = [RuntimeBinding.model_validate(raw) for raw in payload]
        if len({item.id for item in items}) != len(items):
            raise ValueError("duplicate binding id")
        defaults = [item.agent_id for item in items if item.is_default]
        if len(set(defaults)) != len(defaults):
            raise ValueError("multiple default bindings for agent")
        native_refs = [(item.runtime_id, item.native_agent_ref) for item in items]
        if len(set(native_refs)) != len(native_refs):
            raise ValueError("duplicate native agent ref in runtime")
        restored = {item.id: item for item in items}
        with self._lock:
            self._bindings = restored

    def _clear_default_locked(self, agent_id: str, should_clear: bool) -> None:
        if not should_clear:
            return
        for item in self._bindings.values():
            if item.agent_id == agent_id:
                item.is_default = False


class _HttpRuntimeAdapter:
    health_path = "/health"
    static_capabilities = RuntimeCapabilities()

    async def probe_capabilities(self, runtime: RuntimeDefinition) -> RuntimeCapabilities:
        if not runtime.enabled:
            return runtime.capabilities
        async with httpx.AsyncClient(timeout=_runtime_timeout()) as client:
            response = await client.get(
                f"{runtime.base_url.rstrip('/')}{self.health_path}",
                headers=_runtime_headers(runtime),
            )
            response.raise_for_status()
        return self.static_capabilities.model_copy(deep=True)

    async def sync_binding(
        self,
        runtime: RuntimeDefinition,
        binding: RuntimeBinding,
        *,
        agent: JsonObject,
        skills: Sequence[JsonObject],
        mcp_servers: Sequence[JsonObject],
    ) -> None:
        await asyncio.to_thread(
            _materialize_binding,
            runtime,
            binding,
            agent,
            skills,
            mcp_servers,
        )

    async def follow_events(
        self, runtime: RuntimeDefinition, external_run_id: str, cursor: str | None = None
    ) -> AsyncIterator[JsonObject]:
        if False:  # pragma: no cover - Protocol を満たす空 async generator
            yield {"cursor": cursor}

    async def get_status(self, runtime: RuntimeDefinition, external_run_id: str) -> JsonObject:
        raise RuntimeCapabilityUnsupported("status")

    async def cancel(self, runtime: RuntimeDefinition, external_run_id: str) -> None:
        raise RuntimeCapabilityUnsupported("cancel")

    async def list_artifacts(
        self, runtime: RuntimeDefinition, external_run_id: str
    ) -> list[JsonObject]:
        return []

    async def _post_json(
        self, runtime: RuntimeDefinition, path: str, payload: JsonObject
    ) -> JsonObject:
        async with httpx.AsyncClient(timeout=_runtime_timeout()) as client:
            response = await client.post(
                f"{runtime.base_url.rstrip('/')}{path}",
                json=payload,
                headers=_runtime_headers(runtime),
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise RuntimeAdapterError(
                "runtime.invalid_response",
                "Runtime 応答が object ではありません。",
            )
        return data


class OpenClawAdapter(_HttpRuntimeAdapter):
    health_path = "/readyz"
    static_capabilities = RuntimeCapabilities(
        stream_events=True,
        cancel=True,
        approvals=True,
        skill_sync=True,
        mcp_sync=True,
    )

    async def submit_run(
        self,
        runtime: RuntimeDefinition,
        binding: RuntimeBinding,
        *,
        goal: str,
        instructions: str,
        control_plane_run_id: str,
    ) -> RuntimeSubmission:
        session_key = f"control-plane:{binding.id}:{control_plane_run_id}"
        data = await _openclaw_rpc(
            runtime,
            "chat.send",
            {
                "message": goal,
                "systemPrompt": instructions,
                "sessionKey": session_key,
                "agentId": binding.native_agent_ref,
                "idempotencyKey": control_plane_run_id,
            },
        )
        return _submission_from(data, control_plane_run_id)

    async def get_status(self, runtime: RuntimeDefinition, external_run_id: str) -> JsonObject:
        return await _openclaw_rpc(
            runtime, "agent.wait", {"runId": external_run_id, "timeoutMs": 1}
        )

    async def cancel(self, runtime: RuntimeDefinition, external_run_id: str) -> None:
        await _openclaw_rpc(
            runtime,
            "sessions.abort",
            {"runId": external_run_id, "idempotencyKey": f"cancel:{external_run_id}"},
        )

    async def list_artifacts(
        self, runtime: RuntimeDefinition, external_run_id: str
    ) -> list[JsonObject]:
        payload = await _openclaw_rpc(runtime, "artifacts.list", {"runId": external_run_id})
        values = payload.get("artifacts", [])
        if not isinstance(values, list):
            return []
        return [item for item in values if isinstance(item, dict)]


class HermesAdapter(_HttpRuntimeAdapter):
    health_path = "/health"
    static_capabilities = RuntimeCapabilities(
        stream_events=True,
        cancel=True,
        artifacts=False,
        approvals=False,
        skill_sync=True,
        mcp_sync=True,
    )

    async def probe_capabilities(self, runtime: RuntimeDefinition) -> RuntimeCapabilities:
        if not runtime.enabled:
            return runtime.capabilities
        async with httpx.AsyncClient(timeout=_runtime_timeout()) as client:
            response = await client.get(
                f"{runtime.base_url.rstrip('/')}/v1/capabilities",
                headers=_runtime_headers(runtime),
            )
            response.raise_for_status()
            payload = response.json()
        features = payload.get("features", {}) if isinstance(payload, dict) else {}
        return RuntimeCapabilities(
            stream_events=bool(features.get("run_events_sse", True)),
            cancel=bool(features.get("run_stop", False)),
            artifacts=False,
            approvals=bool(features.get("run_approval", False)),
            skill_sync=True,
            mcp_sync=True,
        )

    async def submit_run(
        self,
        runtime: RuntimeDefinition,
        binding: RuntimeBinding,
        *,
        goal: str,
        instructions: str,
        control_plane_run_id: str,
    ) -> RuntimeSubmission:
        data = await self._post_json(
            runtime,
            "/v1/runs",
            {
                "input": goal,
                "instructions": instructions,
                "session_id": control_plane_run_id,
                "model": binding.native_agent_ref,
            },
        )
        return _submission_from(data, control_plane_run_id)

    async def get_status(self, runtime: RuntimeDefinition, external_run_id: str) -> JsonObject:
        async with httpx.AsyncClient(timeout=_runtime_timeout()) as client:
            response = await client.get(
                f"{runtime.base_url.rstrip('/')}/v1/runs/{external_run_id}",
                headers=_runtime_headers(runtime),
            )
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def follow_events(
        self, runtime: RuntimeDefinition, external_run_id: str, cursor: str | None = None
    ) -> AsyncIterator[JsonObject]:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(_runtime_timeout())) as client,
            client.stream(
                "GET",
                f"{runtime.base_url.rstrip('/')}/v1/runs/{external_run_id}/events",
                headers={**_runtime_headers(runtime), "accept": "text/event-stream"},
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload

    async def cancel(self, runtime: RuntimeDefinition, external_run_id: str) -> None:
        await self._post_json(runtime, f"/v1/runs/{external_run_id}/stop", {})


class DeerFlowAdapter(_HttpRuntimeAdapter):
    health_path = "/api/models"
    static_capabilities = RuntimeCapabilities(
        stream_events=True,
        cancel=False,
        artifacts=True,
        approvals=False,
        skill_sync=True,
        mcp_sync=True,
    )

    async def submit_run(
        self,
        runtime: RuntimeDefinition,
        binding: RuntimeBinding,
        *,
        goal: str,
        instructions: str,
        control_plane_run_id: str,
    ) -> RuntimeSubmission:
        thread = await self._post_json(runtime, "/api/langgraph/threads", {"metadata": {}})
        thread_id = str(thread.get("thread_id") or control_plane_run_id)
        data = await self._post_json(
            runtime,
            f"/api/langgraph/threads/{thread_id}/runs",
            {
                "input": {"messages": [{"role": "user", "content": goal}]},
                "config": {
                    "configurable": {
                        "agent_name": binding.native_agent_ref,
                        "control_plane_instructions": instructions,
                    }
                },
                "stream_mode": ["values", "custom"],
            },
        )
        data.setdefault("thread_id", thread_id)
        submission = _submission_from(data, thread_id)
        submission.external_cursor = thread_id
        return submission

    async def get_status(self, runtime: RuntimeDefinition, external_run_id: str) -> JsonObject:
        thread_id = external_run_id.split(":", 1)[0]
        async with httpx.AsyncClient(timeout=_runtime_timeout()) as client:
            response = await client.get(
                f"{runtime.base_url.rstrip('/')}/api/langgraph/threads/{thread_id}/state",
                headers=_runtime_headers(runtime),
            )
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def list_artifacts(
        self, runtime: RuntimeDefinition, external_run_id: str
    ) -> list[JsonObject]:
        state = await self.get_status(runtime, external_run_id)
        values = state.get("values", {})
        artifacts = values.get("artifacts", []) if isinstance(values, dict) else []
        if not isinstance(artifacts, list):
            return []
        return [item for item in artifacts if isinstance(item, dict)]


class LegacyNativeAdapter(_HttpRuntimeAdapter):
    async def probe_capabilities(self, runtime: RuntimeDefinition) -> RuntimeCapabilities:
        return RuntimeCapabilities()

    async def submit_run(
        self,
        runtime: RuntimeDefinition,
        binding: RuntimeBinding,
        *,
        goal: str,
        instructions: str,
        control_plane_run_id: str,
    ) -> RuntimeSubmission:
        raise RuntimeAdapterError("runtime.legacy_read_only", "legacy-native は履歴参照専用です。")


RUNTIME_ADAPTERS: dict[RuntimeKind, RuntimeAdapter] = {
    "openclaw": cast(RuntimeAdapter, OpenClawAdapter()),
    "hermes": cast(RuntimeAdapter, HermesAdapter()),
    "deerflow": cast(RuntimeAdapter, DeerFlowAdapter()),
    "legacy_native": cast(RuntimeAdapter, LegacyNativeAdapter()),
}


class RuntimeServiceEntry(BaseModel):
    service_id: str
    runtime_id: str
    profile: str
    health_path: str
    dev_port: int


RUNTIME_SERVICES: tuple[RuntimeServiceEntry, ...] = (
    RuntimeServiceEntry(
        service_id="runtime-openclaw",
        runtime_id="openclaw-default",
        profile="openclaw",
        health_path="/readyz",
        dev_port=18789,
    ),
    RuntimeServiceEntry(
        service_id="runtime-hermes",
        runtime_id="hermes-default",
        profile="hermes",
        health_path="/health",
        dev_port=18642,
    ),
    RuntimeServiceEntry(
        service_id="runtime-deerflow",
        runtime_id="deerflow-default",
        profile="deerflow",
        health_path="/api/models",
        dev_port=12026,
    ),
)
_RUNTIME_SERVICE_BY_ID = {item.service_id: item for item in RUNTIME_SERVICES}


async def probe_runtime(runtime_id: str) -> RuntimeDefinition:
    runtime = runtime_registry.get(runtime_id)
    if runtime is None:
        raise KeyError(runtime_id)
    if runtime.kind == "legacy_native":
        return runtime_registry.set_status(runtime_id, "legacy")
    if not runtime.enabled or not runtime.base_url:
        return runtime_registry.set_status(runtime_id, "disabled")
    try:
        capabilities = await RUNTIME_ADAPTERS[runtime.kind].probe_capabilities(runtime)
    except (httpx.HTTPError, RuntimeAdapterError, ValueError):
        return runtime_registry.set_status(runtime_id, "stopped")
    return runtime_registry.set_status(runtime_id, "running", capabilities)


async def control_runtime_service(service_id: str, action: RuntimeServiceAction) -> JsonObject:
    entry = _RUNTIME_SERVICE_BY_ID.get(service_id)
    if entry is None:
        raise KeyError(service_id)
    settings = get_settings()
    if not settings.agent_runtime_service_control_enabled:
        raise RuntimeAdapterError(
            "runtime_service_control_disabled",
            "Runtime service の起動・停止は無効化されています。",
        )
    command = shlex.split(settings.agent_runtime_service_control_command) or ["docker", "compose"]
    args = [*command, "--profile", entry.profile]
    if action == "pull":
        args.extend(["pull", service_id])
    elif action == "start":
        args.extend(["up", "-d", "--no-build", service_id])
    elif action == "stop":
        args.extend(["stop", service_id])
    elif action == "restart":
        args.extend(["restart", service_id])
    else:
        args.extend(["rm", "-f", "-s", service_id])
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=_repo_root(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), settings.agent_runtime_service_control_timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise RuntimeAdapterError(
            "runtime_service.timeout",
            "Runtime service 操作が timeout しました。",
        ) from exc
    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", "replace").strip()
        raise RuntimeAdapterError("runtime_service.failed", detail[-2000:])
    return {"ok": True, "service_id": service_id, "action": action}


async def runtime_service_logs(service_id: str, lines: int = 200) -> JsonObject:
    entry = _RUNTIME_SERVICE_BY_ID.get(service_id)
    if entry is None:
        raise KeyError(service_id)
    settings = get_settings()
    if not settings.agent_runtime_service_control_enabled:
        raise RuntimeAdapterError(
            "runtime_service_control_disabled", "Runtime service のログ取得は無効化されています。"
        )
    command = shlex.split(settings.agent_runtime_service_control_command) or ["docker", "compose"]
    process = await asyncio.create_subprocess_exec(
        *command,
        "--profile",
        entry.profile,
        "logs",
        "--no-color",
        "--tail",
        str(max(1, min(lines, 1000))),
        service_id,
        cwd=_repo_root(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeAdapterError(
            "runtime_service.logs_failed", (stderr or stdout).decode("utf-8", "replace")[-2000:]
        )
    return {
        "service_id": service_id,
        "lines": max(1, min(lines, 1000)),
        "content": stdout.decode("utf-8", "replace"),
    }


def export_control_plane_state() -> JsonObject:
    return {"runtimes": runtime_registry.export(), "bindings": runtime_binding_registry.export()}


def import_control_plane_state(payload: JsonObject) -> None:
    runtimes = payload.get("runtimes", [])
    bindings = payload.get("bindings", [])
    if isinstance(runtimes, list):
        runtime_registry.replace([item for item in runtimes if isinstance(item, dict)])
    if isinstance(bindings, list):
        runtime_binding_registry.replace([item for item in bindings if isinstance(item, dict)])


def _builtin_runtimes() -> list[RuntimeDefinition]:
    return [
        RuntimeDefinition(
            id="legacy-native",
            name="Legacy Native (read-only)",
            kind="legacy_native",
            enabled=False,
            status="legacy",
        ),
        RuntimeDefinition(
            id="openclaw-default",
            name="OpenClaw",
            kind="openclaw",
            base_url="http://runtime-openclaw:18789",
            # secret 値ではなく参照する環境変数名。
            auth_secret_ref="OPENCLAW_GATEWAY_TOKEN",  # nosec B106
            managed_service_id="runtime-openclaw",
            capabilities=OpenClawAdapter.static_capabilities,
        ),
        RuntimeDefinition(
            id="hermes-default",
            name="Hermes",
            kind="hermes",
            base_url="http://runtime-hermes:8642",
            # secret 値ではなく参照する環境変数名。
            auth_secret_ref="HERMES_API_SERVER_KEY",  # nosec B106
            managed_service_id="runtime-hermes",
            capabilities=HermesAdapter.static_capabilities,
        ),
        RuntimeDefinition(
            id="deerflow-default",
            name="DeerFlow",
            kind="deerflow",
            base_url="http://runtime-deerflow:2026",
            # secret 値ではなく参照する環境変数名。
            auth_secret_ref="DEER_FLOW_INTERNAL_AUTH_TOKEN",  # nosec B106
            managed_service_id="runtime-deerflow",
            capabilities=DeerFlowAdapter.static_capabilities,
        ),
    ]


def _validate_id(value: str, label: str) -> None:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {label}")


def _runtime_timeout() -> float:
    return float(get_settings().agent_runtime_adapter_timeout_seconds)


def _runtime_headers(runtime: RuntimeDefinition) -> dict[str, str]:
    headers = {"accept": "application/json"}
    if runtime.auth_secret_ref:
        token = os.environ.get(runtime.auth_secret_ref, "").strip()
        if token:
            headers["authorization"] = f"Bearer {token}"
    return headers


def _openclaw_ws_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.startswith("https://"):
        return f"wss://{url[8:]}"
    if url.startswith("http://"):
        return f"ws://{url[7:]}"
    return url


async def _openclaw_rpc(
    runtime: RuntimeDefinition,
    method: str,
    params: JsonObject,
) -> JsonObject:
    token = os.environ.get(runtime.auth_secret_ref or "", "")
    timeout = _runtime_timeout()
    async with websocket_connect(
        _openclaw_ws_url(runtime.base_url),
        open_timeout=timeout,
        close_timeout=min(timeout, 5.0),
        max_size=25 * 1024 * 1024,
    ) as socket:
        try:
            first = json.loads(await asyncio.wait_for(socket.recv(), timeout))
        except (TimeoutError, ValueError, TypeError) as exc:
            raise RuntimeAdapterError(
                "runtime.invalid_response", "OpenClaw challenge が不正です。"
            ) from exc
        if not isinstance(first, dict) or first.get("event") != "connect.challenge":
            raise RuntimeAdapterError(
                "runtime.invalid_response", "OpenClaw connect.challenge がありません。"
            )
        connect_id = f"connect_{uuid4().hex}"
        await socket.send(
            json.dumps(
                {
                    "type": "req",
                    "id": connect_id,
                    "method": "connect",
                    "params": {
                        "minProtocol": 3,
                        "maxProtocol": 4,
                        "client": {
                            "id": "gateway-client",
                            "version": "2",
                            "platform": "linux",
                            "mode": "backend",
                        },
                        "role": "operator",
                        "scopes": [
                            "operator.read",
                            "operator.write",
                            "operator.approvals",
                        ],
                        "caps": [],
                        "commands": [],
                        "permissions": {},
                        "auth": {"token": token},
                        "locale": "ja-JP",
                        "userAgent": "production-ready-agent-control-plane/2",
                    },
                }
            )
        )
        hello = await _openclaw_response(socket, connect_id, timeout)
        if hello.get("type") != "hello-ok":
            raise RuntimeAdapterError(
                "runtime.authentication_failed", "OpenClaw handshake に失敗しました。"
            )
        request_id = f"rpc_{uuid4().hex}"
        await socket.send(
            json.dumps(
                {"type": "req", "id": request_id, "method": method, "params": params},
                ensure_ascii=False,
            )
        )
        return await _openclaw_response(socket, request_id, timeout)


async def _openclaw_response(socket: Any, request_id: str, timeout: float) -> JsonObject:
    while True:
        try:
            frame = json.loads(await asyncio.wait_for(socket.recv(), timeout))
        except (TimeoutError, ValueError, TypeError) as exc:
            raise RuntimeAdapterError(
                "runtime.invalid_response", "OpenClaw response が不正です。"
            ) from exc
        if not isinstance(frame, dict) or frame.get("type") != "res":
            continue
        if frame.get("id") != request_id:
            continue
        if not frame.get("ok"):
            error = frame.get("error", {})
            message = (
                error.get("message", "OpenClaw RPC failed")
                if isinstance(error, dict)
                else str(error)
            )
            raise RuntimeAdapterError("runtime.remote_error", str(message))
        payload = frame.get("payload", {})
        if not isinstance(payload, dict):
            raise RuntimeAdapterError(
                "runtime.invalid_response", "OpenClaw payload が object ではありません。"
            )
        return payload


def _submission_from(payload: JsonObject, fallback_id: str) -> RuntimeSubmission:
    external_id = str(
        payload.get("id")
        or payload.get("run_id")
        or payload.get("runId")
        or payload.get("thread_id")
        or fallback_id
    )
    return RuntimeSubmission(
        external_run_id=external_id,
        status=str(payload.get("status") or "queued"),
        external_cursor=(str(payload["cursor"]) if payload.get("cursor") is not None else None),
        output=payload,
    )


def _materialize_binding(
    runtime: RuntimeDefinition,
    binding: RuntimeBinding,
    agent: JsonObject,
    skills: Sequence[JsonObject],
    mcp_servers: Sequence[JsonObject],
) -> None:
    root = Path(get_settings().agent_runtime_bindings_dir).expanduser().resolve()
    target = root / binding.id
    staging = root / f".{binding.id}.{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        skill_root = staging / "skills"
        skill_root.mkdir(parents=True)
        skill_ids: list[str] = []
        for skill in skills:
            skill_id = str(skill.get("id", ""))
            _validate_id(skill_id, "skill id")
            skill_ids.append(skill_id)
            directory = skill_root / skill_id
            directory.mkdir()
            description = json.dumps(str(skill.get("description", "")), ensure_ascii=False)
            frontmatter = (
                "---\n"
                f"name: {json.dumps(skill_id, ensure_ascii=False)}\n"
                f"description: {description}\n"
                "---\n\n"
            )
            (directory / "SKILL.md").write_text(
                frontmatter + str(skill.get("instructions", "")), encoding="utf-8"
            )
        safe_mcp = [
            {
                "server_id": item.get("server_id"),
                "base_url": item.get("base_url"),
                "api_key_env": item.get("api_key_env")
                or f"MCP_{str(item.get('server_id', '')).upper().replace('-', '_')}_API_KEY",
            }
            for item in mcp_servers
        ]
        (staging / "binding.json").write_text(
            json.dumps(
                {
                    "runtime_kind": runtime.kind,
                    "native_agent_ref": binding.native_agent_ref,
                    "agent": agent,
                    "skill_allowlist": skill_ids,
                    "mcp_servers": safe_mcp,
                    "policy": binding.policy,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_runtime_fragment(staging, runtime.kind, binding, agent, skill_ids, safe_mcp)
        backup = root / f".{binding.id}.old"
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.rename(backup)
        staging.rename(target)
        if backup.exists():
            shutil.rmtree(backup)
        _publish_runtime_binding(root, runtime, binding, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _write_runtime_fragment(
    root: Path,
    kind: RuntimeKind,
    binding: RuntimeBinding,
    agent: JsonObject,
    skill_ids: list[str],
    mcp_servers: list[JsonObject],
) -> None:
    if kind == "openclaw":
        payload: JsonObject = {
            "agents": {
                "list": [
                    {
                        "id": binding.native_agent_ref,
                        "workspace": str(root),
                        "skills": skill_ids,
                    }
                ]
            },
            "mcpServers": mcp_servers,
        }
        (root / "openclaw.agent.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "hermes":
        (root / "SOUL.md").write_text(str(agent.get("instructions", "")), encoding="utf-8")
        (root / "hermes.config.json").write_text(
            json.dumps({"mcp_servers": mcp_servers}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif kind == "deerflow":
        agent_dir = root / "workspace" / "agents" / binding.native_agent_ref
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text(
            "skills:\n" + "".join(f"  - {skill_id}\n" for skill_id in skill_ids),
            encoding="utf-8",
        )


def _publish_runtime_binding(
    root: Path,
    runtime: RuntimeDefinition,
    binding: RuntimeBinding,
    source: Path,
) -> None:
    """Runtime volume から参照できる安定パスへ Binding を原子的に公開する。"""
    runtime_root = root / "runtimes" / runtime.id
    runtime_root.mkdir(parents=True, exist_ok=True)
    publish = runtime_root / binding.native_agent_ref
    staging = runtime_root / f".{binding.native_agent_ref}.{uuid4().hex}"
    try:
        if runtime.kind == "openclaw":
            shutil.copytree(source, staging)
        elif runtime.kind == "hermes":
            staging.mkdir()
            shutil.copy2(source / "SOUL.md", staging / "SOUL.md")
            shutil.copytree(source / "skills", staging / "skills")
            shutil.copy2(source / "hermes.config.json", staging / "control-plane-mcp.json")
        elif runtime.kind == "deerflow":
            agent_source = source / "workspace" / "agents" / binding.native_agent_ref
            shutil.copytree(agent_source, staging)
            shutil.copytree(source / "skills", staging / "skills")
        else:
            return
        backup = runtime_root / f".{binding.native_agent_ref}.old"
        if backup.exists():
            shutil.rmtree(backup)
        if publish.exists():
            publish.rename(backup)
        staging.rename(publish)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _remove_binding_materialization(binding: RuntimeBinding) -> None:
    root = Path(get_settings().agent_runtime_bindings_dir).expanduser().resolve()
    shutil.rmtree(root / binding.id, ignore_errors=True)
    shutil.rmtree(
        root / "runtimes" / binding.runtime_id / binding.native_agent_ref,
        ignore_errors=True,
    )


runtime_registry = RuntimeRegistry()
runtime_binding_registry = RuntimeBindingRegistry()
