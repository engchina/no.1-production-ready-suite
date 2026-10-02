"""Agent のシステムテーブルの状態・作成 / 更新・全再作成の CLI（#751。NL2SQL と同じ形）。

    uv run python -m app.cli.agent_system_schema --status
    uv run python -m app.cli.agent_system_schema --initialize [--allow-destructive]
    uv run python -m app.cli.agent_system_schema --recreate \
        --confirmation RECREATE_AGENT_SYSTEM_TABLES

共通 `.env`（platform/.env）の `PLATFORM_ORACLE_*` で接続する。作成・更新は、3 製品共通の
`PLATFORM_*` と組み込み SYSTEM_ADMIN ロールを先に冪等に用意してから、`AGENT_*` の不足分と
未適用の migration を当てる。データを消す migration は `--allow-destructive` が無ければ止める
（画面の確認ダイアログと同じ。#619）。何度実行してもよい。ユーザーは作らない。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from app.system_schema import SystemSchemaError, system_schema_manager


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--status", action="store_true", help="状態だけを読む（DDL なし）")
    actions.add_argument("--initialize", action="store_true", help="作成・更新する")
    actions.add_argument("--recreate", action="store_true", help="管理対象を削除して作り直す")
    parser.add_argument("--confirmation", default=None, help="--recreate の確認語")
    parser.add_argument(
        "--allow-destructive",
        action="store_true",
        help="データを消す未適用の migration を当てることを承認する",
    )
    args = parser.parse_args(argv)

    try:
        if args.status:
            result = system_schema_manager.status()
        else:
            result = system_schema_manager.initialize(
                recreate=bool(args.recreate),
                confirmation=args.confirmation,
                allow_destructive=bool(args.allow_destructive),
            )
    except SystemSchemaError as exc:
        print(  # noqa: T201
            _stable_json({"error": {"code": exc.code, "message": exc.public_message}, "ok": False})
        )
        return 2
    except Exception as exc:  # 接続・DDL の失敗は SQL や資格情報を出さずに非 0 で終わる
        print(  # noqa: T201
            _stable_json(
                {
                    "error": {
                        "code": "SCHEMA_OPERATION_FAILED",
                        "message": f"システムテーブル操作に失敗しました（{type(exc).__name__}）。",
                    },
                    "ok": False,
                }
            )
        )
        return 1
    print(_stable_json({"data": result, "ok": True}))  # noqa: T201
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
