"""NL2SQL system table の状態・初期化・全再作成 CLI。"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.features.settings.system_schema import SystemSchemaError, system_schema_manager
from app.features.settings.system_schema_runtime import reset_system_schema_runtime


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--status", action="store_true", help="read-only status")
    actions.add_argument("--initialize", action="store_true", help="create or migrate")
    actions.add_argument("--recreate", action="store_true", help="drop core allowlist and recreate")
    parser.add_argument("--confirmation", default=None, help="required confirmation for --recreate")
    args = parser.parse_args()

    try:
        if args.status:
            result = system_schema_manager.status()
        else:
            result = system_schema_manager.initialize(
                recreate=bool(args.recreate),
                confirmation=args.confirmation,
            )
            reset_system_schema_runtime(
                schema_epoch=int(result["operation_state"]["schema_epoch"])
            )
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
    except Exception:
        print(  # noqa: T201
            _stable_json(
                {
                    "error": {
                        "code": "SCHEMA_OPERATION_FAILED",
                        "message": "システムテーブル操作に失敗しました。",
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
