"""検索方式の値だけを移す更新を、合成.envで検証する。"""

from pathlib import Path

import pytest
from dotenv import dotenv_values

from app.rag.hybrid_search_settings_migration import migrate_env


@pytest.mark.parametrize(
    ("old", "new"),
    [("oracle_native", "oracle_hybrid_index"), ("sudachi", "oracle_separate_sql")],
)
def test_migration_preserves_choice_other_settings_and_permissions(
    tmp_path: Path, old: str, new: str
) -> None:
    env = tmp_path / ".env"
    original = f'# 合成設定\nexport RAG_HYBRID_SEARCH_BACKEND="{old}" # 旧値\nOTHER="a b#c"\n'
    env.write_text(original)
    env.chmod(0o600)
    assert migrate_env(env) == new
    assert env.read_text() == original
    assert migrate_env(env, apply=True) == new
    assert dotenv_values(env)["RAG_HYBRID_SEARCH_BACKEND"] == new
    assert 'OTHER="a b#c"\n' in env.read_text()
    assert env.stat().st_mode & 0o777 == 0o600
    after = env.read_bytes()
    assert migrate_env(env, apply=True) is None
    assert env.read_bytes() == after


@pytest.mark.parametrize("value", ["oracle_hybrid_index", "oracle_separate_sql", None])
def test_current_or_unset_values_are_not_written(tmp_path: Path, value: str | None) -> None:
    env = tmp_path / ".env"
    original = "OTHER=unchanged\n"
    if value is not None:
        original += f"RAG_HYBRID_SEARCH_BACKEND={value}\n"
    env.write_text(original)
    assert migrate_env(env, apply=True) is None
    assert env.read_text() == original


def test_unknown_value_fails_without_writing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    original = "RAG_HYBRID_SEARCH_BACKEND=unknown\nOTHER=unchanged\n"
    env.write_text(original)
    with pytest.raises(ValueError, match="検索方式が不明"):
        migrate_env(env, apply=True)
    assert env.read_text() == original


def test_missing_file_is_not_created(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert migrate_env(env, apply=True) is None
    assert not env.exists()
