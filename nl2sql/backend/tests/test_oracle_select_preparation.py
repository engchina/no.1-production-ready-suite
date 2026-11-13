"""Oracle 実行前解析は安全境界の後で行い、生成の修正回数を増やさない。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any

import pytest
from test_nl2sql_job_runtime import _repository, _worker

from app.features.nl2sql.models import AllowedObjects, Nl2SqlEngine, QueryResults
from app.features.nl2sql.oracle_adapter import (
    OracleAdapterError,
    OracleNl2SqlAdapter,
    OracleSelectPreparationError,
)
from app.features.nl2sql.service import GeneratedSql
from app.features.nl2sql.sql_binding import SqlGenerationValidationError
from app.settings import get_settings

BAD = "SELECT ID AS LEVEL FROM APP.ORDERS"
GOOD = 'SELECT ID AS "LEVEL" FROM APP.ORDERS'


@pytest.mark.parametrize("repair", [False, True])
def test_prepare_uses_existing_generation_repair_budget(
    monkeypatch: pytest.MonkeyPatch, repair: bool
) -> None:
    repository = _repository()
    service = _worker(repository)
    calls: list[dict[str, Any]] = []
    parsed: list[str] = []
    executed: list[str] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=GOOD if repair and len(calls) == 2 else BAD,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(sql: str) -> None:
        parsed.append(sql)
        if sql == BAD:
            raise OracleSelectPreparationError(
                "ORA-00923: FROM keyword not found where expected", code=923, offset=13
            )

    def execute(sql: str, max_rows: int | None) -> QueryResults:
        executed.append(sql)
        return QueryResults(columns=["LEVEL"], rows=[{"LEVEL": 1}], total=1)

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    monkeypatch.setattr(service._oracle_adapter, "execute_select", execute)
    profile = repository.list_profiles(include_archived=False)[0]
    allowed = AllowedObjects(table_names=["APP.ORDERS"])
    if repair:
        result = service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            profile,
            allowed,
            10,
            [],
            prepare_for_execution=True,
        )
        analysis = service.analyze_sql(
            result.generated_sql,
            allowed,
            10,
            catalog=repository.load_catalog(),
            engine=result.engine,
        )
        service.execute_sql(result.generated_sql, allowed, 10, analysis=analysis)
        assert executed == [GOOD]
        assert result.engine_meta["sql_validation_attempts"] == 2
    else:
        with pytest.raises(SqlGenerationValidationError, match="SQL_ORACLE_PARSE_INVALID"):
            service._generate_sql(
                Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
                "一覧",
                profile,
                allowed,
                10,
                [],
                prepare_for_execution=True,
            )
        assert executed == []
    assert len(calls) == 2 and len(parsed) == 2
    assert calls[1]["generation_feedback"]["rejected_sql"] == BAD
    assert "offset=13" in str(calls[1]["generation_feedback"]["violations"])


@pytest.mark.parametrize("code", [942, 1031, 1017, 3113, 0])
def test_non_syntax_failure_is_not_regenerated(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    repository = _repository()
    service = _worker(repository)
    calls: list[int] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(1)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=GOOD,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(sql: str) -> None:
        raise OracleSelectPreparationError(f"ORA-{code:05d}", code=code, offset=7)

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    with pytest.raises(OracleSelectPreparationError):
        service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            repository.list_profiles(include_archived=False)[0],
            AllowedObjects(table_names=["APP.ORDERS"]),
            10,
            [],
            prepare_for_execution=True,
        )
    assert calls == [1]


@pytest.mark.parametrize(
    "sql,enabled,oracle",
    [
        ("DELETE FROM APP.ORDERS", True, True),
        ("SELECT ID FROM APP.OTHER", True, True),
        (GOOD, False, True),
        (GOOD, True, False),
    ],
)
def test_unsafe_or_non_execution_does_not_parse(
    monkeypatch: pytest.MonkeyPatch, sql: str, enabled: bool, oracle: bool
) -> None:
    repository = _repository()
    service = _worker(repository)

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=sql,
            explanation="",
            engine_meta={},
            schema_catalog=None,
        )

    def forbidden(sql: str) -> None:
        raise AssertionError("prepare must not run")

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: oracle)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", forbidden)
    service._generate_sql(
        Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
        "一覧",
        repository.list_profiles(include_archived=False)[0],
        AllowedObjects(table_names=["APP.ORDERS"]),
        10,
        [],
        prepare_for_execution=enabled,
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'A' AS \"LEVEL\" FROM DUAL",
        "SELECT LEVEL FROM DUAL CONNECT BY LEVEL<=2",
        "SELECT 1 FROM DUAL UNION ALL SELECT 2 FROM DUAL",
    ],
)
def test_adapter_parse_does_not_execute_or_fetch(monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    adapter = OracleNl2SqlAdapter(get_settings())
    calls: list[Any] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def parse(self, statement: str) -> None:
            calls.append(statement)

    @contextmanager
    def connection(*, read_only: bool = False) -> Iterator[Any]:
        calls.append(read_only)
        yield SimpleNamespace(cursor=Cursor)

    monkeypatch.setattr(adapter, "user_data_connection", connection)
    monkeypatch.setattr(
        adapter, "_business_session_time_zone", lambda *args, **kwargs: nullcontext()
    )
    adapter.prepare_select(sql)
    assert calls == [True, sql]


@pytest.mark.parametrize(
    "sql", ["CREATE TABLE A(ID NUMBER)", "DELETE FROM A", "SELECT 1 FROM DUAL;DROP TABLE A"]
)
def test_adapter_never_parses_non_query(monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    adapter = OracleNl2SqlAdapter(get_settings())

    def forbidden(**kwargs: Any) -> Any:
        raise AssertionError("no connection")

    monkeypatch.setattr(adapter, "user_data_connection", forbidden)
    with pytest.raises(OracleAdapterError, match="単一文"):
        adapter.prepare_select(sql)


def test_attachment_prepares_composed_sql_and_retains_bound_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql.chat_attachments import (
        AttachmentColumn,
        AttachmentContext,
        AttachmentTable,
        use_attachment_context,
    )

    repository = _repository()
    service = _worker(repository)
    sql = 'SELECT "ID" FROM ATTACHMENT_1'
    parsed: list[str] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=sql,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", parsed.append)
    context = AttachmentContext(
        tables=(
            AttachmentTable(
                name="ATTACHMENT_1",
                attachment_id="a",
                file_name="a.csv",
                sheet="a",
                columns=(AttachmentColumn(name="ID", sql_type="NUMBER"),),
                rows=((1,),),
            ),
        )
    )
    with use_attachment_context(context):
        service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            repository.list_profiles(include_archived=False)[0],
            AllowedObjects(table_names=["APP.ORDERS"]),
            10,
            [],
            prepare_for_execution=True,
        )
    assert len(parsed) == 1
    assert "JSON_TABLE" in parsed[0] and ":attachment_1" in parsed[0]
    assert "[[1]]" not in parsed[0]


@pytest.mark.parametrize("where", ["connection", "timezone", "parse"])
def test_infrastructure_failure_keeps_typed_cause(
    monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    adapter = OracleNl2SqlAdapter(get_settings())
    error = TimeoutError("call timeout")

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def parse(self, sql: str) -> None:
            if where == "parse":
                raise error

    @contextmanager
    def connection(*, read_only: bool = False) -> Iterator[Any]:
        if where == "connection":
            raise error
        yield SimpleNamespace(cursor=Cursor)

    @contextmanager
    def timezone(*args: Any, **kwargs: Any) -> Iterator[None]:
        if where == "timezone":
            raise error
        yield

    monkeypatch.setattr(adapter, "user_data_connection", connection)
    monkeypatch.setattr(adapter, "_business_session_time_zone", timezone)
    with pytest.raises(OracleAdapterError) as failure:
        adapter.prepare_select("SELECT 1 FROM DUAL")
    assert failure.value.__cause__ is error
    if isinstance(failure.value, OracleSelectPreparationError):
        assert not failure.value.allows_generation_repair


def test_syntax_wrapper_cannot_hide_connection_failure() -> None:
    error = OracleSelectPreparationError("ORA-00923", code=923, offset=7)
    error.__cause__ = TimeoutError("call timeout")
    assert not error.allows_generation_repair


def test_cancellation_during_prepare_stops_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository()
    service = _worker(repository)
    cancelled = False
    calls: list[int] = []

    class Cancelled(RuntimeError):
        pass

    def check() -> None:
        if cancelled:
            raise Cancelled("cancelled")

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(1)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=BAD,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(sql: str) -> None:
        nonlocal cancelled
        cancelled = True
        raise OracleSelectPreparationError("ORA-00923", code=923, offset=13)

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    with pytest.raises(Cancelled):
        service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            repository.list_profiles(include_archived=False)[0],
            AllowedObjects(table_names=["APP.ORDERS"]),
            10,
            [],
            prepare_for_execution=True,
            before_attempt=check,
        )
    assert calls == [1]


@pytest.mark.parametrize("generation_only,chat", [(False, False), (False, True), (True, False)])
def test_job_passes_explicit_execution_intent(
    monkeypatch: pytest.MonkeyPatch, generation_only: bool, chat: bool
) -> None:
    from test_nl2sql_job_runtime import _request

    monkeypatch.setattr(get_settings(), "nl2sql_job_worker_mode", "external")
    repository = _repository()
    service = _worker(repository)
    flags: list[bool] = []
    original = service._generate_sql

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        flags.append(kwargs["prepare_for_execution"])
        assert callable(kwargs["before_attempt"])
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_generate_sql", generate)
    created = service.start_job(
        _request().model_copy(
            update={"use_ontology_context": False, "generation_only": generation_only, "chat": chat}
        ),
        actor_user_uuid="user-1",
    )
    assert service.run_next_nl2sql_job(worker_id="prepare", job_id=created.job_id)
    assert flags == [not generation_only]


def test_unknown_attachment_column_remains_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.features.nl2sql.chat_attachments import (
        AttachmentColumn,
        AttachmentContext,
        AttachmentTable,
        use_attachment_context,
    )

    repository = _repository()
    service = _worker(repository)

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql='SELECT "UNKNOWN" FROM ATTACHMENT_1',
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    context = AttachmentContext(
        tables=(
            AttachmentTable(
                name="ATTACHMENT_1",
                attachment_id="a",
                file_name="a.csv",
                sheet="a",
                columns=(AttachmentColumn(name="ID", sql_type="NUMBER"),),
                rows=((1,),),
            ),
        )
    )
    with (
        use_attachment_context(context),
        pytest.raises(SqlGenerationValidationError, match="UNKNOWN"),
    ):
        service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            repository.list_profiles(include_archived=False)[0],
            AllowedObjects(table_names=["APP.ORDERS"]),
            10,
            [],
        )
