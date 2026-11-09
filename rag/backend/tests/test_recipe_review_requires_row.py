"""抽出確認の保存・復元は正式行をロックし、旧文書からレシピを作らない。"""

from collections.abc import Mapping
from copy import deepcopy

import pytest

from app.clients.oracle import (
    DocumentReviewEditConflictError,
    OracleClient,
    ReviewedExtractionWrite,
)
from tests.test_oracle_adapter import (
    FakeOracleConnection,
    FakeOracleCursor,
    FakeOraclePool,
    _oci_settings,
    _oracle_document_row,
    _run_inline,
)
from tests.test_oracle_db_paths import _default_recipe_id, _recipe_row
from tests.test_recipe_review_save_oracle import _artifact_row, _write


class ReviewConnection(FakeOracleConnection):
    """SELECTの対象行と旧seedを区別する合成transaction。実Oracleへの接続はしない。"""

    def __init__(
        self,
        row: dict[str, object] | None,
        *,
        old_status: str = "REVIEW",
        active: bool = False,
    ) -> None:
        super().__init__([])
        self.row = deepcopy(row)
        self.initial = deepcopy(row)
        self.old_status = old_status
        self.active = active

    def cursor(self) -> "ReviewCursor":
        return ReviewCursor(self)

    def rollback(self) -> None:
        super().rollback()
        self.row = deepcopy(self.initial)


class ReviewCursor(FakeOracleCursor):
    def __init__(self, connection: ReviewConnection) -> None:
        super().__init__(connection)
        self.state = connection

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        sql = " ".join(statement.split())
        binds = dict(parameters or {})
        if sql.startswith("MERGE INTO rag_document_recipes"):
            self.state.row = _recipe_row(
                status=self.state.old_status, active_extraction_recipe_id=None
            )
        elif not sql.startswith("SELECT"):
            raise AssertionError("この合成テストでは変更DMLを実行しません。")
        rows: list[dict[str, object]] = []
        if sql.startswith("SELECT"):
            row = self.state.row
            if "FROM rag_document_recipes" in sql:
                if (
                    row is not None
                    and row["document_id"] == binds["document_id"]
                    and (
                        row["recipe_id"] == binds["recipe_id"]
                        if "recipe_id" in binds
                        else row["slot_no"] == 1
                    )
                ):
                    rows = [dict(row)]
            elif "FROM rag_documents" in sql:
                rows = [_oracle_document_row(status=self.state.old_status)]
            elif "FROM rag_ingestion_jobs" in sql:
                rows = [{"job_id": "synthetic-job"}] if self.state.active else []
            elif "FROM rag_document_extractions" in sql:
                rows = [_artifact_row()]
            elif "FROM rag_document_extraction_revisions" not in sql:
                raise AssertionError(f"想定外のSELECT: {sql}")
            self.state._execute_results.append(rows)
        super().execute(statement, parameters)


def _client(
    row: dict[str, object] | None, *, old_status: str = "REVIEW", active: bool = False
) -> tuple[OracleClient, ReviewConnection]:
    connection = ReviewConnection(row, old_status=old_status, active=active)
    pool = FakeOraclePool()
    pool.connection = connection
    return OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline), connection


async def _change(client: OracleClient, recipe_id: str, mode: str) -> object:
    if mode == "save":
        return await client.save_document_recipe_review_extraction(
            document_id="doc-1", recipe_id=recipe_id, build=_write
        )
    return await client.restore_document_recipe_extraction_revision(
        document_id="doc-1",
        recipe_id=recipe_id,
        revision_no=1,
        expected_extraction_recipe_id="er_base",
        build=lambda *_: _write({}, {}),
    )


@pytest.mark.parametrize("mode", ["save", "restore"])
@pytest.mark.parametrize("old_status", ["UPLOADED", "REVIEW", "INDEXED"])
async def test_missing_default_review_does_not_seed(mode: str, old_status: str) -> None:
    client, connection = _client(None, old_status=old_status)
    with pytest.raises(KeyError, match="レシピが見つかりません"):
        await _change(client, _default_recipe_id(), mode)
    assert connection.row is None
    assert connection.commits == 0 and connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    assert not any("d.processing_config" in call.statement for call in connection.calls)


@pytest.mark.parametrize("mode", ["save", "restore"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_absent_or_foreign_review_recipe_is_missing(mode: str, foreign: bool) -> None:
    row = _recipe_row(recipe_id="formal-recipe", document_id="doc-other") if foreign else None
    client, connection = _client(row)
    with pytest.raises(KeyError):
        await _change(client, "formal-recipe", mode)
    assert connection.row == row
    assert connection.commits == 0 and connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)


@pytest.mark.parametrize(
    "recipe_id,slot", [(_default_recipe_id(), 1), ("formal-slot-one", 1), ("formal-slot-two", 2)]
)
async def test_formal_review_no_change_keeps_source_and_no_revision(
    recipe_id: str, slot: int
) -> None:
    row = _recipe_row(
        recipe_id=recipe_id, slot_no=slot, status="REVIEW", active_extraction_recipe_id="er_base"
    )
    client, connection = _client(row)

    def unchanged(
        recipe: Mapping[str, object], artifact: Mapping[str, object]
    ) -> ReviewedExtractionWrite:
        assert recipe["recipe_id"] == recipe_id
        assert artifact["source_sha256"] == "a" * 64
        assert artifact["recipe_subset"] == {"rag_preprocess_profile": "passthrough"}
        extraction = artifact["extraction_json"]
        assert isinstance(extraction, Mapping)
        return ReviewedExtractionWrite(
            extraction_recipe_id="unused-new",
            source_sha256="a" * 64,
            extraction=extraction,
            recipe_subset=None,
            status="materialized",
            parse_extraction_recipe_id="er_parse",
        )

    assert (
        await client.save_document_recipe_review_extraction(
            document_id="doc-1", recipe_id=recipe_id, build=unchanged
        )
        == "er_base"
    )
    assert connection.row == row
    assert connection.commits == 1 and connection.rollbacks == 0
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)
    lock = next(call for call in connection.calls if "FOR UPDATE" in call.statement)
    assert "JOIN rag_documents" in lock.statement
    assert lock.parameters["document_id"] == "doc-1"
    assert lock.parameters["recipe_id"] == recipe_id


@pytest.mark.parametrize("mode", ["save", "restore"])
@pytest.mark.parametrize("guard", ["active", "not-review", "no-pointer"])
async def test_formal_review_guards_remain(mode: str, guard: str) -> None:
    row = _recipe_row(
        recipe_id=_default_recipe_id(),
        status="CHUNKED" if guard == "not-review" else "REVIEW",
        active_extraction_recipe_id=None if guard == "no-pointer" else "er_base",
    )
    client, connection = _client(row, active=guard == "active")
    with pytest.raises(DocumentReviewEditConflictError):
        await _change(client, _default_recipe_id(), mode)
    assert connection.row == row
    assert connection.commits == 0 and connection.rollbacks == 1
    assert all(call.statement.lstrip().startswith("SELECT") for call in connection.calls)


async def test_formal_restore_keeps_expected_source_fence() -> None:
    row = _recipe_row(
        recipe_id=_default_recipe_id(), status="REVIEW", active_extraction_recipe_id="er_changed"
    )
    client, connection = _client(row)
    with pytest.raises(DocumentReviewEditConflictError, match="ほかの操作"):
        await _change(client, _default_recipe_id(), "restore")
    assert connection.row == row
    assert connection.commits == 0 and connection.rollbacks == 1
    assert not any(
        "FROM rag_document_extraction_revisions" in call.statement for call in connection.calls
    )
