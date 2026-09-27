"""Agent の認証/RBAC のテーブルを冪等に作る（#215）。

    uv run python -m app.cli.agent_security_migrate

共通 `.env`（platform/.env）の `PLATFORM_ORACLE_*` で接続し、3 製品共通の `PLATFORM_*`、
Agent の `AGENT_ROLE_*`、組み込み SYSTEM_ADMIN ロールを用意する。何度実行してもよい。
ユーザーは作らない（最初は構成管理者 `system_admin` でログインする）。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Any

from pr_system_settings.auth.migrations import PLATFORM_AUTH_DDL

from app.oracle_connection import platform_oracle_connection
from app.security.migrations import AGENT_SECURITY_DDL, apply_security_schema


def _summary(results: Sequence[dict[str, str]]) -> str:
    applied = sum(1 for item in results if item.get("status") == "ok")
    skipped = sum(1 for item in results if item.get("status") == "skipped")
    return f"applied={applied} skipped={skipped}"


def run(
    connection_factory: Callable[[], AbstractContextManager[Any]] | None = None,
    *,
    apply: bool = True,
) -> str:
    """migration を適用し、結果の 1 行を返す（`apply=False` は件数だけ）。

    `connection_factory` を省略すると共通 `.env` の `PLATFORM_ORACLE_*` で接続する。
    """
    if not apply:
        return (
            f"mode=preview platform_statements={len(PLATFORM_AUTH_DDL)} "
            f"agent_statements={len(AGENT_SECURITY_DDL)}"
        )
    factory = connection_factory or platform_oracle_connection
    with factory() as connection:
        results = apply_security_schema(connection)
    return (
        f"mode=applied platform({_summary(results['platform'])}) "
        f"agent({_summary(results['agent'])}) system_admin_role=ok"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Oracle に接続せず、適用する DDL の件数だけを表示する",
    )
    args = parser.parse_args(argv)
    try:
        print(run(apply=not args.dry_run))
    except Exception as exc:  # 接続・DDL の失敗は理由を表示して非 0 で終わる
        print(f"agent_security_migrate failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
