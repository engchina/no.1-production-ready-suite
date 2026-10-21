"""旧設定を移行しても、他の設定・明示値・アクセス権を保持する。"""

import stat
from pathlib import Path

import pytest

from app.rag.guardrail_settings_migration import migrate_env


@pytest.mark.parametrize(("backend", "expected"), [("local", "false"), ("oci_guardrails", "true")])
def test_migrate_backend_and_remove_policy_once(
    tmp_path: Path, backend: str, expected: str
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        f"# 設定\nRAG_GUARDRAIL_BACKEND={backend}\n"
        "RAG_GUARDRAIL_POLICY=strict\nRAG_CHUNK_SIZE=800\n"
    )
    path.chmod(0o600)
    assert migrate_env(path)
    result = path.read_text()
    assert result == f"# 設定\nRAG_CHUNK_SIZE=800\nRAG_GUARDRAILS_ENABLED={expected}\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not migrate_env(path)
    assert path.read_text() == result


def test_explicit_switch_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("RAG_GUARDRAIL_BACKEND=oci_guardrails\nRAG_GUARDRAILS_ENABLED=false\n")
    migrate_env(path)
    assert path.read_text() == "RAG_GUARDRAILS_ENABLED=false\n"


def test_existing_staging_file_is_not_overwritten_or_removed(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    before = "RAG_GUARDRAIL_BACKEND=oci_guardrails\n"
    path.write_text(before)
    staging = path.with_name(".env.guardrails-migration")
    staging.write_text("別の処理の内容")
    with pytest.raises(FileExistsError):
        migrate_env(path)
    assert path.read_text() == before
    assert staging.read_text() == "別の処理の内容"
