"""RAG system schema の CLI（`app.rag.system_schema_cli`）のテスト。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.rag import system_schema_cli
from app.rag.system_schema import (
    DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED,
    SystemSchemaError,
    system_schema_manager,
)


def test_delete_orphans_passes_confirmed_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    def delete_orphaned_rows(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"operation": "orphans_deleted", "deleted_row_count": 3}

    monkeypatch.setattr(system_schema_manager, "delete_orphaned_rows", delete_orphaned_rows)

    code = system_schema_cli.main(
        ["delete-orphans", "--constraint", "RAG_CHUNK_SETS_DOCUMENT_FK", "--expected-rows", "3"]
    )

    assert code == 0
    assert captured == {"constraint_name": "RAG_CHUNK_SETS_DOCUMENT_FK", "expected_orphan_rows": 3}
    assert json.loads(capsys.readouterr().out)["deleted_row_count"] == 3


def test_delete_orphans_requires_constraint_and_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("件数を確認していない削除を実行してはならない")

    monkeypatch.setattr(system_schema_manager, "delete_orphaned_rows", fail)

    with pytest.raises(SystemExit):
        system_schema_cli.main(["delete-orphans", "--constraint", "RAG_CHUNK_SETS_DOCUMENT_FK"])
    capsys.readouterr()

    code = system_schema_cli.main(
        ["delete-orphans", "--constraint", "RAG_CHUNK_SETS_DOCUMENT_FK", "--expected-rows", "-1"]
    )
    assert code == 1
    assert json.loads(capsys.readouterr().err)["error_code"] == "SCHEMA_ORPHAN_ROWS_INVALID"


def test_delete_orphans_reports_business_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def delete_orphaned_rows(**_kwargs: Any) -> dict[str, Any]:
        raise SystemSchemaError(
            "SCHEMA_ORPHAN_ROWS_CHANGED", "件数が増えています。", status_code=409
        )

    monkeypatch.setattr(system_schema_manager, "delete_orphaned_rows", delete_orphaned_rows)

    code = system_schema_cli.main(
        ["delete-orphans", "--constraint", "RAG_CHUNK_SETS_DOCUMENT_FK", "--expected-rows", "1"]
    )

    assert code == 1
    assert json.loads(capsys.readouterr().err) == {
        "error_code": "SCHEMA_ORPHAN_ROWS_CHANGED",
        "error_message": "件数が増えています。",
    }


def test_initialize_passes_destructive_approval_only_when_given(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`initialize` は `--allow-destructive` があるときだけ承認を渡す（#619）。"""
    calls: list[dict[str, Any]] = []

    def initialize(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if not kwargs.get("allow_destructive"):
            raise SystemSchemaError(
                DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED,
                "データを削除する migration が未適用です（20260930_005）。",
                status_code=409,
            )
        return {"operation": "migrated"}

    monkeypatch.setattr(system_schema_manager, "initialize", initialize)

    assert system_schema_cli.main(["initialize"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["error_code"] == DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED
    assert "--allow-destructive" in error["hint"]

    assert system_schema_cli.main(["initialize", "--allow-destructive"]) == 0
    assert json.loads(capsys.readouterr().out)["operation"] == "migrated"
    assert calls == [{"allow_destructive": False}, {"allow_destructive": True}]


def test_check_returns_zero_only_when_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """配備の確認（#1648）: ready なら 0、それ以外は 1 と 1 行の要約（ORA コードを含む）。"""

    monkeypatch.setattr(
        system_schema_manager,
        "status",
        lambda: {"status": "ready", "operation_state": {"status": "idle"}},
    )
    assert system_schema_cli.main(["check"]) == 0
    assert capsys.readouterr().out.strip() == "status=ready"

    monkeypatch.setattr(
        system_schema_manager,
        "status",
        lambda: {
            "status": "missing",
            "operation_state": {"status": "failed", "last_error_code": "ORA-01031"},
        },
    )
    assert system_schema_cli.main(["check"]) == 1
    assert "ORA-01031" in capsys.readouterr().out

    def unavailable() -> dict[str, Any]:
        raise RuntimeError("ORA-01017: invalid credential secret")

    monkeypatch.setattr(system_schema_manager, "status", unavailable)
    assert system_schema_cli.main(["check"]) == 1
    output = capsys.readouterr().out
    assert "ORA-01017" in output
    assert "secret" not in output


def test_initialize_reports_ora_code_for_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """lease の前の失敗（制御テーブルの作成の権限不足など）も ORA コードを出す（#1648）。"""

    def initialize(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("ORA-01031: insufficient privileges")

    monkeypatch.setattr(system_schema_manager, "initialize", initialize)
    assert system_schema_cli.main(["initialize"]) == 1
    assert json.loads(capsys.readouterr().err)["error_code"] == "ORA-01031"
