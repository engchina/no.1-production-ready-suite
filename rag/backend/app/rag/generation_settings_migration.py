"""旧回答生成設定を Oracle 正本へ一度だけ移行する CLI。

dry-run は既定で、`.env` 由来 profile と既存 prompt-versions.json の件数・active ID
だけを表示する。`--apply` は Prompt 版を version ID で MERGE し、GLOBAL 行が存在しない
場合に限って旧 profile と active pointer を取り込む。旧ファイルは変更しない。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from typing import Any

from app.clients.oracle import OracleClient
from app.config import get_settings
from app.rag.generation_adapter import normalize_generation_profile
from app.rag.prompt_versions import load_prompt_version_store


def legacy_import_plan() -> dict[str, object]:
    """副作用なしで legacy import 対象を返す。"""

    settings = get_settings()
    store = load_prompt_version_store()
    version_ids = {version.version_id for version in store.versions}
    active_version_id = store.active_version_id if store.active_version_id in version_ids else None
    return {
        "mode": "dry-run",
        "profile": normalize_generation_profile(settings.rag_generation_profile),
        "prompt_version_count": len(store.versions),
        "active_prompt_version_id": active_version_id,
        "legacy_files_preserved": True,
    }


async def apply_legacy_import() -> dict[str, object]:
    """legacy data を idempotent に Oracle へ適用する。"""

    plan = legacy_import_plan()
    store = load_prompt_version_store()
    result = await OracleClient().import_legacy_generation_settings(
        profile=str(plan["profile"]),
        versions=[version.model_dump(mode="python") for version in store.versions],
        active_version_id=plan["active_prompt_version_id"],  # type: ignore[arg-type]
    )
    return {
        "mode": "apply",
        **result,
        "legacy_files_preserved": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="旧回答スタイルと Prompt 版を Oracle へ移行します。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Oracle へ適用する。省略時は dry-run。",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="結果の表示形式。",
    )
    return parser


def _render(payload: dict[str, object], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    labels = {
        "mode": "mode",
        "profile": "legacy profile",
        "prompt_version_count": "legacy prompt count",
        "active_prompt_version_id": "legacy active prompt ID",
        "settings_created": "Oracle GLOBAL created",
        "legacy_prompt_count": "legacy prompts considered",
        "legacy_files_preserved": "legacy files preserved",
    }
    return "\n".join(
        f"{labels.get(key, key)}: {value if value is not None else '-'}"
        for key, value in payload.items()
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload: dict[str, Any] = (
        asyncio.run(apply_legacy_import()) if args.apply else legacy_import_plan()
    )
    print(_render(payload, output_format=args.format))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
