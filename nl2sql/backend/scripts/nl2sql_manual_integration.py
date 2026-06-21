"""Manual Oracle Select AI / Select AI Agent integration smoke test.

Run from `backend/`:

    uv run python scripts/nl2sql_manual_integration.py --require-oracle --refresh-assets

The script intentionally prints only non-secret summaries. Asset refresh is opt-in because it
creates/replaces Select AI / Select AI Agent objects in Oracle.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass

from app.features.nl2sql.models import (
    AllowedObjects,
    AssetCleanupData,
    AssetRefreshData,
    JobCreateRequest,
    JobData,
    JobStatus,
    Nl2SqlEngine,
    Nl2SqlProfile,
    PreviewData,
    PreviewRequest,
)
from app.features.nl2sql.service import nl2sql_service
from app.settings import get_settings

DEFAULT_QUESTION = "今月の請求金額が大きい取引先を表示して"


@dataclass(frozen=True)
class StepResult:
    name: str
    ok: bool
    message: str


def _parse_engines(value: str) -> list[Nl2SqlEngine]:
    engines: list[Nl2SqlEngine] = []
    for raw in value.split(","):
        normalized = raw.strip()
        if not normalized:
            continue
        try:
            engine = Nl2SqlEngine(normalized)
        except ValueError as exc:
            allowed = ", ".join(engine.value for engine in Nl2SqlEngine)
            raise argparse.ArgumentTypeError(
                f"Unknown engine '{normalized}'. Choose from: {allowed}"
            ) from exc
        if engine == Nl2SqlEngine.AUTO:
            raise argparse.ArgumentTypeError("Use concrete engines for manual integration.")
        engines.append(engine)
    if not engines:
        raise argparse.ArgumentTypeError("At least one engine is required.")
    return engines


def _parse_tables(values: list[str]) -> list[str]:
    tables: list[str] = []
    for value in values:
        tables.extend(part.strip().upper() for part in value.split(",") if part.strip())
    return tables


def _status_line(result: StepResult) -> str:
    prefix = "ok" if result.ok else "ng"
    return f"[{prefix}] {result.name}: {result.message}"


def _diagnostics(require_oracle: bool) -> StepResult:
    settings = get_settings()
    diagnostics = nl2sql_service.diagnostics()
    warnings = [check.name for check in diagnostics.checks if check.status != "ok"]
    runtime_mode = settings.nl2sql_runtime_mode.strip().lower()
    if require_oracle and runtime_mode != "oracle":
        return StepResult(
            name="diagnostics",
            ok=False,
            message=f"NL2SQL_RUNTIME_MODE must be oracle, current={runtime_mode or '-'}",
        )
    oracle_ready = next(
        (check for check in diagnostics.checks if check.name == "ORACLE_RUNTIME_READY"), None
    )
    if require_oracle and (oracle_ready is None or oracle_ready.status != "ok"):
        message = oracle_ready.message if oracle_ready else "ORACLE_RUNTIME_READY check missing."
        return StepResult(name="diagnostics", ok=False, message=message)
    warning_text = ",".join(warnings) if warnings else "none"
    return StepResult(
        name="diagnostics",
        ok=True,
        message=f"runtime={runtime_mode}; warnings={warning_text}",
    )


def _diagnostics_worker(require_oracle: bool, queue: mp.Queue) -> None:
    try:
        queue.put(_diagnostics(require_oracle))
    except Exception as exc:  # pragma: no cover - subprocess safety boundary
        queue.put(StepResult(name="diagnostics", ok=False, message=str(exc)))


def _diagnostics_with_timeout(require_oracle: bool, timeout_seconds: float) -> StepResult:
    if not require_oracle or timeout_seconds <= 0:
        return _diagnostics(require_oracle)
    method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    context = mp.get_context(method)
    queue: mp.Queue = context.Queue(maxsize=1)
    process = context.Process(target=_diagnostics_worker, args=(require_oracle, queue))
    process.daemon = True
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(2)
        return StepResult(
            name="diagnostics",
            ok=False,
            message=f"diagnostics timeout after {timeout_seconds:.1f}s",
        )
    if queue.empty():
        return StepResult(
            name="diagnostics", ok=False, message="diagnostics did not return a result"
        )
    return queue.get()


def _refresh_catalog(enabled: bool) -> StepResult:
    if not enabled:
        catalog = nl2sql_service.get_catalog()
        return StepResult(
            name="schema_catalog",
            ok=True,
            message=f"using cached catalog; tables={len(catalog.tables)}",
        )
    try:
        catalog = nl2sql_service.refresh_catalog()
    except Exception as exc:
        return StepResult(name="schema_catalog", ok=False, message=str(exc))
    return StepResult(
        name="schema_catalog",
        ok=True,
        message=f"refreshed; tables={len(catalog.tables)}; refreshed_at={catalog.refreshed_at}",
    )


def _prepare_profile(
    *, profile_id: str | None, allowed_tables: list[str], row_limit: int
) -> tuple[StepResult | None, str | None]:
    if not allowed_tables:
        return None, profile_id
    resolved_profile_id = profile_id or "manual_integration"
    profile = Nl2SqlProfile(
        id=resolved_profile_id,
        name="Manual integration",
        description="Manual Oracle Select AI / Agent integration profile",
        allowed_tables=allowed_tables,
        glossary={},
        sql_rules=["SELECT/WITH のみ", "Oracle runtime manual integration"],
        default_row_limit=row_limit,
        safety_policy="select_only",
        few_shot_examples=[],
        archived=False,
    )
    existing_ids = {item.id for item in nl2sql_service.list_profiles()}
    if resolved_profile_id in existing_ids:
        nl2sql_service.update_profile(resolved_profile_id, lambda _current: profile)
        action = "updated"
    else:
        nl2sql_service.create_profile(profile)
        action = "created"
    return (
        StepResult(
            name="manual_profile",
            ok=True,
            message=(
                f"{action}; profile_id={resolved_profile_id}; "
                f"tables={','.join(allowed_tables)}"
            ),
        ),
        resolved_profile_id,
    )


def _refresh_asset(engine: Nl2SqlEngine, profile_id: str | None) -> StepResult:
    try:
        if engine == Nl2SqlEngine.SELECT_AI:
            data = nl2sql_service.refresh_select_ai_profile(profile_id)
        elif engine == Nl2SqlEngine.SELECT_AI_AGENT:
            data = nl2sql_service.refresh_select_ai_agent_assets(profile_id)
        else:
            return StepResult(
                name=f"refresh_{engine.value}",
                ok=True,
                message="no Oracle asset refresh is required for this engine",
            )
    except Exception as exc:
        return StepResult(name=f"refresh_{engine.value}", ok=False, message=str(exc))
    return _asset_result(engine, data)


def _asset_result(engine: Nl2SqlEngine, data: AssetRefreshData) -> StepResult:
    asset_names = ", ".join(f"{kind}={name}" for kind, name in sorted(data.asset_names.items()))
    message = f"status={data.status}; refreshed={data.refreshed}; {asset_names or 'assets=-'}"
    if data.warning:
        message = f"{message}; warning={data.warning}"
    return StepResult(
        name=f"refresh_{engine.value}",
        ok=data.refreshed and data.status != "error",
        message=message,
    )


def _cleanup_assets(
    *, engines: list[Nl2SqlEngine], profile_id: str | None, confirm: bool
) -> list[StepResult]:
    cleanup_results = nl2sql_service.cleanup_select_ai_assets(
        profile_id=profile_id,
        engines=engines,
        execute=confirm,
    )
    return [_cleanup_result(data) for data in cleanup_results]


def _cleanup_result(data: AssetCleanupData) -> StepResult:
    asset_names = ", ".join(f"{kind}={name}" for kind, name in sorted(data.asset_names.items()))
    message = (
        f"status={data.status}; executed={data.executed}; "
        f"{asset_names or 'assets=-'}"
    )
    if data.warning:
        message = f"{message}; warning={data.warning}"
    return StepResult(
        name=f"cleanup_{data.engine.value}",
        ok=data.status != "error",
        message=message,
    )


def _preview(
    *,
    engine: Nl2SqlEngine,
    profile_id: str | None,
    question: str,
    allowed: AllowedObjects,
    row_limit: int,
    require_oracle: bool,
) -> tuple[StepResult, PreviewData | None]:
    try:
        data = nl2sql_service.preview(
            PreviewRequest(
                question=question,
                engine=engine,
                profile_id=profile_id,
                allowed_objects=allowed,
                row_limit=row_limit,
            )
        )
    except Exception as exc:
        return StepResult(name=f"preview_{engine.value}", ok=False, message=str(exc)), None
    runtime = str(data.engine_meta.get("runtime") or "deterministic")
    ok = data.is_safe
    if require_oracle and engine in {Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT}:
        ok = ok and runtime == "oracle" and not data.fallback_reason
    message = (
        f"engine={data.engine.value}; runtime={runtime}; safe={data.is_safe}; "
        f"row_limit={data.row_limit}; sql={_one_line(data.sql)}"
    )
    if data.fallback_reason:
        message = f"{message}; fallback={data.fallback_reason}"
    return StepResult(name=f"preview_{engine.value}", ok=ok, message=message), data


def _job(
    *,
    engine: Nl2SqlEngine,
    profile_id: str | None,
    question: str,
    allowed: AllowedObjects,
    row_limit: int,
    timeout_seconds: float,
    require_oracle: bool,
) -> StepResult:
    try:
        created = nl2sql_service.start_job(
            JobCreateRequest(
                question=question,
                engine=engine,
                profile_id=profile_id,
                allowed_objects=allowed,
                row_limit=row_limit,
            )
        )
        data = _wait_for_job(created.job_id, timeout_seconds)
    except Exception as exc:
        return StepResult(name=f"job_{engine.value}", ok=False, message=str(exc))
    if data is None:
        return StepResult(
            name=f"job_{engine.value}",
            ok=False,
            message=f"timeout after {timeout_seconds:.1f}s",
        )
    result_runtime = (
        str(data.result.engine_meta.get("runtime") or "deterministic")
        if data.result
        else "-"
    )
    ok = data.status == JobStatus.DONE and data.result is not None and data.result.safety.is_safe
    if require_oracle and engine in {Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT}:
        ok = ok and result_runtime == "oracle" and not (data.result and data.result.fallback_reason)
    message = (
        f"status={data.status.value}; runtime={result_runtime}; "
        f"elapsed_ms={data.elapsed_ms}; rows={data.result.results.total if data.result else 0}"
    )
    if data.error_message:
        message = f"{message}; error={data.error_message}"
    if data.result and data.result.fallback_reason:
        message = f"{message}; fallback={data.result.fallback_reason}"
    return StepResult(name=f"job_{engine.value}", ok=ok, message=message)


def _wait_for_job(job_id: str, timeout_seconds: float) -> JobData | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        data = nl2sql_service.get_job(job_id)
        if data and data.status in {JobStatus.DONE, JobStatus.ERROR}:
            return data
        time.sleep(0.25)
    return nl2sql_service.get_job(job_id)


def _one_line(value: str, max_len: int = 180) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= max_len else f"{compact[: max_len - 3]}..."


def _print_results(results: Iterable[StepResult]) -> int:
    failed = False
    for result in results:
        print(_status_line(result))
        failed = failed or not result.ok
    return 1 if failed else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manual NL2SQL Select AI / Select AI Agent integration smoke test.",
    )
    parser.add_argument("--profile-id", default=None, help="NL2SQL profile id. Default: default")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="Natural language question.")
    parser.add_argument(
        "--engines",
        type=_parse_engines,
        default=_parse_engines("select_ai,select_ai_agent"),
        help="Comma-separated concrete engines. Default: select_ai,select_ai_agent",
    )
    parser.add_argument(
        "--allowed-table",
        action="append",
        default=[],
        help="Allowed table name. Repeat or pass comma-separated values.",
    )
    parser.add_argument("--row-limit", type=int, default=20, help="Row limit for preview/job.")
    parser.add_argument(
        "--refresh-catalog",
        action="store_true",
        help="Refresh schema catalog from Oracle before running checks.",
    )
    parser.add_argument(
        "--refresh-assets",
        action="store_true",
        help="Create/replace Select AI profile and Select AI Agent assets. Mutates Oracle.",
    )
    parser.add_argument(
        "--cleanup-assets",
        action="store_true",
        help=(
            "List Select AI / Agent assets that would be dropped. "
            "Use --confirm-cleanup to execute. Cleanup mode exits before preview."
        ),
    )
    parser.add_argument(
        "--confirm-cleanup",
        action="store_true",
        help="Actually drop assets listed by --cleanup-assets. Mutates Oracle.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run async NL2SQL jobs and execute generated SELECT SQL. Mutates history.",
    )
    parser.add_argument(
        "--require-oracle",
        action="store_true",
        help=(
            "Fail if runtime is not Oracle or if an Oracle engine falls back to "
            "deterministic mode."
        ),
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Job polling timeout seconds.")
    parser.add_argument(
        "--diagnostics-timeout",
        type=float,
        default=8.0,
        help="Oracle diagnostics outer timeout seconds. Use 0 to disable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.confirm_cleanup and not args.cleanup_assets:
        parser.error("--confirm-cleanup requires --cleanup-assets")
    allowed = AllowedObjects(table_names=_parse_tables(args.allowed_table), columns={})
    if args.cleanup_assets and not args.confirm_cleanup and not args.require_oracle:
        return _print_results(
            _cleanup_assets(
                engines=args.engines,
                profile_id=args.profile_id,
                confirm=False,
            )
        )
    diagnostics_result = _diagnostics_with_timeout(
        args.require_oracle, args.diagnostics_timeout
    )
    results: list[StepResult] = [diagnostics_result]
    if args.require_oracle and not diagnostics_result.ok:
        return _print_results(results)

    results.append(_refresh_catalog(args.refresh_catalog))
    profile_result, profile_id = _prepare_profile(
        profile_id=args.profile_id,
        allowed_tables=allowed.table_names,
        row_limit=args.row_limit,
    )
    if profile_result:
        results.append(profile_result)

    if args.cleanup_assets:
        results.extend(
            _cleanup_assets(
                engines=args.engines,
                profile_id=profile_id,
                confirm=args.confirm_cleanup,
            )
        )
        return _print_results(results)

    if args.refresh_assets:
        for engine in args.engines:
            results.append(_refresh_asset(engine, profile_id))

    for engine in args.engines:
        result, _preview_data = _preview(
            engine=engine,
            profile_id=profile_id,
            question=args.question,
            allowed=allowed,
            row_limit=args.row_limit,
            require_oracle=args.require_oracle,
        )
        results.append(result)

    if args.execute:
        for engine in args.engines:
            results.append(
                _job(
                    engine=engine,
                    profile_id=profile_id,
                    question=args.question,
                    allowed=allowed,
                    row_limit=args.row_limit,
                    timeout_seconds=args.timeout,
                    require_oracle=args.require_oracle,
                )
            )

    return _print_results(results)


if __name__ == "__main__":
    sys.exit(main())
