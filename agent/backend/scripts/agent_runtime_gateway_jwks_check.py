"""Gateway / JWKS / RBAC integration validation for Agent Runtime.

The script is safe by default: `--dry-run` only reports resolved configuration.
Live mode can fetch JWKS, probe an external RBAC policy service, and optionally
call the backend with a supplied Bearer token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from time import sleep
from typing import Any
from uuid import uuid4

import httpx


def main() -> int:
    args = _parse_args()
    config = _config(args)
    if args.dry_run:
        return _print(
            {
                "ok": True,
                "dry_run": True,
                "backend_url": config["backend_url"],
                "jwks_url_configured": bool(config["jwks_url"]),
                "policy_url_configured": bool(config["policy_url"]),
                "sample_token_configured": bool(config["sample_token"]),
                "create_run": args.create_run,
                "rotation_check": args.rotation_check,
                "rotation_interval_seconds": args.rotation_interval_seconds,
                "min_rotated_kids": args.min_rotated_kids,
            }
        )

    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": False,
        "backend_url": config["backend_url"],
        "checks": {},
    }
    with httpx.Client(timeout=args.timeout_seconds) as client:
        jwks_check: dict[str, Any] | None = None
        if config["jwks_url"]:
            jwks_check = _fetch_jwks(client, config["jwks_url"])
            summary["checks"]["jwks"] = jwks_check
        if args.rotation_check:
            if not config["jwks_url"]:
                return _print_error(
                    {
                        "ok": False,
                        "error": "missing_jwks_url",
                        "message": "set AGENT_RBAC_JWT_JWKS_URL or pass --jwks-url",
                    }
                )
            first = jwks_check or _fetch_jwks(client, config["jwks_url"])
            if args.rotation_interval_seconds > 0:
                sleep(args.rotation_interval_seconds)
            second = _fetch_jwks(client, config["jwks_url"])
            rotation = _jwks_rotation_check(
                first,
                second,
                min_rotated_kids=args.min_rotated_kids,
            )
            summary["checks"]["jwks_rotation"] = rotation
            if not rotation["ok"]:
                summary["ok"] = False
        if config["policy_url"]:
            summary["checks"]["policy"] = _probe_policy_service(
                client,
                policy_url=config["policy_url"],
                policy_api_key=config["policy_api_key"],
                actor=args.actor,
            )
        if args.backend_status:
            summary["checks"]["backend_status"] = _backend_status(
                client,
                backend_url=config["backend_url"],
                sample_token=config["sample_token"],
            )
        if args.create_run:
            if not config["sample_token"]:
                return _print_error(
                    {
                        "ok": False,
                        "error": "missing_sample_token",
                        "message": "set AGENT_RBAC_JWT_SAMPLE_TOKEN or pass --sample-token",
                    }
                )
            summary["checks"]["create_run"] = _create_validation_run(
                client,
                backend_url=config["backend_url"],
                sample_token=config["sample_token"],
                business_view_id=args.business_view_id,
            )
    _print(summary)
    return 0 if summary["ok"] else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-url", default=os.getenv("AGENT_RUNTIME_BASE_URL", "http://localhost:8000")
    )
    parser.add_argument("--jwks-url", default=os.getenv("AGENT_RBAC_JWT_JWKS_URL", ""))
    parser.add_argument("--policy-url", default=os.getenv("AGENT_RBAC_POLICY_URL", ""))
    parser.add_argument("--policy-api-key", default=os.getenv("AGENT_RBAC_POLICY_API_KEY", ""))
    parser.add_argument("--sample-token", default=os.getenv("AGENT_RBAC_JWT_SAMPLE_TOKEN", ""))
    parser.add_argument("--actor", default=os.getenv("AGENT_RBAC_TEST_ACTOR", "gateway-check"))
    parser.add_argument(
        "--business-view-id",
        default=os.getenv("AGENT_RBAC_TEST_BUSINESS_VIEW_ID", "gateway-check"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--rotation-check", action="store_true")
    parser.add_argument("--rotation-interval-seconds", type=float, default=30.0)
    parser.add_argument("--min-rotated-kids", type=int, default=1)
    parser.add_argument("--backend-status", action="store_true")
    parser.add_argument("--create-run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than 0")
    if args.rotation_interval_seconds < 0:
        parser.error("--rotation-interval-seconds must be greater than or equal to 0")
    if args.min_rotated_kids < 0:
        parser.error("--min-rotated-kids must be greater than or equal to 0")
    return args


def _config(args: argparse.Namespace) -> dict[str, str]:
    return {
        "backend_url": args.backend_url.rstrip("/"),
        "jwks_url": args.jwks_url,
        "policy_url": args.policy_url,
        "policy_api_key": args.policy_api_key,
        "sample_token": args.sample_token,
    }


def _fetch_jwks(client: httpx.Client, jwks_url: str) -> dict[str, Any]:
    response = client.get(jwks_url)
    response.raise_for_status()
    payload = response.json()
    keys = payload.get("keys") if isinstance(payload, dict) else None
    if not isinstance(keys, list):
        raise RuntimeError("JWKS response must include keys[]")
    return {
        "ok": True,
        "key_count": len(keys),
        "kids": [key.get("kid") for key in keys if isinstance(key, dict) and key.get("kid")],
    }


def _jwks_rotation_check(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    min_rotated_kids: int,
) -> dict[str, Any]:
    first_kids = _kid_set(first)
    second_kids = _kid_set(second)
    added = sorted(second_kids - first_kids)
    removed = sorted(first_kids - second_kids)
    rotated_count = len(set(added) | set(removed))
    return {
        "ok": rotated_count >= min_rotated_kids,
        "first_key_count": first.get("key_count", len(first_kids)),
        "second_key_count": second.get("key_count", len(second_kids)),
        "added_kids": added,
        "removed_kids": removed,
        "common_kids": sorted(first_kids & second_kids),
        "rotated_count": rotated_count,
        "min_rotated_kids": min_rotated_kids,
    }


def _kid_set(jwks_check: dict[str, Any]) -> set[str]:
    kids = jwks_check.get("kids", [])
    if not isinstance(kids, list):
        return set()
    return {kid for kid in kids if isinstance(kid, str) and kid}


def _probe_policy_service(
    client: httpx.Client,
    *,
    policy_url: str,
    policy_api_key: str,
    actor: str,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {policy_api_key}"} if policy_api_key else {}
    response = client.post(policy_url, json={"actor": actor, "claims": {}}, headers=headers)
    response.raise_for_status()
    payload = response.json()
    policy = payload.get("policy", payload) if isinstance(payload, dict) else None
    roles = policy.get("roles", []) if isinstance(policy, dict) else []
    return {"ok": True, "actor": actor, "roles_count": len(roles) if isinstance(roles, list) else 0}


def _backend_status(
    client: httpx.Client,
    *,
    backend_url: str,
    sample_token: str,
) -> dict[str, Any]:
    headers = _bearer_headers(sample_token)
    response = client.get(f"{backend_url}/api/observability/status", headers=headers)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    trace_exporter_configured = (
        data.get("trace_exporter_configured") if isinstance(data, dict) else None
    )
    return {"ok": True, "trace_exporter_configured": trace_exporter_configured}


def _create_validation_run(
    client: httpx.Client,
    *,
    backend_url: str,
    sample_token: str,
    business_view_id: str,
) -> dict[str, Any]:
    response = client.post(
        f"{backend_url}/api/runs",
        headers=_bearer_headers(sample_token),
        json={
            "goal": f"Gateway/JWKS validation {uuid4().hex}",
            "metadata": {"business_view_id": business_view_id, "source": "gateway_jwks_check"},
        },
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    return {"ok": True, "run_id": data.get("id"), "status": data.get("status")}


def _bearer_headers(sample_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {sample_token}"} if sample_token else {}


def _print(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _print_error(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
