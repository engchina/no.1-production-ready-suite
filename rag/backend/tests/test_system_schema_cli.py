"""RAG system schema の CLI（`app.rag.system_schema_cli`）のテスト。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.rag import system_schema_cli
from app.rag.system_schema import SystemSchemaError, system_schema_manager


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
