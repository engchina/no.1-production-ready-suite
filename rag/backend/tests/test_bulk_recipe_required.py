"""既存文書のbulk投入は正式行を補建せず、欠落を文書ごとに拒否する。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.clients.oracle import OracleClient
from app.clients.oracle_bulk_ingestion_store import OracleBulkIngestionStore
from app.config import Settings
from app.rag.bulk_ingestion import accept_bulk_ingestion
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.document import BulkIngestionResult, IngestionJob, SourceProfile
from tests.test_oracle_adapter import (
    FakeOracleConnection,
    FakeOracleCursor,
    FakeOraclePool,
    _oci_settings,
    _run_inline,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)


@dataclass(frozen=True)
class DocumentSpec:
    id: str
    formal: bool = False
    visible: bool = True
    recipe_id: str = "formal-slot-one"
    revision: int = 4
    delete_at_lock: bool = False
    lose_visibility_at_lock: bool = False
    active_at_lock: bool = False


class BulkConnection(FakeOracleConnection):
    def __init__(self, specs: Sequence[DocumentSpec], *, fail_insert: bool = False) -> None:
        super().__init__([])
        self.specs = {s.id: s for s in specs}
        self.recipes: dict[str, dict[str, object]] = {
            s.id: {"document_id": s.id, "recipe_id": s.recipe_id, "config_revision": s.revision}
            for s in specs
            if s.formal
        }
        self.original_recipes = deepcopy(self.recipes)
        self.jobs: list[dict[str, object]] = []
        self.fail_insert = fail_insert

    def cursor(self) -> "BulkCursor":
        return BulkCursor(self)

    def rollback(self) -> None:
        super().rollback()
        self.recipes = deepcopy(self.original_recipes)
        self.jobs.clear()


class BulkCursor(FakeOracleCursor):
    def __init__(self, connection: BulkConnection) -> None:
        super().__init__(connection)
        self.state = connection

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        sql = " ".join(statement.split())
        binds = dict(parameters or {})
        rows: list[dict[str, object]] = []
        if "FROM rag_documents d" in sql:
            requested = {str(v) for k, v in binds.items() if k.startswith("bulk_document_id")}
            for did, spec in self.state.specs.items():
                if did not in requested or not spec.visible:
                    continue
                formal = self.state.recipes.get(did)
                rows.append(
                    {
                        "document_id": did,
                        "file_name": did + ".txt",
                        "document_status": "UPLOADED",
                        "tenant_id_hash": "synthetic-tenant",
                        "content_type": "text/plain",
                        "file_size_bytes": 10,
                        "content_sha256": "a" * 64,
                        "duplicate_of_document_id": None,
                        "processing_config": {"chunk_size": 777},
                        "error_message": None,
                        "uploaded_at": NOW,
                        "indexed_at": None,
                        "has_source": 1,
                        "has_outputs": 0,
                        "list_status": "UPLOADED",
                        "default_recipe_id": formal["recipe_id"] if formal else None,
                        "recipe_processing_config": {"chunk_size": 512} if formal else None,
                    }
                )
        elif "FROM rag_document_recipes r" in sql:
            requested = {str(v) for k, v in binds.items() if k.startswith("bulk_lock_document_id")}
            for did, spec in self.state.specs.items():
                if did not in requested:
                    continue
                if spec.delete_at_lock:
                    self.state.recipes.pop(did, None)
                formal = self.state.recipes.get(did)
                if formal is not None and spec.visible and not spec.lose_visibility_at_lock:
                    rows.append(dict(formal))
        elif "FROM rag_ingestion_jobs" in sql:
            if "MAX(CASE" not in sql:
                rows = [
                    {"document_id": did, "recipe_id": row["recipe_id"]}
                    for did, row in self.state.recipes.items()
                    if self.state.specs[did].active_at_lock
                ]
        else:
            raise AssertionError("想定外のSELECT")
        self.state._execute_results.append(rows)
        super().execute(statement, parameters)

    def executemany(self, statement: str, parameters: Sequence[Mapping[str, object]]) -> None:
        if statement.lstrip().startswith("MERGE INTO rag_document_recipes"):
            for row in parameters:
                self.state.recipes.setdefault(str(row["document_id"]), dict(row, config_revision=1))
        elif statement.lstrip().startswith("INSERT INTO rag_ingestion_jobs"):
            if self.state.fail_insert:
                raise RuntimeError("synthetic job insert failure")
            self.state.jobs.extend(dict(row) for row in parameters)
        else:
            raise AssertionError("想定外の書込み")
        super().executemany(statement, parameters)


CASES: list[tuple[str, list[DocumentSpec], dict[str, tuple[str, str | None]]]] = [
    (
        "missing-default",
        [DocumentSpec("synthetic-missing")],
        {"synthetic-missing": ("rejected", "not_found")},
    ),
    (
        "existing-formal",
        [DocumentSpec("synthetic-formal", formal=True)],
        {"synthetic-formal": ("accepted", None)},
    ),
    (
        "invisible",
        [DocumentSpec("synthetic-invisible", visible=False)],
        {"synthetic-invisible": ("rejected", "not_found")},
    ),
    (
        "deleted-after-snapshot",
        [DocumentSpec("synthetic-deleted", formal=True, delete_at_lock=True)],
        {"synthetic-deleted": ("rejected", "not_found")},
    ),
    (
        "visibility-lost-before-lock",
        [DocumentSpec("synthetic-lost", lose_visibility_at_lock=True)],
        {"synthetic-lost": ("rejected", "not_found")},
    ),
    (
        "active-after-snapshot",
        [DocumentSpec("synthetic-active", formal=True, active_at_lock=True)],
        {"synthetic-active": ("already_active", "job_active")},
    ),
    (
        "mixed-batch",
        [
            DocumentSpec("synthetic-good", formal=True, recipe_id="formal-good", revision=3),
            DocumentSpec("synthetic-bad"),
            DocumentSpec("synthetic-hidden", visible=False),
        ],
        {
            "synthetic-good": ("accepted", None),
            "synthetic-bad": ("rejected", "not_found"),
            "synthetic-hidden": ("rejected", "not_found"),
        },
    ),
]


async def accept(connection: BulkConnection) -> BulkIngestionResult:
    pool = FakeOraclePool()
    pool.connection = connection
    client = OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)

    async def no_single(document_id: str) -> IngestionJob:
        raise AssertionError("未処理の合成文書は単件投入を呼ばない")

    async def no_service(settings: Settings, source: SourceProfile) -> None:
        return None

    token = set_audit_request_context(
        AuditRequestContext(
            request_id="synthetic-bulk",
            tenant_id_hash="synthetic-tenant",
            allowed_document_ids=frozenset(s.id for s in connection.specs.values() if s.visible),
        )
    )
    try:
        return await accept_bulk_ingestion(
            list(connection.specs),
            store=OracleBulkIngestionStore(client),
            resolve_settings=lambda _: Settings.model_construct(),
            enqueue_single=no_single,
            max_attempts=3,
            parser_message=no_service,
            clock=lambda: NOW,
        )
    finally:
        reset_audit_request_context(token)


@pytest.mark.parametrize("name,specs,expected", CASES, ids=[c[0] for c in CASES])
async def test_bulk_uses_only_formal_recipes(
    name: str, specs: list[DocumentSpec], expected: dict[str, tuple[str, str | None]]
) -> None:
    connection = BulkConnection(specs)
    result = await accept(connection)
    assert {
        item.document_id: (item.outcome.value, item.reason) for item in result.items
    } == expected
    assert not any(call.statement.lstrip().startswith("MERGE") for call in connection.many_calls)
    accepted = {did for did, (outcome, _) in expected.items() if outcome == "accepted"}
    assert {str(job["document_id"]) for job in connection.jobs} == accepted
    for job in connection.jobs:
        spec = connection.specs[str(job["document_id"])]
        assert job["recipe_id"] == spec.recipe_id and job["recipe_revision"] == spec.revision
        assert job["status"] == "QUEUED" and job["max_attempts"] == 3
    assert connection.rollbacks == 0
    assert connection.commits == (0 if name == "invisible" else 1)
    locks = [call for call in connection.calls if "FOR UPDATE OF r.recipe_id" in call.statement]
    if name != "invisible":
        assert len(locks) == 1 and "JOIN rag_documents d" in locks[0].statement
        assert "d.document_id IN" in locks[0].statement and "d.tenant_id_hash" in locks[0].statement
    assert all("INSERT INTO rag_documents" not in call.statement for call in connection.many_calls)


async def test_bulk_job_insert_failure_keeps_transaction_rollback() -> None:
    connection = BulkConnection([DocumentSpec("synthetic-formal", formal=True)], fail_insert=True)
    result = await accept(connection)
    assert result.items[0].reason == "temporary_failure"
    assert connection.commits == 0 and connection.rollbacks == 1 and connection.jobs == []
    assert connection.recipes == connection.original_recipes
