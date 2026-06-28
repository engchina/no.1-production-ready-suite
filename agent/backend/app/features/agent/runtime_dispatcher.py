"""外部 Runtime Run を claim/lease で配送する専用 worker。"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from uuid import uuid4

import httpx

from app.features.agent.control_plane import (
    RUNTIME_ADAPTERS,
    RuntimeAdapterError,
    RuntimeCapabilityUnsupported,
    runtime_binding_registry,
    runtime_registry,
)
from app.features.agent.runtime import runtime_repository
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def dispatch_once(worker_id: str) -> bool:
    settings = get_settings()
    run = runtime_repository.claim_control_plane_run(
        worker_id,
        lease_seconds=settings.agent_runtime_dispatch_lease_seconds,
    )
    if run is None:
        return False
    binding = runtime_binding_registry.get(run.binding_id or "")
    runtime = runtime_registry.get(run.runtime_id)
    agent = next(
        (item for item in runtime_repository.list_agents() if item.id == run.agent_id),
        None,
    )
    if binding is None or runtime is None or agent is None:
        runtime_repository.mark_runtime_failed(
            run.id,
            code="runtime.dispatch_configuration_missing",
            detail="Runtime / Binding / Agent の復元に失敗しました。",
        )
        return True
    try:
        adapter = RUNTIME_ADAPTERS[runtime.kind]
        submission = await adapter.submit_run(
            runtime,
            binding,
            goal=run.goal,
            instructions=agent.instructions,
            control_plane_run_id=run.id,
        )
        runtime_repository.mark_runtime_submitted(
            run.id,
            external_run_id=submission.external_run_id,
            external_cursor=submission.external_cursor,
            external_status=submission.status,
        )
    except RuntimeAdapterError as exc:
        runtime_repository.mark_runtime_failed(run.id, code=exc.code, detail=str(exc))
        return True
    except (httpx.HTTPError, ValueError) as exc:
        runtime_repository.mark_runtime_failed(
            run.id,
            code="runtime.unavailable",
            detail=str(exc),
        )
        return True

    if runtime.capabilities.stream_events:
        try:
            async for event in adapter.follow_events(
                runtime,
                submission.external_run_id,
                submission.external_cursor,
            ):
                cursor = _event_cursor(event)
                runtime_repository.record_runtime_event(run.id, event, cursor=cursor)
                event_status = _status_from_payload(event)
                if event_status:
                    runtime_repository.reconcile_runtime_status(
                        run.id,
                        external_status=event_status,
                        payload=event,
                    )
        except (RuntimeAdapterError, httpx.HTTPError, ValueError) as exc:
            logger.warning("runtime event stream interrupted for %s: %s", run.id, exc)

    try:
        status_payload = await adapter.get_status(runtime, submission.external_run_id)
        external_status = _status_from_payload(status_payload)
        if external_status:
            runtime_repository.reconcile_runtime_status(
                run.id,
                external_status=external_status,
                payload=status_payload,
            )
    except RuntimeCapabilityUnsupported:
        pass
    except (RuntimeAdapterError, httpx.HTTPError, ValueError) as exc:
        logger.warning("runtime status reconciliation failed for %s: %s", run.id, exc)

    if runtime.capabilities.artifacts:
        try:
            artifacts = await adapter.list_artifacts(runtime, submission.external_run_id)
            runtime_repository.replace_runtime_artifacts(run.id, artifacts)
        except (RuntimeAdapterError, httpx.HTTPError, ValueError) as exc:
            logger.warning("runtime artifact reconciliation failed for %s: %s", run.id, exc)
    return True


def _event_cursor(payload: dict[str, object]) -> str | None:
    for key in ("cursor", "event_id", "eventId", "id"):
        value = payload.get(key)
        if isinstance(value, str | int):
            return str(value)
    return None


def _status_from_payload(payload: dict[str, object]) -> str | None:
    for key in ("status", "state", "run_status", "runStatus"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = value.get("status")
            if isinstance(nested, str):
                return nested
    for key in ("data", "run", "values"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested = _status_from_payload(value)
            if nested:
                return nested
    return None


async def run_forever() -> None:
    settings = get_settings()
    worker_id = os.environ.get(
        "AGENT_RUNTIME_DISPATCHER_ID",
        f"{socket.gethostname()}-{uuid4().hex[:8]}",
    )
    poll_seconds = max(0.1, settings.agent_runtime_dispatch_poll_seconds)
    logger.info("runtime-dispatcher started: %s", worker_id)
    while True:
        claimed = await dispatch_once(worker_id)
        if not claimed:
            await asyncio.sleep(poll_seconds)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
