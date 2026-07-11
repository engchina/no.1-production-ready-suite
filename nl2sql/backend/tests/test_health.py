"""health / NL2SQL preview の疎通テスト（Oracle 不要）。"""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest

from app.features.nl2sql.models import (
    AgentTeamRunRequest,
    CompareRequest,
    EvaluateRequest,
    EvaluationSetUpsertRequest,
    FeedbackIndexRequest,
    FeedbackRating,
    HistoryItem,
    JobCreateRequest,
    JobStatus,
    Nl2SqlEngine,
    Nl2SqlProfile,
    PreviewRequest,
    SampleDataMutationRequest,
    SampleDataStep,
    SchemaCatalog,
    SchemaTable,
    SelectAiFeedbackAddRequest,
    SelectAiFeedbackDeleteRequest,
    SelectAiFeedbackVectorIndexRequest,
    SimilarHistoryRequest,
    SyntheticCase,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter, _extract_select_statement
from app.features.nl2sql.router import is_select_only
from app.features.nl2sql.service import Nl2SqlService, _extract_referenced_tables
from app.features.nl2sql.store import MemoryNl2SqlStore, OracleJsonNl2SqlStore
from app.main import app
from app.settings import get_settings


def _transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=app)


class _FakeOracleDb:
    def __init__(self) -> None:
        self.state_json: object = ""
        self.executed: list[str] = []
        self.executed_params: list[dict[str, object] | None] = []
        self.input_sizes: list[dict[str, object]] = []
        self.insert_batches: list[tuple[str, list[dict[str, object]]]] = []
        self.commits = 0
        self.catalog_rows: list[tuple[object, ...]] = []
        self.constraint_rows: list[tuple[object, ...]] = []
        self.sample_values: dict[tuple[str, str], list[object]] = {}
        self.feedback_vector_rows: list[tuple[object, ...]] = []
        self.select_ai_feedback_rows: list[tuple[object, ...]] = []
        self.select_ai_feedback_missing = False
        self.unsupported_agent_runtime = False
        self.run_team_signature_failures = 0
        self.run_team_calls = 0
        self.run_team_profile_loss = False
        self.create_team_profile_exists_failures = 0
        self.create_team_calls = 0
        self.synthetic_function_signature_failures = 0
        self.synthetic_procedure_calls = 0

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
        self._row: tuple[object, ...] | None = None
        self._rows: list[tuple[object, ...]] = []
        self.description: list[tuple[str]] = []

    def __enter__(self) -> "_FakeOracleCursor":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
        normalized_sql = " ".join(sql.split())
        self.db.executed.append(normalized_sql)
        self.db.executed_params.append(dict(params) if params is not None else None)
        self._row = None
        self._rows = []
        if self.db.unsupported_agent_runtime and "DBMS_CLOUD_AI_AGENT" in normalized_sql:
            raise RuntimeError("ORA-00904: invalid identifier")
        if "DBMS_CLOUD_AI_AGENT.CREATE_TEAM" in normalized_sql:
            self.db.create_team_calls += 1
            if self.db.create_team_calls <= self.db.create_team_profile_exists_failures:
                raise RuntimeError("ORA-20046: Profile AGENT$NL2SQL_DEFAULT_TEAM already exists.")
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
        elif "VECTOR_DISTANCE" in normalized_sql:
            self._rows = self.db.feedback_vector_rows
        elif "FEEDBACK_VECINDEX$VECTAB" in normalized_sql:
            if self.db.select_ai_feedback_missing:
                raise RuntimeError("ORA-00942: table or view does not exist")
            self.description = [("CONTENT",), ("SQL_ID",), ("SQL_TEXT",), ("ATTRIBUTES",)]
            self._rows = self.db.select_ai_feedback_rows
        elif (
            "DBMS_CLOUD_AI.FEEDBACK" in normalized_sql
            or "DBMS_CLOUD_AI.UPDATE_VECTOR_INDEX" in normalized_sql
        ):
            if self.db.select_ai_feedback_missing:
                raise RuntimeError("ORA-00942: table or view does not exist")
        elif "DBMS_CLOUD_AI_AGENT.CREATE_CONVERSATION" in normalized_sql:
            self._row = ("conversation-001",)
        elif "DBMS_CLOUD_AI.GENERATE(" in normalized_sql:
            self._row = ("SELECT TOTAL_AMOUNT FROM INVOICES",)
        elif "DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA" in normalized_sql:
            if normalized_sql.startswith("SELECT"):
                if self.db.synthetic_function_signature_failures > 0:
                    self.db.synthetic_function_signature_failures -= 1
                    raise RuntimeError(
                        'ORA-00904: "DBMS_CLOUD_AI"."GENERATE_SYNTHETIC_DATA": '
                        "invalid identifier"
                    )
                self._row = ("operation-001",)
            else:
                self.db.synthetic_procedure_calls += 1
        elif "DBMS_CLOUD_AI_AGENT.RUN_TEAM" in normalized_sql:
            self.db.run_team_calls += 1
            if self.db.run_team_profile_loss:
                raise RuntimeError("ORA-20046: Invalid profile")
            if self.db.run_team_calls <= self.db.run_team_signature_failures:
                raise RuntimeError(
                    "ORA-06553: PLS-306: wrong number or types of arguments "
                    "in call to 'RUN_TEAM'"
                )
            self._row = ('{"sql":"SELECT TOTAL_AMOUNT FROM INVOICES"}',)
        elif "DBMS_CLOUD_AI_AGENT.RUN_TOOL" in normalized_sql:
            self._row = ("SELECT TOTAL_AMOUNT FROM INVOICES",)

    def setinputsizes(self, **kwargs: object) -> None:
        self.db.input_sizes.append(kwargs)

    def executemany(self, sql: str, rows: list[dict[str, object]]) -> None:
        normalized_sql = " ".join(sql.split())
        self.db.executed.append(normalized_sql)
        self.db.insert_batches.append((normalized_sql, rows))

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(self._rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row

    def fetchmany(self, _max_rows: int) -> list[tuple[object, ...]]:
        return self._rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _FakeLob:
    def __init__(self, value: str | bytes) -> None:
        self.value = value

    def read(self) -> str | bytes:
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


class _QuestionCaptureOracleAdapter(_FakeRuntimeOracleAdapter):
    def __init__(self, db: _FakeOracleDb) -> None:
        super().__init__(db)
        self.questions: list[str] = []
        self.attributes: list[dict[str, str] | None] = []

    def generate_select_ai_sql(
        self,
        *,
        profile_name: str,
        question: str,
        action: str = "showsql",
        attributes: dict[str, str] | None = None,
    ) -> str:
        del profile_name, action
        self.questions.append(question)
        self.attributes.append(attributes)
        return "SELECT TOTAL_AMOUNT FROM INVOICES"

    def run_select_ai_agent_team(
        self, *, team_name: str, question: str, tool_name: str | None = None
    ) -> tuple[str, str]:
        self.questions.append(question)
        return "SELECT TOTAL_AMOUNT FROM INVOICES", "conversation-001"


class _SampleAdminOracleAdapter(_FakeRuntimeOracleAdapter):
    def __init__(self, db: _FakeOracleDb, *, missing_objects: bool = False) -> None:
        super().__init__(db)
        self.missing_objects = missing_objects
        self.admin_statements: list[str] = []
        self.admin_execute_calls = 0

    def execute_admin_statements(
        self, statements: list[str], *, atomic: bool = True
    ) -> list[dict[str, Any]]:
        _ = atomic
        self.admin_execute_calls += 1
        self.admin_statements.extend(statements)
        results: list[dict[str, Any]] = []
        for index, statement in enumerate(statements, start=1):
            statement_type = statement.split(None, 1)[0].upper()
            if self.missing_objects:
                results.append(
                    {
                        "index": index,
                        "statement_type": statement_type,
                        "status": "error",
                        "sql": statement,
                        "error_message": "ORA-00942: table or view does not exist",
                    }
                )
            else:
                results.append(
                    {
                        "index": index,
                        "statement_type": statement_type,
                        "status": "success",
                        "sql": statement,
                        "message": "ok",
                    }
                )
        return results

    def fetch_catalog(self) -> SchemaCatalog:
        return SchemaCatalog(
            refreshed_at="2026-06-23T00:00:00+00:00",
            tables=[
                SchemaTable(table_name=name, logical_name=name)
                for name in [
                    "DEPARTMENT",
                    "EMPLOYEE",
                    "PROJECT",
                    "V_EMP_DEPT",
                    "V_DEPT_PROJECT",
                ]
            ],
        )


class _FakeEmbeddingClient:
    def is_configured(self) -> bool:
        return True

    def module_available(self) -> bool:
        return True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.01 for _ in range(1536)] for _text in texts]


