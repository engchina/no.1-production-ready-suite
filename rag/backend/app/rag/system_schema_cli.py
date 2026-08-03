"""RAG system schema の status / initialize / recreate CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from app.rag.system_schema import (
    RECREATE_CONFIRMATION,
    SystemSchemaError,
    system_schema_manager,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG Oracle system schema を管理します。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="DDL を実行せず現在状態を表示します。")
    subparsers.add_parser("initialize", help="不足 object を作成・更新します。")
    recreate = subparsers.add_parser(
        "recreate",
        help="管理対象 RAG object を削除して再作成します。",
    )
    recreate.add_argument(
        "--confirmation",
        required=True,
        help=f"確認値: {RECREATE_CONFIRMATION}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            result = system_schema_manager.status()
        elif args.command == "initialize":
            result = system_schema_manager.initialize()
        else:
            result = system_schema_manager.initialize(
                recreate=True,
                confirmation=args.confirmation,
            )
    except SystemSchemaError as exc:
        print(
            json.dumps(
                {"error_code": exc.code, "error_message": exc.public_message},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
