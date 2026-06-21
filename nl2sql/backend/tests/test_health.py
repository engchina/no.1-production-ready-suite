"""health / NL2SQL preview の疎通テスト（Oracle 不要）。"""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from app.features.nl2sql.models import (
    CompareRequest,
    CsvImportRequest,
    FeedbackRating,
    JobCreateRequest,
    JobStatus,
    Nl2SqlEngine,
    Nl2SqlProfile,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.router import is_select_only
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore, OracleJsonNl2SqlStore
from app.main import app
from app.settings import get_settings


def _transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app)


class _FakeOracleDb:
    def __init__(self) -> None:
        self.state_json = ""
        self.executed: list[str] = []
        self.insert_batches: list[tuple[str, list[dict[str, object]]]] = []
        self.commits = 0
        self.catalog_rows: list[tuple[object, ...]] = []
        self.constraint_rows: list[tuple[object, ...]] = []
        self.sample_values: dict[tuple[str, str], list[object]] = {}
        self.unsupported_agent_runtime = False

    def connection(self) -> "_FakeOracleConnection":
        return _FakeOracleConnection(self)


class _FakeOracleConnection:
    def __init__(self, db: _FakeOracleDb) -> None:
        self.db = db

    def __enter__(self) -> "_FakeOracleConnection":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def cursor(self) -> "_FakeOracleCursor":
        return _FakeOracleCursor(self.db)

    def commit(self) -> None:
        self.db.commits += 1


class _FakeOracleCursor:
    def __init__(self, db: _FakeOracleDb) -> None:
        self.db = db
        self._row: tuple[str] | None = None
        self._rows: list[tuple[object, ...]] = []
        self.description: list[tuple[str]] = []

    def __enter__(self) -> "_FakeOracleCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
        normalized_sql = " ".join(sql.split())
        self.db.executed.append(normalized_sql)
        self._row = None
        self._rows = []
        if self.db.unsupported_agent_runtime and "DBMS_CLOUD_AI_AGENT" in normalized_sql:
            raise RuntimeError("ORA-00904: invalid identifier")
        if normalized_sql.startswith("MERGE INTO"):
            self.db.state_json = str((params or {})["state_json"])
        elif normalized_sql.startswith("SELECT state_json"):
            self._row = (self.db.state_json,) if self.db.state_json else None
        elif "FROM user_tab_columns" in normalized_sql:
            self._rows = self.db.catalog_rows
        elif "FROM user_constraints" in normalized_sql:
            self._rows = self.db.constraint_rows
        elif normalized_sql.startswith("SELECT DISTINCT"):
            table_name = normalized_sql.split('FROM "')[1].split('"', 1)[0]
            column_name = normalized_sql.split('SELECT DISTINCT "')[1].split('"', 1)[0]
            self._rows = [
                (value,) for value in self.db.sample_values.get((table_name, column_name), [])
            ]
        elif normalized_sql.startswith("SELECT CLOB_COL FROM"):
            self.description = [("CLOB_COL",)]
            self._rows = [(_FakeLob("long text"),)]

    def executemany(self, sql: str, rows: list[dict[str, object]]) -> None:
        normalized_sql = " ".join(sql.split())
        self.db.executed.append(normalized_sql)
        self.db.insert_batches.append((normalized_sql, rows))

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(self._rows)

    def fetchone(self) -> tuple[str] | None:
        return self._row

    def fetchmany(self, _max_rows: int) -> list[tuple[object, ...]]:
        return self._rows


class _FakeLob:
    def __init__(self, value: str) -> None:
        self.value = value

    def read(self) -> str:
        return self.value


class _FakeRuntimeOracleAdapter(OracleNl2SqlAdapter):
    def __init__(self, db: _FakeOracleDb) -> None:
        super().__init__(get_settings())
        self.db = db

    def is_configured(self) -> bool:
        return True

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self.db.connection() as conn:
            yield conn


