"""native の byte 位置を program が解読し、SQL 片は修正用データだけへ渡す。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any

import pytest
from test_nl2sql_job_runtime import _repository, _worker

from app.features.nl2sql import service as service_module
from app.features.nl2sql.chat_attachments import (
    AttachmentColumn,
    AttachmentContext,
    AttachmentTable,
    use_attachment_context,
)
from app.features.nl2sql.models import AllowedObjects, Nl2SqlEngine
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter, OracleSelectPreparationError
from app.features.nl2sql.oracle_sql_diagnostics import oracle_parse_diagnostic
from app.features.nl2sql.service import GeneratedSql
from app.features.nl2sql.sql_binding import SqlGenerationValidationError
from app.settings import get_settings


@pytest.mark.parametrize("prefix", ["A", "あ", "😀", "あ😀"])
def test_native_utf8_bytes_are_not_python_or_utf16_positions(prefix: str) -> None:
    sql = f"SELECT '{prefix}' AS LEVEL FROM DUAL"
    start = sql.index("LEVEL")
    native = len(sql[:start].encode("utf-8"))
    result = oracle_parse_diagnostic(
        sql, code=923, offset=native, offset_basis="utf8_bytes_zero_based"
    )
    location = result["location"]
    assert location is not None
    assert result["offset_unit"] == "utf8_bytes" and result["offset_base"] == 0
    assert result["sql_source"] == "prepared_sql" and "sql_sha256" not in result
    assert location["character_offset"] == start
    assert location["line_1_based"] == 1 and location["column_1_based"] == start + 1
    assert location["token"] is not None and location["token"]["text"] == "LEVEL"
    assert location["excerpt"] == sql


@pytest.mark.parametrize("token", ["LEVEL", "ORDER", '"列😀"', "123"])
def test_generic_token_and_multiline_position(token: str) -> None:
    sql = f"SELECT 'あ😀',\n  {token}\nFROM DUAL"
    start = sql.index(token)
    result = oracle_parse_diagnostic(
        sql, code=923, offset=len(sql[:start].encode()), offset_basis="utf8_bytes_zero_based"
    )
    location = result["location"]
    assert location is not None and location["line_1_based"] == 2
    assert location["column_1_based"] == 3
    assert location["token"] is not None and location["token"]["text"] == token
    assert location["token"]["start_character"] == start
    assert location["token"]["end_character_exclusive"] == start + len(token)


@pytest.mark.parametrize("offset", [0, -1, 999, True])
def test_invalid_or_unspecified_offset_is_not_guessed(offset: int) -> None:
    result = oracle_parse_diagnostic(
        "SELECT 1 FROM DUAL", code=923, offset=offset, offset_basis="utf8_bytes_zero_based"
    )
    assert result["location"] is None
    assert result["unavailable_reason"] == "invalid_or_unspecified_offset"


def test_unknown_basis_never_guesses_units() -> None:
    result = oracle_parse_diagnostic("SELECT 1 FROM DUAL", code=923, offset=7, offset_basis=None)
    assert result["location"] is None and result["offset_unit"] is None
    assert result["offset_base"] is None and result["unavailable_reason"] == "unknown_offset_basis"


@pytest.mark.parametrize("offset", [9, 10, 12, 13, 14])
def test_offset_inside_unicode_byte_sequence_does_not_round(offset: int) -> None:
    # 「あ」は byte 8、emoji は byte 11 で始まり、指定した位置はその途中。
    sql = "SELECT 'あ😀' FROM DUAL"
    result = oracle_parse_diagnostic(
        sql, code=923, offset=offset, offset_basis="utf8_bytes_zero_based"
    )
    assert (
        result["location"] is None
        and result["unavailable_reason"] == "offset_inside_utf8_character"
    )


def test_eof_is_explicit_and_has_no_token() -> None:
    sql = "SELECT 'あ😀' FROM"
    result = oracle_parse_diagnostic(
        sql, code=936, offset=len(sql.encode()), offset_basis="utf8_bytes_zero_based"
    )
    location = result["location"]
    assert location is not None and location["at_eof"]
    assert location["character_offset"] == len(sql) and location["token"] is None


def test_lexically_invalid_sql_keeps_location_without_inventing_token() -> None:
    sql = "SELECT 'unclosed FROM DUAL"
    result = oracle_parse_diagnostic(sql, code=923, offset=12, offset_basis="utf8_bytes_zero_based")
    location = result["location"]
    assert location is not None and location["token"] is None
    assert location["character_offset"] == 12


def test_whitespace_is_not_replaced_by_nearby_token() -> None:
    sql = "SELECT  1 FROM DUAL"
    result = oracle_parse_diagnostic(sql, code=923, offset=7, offset_basis="utf8_bytes_zero_based")
    assert result["location"] is not None and result["location"]["token"] is None


def test_unencodable_sql_safely_omits_location() -> None:
    result = oracle_parse_diagnostic(
        "SELECT '\ud800'", code=923, offset=10, offset_basis="utf8_bytes_zero_based"
    )
    assert result["location"] is None and result["unavailable_reason"] == "sql_not_utf8_encodable"


def test_long_literal_and_context_are_bounded_and_marked() -> None:
    sql = "SELECT '" + "x" * 1000 + "' FROM DUAL"
    result = oracle_parse_diagnostic(
        sql, code=923, offset=500, offset_basis="utf8_bytes_zero_based"
    )
    location = result["location"]
    assert location is not None and len(location["excerpt"]) <= 160
    token = location["token"]
    assert token is not None and len(token["text"]) == 160 and token["text_truncated"]
    assert token["end_character_exclusive"] - token["start_character"] == 1002


@pytest.mark.parametrize("thin", [True, False, None])
def test_native_driver_modes_provide_known_basis_or_explicit_unknown(
    monkeypatch: pytest.MonkeyPatch, thin: bool | None
) -> None:
    adapter = OracleNl2SqlAdapter(get_settings())

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def parse(self, sql: str) -> None:
            raise RuntimeError(SimpleNamespace(code=923, offset=14))

    @contextmanager
    def connection(*, read_only: bool = False) -> Iterator[Any]:
        assert read_only
        yield SimpleNamespace(cursor=Cursor, thin=thin)

    monkeypatch.setattr(adapter, "user_data_connection", connection)
    monkeypatch.setattr(
        adapter, "_business_session_time_zone", lambda *args, **kwargs: nullcontext()
    )
    with pytest.raises(OracleSelectPreparationError) as error:
        adapter.prepare_select("SELECT 'A' AS LEVEL FROM DUAL")
    assert error.value.offset_basis == ("utf8_bytes_zero_based" if thin is not None else None)


@pytest.mark.parametrize("attachments", [False, True])
@pytest.mark.parametrize("repair", [False, True])
def test_only_repair_feedback_receives_actual_prepared_source_location(
    monkeypatch: pytest.MonkeyPatch,
    attachments: bool,
    repair: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository()
    service = _worker(repository)
    sql = (
        "SELECT \"ID\", 'private_literal' AS LEVEL FROM ATTACHMENT_1"
        if attachments
        else "SELECT ID,'private_literal' AS LEVEL FROM APP.ORDERS"
    )
    good = sql.replace(" AS LEVEL", ' AS "LEVEL"')
    generated: list[dict[str, Any]] = []
    prepared: list[str] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        generated.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=good if repair and len(generated) == 2 else sql,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(actual: str) -> None:
        prepared.append(actual)
        if " AS LEVEL" in actual:
            raise OracleSelectPreparationError(
                "ORA-00923",
                code=923,
                offset=len(actual[: actual.index("LEVEL")].encode()),
                offset_basis="utf8_bytes_zero_based",
            )

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    context = (
        AttachmentContext(
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
        if attachments
        else AttachmentContext()
    )
    with use_attachment_context(context):
        if repair:
            service._generate_sql(
                Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
                "一覧",
                repository.list_profiles(include_archived=False)[0],
                AllowedObjects(table_names=["APP.ORDERS"]),
                10,
                [],
                prepare_for_execution=True,
            )
        else:
            with pytest.raises(SqlGenerationValidationError) as failure:
                service._generate_sql(
                    Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
                    "一覧",
                    repository.list_profiles(include_archived=False)[0],
                    AllowedObjects(table_names=["APP.ORDERS"]),
                    10,
                    [],
                    prepare_for_execution=True,
                )
            assert "private_literal" not in str(failure.value)
    assert len(generated) == len(prepared) == 2
    assert generated[0]["generation_feedback"] is None
    feedback = generated[1]["generation_feedback"]
    assert feedback["operation"] == "repair_rejected_sql"
    diagnostic = feedback["oracle_diagnostics"][0]
    assert feedback["rejected_sql"] == sql
    assert diagnostic == oracle_parse_diagnostic(
        prepared[0],
        code=923,
        offset=len(prepared[0][: prepared[0].index("LEVEL")].encode()),
        offset_basis="utf8_bytes_zero_based",
    )
    if attachments:
        assert "JSON_TABLE" in prepared[0] and ":attachment_1" in prepared[0]
        assert diagnostic["location"]["character_offset"] != sql.index("LEVEL")
    assert "private_literal" not in caplog.text


def test_diagnostic_failure_does_not_mask_original_parse_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    repository = _repository()
    service = _worker(repository)
    calls: list[dict[str, Any]] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql="SELECT ID AS LEVEL FROM APP.ORDERS",
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(sql: str) -> None:
        raise OracleSelectPreparationError(
            "ORA-00923", code=923, offset=13, offset_basis="utf8_bytes_zero_based"
        )

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private_diagnostic_literal")

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    monkeypatch.setattr(service_module, "oracle_parse_diagnostic", broken)
    with pytest.raises(SqlGenerationValidationError, match="ORA-00923") as failure:
        service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            repository.list_profiles(include_archived=False)[0],
            AllowedObjects(table_names=["APP.ORDERS"]),
            10,
            [],
            prepare_for_execution=True,
        )
    assert len(calls) == 2 and "oracle_diagnostics" not in calls[1]["generation_feedback"]
    assert "private_diagnostic_literal" not in str(failure.value) + caplog.text


@pytest.mark.parametrize("initially_unavailable", [False, True])
def test_mode_getter_failure_cannot_mask_original_parse_error(
    monkeypatch: pytest.MonkeyPatch, initially_unavailable: bool
) -> None:
    adapter = OracleNl2SqlAdapter(get_settings())
    closed = False

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def parse(self, sql: str) -> None:
            nonlocal closed
            closed = True
            raise RuntimeError(SimpleNamespace(code=3113, offset=14))

    class Connection:
        @property
        def thin(self) -> bool:
            if closed or initially_unavailable:
                raise RuntimeError("private_mode_getter_detail")
            return True

        def cursor(self) -> Cursor:
            return Cursor()

    @contextmanager
    def connection(*, read_only: bool = False) -> Iterator[Any]:
        yield Connection()

    monkeypatch.setattr(adapter, "user_data_connection", connection)
    monkeypatch.setattr(
        adapter, "_business_session_time_zone", lambda *args, **kwargs: nullcontext()
    )
    with pytest.raises(OracleSelectPreparationError) as failure:
        adapter.prepare_select("SELECT 'A' AS LEVEL FROM DUAL")
    assert failure.value.code == 3113 and failure.value.offset == 14
    assert failure.value.offset_basis == (
        None if initially_unavailable else "utf8_bytes_zero_based"
    )
    assert "private_mode_getter_detail" not in str(failure.value)
    assert not failure.value.allows_generation_repair
