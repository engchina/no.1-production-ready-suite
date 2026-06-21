"""Oracle normalized Agent Runtime load validation.

This script is intentionally small and dependency-light: it reuses the runtime
repository and prints a JSON summary suitable for CI logs or manual DBA review.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from time import perf_counter
from uuid import uuid4

from app.features.agent.runtime import AgentRuntimeOracleNormalizedRepository, RunCreateRequest
from app.features.agent.tools import ToolCall


def main() -> int:
    args = _parse_args()
    config = _oracle_config_from_env()
    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "configured": _configured(config),
                    "runs": args.runs,
                    "audit_iterations": args.audit_iterations,
                    "audit_limit": args.audit_limit,
                    "projection_prefix": args.projection_prefix,
                    "write_mode": args.write_mode,
                    "retention_days": args.retention_days,
                    "sla_write_ms": args.sla_write_ms,
                    "sla_audit_p95_ms": args.sla_audit_p95_ms,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    missing = [key for key, value in config.items() if not value]
    if missing:
        print(
            json.dumps(
                {"ok": False, "error": "missing_oracle_env", "missing": missing},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    repository = AgentRuntimeOracleNormalizedRepository(
        dsn=config["dsn"],
        user=config["user"],
        password=config["password"],
        table_name=args.table_name,
        checkpoint_key=args.checkpoint_key,
        projection_prefix=args.projection_prefix,
        projection_retention_days=args.retention_days,
        projection_write_mode=args.write_mode,
        create_schema=not args.skip_schema_create,
    )
    marker = f"load-{uuid4().hex}"
    started = perf_counter()
    write_latencies_ms: list[int] = []
    for index in range(args.runs):
        run_started = perf_counter()
        repository.create_run(
            RunCreateRequest(
                goal=f"Oracle load validation {marker} #{index}",
                metadata={"business_view_id": marker, "load_marker": marker},
                tool_calls=[
                    ToolCall(
                        name="echo",
                        arguments={"load_marker": marker, "index": index},
                        trace_id=f"{marker}-{index}",
                    )
                ],
            )
        )
        write_latencies_ms.append(round((perf_counter() - run_started) * 1000))
    write_duration_ms = round((perf_counter() - started) * 1000)

    audit_total = 0
    audit_latencies_ms: list[int] = []
    for _ in range(args.audit_iterations):
        audit_started = perf_counter()
        audit = repository.list_tool_call_audit_projection(
            tool_name="echo",
            business_view_ids={marker},
            limit=min(args.runs, args.audit_limit),
        )
        audit_latencies_ms.append(round((perf_counter() - audit_started) * 1000))
        audit_total = audit.total
    violations = _sla_violations(
        write_duration_ms=write_duration_ms,
        audit_p95_ms=_percentile(audit_latencies_ms, 95),
        sla_write_ms=args.sla_write_ms,
        sla_audit_p95_ms=args.sla_audit_p95_ms,
    )
    ok = not violations
    print(
        json.dumps(
            {
                "ok": ok,
                "dry_run": False,
                "runs": args.runs,
                "audit_iterations": args.audit_iterations,
                "audit_limit": args.audit_limit,
                "audit_total": audit_total,
                "write_duration_ms": write_duration_ms,
                "write_p50_ms": _percentile(write_latencies_ms, 50),
                "write_p95_ms": _percentile(write_latencies_ms, 95),
                "write_max_ms": max(write_latencies_ms),
                "audit_duration_ms": audit_latencies_ms[-1],
                "audit_p50_ms": _percentile(audit_latencies_ms, 50),
                "audit_p95_ms": _percentile(audit_latencies_ms, 95),
                "audit_max_ms": max(audit_latencies_ms),
                "projection_prefix": args.projection_prefix,
                "write_mode": args.write_mode,
                "retention_days": args.retention_days,
                "marker": marker,
                "violations": violations,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if ok else 3


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    parser.add_argument("--audit-iterations", type=int, default=5)
    parser.add_argument("--audit-limit", type=int, default=100)
    parser.add_argument(
        "--table-name",
        default=os.getenv("AGENT_RUNTIME_ORACLE_TABLE", "AGENT_RUNTIME_CHECKPOINTS"),
    )
    parser.add_argument(
        "--checkpoint-key",
        default=os.getenv("AGENT_RUNTIME_ORACLE_CHECKPOINT_KEY", "load-check"),
    )
    parser.add_argument(
        "--projection-prefix",
        default=os.getenv("AGENT_RUNTIME_ORACLE_PROJECTION_PREFIX", "AGENT_RUNTIME"),
    )
    parser.add_argument(
        "--write-mode",
        choices=["replace", "incremental"],
        default=os.getenv("AGENT_RUNTIME_ORACLE_PROJECTION_WRITE_MODE", "incremental"),
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.getenv("AGENT_RUNTIME_ORACLE_PROJECTION_RETENTION_DAYS", "0")),
    )
    parser.add_argument("--sla-write-ms", type=int, default=0)
    parser.add_argument("--sla-audit-p95-ms", type=int, default=0)
    parser.add_argument("--skip-schema-create", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be greater than or equal to 1")
    if args.audit_iterations < 1:
        parser.error("--audit-iterations must be greater than or equal to 1")
    if args.audit_limit < 1:
        parser.error("--audit-limit must be greater than or equal to 1")
    if args.sla_write_ms < 0:
        parser.error("--sla-write-ms must be greater than or equal to 0")
    if args.sla_audit_p95_ms < 0:
        parser.error("--sla-audit-p95-ms must be greater than or equal to 0")
    return args


def _oracle_config_from_env() -> dict[str, str]:
    return {
        "dsn": os.getenv("AGENT_RUNTIME_ORACLE_DSN", ""),
        "user": os.getenv("AGENT_RUNTIME_ORACLE_USER", ""),
        "password": os.getenv("AGENT_RUNTIME_ORACLE_PASSWORD", ""),
    }


def _configured(config: dict[str, str]) -> bool:
    return all(config.values())


def _percentile(values: Sequence[int], percentile: int) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    index = round((len(sorted_values) - 1) * (percentile / 100))
    return sorted_values[index]


def _sla_violations(
    *,
    write_duration_ms: int,
    audit_p95_ms: int,
    sla_write_ms: int,
    sla_audit_p95_ms: int,
) -> list[str]:
    violations: list[str] = []
    if sla_write_ms and write_duration_ms > sla_write_ms:
        violations.append("write_duration_ms")
    if sla_audit_p95_ms and audit_p95_ms > sla_audit_p95_ms:
        violations.append("audit_p95_ms")
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