class _OracleRuntimeNl2SqlService(Nl2SqlService):
    def __init__(self, adapter: OracleNl2SqlAdapter) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter

    def _use_oracle_runtime(self) -> bool:
        return True


async def test_health() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


async def test_ready() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.get("/api/ready")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "ok"


async def test_nl2sql_preview_returns_safe_select() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post("/api/nl2sql/preview", json={"question": "売上トップ10は?"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["is_safe"] is True
    assert data["sql"].lower().startswith("select")
    assert data["engine"] == "select_ai_agent"
    assert data["timing"]["elapsed_ms"] >= 0


def test_select_only_guard() -> None:
    assert is_select_only("SELECT * FROM t") is True
    assert is_select_only("WITH x AS (SELECT 1) SELECT * FROM x") is True
    assert is_select_only("DELETE FROM t") is False
    assert is_select_only("drop table t") is False
    assert is_select_only("SELECT * FROM t; DELETE FROM t") is False


async def test_schema_catalog_returns_tables_for_picker() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.get("/api/schema/catalog")
    assert resp.status_code == 200
    tables = resp.json()["data"]["tables"]
    assert {table["table_name"] for table in tables} >= {"INVOICES", "CUSTOMERS", "PAYMENTS"}
    assert tables[0]["columns"][0]["logical_name"]


async def test_job_supports_select_ai_agent_and_timing() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/nl2sql/jobs",
            json={
                "question": "請求金額を見たい",
                "engine": "select_ai_agent",
                "allowed_objects": {
                    "table_names": ["INVOICES"],
                    "columns": {"INVOICES": ["INVOICE_ID"]},
                },
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["data"]["job_id"]

        result_resp = await client.get(f"/api/nl2sql/jobs/{job_id}")
        assert result_resp.status_code == 200
        data = result_resp.json()["data"]
        # background thread may still be running on very fast machines; poll once more if needed.
        if data["status"] in {"pending", "running"}:
            result_resp = await client.get(f"/api/nl2sql/jobs/{job_id}")
            data = result_resp.json()["data"]
    assert data["status"] in {"done", "running", "pending"}
    if data["status"] == "done":
        assert data["result"]["engine"] == "select_ai_agent"
        assert data["result"]["engine_meta"]["team_name"].endswith("_TEAM")
        assert data["result"]["timing"]["elapsed_ms"] >= 0


async def test_auto_falls_back_from_agent_to_select_ai() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/nl2sql/preview",
            json={"question": "select_ai_agent_fail 請求一覧", "engine": "auto"},
        )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["engine"] == "select_ai"
    assert "select_ai_agent" in data["fallback_reason"]


async def test_execute_rejects_unsafe_sql() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post("/api/nl2sql/execute", json={"sql": "DROP TABLE INVOICES"})
    assert resp.status_code == 400


async def test_allowed_objects_rejects_unselected_columns() -> None:
    payload = {
        "sql": "SELECT TOTAL_AMOUNT FROM INVOICES",
        "allowed_objects": {
            "table_names": ["INVOICES"],
            "columns": {"INVOICES": ["INVOICE_ID"]},
        },
    }
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        analyze_resp = await client.post("/api/nl2sql/analyze", json=payload)
        execute_resp = await client.post("/api/nl2sql/execute", json=payload)

    assert analyze_resp.status_code == 200
    safety = analyze_resp.json()["data"]["safety"]
    assert safety["is_safe"] is False
    assert safety["blocked_reason"] == "許可されていない列を参照しています。"
    assert safety["referenced_columns"] == ["INVOICES.TOTAL_AMOUNT"]
    assert "INVOICES.INVOICE_ID" in " ".join(analyze_resp.json()["data"]["recommendations"])
    assert execute_resp.status_code == 400


async def test_analyze_converts_limit_clause_to_oracle_fetch_first() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/nl2sql/analyze",
            json={"sql": "SELECT INVOICE_ID FROM INVOICES LIMIT 10;"},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    expected = "SELECT INVOICE_ID FROM INVOICES FETCH FIRST 100 ROWS ONLY"
    assert data["safety"]["is_safe"] is True
    assert data["executable_sql"] == expected
    assert data["repaired_sql"] == expected
    assert "LIMIT" in " ".join(data["safety"]["warnings"])
    assert "FETCH FIRST" in " ".join(data["recommendations"])


async def test_allowed_objects_rejects_wildcard_when_columns_are_limited() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/nl2sql/analyze",
            json={
                "sql": "SELECT * FROM INVOICES",
                "allowed_objects": {
                    "table_names": ["INVOICES"],
                    "columns": {"INVOICES": ["INVOICE_ID"]},
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["safety"]["is_safe"] is False
    assert "SELECT *" in " ".join(data["safety"]["warnings"])
    assert data["repaired_sql"] == ("SELECT INVOICE_ID FROM INVOICES FETCH FIRST 100 ROWS ONLY")
    assert data["optimization_hints"]


async def test_analyze_repairs_first_select_from_multi_statement() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/nl2sql/analyze",
            json={"sql": "SELECT INVOICE_ID FROM INVOICES; DELETE FROM INVOICES"},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["safety"]["is_safe"] is False
    assert data["safety"]["is_select_only"] is False
    assert data["executable_sql"] == ""
    assert data["repaired_sql"] == ("SELECT INVOICE_ID FROM INVOICES FETCH FIRST 100 ROWS ONLY")
    assert "修復候補" in " ".join(data["recommendations"])


async def test_asset_refresh_feedback_and_evaluation() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        profile_resp = await client.post("/api/nl2sql/select-ai-agent/assets/refresh")
        assert profile_resp.status_code == 200
        asset_data = profile_resp.json()["data"]
        assert asset_data["team_name"]
        assert asset_data["status"] == "ready"
        assert {"profile", "tool", "agent", "task", "team"} <= set(asset_data["asset_names"])

        eval_resp = await client.post(
            "/api/nl2sql/evaluate",
            json={"cases": [{"question": "請求一覧", "expected_sql": "SELECT * FROM INVOICES"}]},
        )
    assert eval_resp.status_code == 200
    assert eval_resp.json()["data"]["total_cases"] == 1


async def test_profile_crud_create_update_archive() -> None:
    payload = {
        "name": "請求分析",
        "description": "請求テーブルだけを見る profile",
        "allowed_tables": ["INVOICES"],
        "glossary": {"売上": "INVOICES.TOTAL_AMOUNT"},
        "sql_rules": ["SELECT/WITH のみ"],
        "default_row_limit": 25,
        "safety_policy": "select_only",
        "few_shot_examples": [{"question": "請求一覧", "sql": "SELECT INVOICE_ID FROM INVOICES"}],
    }
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        create_resp = await client.post("/api/nl2sql/profiles", json=payload)
        assert create_resp.status_code == 200
        created = create_resp.json()["data"]
        profile_id = created["id"]
        assert created["allowed_tables"] == ["INVOICES"]

        update_resp = await client.patch(
            f"/api/nl2sql/profiles/{profile_id}",
            json={**payload, "name": "請求分析 v2", "default_row_limit": 50},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["data"]["name"] == "請求分析 v2"
        assert update_resp.json()["data"]["default_row_limit"] == 50

        archive_resp = await client.post(f"/api/nl2sql/profiles/{profile_id}/archive")
        assert archive_resp.status_code == 200
        list_resp = await client.get("/api/nl2sql/profiles")

    assert list_resp.status_code == 200
    assert profile_id not in {profile["id"] for profile in list_resp.json()["data"]}


async def test_recommend_profile_returns_business_profile_and_rewrite() -> None:
    payload = {
        "name": "入金分析",
        "description": "入金と支払状況を確認する profile",
        "allowed_tables": ["PAYMENTS"],
        "glossary": {"入金": "PAYMENTS.PAID_AMOUNT"},
        "sql_rules": ["SELECT/WITH のみ"],
        "default_row_limit": 30,
        "safety_policy": "select_only",
        "few_shot_examples": [
            {"question": "入金方法ごとの金額", "sql": "SELECT PAYMENT_METHOD FROM PAYMENTS"}
        ],
    }
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        create_resp = await client.post("/api/nl2sql/profiles", json=payload)
        assert create_resp.status_code == 200
        profile_id = create_resp.json()["data"]["id"]

        recommend_resp = await client.post(
            "/api/nl2sql/recommend-profile",
            json={"question": "入金方法ごとの入金額を見たい"},
        )
        assert recommend_resp.status_code == 200
        data = recommend_resp.json()["data"]
        assert data["recommended_profile_id"] == profile_id
        assert data["recommended_allowed_objects"]["table_names"] == ["PAYMENTS"]
        assert "PAYMENTS.PAID_AMOUNT" in data["rewritten_question"]
        assert data["candidates"]

        archive_resp = await client.post(f"/api/nl2sql/profiles/{profile_id}/archive")
        assert archive_resp.status_code == 200


async def test_feedback_history_is_retrieved_as_similar_few_shot() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        job_resp = await client.post(
            "/api/nl2sql/jobs",
            json={"question": "請求金額を確認したい", "engine": "select_ai_agent"},
        )
        assert job_resp.status_code == 200
        job_id = job_resp.json()["data"]["job_id"]
        data = {}
        for _ in range(10):
            job_result = await client.get(f"/api/nl2sql/jobs/{job_id}")
            data = job_result.json()["data"]
            if data["status"] == "done":
                break
            await asyncio.sleep(0.01)
        assert data["status"] == "done"

        history_resp = await client.get("/api/nl2sql/history")
        assert history_resp.status_code == 200
        history_item = history_resp.json()["data"]["items"][0]
        assert history_item["profile_id"] == "default"
        assert history_item["result_columns"]

        feedback_resp = await client.post(
            "/api/nl2sql/feedback",
            json={
                "history_id": history_item["id"],
                "rating": "good",
                "comment": "few-shot に使える",
            },
        )
        assert feedback_resp.status_code == 200
        assert feedback_resp.json()["data"]["comment"] == "few-shot に使える"

        similar_resp = await client.post(
            "/api/nl2sql/similar-history",
            json={"question": "請求金額をもう一度確認したい", "profile_id": "default"},
        )
        assert similar_resp.status_code == 200
        similar = similar_resp.json()["data"]["items"]
        assert similar
        assert similar[0]["item"]["feedback_rating"] == "good"
        assert similar[0]["score"] > 0

        preview_resp = await client.post(
            "/api/nl2sql/preview",
            json={"question": "請求金額をもう一度確認したい", "profile_id": "default"},
        )
        assert preview_resp.status_code == 200
        examples = preview_resp.json()["data"]["engine_meta"]["similar_history_examples"]
        assert examples[0]["history_id"] == history_item["id"]


async def test_compare_reverse_comments_synthetic_and_diagnostics() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        compare_resp = await client.post(
            "/api/nl2sql/compare", json={"question": "請求金額を見たい"}
        )
        assert compare_resp.status_code == 200
        compare_data = compare_resp.json()["data"]
        assert len(compare_data["results"]) >= 2
        assert compare_data["recommendation"]
        assert compare_data["execution_results"] == []

        compare_execute_resp = await client.post(
            "/api/nl2sql/compare", json={"question": "請求金額を見たい", "execute": True}
        )
        assert compare_execute_resp.status_code == 200
        compare_execute_data = compare_execute_resp.json()["data"]
        assert len(compare_execute_data["execution_results"]) == len(
            compare_execute_data["results"]
        )
        assert 0 <= compare_execute_data["error_rate"] <= 1
        assert compare_execute_data["execution_results"][0]["row_count"] >= 0

        reverse_resp = await client.post(
            "/api/nl2sql/reverse", json={"sql": "SELECT TOTAL_AMOUNT FROM INVOICES"}
        )
        assert reverse_resp.status_code == 200
        assert reverse_resp.json()["data"]["referenced_tables"] == ["INVOICES"]

        comments_resp = await client.post("/api/nl2sql/comments/suggest")
        assert comments_resp.status_code == 200
        assert comments_resp.json()["data"]["suggestions"]

        synthetic_resp = await client.post("/api/nl2sql/synthetic-cases")
        assert synthetic_resp.status_code == 200
        assert synthetic_resp.json()["data"]["cases"]

        diagnostics_resp = await client.get("/api/nl2sql/diagnostics")
        assert diagnostics_resp.status_code == 200
        checks = diagnostics_resp.json()["data"]["checks"]
        assert checks
        check_names = {check["name"] for check in checks}
        assert {
            "NL2SQL_RUNTIME_MODE",
            "NL2SQL_PERSISTENCE_MODE",
            "NL2SQL_PERSISTENCE_READY",
            "PYTHON_ORACLEDB",
            "ORACLE_RUNTIME_READY",
        } <= check_names


def test_compare_execute_collects_engine_execution_errors() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    original_execute_sql = service.execute_sql
    calls = 0

    def flaky_execute(sql: str, allowed: Any, row_limit: int) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("ORA-00942 mock")
        return original_execute_sql(sql, allowed, row_limit)

    service.execute_sql = flaky_execute  # type: ignore[method-assign]

    data = service.compare_engines(
        CompareRequest(
            question="請求金額を見たい",
            execute=True,
            engines=[Nl2SqlEngine.SELECT_AI_AGENT, Nl2SqlEngine.SELECT_AI],
        )
    )

    assert len(data.execution_results) == 2
    assert data.error_rate == 0.5
    assert data.execution_results[0].executed is True
    assert data.execution_results[1].executed is False
    assert "ORA-00942 mock" in data.execution_results[1].error_message


async def test_nl2sql_store_persists_profiles_jobs_history_and_feedback() -> None:
    store = MemoryNl2SqlStore()
    service = Nl2SqlService(store=store)
    profile = service.create_profile(
        Nl2SqlProfile(
            id="persisted_profile",
            name="永続化テスト",
            allowed_tables=["INVOICES"],
            glossary={"請求": "INVOICES.INVOICE_ID"},
            default_row_limit=20,
        )
    )

    job_info = service.start_job(
        JobCreateRequest(
            question="請求金額を確認したい",
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            profile_id=profile.id,
        )
    )
    job = None
    for _ in range(20):
        job = service.get_job(job_info.job_id)
        if job and job.status == JobStatus.DONE:
            break
        await asyncio.sleep(0.01)

    assert job is not None
    assert job.status == JobStatus.DONE
    history_item = service.list_history().items[0]
    service.save_feedback(history_item.id, FeedbackRating.GOOD, "永続化された feedback")

    reloaded = Nl2SqlService(store=store)
    assert reloaded.get_profile(profile.id).name == "永続化テスト"
    restored_job = reloaded.get_job(job_info.job_id)
    assert restored_job is not None
    assert restored_job.status == JobStatus.DONE
    restored_history = reloaded.list_history().items[0]
    assert restored_history.id == history_item.id
    assert restored_history.feedback_rating == FeedbackRating.GOOD
    assert restored_history.feedback_comment == "永続化された feedback"


def test_oracle_json_store_saves_loads_and_checks_snapshot() -> None:
    fake_db = _FakeOracleDb()
    store = OracleJsonNl2SqlStore(
        connection_factory=fake_db.connection,
        table_name="nl2sql_state_store",
    )

    assert store.table_name == "NL2SQL_STATE_STORE"
    assert store.load_snapshot() is None

    store.save_snapshot({"schema_version": 1, "profiles": [{"id": "default"}]})
    restored = store.load_snapshot()
    ready, message = store.check()

    assert ready is True
    assert "NL2SQL_STATE_STORE" in message
    assert restored == {"schema_version": 1, "profiles": [{"id": "default"}]}
    assert fake_db.commits >= 2
    assert any(sql.startswith("CREATE TABLE NL2SQL_STATE_STORE") for sql in fake_db.executed)
    assert any(sql.startswith("MERGE INTO NL2SQL_STATE_STORE") for sql in fake_db.executed)
    assert any(
        sql.startswith("SELECT state_json FROM NL2SQL_STATE_STORE") for sql in fake_db.executed
    )


def test_oracle_json_store_rejects_unsafe_table_name() -> None:
    try:
        OracleJsonNl2SqlStore(connection_factory=_FakeOracleDb().connection, table_name="X;DROP")
    except ValueError as exc:
        assert "table name" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("unsafe table name must be rejected")


def test_oracle_adapter_refresh_select_ai_profile_executes_dbms_cloud_ai() -> None:
    fake_db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    meta = adapter.refresh_select_ai_profile(
        profile_name="NL2SQL_DEFAULT_PROFILE",
        allowed_tables=["INVOICES", "APP.CUSTOMERS"],
        row_limit=50,
        description="請求 profile",
    )

    assert meta["package"] == "DBMS_CLOUD_AI"
    assert meta["runtime"] == "oracle"
    assert meta["profile_name"] == "NL2SQL_DEFAULT_PROFILE"
    assert "max_rows" not in meta["profile_attributes"]
    assert "description" not in meta["profile_attributes"]
    assert {"owner": "APP", "name": "CUSTOMERS"} in meta["profile_attributes"]["object_list"]
    assert any("DBMS_CLOUD_AI.DROP_PROFILE" in sql for sql in fake_db.executed)
    assert any("DBMS_CLOUD_AI.CREATE_PROFILE" in sql for sql in fake_db.executed)


def test_oracle_adapter_fetch_catalog_includes_constraints_row_counts_and_samples() -> None:
    fake_db = _FakeOracleDb()
    fake_db.catalog_rows = [
        (
            "INVOICES",
            "請求",
            "INVOICE_ID",
            "請求ID",
            "VARCHAR2",
            "N",
            1,
            1280,
        ),
        (
            "INVOICES",
            "請求",
            "CUSTOMER_NAME",
            "取引先名",
            "VARCHAR2",
            "Y",
            2,
            1280,
        ),
    ]
    fake_db.constraint_rows = [
        ("INVOICES", "PK_INVOICES", "P", "INVOICE_ID"),
        ("INVOICES", "UK_INVOICES_CUSTOMER", "U", "CUSTOMER_NAME"),
    ]
    fake_db.sample_values = {
        ("INVOICES", "INVOICE_ID"): ["INV-001", "INV-002"],
        ("INVOICES", "CUSTOMER_NAME"): ["青山商事", "東京製作所"],
    }
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    catalog = adapter.fetch_catalog()
    table = catalog.tables[0]

    assert table.table_name == "INVOICES"
    assert table.row_count == 1280
    assert table.constraints == [
        "PK_INVOICES P(INVOICE_ID)",
        "UK_INVOICES_CUSTOMER U(CUSTOMER_NAME)",
    ]
    assert table.columns[0].logical_name == "請求ID"
    assert table.columns[0].nullable is False
    assert table.columns[0].sample_values == ["INV-001", "INV-002"]
    assert table.columns[1].sample_values == ["青山商事", "東京製作所"]
    assert any("FROM user_tab_columns" in sql for sql in fake_db.executed)
    assert any("FROM user_constraints" in sql for sql in fake_db.executed)
    assert any('SELECT DISTINCT "INVOICE_ID"' in sql for sql in fake_db.executed)


def test_oracle_adapter_execute_select_coerces_lob_values() -> None:
    fake_db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    results = adapter.execute_select("SELECT CLOB_COL FROM T", 10)

    assert results.columns == ["CLOB_COL"]
    assert results.rows == [{"CLOB_COL": "long text"}]


def test_oracle_adapter_refresh_select_ai_agent_assets_executes_dbms_cloud_ai_agent() -> None:
    fake_db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    meta = adapter.refresh_select_ai_agent_assets(
        profile_name="NL2SQL_DEFAULT_PROFILE",
        tool_name="NL2SQL_DEFAULT_TOOL",
        agent_name="NL2SQL_DEFAULT_AGENT",
        task_name="NL2SQL_DEFAULT_TASK",
        team_name="NL2SQL_DEFAULT_TEAM",
        allowed_tables=["INVOICES"],
        row_limit=100,
        description="請求 agent",
    )

    assert meta["package"] == "DBMS_CLOUD_AI_AGENT"
    assert meta["runtime"] == "oracle"
    assert meta["select_ai_profile_meta"]["package"] == "DBMS_CLOUD_AI"
    assert meta["tool_attributes"]["tool_type"] == "SQL"
    assert meta["tool_attributes"]["tool_params"] == {"profile_name": "NL2SQL_DEFAULT_PROFILE"}
    assert meta["agent_attributes"]["tools"] == ["NL2SQL_DEFAULT_TOOL"]
    assert meta["task_attributes"]["tools"] == ["NL2SQL_DEFAULT_TOOL"]
    assert meta["task_attributes"]["enable_human_tool"] is False
    assert meta["team_attributes"] == {
        "agents": [{"name": "NL2SQL_DEFAULT_AGENT", "task": "NL2SQL_DEFAULT_TASK"}],
        "process": "sequential",
    }
    for procedure in [
        "CREATE_TOOL",
        "CREATE_AGENT",
        "CREATE_TASK",
        "CREATE_TEAM",
    ]:
        assert any(f"DBMS_CLOUD_AI_AGENT.{procedure}" in sql for sql in fake_db.executed)
    assert any("DBMS_CLOUD_AI.CREATE_PROFILE" in sql for sql in fake_db.executed)


def test_oracle_adapter_drop_select_ai_agent_assets_executes_best_effort_drops() -> None:
    fake_db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    meta = adapter.drop_select_ai_agent_assets(
        profile_name="NL2SQL_DEFAULT_PROFILE",
        tool_name="NL2SQL_DEFAULT_TOOL",
        agent_name="NL2SQL_DEFAULT_AGENT",
        task_name="NL2SQL_DEFAULT_TASK",
        team_name="NL2SQL_DEFAULT_TEAM",
    )

    assert meta["package"] == "DBMS_CLOUD_AI_AGENT"
    assert meta["runtime"] == "oracle"
    assert fake_db.commits == 1
    for procedure in [
        "DROP_TEAM",
        "DROP_TASK",
        "DROP_AGENT",
        "DROP_TOOL",
    ]:
        assert any(f"DBMS_CLOUD_AI_AGENT.{procedure}" in sql for sql in fake_db.executed)
    assert sum("DBMS_CLOUD_AI.DROP_PROFILE" in sql for sql in fake_db.executed) == 2


def test_oracle_adapter_wraps_unsupported_select_ai_agent_runtime() -> None:
    fake_db = _FakeOracleDb()
    fake_db.unsupported_agent_runtime = True
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    try:
        adapter.create_agent_conversation()
    except RuntimeError as exc:
        assert "Select AI Agent runtime API" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("unsupported Select AI Agent runtime must be wrapped")


def test_service_refresh_uses_oracle_adapter_when_runtime_is_oracle() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    select_ai = service.refresh_select_ai_profile(None)
    agent = service.refresh_select_ai_agent_assets(None)

    assert select_ai.status == "ready"
    assert select_ai.engine_meta["runtime"] == "oracle"
    assert select_ai.warning == ""
    assert agent.status == "ready"
    assert agent.engine_meta["runtime"] == "oracle"
    assert agent.asset_names["team"].endswith("_TEAM")
    assert any("DBMS_CLOUD_AI.CREATE_PROFILE" in sql for sql in fake_db.executed)
    assert any("DBMS_CLOUD_AI_AGENT.CREATE_TEAM" in sql for sql in fake_db.executed)


def test_service_cleanup_assets_dry_run_lists_targets_without_oracle_calls() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    cleanup = service.cleanup_select_ai_assets(
        profile_id=None,
        engines=[Nl2SqlEngine.SELECT_AI_AGENT],
        execute=False,
    )

    assert len(cleanup) == 1
    assert cleanup[0].status == "dry_run"
    assert cleanup[0].executed is False
    assert cleanup[0].asset_names["team"].endswith("_TEAM")
    assert fake_db.executed == []


def test_service_cleanup_assets_executes_oracle_drops_when_confirmed() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    cleanup = service.cleanup_select_ai_assets(
        profile_id=None,
        engines=[Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT],
        execute=True,
    )

    assert [item.status for item in cleanup] == ["cleaned", "cleaned"]
    assert all(item.executed for item in cleanup)
    assert any("DBMS_CLOUD_AI_AGENT.DROP_TEAM" in sql for sql in fake_db.executed)
    assert any("DBMS_CLOUD_AI.DROP_PROFILE" in sql for sql in fake_db.executed)


async def test_schema_import_csv_dry_run_parses_columns_and_rows() -> None:
    csv_text = "取引先名,請求金額,請求金額\n青山商事,12000,13000\n東京製作所,9800,\n"
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/schema/import-csv",
            json={
                "table_name": "sample invoices",
                "csv_text": csv_text,
                "execute": False,
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["table_name"] == "SAMPLE_INVOICES"
    assert data["dry_run"] is True
    assert data["executed"] is False
    assert data["row_count"] == 2
    assert [column["column_name"] for column in data["columns"]] == [
        "COLUMN_1",
        "COLUMN_2",
        "COLUMN_3",
    ]
    assert [column["source_name"] for column in data["columns"]] == [
        "取引先名",
        "請求金額",
        "請求金額",
    ]
    assert data["columns"][1]["data_type"] == "NUMBER"
    assert 'CREATE TABLE "SAMPLE_INVOICES"' in data["ddl"]
    assert data["sample_rows"][0]["COLUMN_2"] == "12000"


def test_service_import_csv_execute_uses_oracle_adapter() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    data = service.import_csv_sample(
        CsvImportRequest(
            table_name="imported_customers",
            csv_text="CUSTOMER_ID,CUSTOMER_NAME\n1,青山商事\n2,東京製作所\n",
            replace_existing=True,
            execute=True,
        )
    )

    assert data.executed is True
    assert data.dry_run is False
    assert data.table_name == "IMPORTED_CUSTOMERS"
    assert any('DROP TABLE "IMPORTED_CUSTOMERS" PURGE' in sql for sql in fake_db.executed)
    assert any('CREATE TABLE "IMPORTED_CUSTOMERS"' in sql for sql in fake_db.executed)
    assert fake_db.insert_batches
    insert_sql, rows = fake_db.insert_batches[0]
    assert 'INSERT INTO "IMPORTED_CUSTOMERS"' in insert_sql
    assert rows[0]["c0"] == 1
    assert rows[0]["c1"] == "青山商事"
    assert fake_db.commits >= 1


def test_service_import_csv_execute_stays_dry_run_without_oracle_runtime() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())

    data = service.import_csv_sample(
        CsvImportRequest(
            table_name="local_import",
            csv_text="ID,NAME\n1,local\n",
            execute=True,
        )
    )

    assert data.executed is False
    assert data.dry_run is True
    assert any("deterministic runtime" in warning for warning in data.warnings)
