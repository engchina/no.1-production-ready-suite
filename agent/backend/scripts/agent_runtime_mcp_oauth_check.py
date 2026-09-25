"""External MCP OAuth / JWKS integration validation for Agent Runtime.

`--dry-run` is safe for CI because it only reports resolved configuration.
Live mode can request an OAuth client-credentials token, call MCP `tools/list`,
and optionally fetch / compare JWKS key ids for gateway identity integration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from time import sleep
from typing import Any

import httpx

JsonObject = dict[str, Any]


def main() -> int:
    args = _parse_args()
    config = _config(args)
    if args.dry_run:
        return _print(
            {
                "ok": True,
                "dry_run": True,
                "mcp_base_url_configured": bool(config["mcp_base_url"]),
                "oauth_configured": _oauth_configured(config),
                "api_key_configured": bool(config["api_key"]),
                "session_configured": bool(config["session_id"]),
                "auth_mode": _auth_mode(config),
                "jwks_url_configured": bool(config["jwks_url"]),
                "tools_list": args.tools_list,
                "require_oauth": args.require_oauth,
                "require_jwks": args.require_jwks,
                "rotation_check": args.rotation_check,
                "rotation_interval_seconds": args.rotation_interval_seconds,
                "min_rotated_kids": args.min_rotated_kids,
            }
        )

    if args.require_oauth and not _oauth_configured(config):
        return _print_error(
            {
                "ok": False,
                "error": "missing_oauth_config",
                "message": "set AGENT_EXTERNAL_MCP_OAUTH_* or pass OAuth arguments",
            }
        )
    if args.require_jwks and not config["jwks_url"]:
        return _print_error(
            {
                "ok": False,
                "error": "missing_jwks_url",
                "message": "set AGENT_RBAC_JWT_JWKS_URL or pass --jwks-url",
            }
        )
    if args.tools_list and not config["mcp_base_url"]:
        return _print_error(
            {
                "ok": False,
                "error": "missing_mcp_base_url",
                "message": "set AGENT_EXTERNAL_MCP_BASE_URL or pass --mcp-base-url",
            }
        )

    summary: JsonObject = {
        "ok": True,
        "dry_run": False,
        "auth_mode": _auth_mode(config),
        "checks": {},
    }
    with httpx.Client(timeout=args.timeout_seconds) as client:
        token: str | None = None
        if _oauth_configured(config):
            oauth_check = _fetch_oauth_token(client, config)
            token = oauth_check.pop("_access_token")
            summary["checks"]["oauth_token"] = oauth_check
        if config["jwks_url"]:
            jwks_check = _fetch_jwks(client, config["jwks_url"])
            summary["checks"]["jwks"] = jwks_check
            if args.rotation_check:
                if args.rotation_interval_seconds > 0:
                    sleep(args.rotation_interval_seconds)
                second = _fetch_jwks(client, config["jwks_url"])
                rotation = _jwks_rotation_check(
                    jwks_check,
                    second,
                    min_rotated_kids=args.min_rotated_kids,
                )
                summary["checks"]["jwks_rotation"] = rotation
                if not rotation["ok"]:
                    summary["ok"] = False
        elif args.rotation_check:
            return _print_error(
                {
                    "ok": False,
                    "error": "missing_jwks_url",
                    "message": "rotation check requires AGENT_RBAC_JWT_JWKS_URL or --jwks-url",
                }
            )
        if args.tools_list:
            tools_check = _mcp_tools_list(client, config, oauth_token=token)
            summary["checks"]["tools_list"] = tools_check
    _print(summary)
    return 0 if summary["ok"] else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-base-url", default=os.getenv("AGENT_EXTERNAL_MCP_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.getenv("AGENT_EXTERNAL_MCP_API_KEY", ""))
    parser.add_argument("--session-id", default=os.getenv("AGENT_EXTERNAL_MCP_SESSION_ID", ""))
    parser.add_argument(
        "--oauth-token-url",
        default=os.getenv("AGENT_EXTERNAL_MCP_OAUTH_TOKEN_URL", ""),
    )
    parser.add_argument(
        "--oauth-client-id",
        default=os.getenv("AGENT_EXTERNAL_MCP_OAUTH_CLIENT_ID", ""),
    )
    parser.add_argument(
        "--oauth-client-secret",
        default=os.getenv("AGENT_EXTERNAL_MCP_OAUTH_CLIENT_SECRET", ""),
    )
    parser.add_argument("--oauth-scope", default=os.getenv("AGENT_EXTERNAL_MCP_OAUTH_SCOPE", ""))
    parser.add_argument("--jwks-url", default=os.getenv("AGENT_RBAC_JWT_JWKS_URL", ""))
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--tools-list", action="store_true")
    parser.add_argument("--server-id", default=os.getenv("AGENT_EXTERNAL_MCP_TEST_SERVER_ID", ""))
    parser.add_argument("--require-oauth", action="store_true")
    parser.add_argument("--require-jwks", action="store_true")
    parser.add_argument("--rotation-check", action="store_true")
    parser.add_argument("--rotation-interval-seconds", type=float, default=30.0)
    parser.add_argument("--min-rotated-kids", type=int, default=1)
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
        "mcp_base_url": args.mcp_base_url.rstrip("/"),
        "api_key": args.api_key,
        "session_id": args.session_id,
        "oauth_token_url": args.oauth_token_url,
        "oauth_client_id": args.oauth_client_id,
        "oauth_client_secret": args.oauth_client_secret,
        "oauth_scope": args.oauth_scope,
        "jwks_url": args.jwks_url,
        "server_id": args.server_id,
    }


def _oauth_configured(config: dict[str, str]) -> bool:
    return bool(
        config["oauth_token_url"] and config["oauth_client_id"] and config["oauth_client_secret"]
    )


def _auth_mode(config: dict[str, str]) -> str:
    if _oauth_configured(config):
        return "oauth_client_credentials"
    if config["api_key"]:
        return "api_key"
    return "none"


def _fetch_oauth_token(client: httpx.Client, config: dict[str, str]) -> JsonObject:
    response = client.post(
        config["oauth_token_url"],
        data={
            "grant_type": "client_credentials",
            **({"scope": config["oauth_scope"]} if config["oauth_scope"] else {}),
        },
        auth=(config["oauth_client_id"], config["oauth_client_secret"]),
    )
    response.raise_for_status()
    payload = response.json()
    access_token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("OAuth token response is missing access_token")
    expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
    token_type = payload.get("token_type") if isinstance(payload, dict) else None
    return {
        "ok": True,
        "token_type": token_type if isinstance(token_type, str) else "Bearer",
        "expires_in": expires_in if isinstance(expires_in, int | float) else None,
        "_access_token": access_token,
    }


def _fetch_jwks(client: httpx.Client, jwks_url: str) -> JsonObject:
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
    first: JsonObject,
    second: JsonObject,
    *,
    min_rotated_kids: int,
) -> JsonObject:
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


def _kid_set(jwks_check: JsonObject) -> set[str]:
    kids = jwks_check.get("kids", [])
    if not isinstance(kids, list):
        return set()
    return {kid for kid in kids if isinstance(kid, str) and kid}


def _mcp_tools_list(
    client: httpx.Client,
    config: dict[str, str],
    *,
    oauth_token: str | None,
) -> JsonObject:
    headers = _mcp_headers(config, oauth_token=oauth_token)
    params: JsonObject = {}
    if config["server_id"]:
        params["server_id"] = config["server_id"]
    response = client.post(
        config["mcp_base_url"],
        json={
            "jsonrpc": "2.0",
            "id": "agent-runtime-mcp-oauth-check",
            "method": "tools/list",
            "params": params,
        },
        headers=headers,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("result") if isinstance(payload, dict) else None
    tools = result.get("tools", []) if isinstance(result, dict) else []
    if not isinstance(tools, list):
        raise RuntimeError("MCP tools/list response must include result.tools[]")
    return {
        "ok": True,
        "tool_count": len(tools),
        "tool_names": [
            tool.get("name")
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        ],
        "session_header_sent": bool(config["session_id"]),
    }


def _mcp_headers(config: dict[str, str], *, oauth_token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if oauth_token:
        headers["Authorization"] = f"Bearer {oauth_token}"
    elif config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    if config["session_id"]:
        headers["Mcp-Session-Id"] = config["session_id"]
    return headers


def _print(payload: JsonObject) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _print_error(payload: JsonObject) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
