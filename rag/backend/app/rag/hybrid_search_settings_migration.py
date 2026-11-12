"""検索方式の旧値を、索引・SQL実行経路を表す値へ一度だけ移す。"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import dotenv_values
from pr_system_settings.env_file import env_assignment_key, locked_env_file, replace_env_file

_KEY = "RAG_HYBRID_SEARCH_BACKEND"
_RENAMES = {
    "oracle_native": "oracle_hybrid_index",
    "sudachi": "oracle_separate_sql",
}
_CURRENT_VALUES = frozenset(_RENAMES.values())


def migrate_env(path: Path, *, apply: bool = False) -> str | None:
    """現在の方式を保って値だけ移す。不明値は推測せず、他の設定は触らない。"""
    if not path.is_file():
        return None
    with locked_env_file(path) as env_path:
        value = dotenv_values(env_path, interpolate=False).get(_KEY)
        if value is None or value in _CURRENT_VALUES:
            return None
        renamed = _RENAMES.get(value)
        if renamed is None:
            raise ValueError("検索方式が不明なため移行できません。設定値を確認してください。")
        if apply:
            content = "".join(
                f"{_KEY}={renamed}\n" if env_assignment_key(line) == _KEY else line
                for line in env_path.read_text(encoding="utf-8").splitlines(keepends=True)
            )
            replace_env_file(env_path, content)
        return renamed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="検索方式の設定値を移行します。既定はdry-runです。"
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = migrate_env(args.env_file, apply=args.apply)
    if target is None:
        print("unchanged")
    else:
        print(f"{'updated' if args.apply else 'planned'}: {_KEY}={target}")


if __name__ == "__main__":
    main()
