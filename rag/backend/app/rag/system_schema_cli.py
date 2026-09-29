"""RAG system schema の status / initialize / recreate / delete-orphans CLI。"""

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
    # 参照先のない行の削除（#511）。status の orphaned_foreign_keys で件数を確認してから実行する。
    delete_orphans = subparsers.add_parser(
        "delete-orphans",
        help=(
            "外部キーの参照先のない行を削除し、外部キーを検査済み（VALIDATE）にします。"
            "削除した行は復元できません。"
        ),
    )
    delete_orphans.add_argument(
        "--constraint",
        required=True,
        help="外部キーの名前（status の orphaned_foreign_keys の name）。",
    )
    delete_orphans.add_argument(
        "--expected-rows",
        required=True,
        type=int,
        help=(
            "status で確認した参照先のない行の件数。実行時の件数がこれより多いときは削除しません。"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            result = system_schema_manager.status()
        elif args.command == "initialize":
            result = system_schema_manager.initialize()
        elif args.command == "delete-orphans":
            if args.expected_rows < 0:
                raise SystemSchemaError(
                    "SCHEMA_ORPHAN_ROWS_INVALID",
                    "--expected-rows には 0 以上の件数を指定してください。",
                    status_code=422,
                )
            result = system_schema_manager.delete_orphaned_rows(
                constraint_name=args.constraint,
                expected_orphan_rows=args.expected_rows,
            )
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
