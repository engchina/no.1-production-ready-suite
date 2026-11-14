"""RAG の安全チェックの有効化を、3 製品共通の `platform/.env` へ移す一度限りの更新（#5061）。

`backend/.env` の `RAG_GUARDRAILS_INPUT_ENABLED` / `RAG_GUARDRAILS_OUTPUT_ENABLED` を
`PLATFORM_GUARDRAILS_*` へ移し、それと旧い検査方式の行を消す。`platform/.env` に値が
あればそちらを残す。Settings は旧名を読まないので、配備の更新でサービスの起動前に実行する。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import dotenv_values
from pr_system_settings.env_file import write_env_values

_MOVED = {
    "RAG_GUARDRAILS_INPUT_ENABLED": "PLATFORM_GUARDRAILS_INPUT_ENABLED",
    "RAG_GUARDRAILS_OUTPUT_ENABLED": "PLATFORM_GUARDRAILS_OUTPUT_ENABLED",
}
# 入力・出力を分ける前の旧い検査方式の変数（どのコードも読まない）。
_RETIRED = (
    "RAG_GUARDRAIL_BACKEND",
    "RAG_GUARDRAILS_ENABLED",
    "RAG_GUARDRAIL_POLICY",
    "RAG_GUARDRAIL_MAX_QUERY_CHARS",
    "RAG_GUARDRAIL_BLOCK_PROMPT_INJECTION",
    "RAG_GUARDRAIL_MASK_SENSITIVE_IDENTIFIERS",
)


def migrate_env(backend_env: Path, platform_env: Path) -> bool:
    """移した値か消した行があれば True。他の設定は変えない。"""
    if not backend_env.exists():
        return False
    backend = dotenv_values(backend_env)
    old_keys = [key for key in (*_MOVED, *_RETIRED) if key in backend]
    if not old_keys:
        return False
    platform = dotenv_values(platform_env) if platform_env.exists() else {}
    moved = {
        new: str(backend[old]).strip().lower()
        for old, new in _MOVED.items()
        if backend.get(old) is not None and new not in platform
    }
    if moved:
        write_env_values(platform_env, moved, section_comment="# 安全チェック（入力・出力）")
    write_env_values(backend_env, dict.fromkeys(old_keys), section_comment="")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="安全チェックの有効化を platform/.env へ移します。"
    )
    parser.add_argument("--env-file", type=Path, required=True, help="RAG の backend/.env")
    parser.add_argument("--platform-env-file", type=Path, required=True, help="platform/.env")
    args = parser.parse_args()
    print("updated" if migrate_env(args.env_file, args.platform_env_file) else "unchanged")


if __name__ == "__main__":
    main()