class _OracleRuntimeNl2SqlService(Nl2SqlService):
    def __init__(self, adapter: OracleNl2SqlAdapter) -> None:
        super().__init__(store=MemoryNl2SqlStore())
        self._oracle_adapter = adapter
        self._embedding_client = _FakeEmbeddingClient()

    def _use_oracle_runtime(self) -> bool:
        return True


class _FakeEnterpriseAiClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, str]] = []

    def is_configured(self) -> bool:
        return True

    def model_id(self) -> str:
        return "enterprise-nl2sql-model"

    def generate(self, *, prompt: str, context: str, system_prompt: str) -> str:
        self.calls.append({"prompt": prompt, "context": context, "system_prompt": system_prompt})
        return self.text


def _import_sample(service: Nl2SqlService) -> None:
    service.import_sample_data(
        SampleDataMutationRequest(
            step=SampleDataStep.ALL,
            confirmation="SQL_ASSIST_SAMPLE",
        )
    )


async def _api_import_sample(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/nl2sql/sample-data/import",
        json={"step": "all", "confirmation": "SQL_ASSIST_SAMPLE"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["executed"] is True


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


async def test_nl2sql_starts_with_empty_business_catalog_and_profile() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post("/api/nl2sql/preview", json={"question": "売上トップ10は?"})
        catalog_resp = await client.get("/api/schema/catalog")
        profiles_resp = await client.get("/api/nl2sql/profiles")

    assert resp.status_code == 400
    assert "Schema catalog が空です" in " ".join(resp.json()["error_messages"])
    assert catalog_resp.status_code == 200
    assert catalog_resp.json()["data"]["tables"] == []
    default_profile = next(
        profile for profile in profiles_resp.json()["data"] if profile["id"] == "default"
    )
    assert default_profile["allowed_tables"] == []
    assert default_profile["glossary"] == {}
    assert default_profile["few_shot_examples"] == []


async def test_sample_import_enables_preview_and_delete() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        info_resp = await client.get("/api/nl2sql/sample-data")
        assert info_resp.status_code == 200
        assert info_resp.json()["data"]["imported_objects"] == []

        legacy_execute_resp = await client.post(
            "/api/nl2sql/sample-data/import",
            json={"step": "tables", "execute": False},
        )
        assert legacy_execute_resp.status_code == 422

        await _api_import_sample(client)
        catalog_resp = await client.get("/api/schema/catalog")
        assert catalog_resp.status_code == 200
        table_names = {table["table_name"] for table in catalog_resp.json()["data"]["tables"]}
        assert {"DEPARTMENT", "EMPLOYEE", "PROJECT", "V_EMP_DEPT", "V_DEPT_PROJECT"} <= table_names

        resp = await client.post(
            "/api/nl2sql/preview",
            json={"question": "社員一覧を見たい", "profile_id": "sql_assist_sample"},
        )
        assert resp.status_code == 200
        delete_resp = await client.post(
            "/api/nl2sql/sample-data/delete",
            json={"confirmation": "SQL_ASSIST_SAMPLE"},
        )

    data = resp.json()["data"]
    assert data["is_safe"] is True
    assert data["sql"].lower().startswith("select")
    assert data["engine"] == "select_ai_agent"
    assert data["timing"]["elapsed_ms"] >= 0
    assert delete_resp.status_code == 200
    assert delete_resp.json()["data"]["executed"] is True


def test_enterprise_ai_direct_preview_uses_configured_client() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    _import_sample(service)
    fake_client = _FakeEnterpriseAiClient(
        '{"sql":"SELECT EMPLOYEE_NAME, DEPARTMENT_NAME FROM V_EMP_DEPT",'
        '"explanation":"社員と部署を取得します。"}'
    )
    service._enterprise_ai_client = fake_client

    preview = service.preview(
        PreviewRequest(
            question="社員と部署を確認したい",
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            profile_id="sql_assist_sample",
        )
    )

    assert preview.engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT
    assert preview.sql == "SELECT EMPLOYEE_NAME, DEPARTMENT_NAME FROM V_EMP_DEPT"
    assert preview.engine_meta["runtime"] == "oci_enterprise_ai"
    assert preview.engine_meta["model"] == "enterprise-nl2sql-model"
    assert preview.executable_sql.endswith("FETCH FIRST 100 ROWS ONLY")
    assert fake_client.calls
    assert "EMPLOYEE" in fake_client.calls[0]["context"]
    assert "V_EMP_DEPT" in fake_client.calls[0]["context"]
    assert "SELECT または WITH" in fake_client.calls[0]["system_prompt"]


def test_oracle_runtime_question_includes_learning_examples() -> None:
    adapter = _QuestionCaptureOracleAdapter(_FakeOracleDb())
    service = _OracleRuntimeNl2SqlService(adapter)
    profile = service.create_profile(
        Nl2SqlProfile(
            id="oracle_fixture_profile",
            name="Oracle fixture",
            few_shot_examples=[
                {
                    "question": "請求金額を一覧したい",
                    "sql": "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
                }
            ],
        )
    )

    preview = service.preview(
        PreviewRequest(
            question="請求金額を確認したい",
            engine=Nl2SqlEngine.SELECT_AI,
            profile_id=profile.id,
        )
    )

    assert preview.sql == "SELECT TOTAL_AMOUNT FROM INVOICES"
    assert adapter.questions
    assert "learning_examples:" in adapter.questions[0]
    assert "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES" in adapter.questions[0]
    assert "今回の質問:" in adapter.questions[0]
    assert "請求金額を確認したい" in adapter.questions[0]


def test_select_ai_request_overrides_use_effective_profile_context() -> None:
    adapter = _QuestionCaptureOracleAdapter(_FakeOracleDb())
    service = _OracleRuntimeNl2SqlService(adapter)
    profile = service.create_profile(
        Nl2SqlProfile(
            id="finance_context",
            name="財務分析",
            description="請求と入金を分析します。",
            glossary={"四半期": "4 月開始の会計四半期", "売上": "INVOICES.TOTAL_AMOUNT"},
            sql_rules=["SELECT/WITH のみ", "日付は DATE 型で返す"],
            select_ai_config={
                "role": "既定の財務 SQL アシスタント",
                "additional_instructions": "金額は円単位で表示する。",
            },
        )
    )

    preview = service.preview(
        PreviewRequest(
            question="前四半期の売上を確認したい",
            engine=Nl2SqlEngine.SELECT_AI,
            profile_id=profile.id,
            select_ai_overrides={
                "role": "今回だけ CFO 向けに説明するアシスタント",
                "additional_instructions": "現在日付を基準にしてください。",
            },
        )
    )

    assert adapter.attributes
    attributes = adapter.attributes[0] or {}
    assert attributes["role"] == "今回だけ CFO 向けに説明するアシスタント"
    instructions = attributes["additional_instructions"]
    assert instructions.index("## 業務説明") < instructions.index("## 業務用語集")
    assert instructions.index("- 四半期:") < instructions.index("- 売上:")
    assert "## SQL 生成ルール" in instructions
    assert "## プロファイル追加指示" in instructions
    assert instructions.endswith("## 今回の追加指示\n現在日付を基準にしてください。")
    assert preview.engine_meta["select_ai_role_applied"] is True
    assert preview.engine_meta["select_ai_additional_instructions_length"] == len(instructions)
    assert "現在日付を基準にしてください。" not in str(preview.engine_meta)


def test_select_ai_job_passes_request_overrides_to_oracle() -> None:
    adapter = _QuestionCaptureOracleAdapter(_FakeOracleDb())
    service = _OracleRuntimeNl2SqlService(adapter)
    profile = service.create_profile(
        Nl2SqlProfile(
            id="job_context",
            name="ジョブ文脈",
            allowed_tables=["INVOICES"],
            select_ai_config={"role": "既定ロール"},
        )
    )

    created = service.start_job(
        JobCreateRequest(
            question="請求を確認したい",
            engine=Nl2SqlEngine.SELECT_AI,
            profile_id=profile.id,
            select_ai_overrides={
                "role": "ジョブ専用ロール",
                "additional_instructions": "最新月だけを対象にする。",
            },
        )
    )
    job = service.get_job(created.job_id)
    for _ in range(100):
        if job is not None and job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
            break
        time.sleep(0.01)
        job = service.get_job(created.job_id)

    assert job is not None
    assert job.status == JobStatus.DONE
    assert adapter.attributes[0] is not None
    assert adapter.attributes[0]["role"] == "ジョブ専用ロール"
    assert "## 今回の追加指示\n最新月だけを対象にする。" in adapter.attributes[0][
        "additional_instructions"
    ]


def test_oracle_adapter_generate_select_ai_sql_binds_unicode_attributes() -> None:
    fake_db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    sql = adapter.generate_select_ai_sql(
        profile_name="FINANCE_PROFILE",
        question="前四半期の売上は?",
        attributes={
            "role": "財務 SQL アシスタント",
            "additional_instructions": "日付は DATE 型で返す。",
        },
    )

    assert sql == "SELECT TOTAL_AMOUNT FROM INVOICES"
    assert "attributes => :attributes" in fake_db.executed[-1]
    params = fake_db.executed_params[-1] or {}
    assert '"role": "財務 SQL アシスタント"' in str(params["attributes"])


async def test_select_ai_overrides_require_select_ai_engine() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        preview_resp = await client.post(
            "/api/nl2sql/preview",
            json={
                "question": "売上を確認したい",
                "engine": "auto",
                "select_ai_overrides": {"role": "財務アシスタント"},
            },
        )
        job_resp = await client.post(
            "/api/nl2sql/jobs",
            json={
                "question": "売上を確認したい",
                "engine": "enterprise_ai_direct",
                "select_ai_overrides": {"additional_instructions": "円で表示"},
            },
        )

    assert preview_resp.status_code == 422
    assert job_resp.status_code == 422


def test_oracle_select_ai_extracts_sql_from_error_wrapped_response() -> None:
    raw = (
        "Sorry, unfortunately a valid SELECT statement could not be generated. "
        'SELECT t1."name", SUM(t2."amount") FROM "owner"."trading_partners" t1 '
        'JOIN "owner"."bills" t2 ON t1."id" = t2."trading_partner_id" '
        "Exception encountered: ORA-00942: table or view does not exist"
    )

    sql = _extract_select_statement(raw)

    assert sql == (
        'SELECT t1."name", SUM(t2."amount") FROM "owner"."trading_partners" t1 '
        'JOIN "owner"."bills" t2 ON t1."id" = t2."trading_partner_id"'
    )


def test_referenced_tables_include_quoted_schema_qualified_names() -> None:
    sql = (
        'SELECT * FROM "owner"."trading_partners" t '
        'JOIN "owner"."bills" b ON t."id" = b."trading_partner_id"'
    )

    assert _extract_referenced_tables(sql) == ["TRADING_PARTNERS", "BILLS"]


def test_select_only_guard() -> None:
    assert is_select_only("SELECT * FROM t") is True
    assert is_select_only("WITH x AS (SELECT 1) SELECT * FROM x") is True
    assert is_select_only("DELETE FROM t") is False
    assert is_select_only("drop table t") is False
    assert is_select_only("SELECT * FROM t; DELETE FROM t") is False


async def test_schema_catalog_returns_tables_for_picker() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        await _api_import_sample(client)
        resp = await client.get("/api/schema/catalog")
    assert resp.status_code == 200
    tables = resp.json()["data"]["tables"]
    assert {table["table_name"] for table in tables} >= {
        "DEPARTMENT",
        "EMPLOYEE",
        "PROJECT",
    }
    assert tables[0]["columns"][0]["logical_name"]


def test_sample_data_oracle_fake_import_and_repeated_delete_warning() -> None:
    adapter = _SampleAdminOracleAdapter(_FakeOracleDb())
    service = _OracleRuntimeNl2SqlService(adapter)

    imported = service.import_sample_data(
        SampleDataMutationRequest(confirmation="SQL_ASSIST_SAMPLE")
    )

    assert imported.executed is True
    assert imported.runtime == "oracle"
    assert adapter.admin_execute_calls == 1
    assert any("CREATE TABLE DEPARTMENT" in statement for statement in adapter.admin_statements)
    assert len(adapter.admin_statements) > 5
    assert adapter.admin_statements[0].startswith("CREATE TABLE DEPARTMENT")
    assert adapter.admin_statements[1].startswith("CREATE TABLE EMPLOYEE")
    assert adapter.admin_statements[2].startswith("CREATE TABLE PROJECT")
    assert not any(
        "CREATE TABLE DEPARTMENT" in statement and "CREATE TABLE EMPLOYEE" in statement
        for statement in adapter.admin_statements
    )
    profile = service.get_profile(imported.profile_id)
    assert set(profile.allowed_tables) == {
        "DEPARTMENT",
        "EMPLOYEE",
        "PROJECT",
        "V_EMP_DEPT",
        "V_DEPT_PROJECT",
    }

    missing_adapter = _SampleAdminOracleAdapter(_FakeOracleDb(), missing_objects=True)
    service._oracle_adapter = missing_adapter
    deleted = service.delete_sample_data(
        SampleDataMutationRequest(confirmation="SQL_ASSIST_SAMPLE")
    )

    assert deleted.executed is True
    assert {statement.status for statement in deleted.statements} == {"skipped_missing_object"}
    assert deleted.warnings
    assert missing_adapter.admin_execute_calls == 1
    assert all("DROP" in statement for statement in missing_adapter.admin_statements)

    rejected = service.import_sample_data(
        SampleDataMutationRequest(confirmation="WRONG")
    )

    assert rejected.executed is False
    assert {statement.status for statement in rejected.statements} == {"confirmation_required"}


async def test_job_supports_select_ai_agent_and_timing() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        await _api_import_sample(client)
        resp = await client.post(
            "/api/nl2sql/jobs",
            json={
                "question": "社員一覧を見たい",
                "engine": "select_ai_agent",
                "allowed_objects": {
                    "table_names": ["EMPLOYEE"],
                    "columns": {"EMPLOYEE": ["EMPLOYEE_ID"]},
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
        await _api_import_sample(client)
        resp = await client.post(
            "/api/nl2sql/preview",
            json={"question": "select_ai_agent_fail 社員一覧", "engine": "auto"},
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


async def test_repair_oracle_error_replaces_invalid_column_with_allowed_columns() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/nl2sql/repair",
            json={
                "sql": "SELECT BAD_COLUMN FROM INVOICES",
                "error_message": 'ORA-00904: "BAD_COLUMN": invalid identifier',
                "allowed_objects": {
                    "table_names": ["INVOICES"],
                    "columns": {"INVOICES": ["INVOICE_ID", "TOTAL_AMOUNT"]},
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["error_code"] == "ORA-00904"
    assert data["repaired_sql"] == (
        "SELECT INVOICE_ID, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY"
    )
    assert data["safety"]["is_safe"] is True
    assert "列名" in data["explanation"]


async def test_repair_oracle_error_converts_limit_syntax() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/nl2sql/repair",
            json={
                "sql": "SELECT INVOICE_ID FROM INVOICES LIMIT 10;",
                "error_message": "ORA-00933: SQL command not properly ended",
            },
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["error_code"] == "ORA-00933"
    assert data["repaired_sql"] == ("SELECT INVOICE_ID FROM INVOICES FETCH FIRST 100 ROWS ONLY")
    assert "FETCH FIRST" in " ".join(data["recommendations"])


async def test_asset_refresh_feedback_and_evaluation() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        await _api_import_sample(client)
        profile_resp = await client.post("/api/nl2sql/select-ai-agent/assets/refresh")
        assert profile_resp.status_code == 200
        asset_data = profile_resp.json()["data"]
        assert asset_data["team_name"]
        assert asset_data["status"] == "ready"
        assert {"profile", "tool", "agent", "task", "team"} <= set(asset_data["asset_names"])

        eval_resp = await client.post(
            "/api/nl2sql/evaluate",
            json={
                "cases": [
                    {"question": "社員一覧", "expected_sql": "SELECT EMPLOYEE_ID FROM EMPLOYEE"}
                ]
            },
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

        archived_list_resp = await client.get(
            "/api/nl2sql/profiles", params={"include_archived": "true"}
        )
        assert archived_list_resp.status_code == 200
        archived_profiles = archived_list_resp.json()["data"]
        assert any(
            profile["id"] == profile_id and profile["archived"]
            for profile in archived_profiles
        )

        restore_resp = await client.post(f"/api/nl2sql/profiles/{profile_id}/restore")
        assert restore_resp.status_code == 200
        assert restore_resp.json()["data"]["archived"] is False
        restored_list_resp = await client.get("/api/nl2sql/profiles")
        assert profile_id in {
            profile["id"] for profile in restored_list_resp.json()["data"]
        }

        missing_restore_resp = await client.post(
            "/api/nl2sql/profiles/profile-does-not-exist/restore"
        )
        assert missing_restore_resp.status_code == 404

    assert list_resp.status_code == 200
    assert profile_id not in {profile["id"] for profile in list_resp.json()["data"]}


async def test_profile_training_examples_update_and_evaluate() -> None:
    payload = {
        "name": "モデル訓練",
        "description": "few-shot 訓練データを管理する profile",
        "allowed_tables": ["EMPLOYEE"],
        "glossary": {"社員": "EMPLOYEE.EMPLOYEE_NAME"},
        "sql_rules": ["SELECT/WITH のみ"],
        "default_row_limit": 25,
        "safety_policy": "select_only",
        "few_shot_examples": [],
    }
    training_examples = [
        {"question": "社員名を見たい", "sql": "SELECT EMPLOYEE_NAME FROM EMPLOYEE"},
        {"question": "部署 ID を見たい", "sql": "SELECT DEPARTMENT_ID FROM EMPLOYEE"},
    ]
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        await _api_import_sample(client)
        create_resp = await client.post("/api/nl2sql/profiles", json=payload)
        assert create_resp.status_code == 200
        profile_id = create_resp.json()["data"]["id"]

        update_resp = await client.patch(
            f"/api/nl2sql/profiles/{profile_id}",
            json={**payload, "few_shot_examples": training_examples},
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()["data"]
        assert updated["few_shot_examples"] == training_examples

        eval_resp = await client.post(
            "/api/nl2sql/evaluate",
            json={
                "cases": [
                    {"question": item["question"], "expected_sql": item["sql"]}
                    for item in updated["few_shot_examples"]
                ],
                "engine": "auto",
            },
        )
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()["data"]
        assert eval_data["total_cases"] == 2
        assert eval_data["executable_rate"] == 1.0
        assert eval_data["select_only_rate"] == 1.0

        archive_resp = await client.post(f"/api/nl2sql/profiles/{profile_id}/archive")
        assert archive_resp.status_code == 200


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
        await _api_import_sample(client)
        job_resp = await client.post(
            "/api/nl2sql/jobs",
            json={"question": "社員一覧を確認したい", "engine": "select_ai_agent"},
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

        index_status_resp = await client.get("/api/nl2sql/feedback-index")
        assert index_status_resp.status_code == 200
        index_status = index_status_resp.json()["data"]
        assert index_status["vector_dimension"] == 1536
        assert index_status["vector_backend"] == "oracle_26ai"
        assert index_status["indexable_count"] >= 1

        legacy_rebuild_resp = await client.post(
            "/api/nl2sql/feedback-index/rebuild", json={"execute": False}
        )
        assert legacy_rebuild_resp.status_code == 422

        rebuild_resp = await client.post("/api/nl2sql/feedback-index/rebuild", json={})
        assert rebuild_resp.status_code == 200
        rebuild_data = rebuild_resp.json()["data"]
        assert rebuild_data["executed"] is False
        assert rebuild_data["status"] == "stale"
        assert "NL2SQL_RUNTIME_MODE=oracle" in " ".join(rebuild_data["warnings"])

        similar_resp = await client.post(
            "/api/nl2sql/similar-history",
            json={"question": "社員一覧をもう一度確認したい", "profile_id": "default"},
        )
        assert similar_resp.status_code == 200
        similar = similar_resp.json()["data"]["items"]
        assert similar
        assert similar[0]["item"]["feedback_rating"] == "good"
        assert similar[0]["score"] > 0

        preview_resp = await client.post(
            "/api/nl2sql/preview",
            json={"question": "社員一覧をもう一度確認したい", "profile_id": "default"},
        )
        assert preview_resp.status_code == 200
        examples = preview_resp.json()["data"]["engine_meta"]["similar_history_examples"]
        assert examples[0]["history_id"] == history_item["id"]

        clear_resp = await client.post("/api/nl2sql/feedback-index/clear", json={})
        assert clear_resp.status_code == 200
        clear_data = clear_resp.json()["data"]
        assert clear_data["executed"] is False
        assert "NL2SQL_RUNTIME_MODE=oracle" in " ".join(clear_data["warnings"])


async def test_demo_learning_seed_no_longer_creates_fixed_business_history() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        seed_resp = await client.post("/api/nl2sql/demo/learning")
        assert seed_resp.status_code == 200
        seed_data = seed_resp.json()["data"]
        assert seed_data["history_ids"] == []
        assert seed_data["profile_ids"] == []
        assert seed_data["seeded_history_count"] == 0
        assert seed_data["seeded_feedback_count"] == 0
        assert "sample data" in seed_data["message"]

        history_resp = await client.get("/api/nl2sql/history")
        assert history_resp.status_code == 200
        history_items = history_resp.json()["data"]["items"]
        assert not any(str(item["id"]).startswith("demo-learning-") for item in history_items)

        index_resp = await client.get("/api/nl2sql/feedback-index")
        assert index_resp.status_code == 200
        assert index_resp.json()["data"]["indexable_count"] >= 0


async def test_compare_reverse_comments_synthetic_and_diagnostics() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        await _api_import_sample(client)
        compare_resp = await client.post(
            "/api/nl2sql/compare", json={"question": "社員一覧を見たい"}
        )
        assert compare_resp.status_code == 200
        compare_data = compare_resp.json()["data"]
        assert len(compare_data["results"]) >= 2
        assert compare_data["recommendation"]
        assert compare_data["execution_results"] == []

        compare_execute_resp = await client.post(
            "/api/nl2sql/compare", json={"question": "社員一覧を見たい", "execute": True}
        )
        assert compare_execute_resp.status_code == 200
        compare_execute_data = compare_execute_resp.json()["data"]
        assert len(compare_execute_data["execution_results"]) == len(
            compare_execute_data["results"]
        )
        assert 0 <= compare_execute_data["error_rate"] <= 1
        assert compare_execute_data["execution_results"][0]["row_count"] >= 0

        compare_history_resp = await client.get("/api/nl2sql/compare-history")
        assert compare_history_resp.status_code == 200
        compare_history = compare_history_resp.json()["data"]["items"]
        assert compare_history
        assert compare_history[0]["question"] == "社員一覧を見たい"
        assert compare_history[0]["comparison"]["recommendation"]
        assert "NL2SQL engine comparison" in compare_history[0]["report"]
        assert "SELECT" in compare_history[0]["report"]

        reverse_resp = await client.post(
            "/api/nl2sql/reverse", json={"sql": "SELECT EMPLOYEE_NAME FROM EMPLOYEE"}
        )
        assert reverse_resp.status_code == 200
        assert reverse_resp.json()["data"]["referenced_tables"] == ["EMPLOYEE"]

        comments_resp = await client.post("/api/nl2sql/comments/suggest")
        assert comments_resp.status_code == 200
        assert comments_resp.json()["data"]["suggestions"]

        apply_resp = await client.post(
            "/api/nl2sql/comments/apply",
            json={
                "items": [
                    {
                        "object_name": "EMPLOYEE",
                        "object_type": "table",
                        "comment": "社員情報's 確認",
                    },
                    {
                        "object_name": "EMPLOYEE.EMPLOYEE_NAME",
                        "object_type": "column",
                        "comment": "社員名",
                    },
                ],
            },
        )
        assert apply_resp.status_code == 200
        apply_data = apply_resp.json()["data"]
        assert apply_data["executed"] is False
        assert apply_data["statements"][0]["status"] == "confirmation_required"
        assert (
            apply_data["statements"][0]["sql"]
            == "COMMENT ON TABLE \"EMPLOYEE\" IS '社員情報''s 確認';"
        )
        assert (
            apply_data["statements"][1]["sql"]
            == 'COMMENT ON COLUMN "EMPLOYEE"."EMPLOYEE_NAME" IS \'社員名\';'
        )

        execute_apply_resp = await client.post(
            "/api/nl2sql/comments/apply",
            json={
                "items": [
                    {
                        "object_name": "EMPLOYEE.EMPLOYEE_NAME",
                        "object_type": "column",
                        "comment": "社員名",
                    }
                ],
            },
        )
        assert execute_apply_resp.status_code == 200
        execute_apply_data = execute_apply_resp.json()["data"]
        assert execute_apply_data["executed"] is False
        assert execute_apply_data["statements"][0]["status"] == "confirmation_required"
        assert "confirmation" in " ".join(execute_apply_data["warnings"]).lower()

        confirmed_apply_resp = await client.post(
            "/api/nl2sql/comments/apply",
            json={
                "items": [
                    {
                        "object_name": "EMPLOYEE.EMPLOYEE_NAME",
                        "object_type": "column",
                        "comment": "社員名",
                    }
                ],
                "confirmation": "ADMIN_EXECUTE",
            },
        )
        assert confirmed_apply_resp.status_code == 200
        confirmed_apply_data = confirmed_apply_resp.json()["data"]
        assert confirmed_apply_data["executed"] is False
        assert confirmed_apply_data["statements"][0]["status"] == "requires_oracle"
        assert "NL2SQL_RUNTIME_MODE=oracle" in " ".join(confirmed_apply_data["warnings"])

        synthetic_resp = await client.post("/api/nl2sql/synthetic-cases")
        assert synthetic_resp.status_code == 200
        assert synthetic_resp.json()["data"]["cases"]

        diagnostics_resp = await client.get("/api/nl2sql/diagnostics")
        assert diagnostics_resp.status_code == 200
        diagnostics_data = diagnostics_resp.json()["data"]
        checks = diagnostics_data["checks"]
        assert checks
        check_names = {check["name"] for check in checks}
        assert {
            "NL2SQL_RUNTIME_MODE",
            "NL2SQL_PERSISTENCE_MODE",
            "NL2SQL_PERSISTENCE_READY",
            "PYTHON_ORACLEDB",
            "ORACLE_RUNTIME_READY",
            "OCI_ENTERPRISE_AI_ENDPOINT",
            "OCI_ENTERPRISE_AI_API_KEY",
            "OCI_ENTERPRISE_AI_LLM_MODEL",
            "OCI_GENAI_ENDPOINT",
            "OCI_GENAI_EMBED_MODEL_ID",
            "NL2SQL_SELECT_AI_PROFILE_REFRESHED",
            "NL2SQL_SELECT_AI_AGENT_ASSETS_REFRESHED",
        } <= check_names
        readiness = diagnostics_data["readiness"]
        assert readiness
        readiness_areas = {item["area"] for item in readiness}
        assert {
            "oracle_adb",
            "select_ai",
            "select_ai_agent",
            "enterprise_ai_direct",
            "feedback_embedding",
            "persistence",
        } <= readiness_areas
        smoke_checks = diagnostics_data["smoke_checks"]
        assert smoke_checks
        smoke_ids = {item["id"] for item in smoke_checks}
        assert {
            "refresh_select_ai_profile",
            "refresh_select_ai_agent_assets",
            "preview_select_ai",
            "preview_select_ai_agent",
            "preview_enterprise_ai_direct",
            "feedback_vector_rebuild",
            "manual_integration_script",
        } <= smoke_ids
        agent_smoke = next(item for item in smoke_checks if item["id"] == "preview_select_ai_agent")
        assert agent_smoke["endpoint"] == "/api/nl2sql/preview"
        assert "conversation_id" in agent_smoke["expected"]
        config_guides = diagnostics_data["config_guides"]
        guide_ids = {item["id"] for item in config_guides}
        assert {
            "enterprise_ai_direct",
            "feedback_embedding",
            "production_release_gate",
        } <= guide_ids
        enterprise_guide = next(
            item for item in config_guides if item["id"] == "enterprise_ai_direct"
        )
        assert (
            "OCI_ENTERPRISE_AI_ENDPOINT=<enterprise-ai-endpoint>"
            in enterprise_guide["env_template"]
        )
        assert "ORACLE_PASSWORD" not in enterprise_guide["env_template"]
        feedback_guide = next(item for item in config_guides if item["id"] == "feedback_embedding")
        required_feedback_env = {item["name"] for item in feedback_guide["required_env_vars"]}
        assert {
            "NL2SQL_FEEDBACK_EMBEDDING_ENABLED",
            "OCI_GENAI_ENDPOINT",
            "OCI_GENAI_EMBED_MODEL_ID",
        } <= required_feedback_env


def test_compare_execute_collects_engine_execution_errors() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    _import_sample(service)
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
            question="社員一覧を見たい",
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
    _import_sample(service)
    profile = service.create_profile(
        Nl2SqlProfile(
            id="persisted_profile",
            name="永続化テスト",
            allowed_tables=["EMPLOYEE"],
            glossary={"社員": "EMPLOYEE.EMPLOYEE_ID"},
            default_row_limit=20,
        )
    )

    job_info = service.start_job(
        JobCreateRequest(
            question="社員一覧を確認したい",
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
    compare_data = service.compare_engines(
        CompareRequest(question="社員一覧を比較したい", execute=True)
    )
    compare_record = service.list_compare_records().items[0]
    evaluation_set = service.create_evaluation_set(
        EvaluationSetUpsertRequest(
            name="永続化評価セット",
            profile_id=profile.id,
            engine=Nl2SqlEngine.SELECT_AI,
            cases=[
                SyntheticCase(
                    question="社員名を一覧したい",
                    expected_sql="SELECT EMPLOYEE_NAME FROM EMPLOYEE",
                )
            ],
        )
    )
    evaluation = service.evaluate(
        EvaluateRequest(
            evaluation_set_id=evaluation_set.id,
            profile_id=profile.id,
            engine=Nl2SqlEngine.SELECT_AI,
            cases=[
                {
                    "question": "社員名を一覧したい",
                    "expected_sql": "SELECT EMPLOYEE_NAME FROM EMPLOYEE",
                }
            ],
        )
    )
    evaluation_run = service.list_evaluation_runs().items[0]

    reloaded = Nl2SqlService(store=store)
    assert reloaded.get_profile(profile.id).name == "永続化テスト"
    restored_job = reloaded.get_job(job_info.job_id)
    assert restored_job is not None
    assert restored_job.status == JobStatus.DONE
    restored_history = reloaded.list_history().items[0]
    assert restored_history.id == history_item.id
    assert restored_history.feedback_rating == FeedbackRating.GOOD
    assert restored_history.feedback_comment == "永続化された feedback"
    restored_compare = reloaded.list_compare_records().items[0]
    assert restored_compare.id == compare_record.id
    assert restored_compare.comparison.recommendation == compare_data.recommendation
    assert "NL2SQL engine comparison" in restored_compare.report
    restored_evaluation_set = reloaded.list_evaluation_sets().items[0]
    assert restored_evaluation_set.id == evaluation_set.id
    assert restored_evaluation_set.name == "永続化評価セット"
    assert restored_evaluation_set.profile_id == profile.id
    assert restored_evaluation_set.cases[0].profile_id == profile.id
    restored_evaluation_run = reloaded.list_evaluation_runs().items[0]
    assert restored_evaluation_run.id == evaluation_run.id
    assert restored_evaluation_run.evaluation_set_id == evaluation_set.id
    assert restored_evaluation_run.profile_id == profile.id
    assert restored_evaluation_run.result.total_cases == evaluation.total_cases
    assert "NL2SQL deterministic evaluation" in restored_evaluation_run.report


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
    assert any("state_json" in input_sizes for input_sizes in fake_db.input_sizes)
    assert any(
        sql.startswith("SELECT state_json FROM NL2SQL_STATE_STORE") for sql in fake_db.executed
    )


def test_oracle_json_store_decodes_bytes_lob_snapshot() -> None:
    fake_db = _FakeOracleDb()
    fake_db.state_json = _FakeLob(b'{"schema_version":1,"profiles":[]}')
    store = OracleJsonNl2SqlStore(
        connection_factory=fake_db.connection,
        table_name="nl2sql_state_store",
    )

    assert store.load_snapshot() == {"schema_version": 1, "profiles": []}


def test_oracle_json_store_accepts_driver_decoded_json_snapshot() -> None:
    fake_db = _FakeOracleDb()
    fake_db.state_json = {"schema_version": 1, "profiles": []}
    store = OracleJsonNl2SqlStore(
        connection_factory=fake_db.connection,
        table_name="nl2sql_state_store",
    )

    assert store.load_snapshot() == {"schema_version": 1, "profiles": []}


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
    assert meta["profile_attributes"]["enforce_object_list"] is True
    assert meta["profile_attributes"]["annotations"] is True
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


def test_oracle_adapter_apply_comment_statements_strips_semicolon() -> None:
    fake_db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    meta = adapter.apply_comment_statements(
        ['COMMENT ON COLUMN "INVOICES"."TOTAL_AMOUNT" IS \'税込請求金額\';']
    )

    assert meta["runtime"] == "oracle"
    assert meta["statement_count"] == 1
    assert fake_db.executed[-1] == (
        'COMMENT ON COLUMN "INVOICES"."TOTAL_AMOUNT" IS \'税込請求金額\''
    )
    assert fake_db.commits == 1


def test_service_feedback_index_rebuild_uses_embedding_and_oracle_vector_table() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_QuestionCaptureOracleAdapter(fake_db))
    service._embedding_client = _FakeEmbeddingClient()
    service._history.append(
        HistoryItem(
            id="hist-vector-001",
            question="請求金額を確認したい",
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql="SELECT TOTAL_AMOUNT FROM INVOICES",
            created_at="2026-06-21T10:00:00+00:00",
            feedback_rating=FeedbackRating.GOOD,
            profile_id="default",
            profile_name="既定プロファイル",
            rewritten_question="請求金額を確認したい",
            feedback_comment="正しい SQL",
        )
    )

    data = service.rebuild_feedback_index(FeedbackIndexRequest())

    assert data.executed is True
    assert data.status == "ready"
    assert data.indexed_count == 1
    assert data.embedding_configured is True
    assert any('CREATE TABLE "NL2SQL_FEEDBACK_VECTORS"' in sql for sql in fake_db.executed)
    assert any('CREATE VECTOR INDEX "NL2SQL_FEEDBACK_VEC_IDX"' in sql for sql in fake_db.executed)
    assert fake_db.insert_batches
    insert_sql, rows = fake_db.insert_batches[0]
    assert "TO_VECTOR(:embedding_json)" in insert_sql
    assert rows[0]["history_id"] == "hist-vector-001"
    assert str(rows[0]["embedding_json"]).startswith("[0.01")


def test_service_select_ai_feedback_management_uses_dbms_cloud_ai() -> None:
    fake_db = _FakeOracleDb()
    fake_db.select_ai_feedback_rows = [
        (
            "select ai showsql 請求金額を確認したい",
            "sql-001",
            "SELECT TOTAL_AMOUNT FROM INVOICES",
            '{"sql_id":"sql-001","sql_text":"SELECT TOTAL_AMOUNT FROM INVOICES"}',
        )
    ]
    service = _OracleRuntimeNl2SqlService(_QuestionCaptureOracleAdapter(fake_db))

    entries = service.list_select_ai_feedback_entries("default")

    assert entries.runtime == "oracle"
    assert entries.profile_name == "DEFAULT"
    assert entries.table_name == "DEFAULT_FEEDBACK_VECINDEX$VECTAB"
    assert entries.items[0].sql_id == "sql-001"
    assert entries.items[0].sql_text == "SELECT TOTAL_AMOUNT FROM INVOICES"
    assert any('FROM "DEFAULT_FEEDBACK_VECINDEX$VECTAB"' in sql for sql in fake_db.executed)

    added = service.add_select_ai_feedback(
        SelectAiFeedbackAddRequest(
            profile_id="default",
            question="請求金額を確認したい;",
            feedback_type="positive",
            generated_sql="SELECT TOTAL_AMOUNT FROM INVOICES",
            feedback_content="正しい SQL",
        )
    )

    assert added.executed is True
    assert added.status == "added"
    assert added.profile_name == "NL2SQL_DEFAULT_PROFILE"
    assert added.sql_text == "select ai showsql 請求金額を確認したい"
    assert added.stored_feedback_type == "NEGATIVE"
    assert "operation => 'ADD'" in added.plsql_preview
    assert any(
        (params or {}).get("feedback_type") == "NEGATIVE"
        and (params or {}).get("response") == "SELECT TOTAL_AMOUNT FROM INVOICES"
        and (params or {}).get("feedback_content") == "正しい SQL"
        for params in fake_db.executed_params
    )

    corrected = service.add_select_ai_feedback(
        SelectAiFeedbackAddRequest(
            profile_id="default",
            profile_name="CUSTOM_PROFILE",
            question="請求金額を確認したい",
            feedback_type="negative",
            response="SELECT INVOICE_ID, TOTAL_AMOUNT FROM INVOICES",
        )
    )

    assert corrected.executed is True
    assert corrected.profile_name == "CUSTOM_PROFILE"
    assert any(
        (params or {}).get("profile_name") == "CUSTOM_PROFILE"
        and (params or {}).get("response") == "SELECT INVOICE_ID, TOTAL_AMOUNT FROM INVOICES"
        for params in fake_db.executed_params
    )

    deleted = service.delete_select_ai_feedback(
        SelectAiFeedbackDeleteRequest(
            profile_name="default",
            sql_text="select ai showsql 請求金額を確認したい",
        )
    )

    assert deleted.executed is True
    assert deleted.status == "deleted"
    assert any("DBMS_CLOUD_AI.FEEDBACK" in sql for sql in fake_db.executed)
    assert any(
        (params or {}).get("sql_text") == "select ai showsql 請求金額を確認したい"
        for params in fake_db.executed_params
    )

    updated = service.update_select_ai_feedback_vector_index(
        SelectAiFeedbackVectorIndexRequest(
            profile_name="default",
            similarity_threshold=0.85,
            match_limit=4,
        )
    )

    assert updated.executed is True
    assert updated.status == "updated"
    assert any("DBMS_CLOUD_AI.UPDATE_VECTOR_INDEX" in sql for sql in fake_db.executed)
    assert any(
        (params or {}).get("index_name") == "DEFAULT_FEEDBACK_VECINDEX"
        and '"match_limit": 4' in str((params or {}).get("attributes"))
        for params in fake_db.executed_params
    )
    assert fake_db.commits >= 2


def test_service_select_ai_feedback_missing_table_returns_warning() -> None:
    fake_db = _FakeOracleDb()
    fake_db.select_ai_feedback_missing = True
    service = _OracleRuntimeNl2SqlService(_QuestionCaptureOracleAdapter(fake_db))

    entries = service.list_select_ai_feedback_entries("default")

    assert entries.runtime == "oracle"
    assert entries.items == []
    assert "feedback vector table" in " ".join(entries.warnings)

    deleted = service.delete_select_ai_feedback(
        SelectAiFeedbackDeleteRequest(profile_name="default", sql_text="select ai showsql x")
    )
    updated = service.update_select_ai_feedback_vector_index(
        SelectAiFeedbackVectorIndexRequest(profile_name="default")
    )

    assert deleted.status == "error"
    assert updated.status == "error"


def test_service_select_ai_feedback_management_requires_oracle_runtime() -> None:
    service = Nl2SqlService()

    entries = service.list_select_ai_feedback_entries("default")
    added = service.add_select_ai_feedback(
        SelectAiFeedbackAddRequest(
            profile_id="default",
            question="請求金額を確認したい",
            feedback_type="positive",
            generated_sql="SELECT TOTAL_AMOUNT FROM INVOICES",
        )
    )
    deleted = service.delete_select_ai_feedback(
        SelectAiFeedbackDeleteRequest(profile_name="default", sql_text="select ai showsql x")
    )
    updated = service.update_select_ai_feedback_vector_index(
        SelectAiFeedbackVectorIndexRequest(profile_name="default")
    )

    assert entries.items == []
    assert "NL2SQL_RUNTIME_MODE=oracle" in " ".join(entries.warnings)
    assert added.status == "requires_oracle"
    assert added.executed is False
    assert deleted.status == "requires_oracle"
    assert updated.status == "requires_oracle"


def test_select_ai_feedback_add_negative_requires_response() -> None:
    with pytest.raises(ValueError):
        SelectAiFeedbackAddRequest(
            profile_id="default",
            question="請求金額を確認したい",
            feedback_type="negative",
            response="",
        )


def test_service_similar_history_uses_oracle_vector_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_feedback_embedding_enabled", True)
    fake_db = _FakeOracleDb()
    fake_db.feedback_vector_rows = [
        (
            "hist-vector-001",
            "default",
            "請求金額を確認したい",
            "SELECT TOTAL_AMOUNT FROM INVOICES",
            "good",
            0.08,
        )
    ]
    service = _OracleRuntimeNl2SqlService(_QuestionCaptureOracleAdapter(fake_db))
    service._embedding_client = _FakeEmbeddingClient()

    similar = service.similar_history(
        SimilarHistoryRequest(question="請求金額を見たい", profile_id="default")
    )

    assert similar.items
    assert similar.items[0].item.id == "hist-vector-001"
    assert similar.items[0].score == 0.92
    assert "Oracle 26ai vector search" in similar.items[0].reason
    assert any("VECTOR_DISTANCE" in sql for sql in fake_db.executed)
    assert any("TO_VECTOR(:embedding_json)" in sql for sql in fake_db.executed)


def test_service_preview_marks_oracle_vector_few_shot_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "nl2sql_feedback_embedding_enabled", True)
    fake_db = _FakeOracleDb()
    fake_db.feedback_vector_rows = [
        (
            "hist-vector-001",
            "default",
            "請求金額を確認したい",
            "SELECT TOTAL_AMOUNT FROM INVOICES",
            "good",
            0.1,
        )
    ]
    service = _OracleRuntimeNl2SqlService(_QuestionCaptureOracleAdapter(fake_db))
    service._embedding_client = _FakeEmbeddingClient()

    preview = service.preview(
        PreviewRequest(question="請求金額を見たい", engine=Nl2SqlEngine.SELECT_AI)
    )

    assert preview.engine_meta["similar_history_source"] == "oracle_vector"
    assert preview.engine_meta["similar_history_examples"][0]["history_id"] == "hist-vector-001"


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
    assert sum("DBMS_CLOUD_AI.DROP_PROFILE" in sql for sql in fake_db.executed) >= 2
    assert any("DBMS_SQL_TRANSLATOR.DROP_PROFILE" in sql for sql in fake_db.executed)


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


def test_oracle_adapter_run_select_ai_agent_team_uses_conversation() -> None:
    fake_db = _FakeOracleDb()
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    sql, conversation_id = adapter.run_select_ai_agent_team(
        team_name="NL2SQL_DEFAULT_TEAM",
        question="請求金額を確認したい",
    )

    assert sql == "SELECT TOTAL_AMOUNT FROM INVOICES"
    assert conversation_id == "conversation-001"
    assert any("DBMS_CLOUD_AI_AGENT.CREATE_CONVERSATION" in sql for sql in fake_db.executed)
    assert any("DBMS_CLOUD_AI_AGENT.RUN_TEAM" in sql for sql in fake_db.executed)
    assert any("conversation_id => :conversation_id" in sql for sql in fake_db.executed)


def test_oracle_adapter_run_select_ai_agent_team_falls_back_to_positional_signature() -> None:
    fake_db = _FakeOracleDb()
    fake_db.run_team_signature_failures = 2
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    sql, conversation_id = adapter.run_select_ai_agent_team(
        team_name="NL2SQL_DEFAULT_TEAM",
        question="請求金額を確認したい",
    )

    assert sql == "SELECT TOTAL_AMOUNT FROM INVOICES"
    assert conversation_id == "conversation-001"
    assert fake_db.run_team_calls == 3
    assert any(
        "DBMS_CLOUD_AI_AGENT.RUN_TEAM(:team_name, :user_prompt, :params)" in sql
        for sql in fake_db.executed
    )


def test_oracle_adapter_run_select_ai_agent_team_uses_explicit_tool_on_profile_loss() -> None:
    fake_db = _FakeOracleDb()
    fake_db.run_team_profile_loss = True
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    sql, conversation_id = adapter.run_select_ai_agent_team(
        team_name="NL2SQL_DEFAULT_TEAM_VABC12345",
        tool_name="NL2SQL_DEFAULT_TOOL",
        question="請求金額を確認したい",
    )

    assert sql == "SELECT TOTAL_AMOUNT FROM INVOICES"
    assert conversation_id == "run_tool:NL2SQL_DEFAULT_TOOL"
    assert any("DBMS_CLOUD_AI_AGENT.RUN_TOOL" in sql for sql in fake_db.executed)


def test_service_preview_select_ai_agent_returns_oracle_conversation_meta() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    preview = service.preview(
        PreviewRequest(question="請求金額を確認したい", engine=Nl2SqlEngine.SELECT_AI_AGENT)
    )

    assert preview.engine == Nl2SqlEngine.SELECT_AI_AGENT
    assert preview.sql == "SELECT TOTAL_AMOUNT FROM INVOICES"
    assert preview.engine_meta["runtime"] == "oracle"
    assert preview.engine_meta["conversation_id"] == "conversation-001"


def test_service_refresh_uses_oracle_adapter_when_runtime_is_oracle() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    select_ai = service.refresh_select_ai_profile(None)
    agent = service.refresh_select_ai_agent_assets(None)

    assert select_ai.status == "ready"
    assert select_ai.engine_meta["runtime"] == "oracle"
    assert select_ai.warning == ""
    profile_attributes = select_ai.engine_meta["profile_attributes"]
    assert "role" not in profile_attributes
    assert "additional_instructions" not in profile_attributes
    assert profile_attributes["additional_instructions_applied"] is True
    assert profile_attributes["additional_instructions_length"] > 0
    oracle_attributes = select_ai.engine_meta["attributes"]
    assert "additional_instructions" not in oracle_attributes
    assert oracle_attributes["additional_instructions_applied"] is True
    assert agent.status == "ready"
    assert agent.engine_meta["runtime"] == "oracle"
    assert agent.asset_names["team"].endswith("_TEAM")
    assert any("DBMS_CLOUD_AI.CREATE_PROFILE" in sql for sql in fake_db.executed)
    assert any("DBMS_CLOUD_AI_AGENT.CREATE_TEAM" in sql for sql in fake_db.executed)


def test_service_refresh_agent_uses_versioned_team_when_generated_profile_remains() -> None:
    fake_db = _FakeOracleDb()
    fake_db.create_team_profile_exists_failures = 2
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    data = service.refresh_select_ai_agent_assets(None)
    preview = service.preview(
        PreviewRequest(question="請求金額を確認したい", engine=Nl2SqlEngine.SELECT_AI_AGENT)
    )

    assert data.status == "ready"
    assert data.team_name.startswith("NL2SQL_DEFAULT_TEAM_V")
    assert "versioned team" in data.warning
    assert preview.engine_meta["team_name"] == data.team_name


def test_service_run_agent_team_uses_runtime_team_name() -> None:
    fake_db = _FakeOracleDb()
    fake_db.create_team_profile_exists_failures = 2
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    assets = service.refresh_select_ai_agent_assets(None)
    result = service.run_select_ai_agent_team(AgentTeamRunRequest(prompt="請求金額を確認したい"))

    assert assets.team_name.startswith("NL2SQL_DEFAULT_TEAM_V")
    assert result.runtime == "oracle"
    assert result.team_name == assets.team_name
    assert any(
        params and params.get("team_name") == assets.team_name for params in fake_db.executed_params
    )


def test_oracle_adapter_generate_synthetic_data_falls_back_to_procedure_signature() -> None:
    fake_db = _FakeOracleDb()
    fake_db.synthetic_function_signature_failures = 2
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    meta = adapter.generate_synthetic_data(
        table_name="NL2SQL_SMOKE_TABLE",
        row_count=1,
        profile_name="NL2SQL_SMOKE_PROFILE",
    )

    assert meta["mode"] == "procedure"
    assert meta["operation_id"] == ""
    assert fake_db.synthetic_procedure_calls == 1
    assert any(
        params
        and params.get("profile_name") == "NL2SQL_SMOKE_PROFILE"
        and params.get("object_name") == "NL2SQL_SMOKE_TABLE"
        for params in fake_db.executed_params
    )


def test_service_refresh_agent_cleans_previous_versioned_team() -> None:
    fake_db = _FakeOracleDb()
    fake_db.create_team_profile_exists_failures = 2
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    first = service.refresh_select_ai_agent_assets(None)
    second = service.refresh_select_ai_agent_assets(None)

    assert first.team_name.startswith("NL2SQL_DEFAULT_TEAM_V")
    assert second.status == "ready"
    assert any(
        params and params.get("name") == first.team_name for params in fake_db.executed_params
    )
    assert "cleanup" in second.warning


def test_oracle_adapter_refresh_agent_retries_when_generated_profile_exists() -> None:
    fake_db = _FakeOracleDb()
    fake_db.create_team_profile_exists_failures = 1
    adapter = _FakeRuntimeOracleAdapter(fake_db)

    meta = adapter.refresh_select_ai_agent_assets(
        profile_name="NL2SQL_DEFAULT_PROFILE",
        tool_name="NL2SQL_DEFAULT_TOOL",
        agent_name="NL2SQL_DEFAULT_AGENT",
        task_name="NL2SQL_DEFAULT_TASK",
        team_name="NL2SQL_DEFAULT_TEAM",
        allowed_tables=["INVOICES"],
        row_limit=20,
    )

    assert meta["runtime"] == "oracle"
    assert fake_db.create_team_calls == 2
    assert sum("DBMS_CLOUD_AI.DROP_PROFILE" in sql for sql in fake_db.executed) >= 2
    assert any("DBMS_SQL_TRANSLATOR.DROP_PROFILE" in sql for sql in fake_db.executed)


def test_service_cleanup_assets_requires_confirmation_without_oracle_calls() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    cleanup = service.cleanup_select_ai_assets(
        profile_id=None,
        engines=[Nl2SqlEngine.SELECT_AI_AGENT],
    )

    assert len(cleanup) == 1
    assert cleanup[0].status == "confirmation_required"
    assert cleanup[0].executed is False
    assert cleanup[0].asset_names == {}
    assert fake_db.executed == []


def test_service_cleanup_assets_executes_oracle_drops_when_confirmed() -> None:
    fake_db = _FakeOracleDb()
    service = _OracleRuntimeNl2SqlService(_FakeRuntimeOracleAdapter(fake_db))

    cleanup = service.cleanup_select_ai_assets(
        profile_id=None,
        engines=[Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT],
        confirmation="ADMIN_EXECUTE",
    )

    assert [item.status for item in cleanup] == ["cleaned", "cleaned"]
    assert all(item.executed for item in cleanup)
    assert any("DBMS_CLOUD_AI_AGENT.DROP_TEAM" in sql for sql in fake_db.executed)
    assert any("DBMS_CLOUD_AI.DROP_PROFILE" in sql for sql in fake_db.executed)


async def test_schema_import_csv_route_was_removed() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url="http://test") as client:
        resp = await client.post(
            "/api/schema/import-csv",
            json={
                "table_name": "sample invoices",
                "csv_text": "ID,NAME\n1,青山商事\n",
                "execute": False,
            },
        )

    assert resp.status_code == 404
