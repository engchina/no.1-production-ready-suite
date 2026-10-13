"""NL2SQL system table の状態・確認・初期化・全再作成 CLI。

`--check` は配備の最後の確認（platform/deploy/suite-health.sh。#1648）が使う。DDL を実行せずに、
system table が ready で、`app_security_migrate --apply` が作る認証・権限の表がそろっているかを
読み、そろっていれば 0、そうでなければ（読めないときも）1 で終わり、1 行の要約を標準出力に出す。
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from pr_backend_core.logging import configure_cli_logging
from pr_system_settings.system_schema import (
    deployment_check_error_summary,
    deployment_check_summary,
    oracle_error_code,
)

from app.features.settings.system_schema import SystemSchemaError, system_schema_manager
from app.features.settings.system_schema_runtime import reset_system_schema_runtime


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    configure_cli_logging("nl2sql")
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--status", action="store_true", help="read-only status")
    actions.add_argument(
        "--check",
        action="store_true",
        help="exit 0 only when system and security tables are ready (deployment check)",
    )
    actions.add_argument("--initialize", action="store_true", help="create or migrate")
    actions.add_argument("--recreate", action="store_true", help="drop core allowlist and recreate")
    parser.add_argument("--confirmation", default=None, help="required confirmation for --recreate")
    args = parser.parse_args()
    if args.check:
        return _check()

    try:
        if args.status:
            result = system_schema_manager.status()
        else:
            result = system_schema_manager.initialize(
                recreate=bool(args.recreate),
                confirmation=args.confirmation,
            )
            reset_system_schema_runtime(schema_epoch=int(result["operation_state"]["schema_epoch"]))
    except SystemSchemaError as exc:
        print(  # noqa: T201
            _stable_json(
                {
                    "error": {"code": exc.code, "message": exc.public_message},
                    "ok": False,
                }
            )
        )
        return 2
    except Exception as exc:
        print(  # noqa: T201
            _stable_json(
                {
                    "error": {
                        # 権限の不足などは ORA コードを出す（配備の log から辿れる。#1648）。
                        "code": oracle_error_code(exc),
                        "message": "システムテーブル操作に失敗しました。",
                    },
                    "ok": False,
                }
            )
        )
        return 1

    print(_stable_json({"data": result, "ok": True}))  # noqa: T201
    return 0


def _check() -> int:
    from app.cli.app_security_migrate import missing_security_tables
    from app.clients.oracle_runtime import get_oracle_pool_manager

    try:
        ok, summary = deployment_check_summary(system_schema_manager.status())
        with get_oracle_pool_manager().control_connection() as connection:
            missing = missing_security_tables(connection)
    except Exception as exc:
        print(deployment_check_error_summary(exc))  # noqa: T201
        return 1
    if missing:
        ok = False
        shown = ", ".join(missing[:5]) + (", …" if len(missing) > 5 else "")
        summary = (
            f"{summary}・認証・権限の表が不足 {len(missing)} 件（{shown}。"
            "app_security_migrate --apply --skip-bootstrap で作成）"
        )
    print(summary)  # noqa: T201
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
