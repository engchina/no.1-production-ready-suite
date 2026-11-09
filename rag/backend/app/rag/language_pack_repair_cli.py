"""現在版が保存契約に合わない環境の言語資源を修復する CLI(#4697)。

既定は読み取りだけの dry-run。`--apply` のときだけ新しい公開版を発行し、dry-run で確かめた現在版の
ID と下書きの revision を引数で求める。出力は項目の名前だけで、言語資源の値は出さない。
対象の環境での `--apply` は利用者の明示の許可を得てから行う
(rag/docs/language-pack-current-contract.md)。条件を満たすなら 0、止めたか失敗したら 1 で終わる。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict

from pr_backend_core.logging import configure_cli_logging
from pr_backend_core.oracle_dsn import enable_cli_connect_mode
from pr_system_settings.system_schema import operation_failure_fields

from app.clients.oracle import OracleClient, shared_pool_connect_kwargs
from app.config import get_settings
from app.rag.language_pack_contract import LanguagePackSnapshotError
from app.rag.language_pack_repair import (
    LanguagePackRepairBlocked,
    LanguagePackRepairPlan,
    apply_language_pack_repair,
    inspect_language_pack_repair,
)
from app.rag.request_context import with_system_access


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="現在版が保存契約に合わないときに、欠けた項目だけを補った新しい公開版を発行します。"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="新しい公開版を発行します。指定しなければ読み取りだけの dry-run です。",
    )
    parser.add_argument(
        "--expected-current-id",
        help="dry-run の current_release_id。--apply に必須です。違えば発行しません。",
    )
    parser.add_argument(
        "--expected-draft-revision",
        type=int,
        help="dry-run の draft_revision。--apply に必須です。違えば発行しません。",
    )
    return parser


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    given = (args.expected_current_id is not None, args.expected_draft_revision is not None)
    if args.apply and not all(given):
        parser.error("--apply には --expected-current-id と --expected-draft-revision が必要です。")
    if not args.apply and any(given):
        parser.error("--expected-current-id と --expected-draft-revision は --apply と使います。")
    return args


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _plan_payload(mode: str, plan: LanguagePackRepairPlan) -> dict[str, object]:
    return {"mode": mode, "status": "blocked" if plan.blockers else "ready", **asdict(plan)}


def _error(code: str, message: str, **fields: str) -> int:
    payload = {"error_code": code, "error_message": message, **fields}
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


@with_system_access
def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging("rag")
    # DB の停止中・届かないときに長く待たずに失敗を返す(system_schema_cli と同じ。#1779)。
    enable_cli_connect_mode()
    args = _parse(argv)
    mode = "apply" if args.apply else "dry-run"
    try:
        oracle = OracleClient(get_settings())
        if not args.apply:
            plan = asyncio.run(inspect_language_pack_repair(oracle))
            _print(_plan_payload(mode, plan))
            return 1 if plan.blockers else 0
        result = asyncio.run(
            apply_language_pack_repair(
                oracle,
                expected_current_id=args.expected_current_id,
                expected_draft_revision=args.expected_draft_revision,
            )
        )
    except LanguagePackRepairBlocked as exc:
        _print(_plan_payload(mode, exc.plan))
        return 1
    except LanguagePackSnapshotError as exc:
        # 原因の連鎖には言語資源の項目の検証の詳細が入るので、固定の文言だけを出す。
        return _error("LANGUAGE_PACK_REPAIR_UNAVAILABLE", str(exc))
    except Exception as exc:
        fields = operation_failure_fields(exc, connect_kwargs=shared_pool_connect_kwargs)
        return _error(
            fields.pop("code"), f"言語資源の修復に失敗しました（{type(exc).__name__}）。", **fields
        )
    _print({"mode": mode, "status": "applied", **asdict(result)})
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
