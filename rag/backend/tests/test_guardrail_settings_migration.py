"""安全チェックの有効化を platform/.env へ移しても、他の設定と明示値を保つ（#5061）。"""

from pathlib import Path

from app.rag.guardrail_settings_migration import migrate_env


def test_moves_switches_to_platform_and_removes_old_keys(tmp_path: Path) -> None:
    backend = tmp_path / "backend.env"
    platform = tmp_path / "platform.env"
    backend.write_text(
        "RAG_CHUNK_SIZE=800\nRAG_GUARDRAIL_POLICY=strict\nRAG_GUARDRAIL_SERVICE_URL=http://x\n"
        "RAG_GUARDRAILS_INPUT_ENABLED=true\nRAG_GUARDRAILS_OUTPUT_ENABLED=false\n"
    )
    platform.write_text("PLATFORM_TIMEZONE=UTC\n")
    assert migrate_env(backend, platform)
    assert "RAG_GUARDRAILS_" not in backend.read_text()
    assert "RAG_GUARDRAIL_POLICY" not in backend.read_text()
    # 明示した変数だけを消す（別の機能の変数は残す）。
    assert "RAG_GUARDRAIL_SERVICE_URL=http://x" in backend.read_text()
    assert "RAG_CHUNK_SIZE=800" in backend.read_text()
    text = platform.read_text()
    assert "PLATFORM_TIMEZONE=UTC" in text
    assert "PLATFORM_GUARDRAILS_INPUT_ENABLED=true" in text
    assert "PLATFORM_GUARDRAILS_OUTPUT_ENABLED=false" in text
    # 2 回目は何もしない（冪等）。
    assert not migrate_env(backend, platform)


def test_existing_platform_value_wins(tmp_path: Path) -> None:
    backend = tmp_path / "backend.env"
    platform = tmp_path / "platform.env"
    backend.write_text("RAG_GUARDRAILS_INPUT_ENABLED=true\n")
    platform.write_text("PLATFORM_GUARDRAILS_INPUT_ENABLED=false\n")
    assert migrate_env(backend, platform)
    assert "PLATFORM_GUARDRAILS_INPUT_ENABLED=false" in platform.read_text()
    assert "PLATFORM_GUARDRAILS_INPUT_ENABLED=true" not in platform.read_text()


def test_missing_backend_env_is_unchanged(tmp_path: Path) -> None:
    assert not migrate_env(tmp_path / "missing.env", tmp_path / "platform.env")
