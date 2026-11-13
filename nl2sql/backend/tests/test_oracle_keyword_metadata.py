"""原生 keyword の観測値を修正へ渡し、判定・補完・追加試行は行わない。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from test_nl2sql_job_runtime import _repository, _worker

from app.features.nl2sql.models import AllowedObjects, Nl2SqlEngine
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter, OracleSelectPreparationError
from app.features.nl2sql.oracle_keyword_metadata import (
    NATIVE_KEYWORD_COLUMNS,
    NativeKeywordMetadata,
    native_keyword_metadata_from_rows,
)
from app.features.nl2sql.service import GeneratedSql
from app.features.nl2sql.sql_binding import SqlGenerationValidationError
from app.settings import get_settings

ROW = ("LEVEL", "N", "N", "N", "Y")
BAD = "SELECT ID AS LEVEL FROM APP.ORDERS"
GOOD = 'SELECT ID AS "LEVEL" FROM APP.ORDERS'


def positive() -> NativeKeywordMetadata:
    return native_keyword_metadata_from_rows("LEVEL", NATIVE_KEYWORD_COLUMNS, [ROW])


def test_positive_wire_contract_preserves_source_flags_and_general_meanings() -> None:
    # 固定入力の実モデル対照へ渡した JSON と同じ合同。特定 token の直し方は含めない。
    assert positive() == {
        "availability": "available",
        "source": "SYS.V_$RESERVED_WORDS",
        "rows": [
            {"KEYWORD": "LEVEL", "RESERVED": "N", "RES_TYPE": "N", "RES_ATTR": "N", "RES_SEMI": "Y"}
        ],
        "field_meanings": {
            "RESERVED": "Y: 識別子として使用できない。N: この列では予約されていない。",
            "RES_TYPE": "Y: 型名として使用できない。N: この列では予約されていない。",
            "RES_ATTR": "Y: 属性名として使用できない。N: この列では予約されていない。",
            "RES_SEMI": (
                "Y: DMLなど一部の状況で識別子として使用できない。N: この列では予約されていない。"
            ),
        },
    }
    assert native_keyword_metadata_from_rows("level", NATIVE_KEYWORD_COLUMNS, [ROW]) == positive()


def test_not_found_does_not_invent_negative_flags_and_results_are_independent() -> None:
    result = native_keyword_metadata_from_rows("UNKNOWN", NATIVE_KEYWORD_COLUMNS, [])
    assert result["availability"] == "not_found" and result["rows"] == []
    result["field_meanings"].clear()
    result["rows"].append(positive()["rows"][0])
    assert positive()["field_meanings"] and len(positive()["rows"]) == 1


@pytest.mark.parametrize(
    "columns",
    [(), ("KEYWORD",), (*NATIVE_KEYWORD_COLUMNS, "EXTRA"), tuple(reversed(NATIVE_KEYWORD_COLUMNS))],
)
def test_missing_or_wrong_columns_are_unavailable(columns: tuple[str, ...]) -> None:
    result = native_keyword_metadata_from_rows("LEVEL", columns, [ROW])
    assert result["availability"] == "unavailable" and result["rows"] == []


@pytest.mark.parametrize("row", [None, 1, "LEVEL", ("LEVEL",), ("LEVEL", "N", "N", "N", "Y", "N")])
def test_malformed_rows_are_not_converted(row: object) -> None:
    result = native_keyword_metadata_from_rows("LEVEL", NATIVE_KEYWORD_COLUMNS, [row])
    assert result["unavailable_reason"] == "invalid_row_shape" and result["rows"] == []


@pytest.mark.parametrize("flag", [None, False, 0, "n", "", "UNKNOWN"])
@pytest.mark.parametrize("position", [1, 2, 3, 4])
def test_every_flag_must_be_an_explicit_native_y_or_n(flag: object, position: int) -> None:
    row: list[object] = list(ROW)
    row[position] = flag
    result = native_keyword_metadata_from_rows("LEVEL", NATIVE_KEYWORD_COLUMNS, [row])
    assert result["unavailable_reason"] == "invalid_flags" and result["rows"] == []


@pytest.mark.parametrize("word", [None, "OTHER", "level"])
def test_unrelated_row_is_not_used(word: object) -> None:
    result = native_keyword_metadata_from_rows("LEVEL", NATIVE_KEYWORD_COLUMNS, [(word, *ROW[1:])])
    assert result["unavailable_reason"] == "keyword_mismatch"


def test_conflicting_rows_are_preserved_without_choosing_a_flag() -> None:
    result = native_keyword_metadata_from_rows(
        "LEVEL", NATIVE_KEYWORD_COLUMNS, [ROW, ("LEVEL", "Y", "N", "N", "N")]
    )
    assert result["availability"] == "available"
    assert [row["RESERVED"] for row in result["rows"]] == ["N", "Y"]
    assert [row["RES_SEMI"] for row in result["rows"]] == ["Y", "N"]
    assert (
        len(native_keyword_metadata_from_rows("LEVEL", NATIVE_KEYWORD_COLUMNS, [ROW] * 16)["rows"])
        == 16
    )
    oversized = native_keyword_metadata_from_rows("LEVEL", NATIVE_KEYWORD_COLUMNS, [ROW] * 17)
    assert oversized["unavailable_reason"] == "row_limit_exceeded" and oversized["rows"] == []


@pytest.mark.parametrize("failure", [None, "acquire", "execute", "fetch", "exit"])
def test_adapter_uses_only_bound_bounded_readonly_lookup(
    monkeypatch: pytest.MonkeyPatch, failure: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = OracleNl2SqlAdapter(get_settings())
    calls: list[object] = []
    keyword = "X' OR 1=1 --"

    class Cursor:
        description = [(name,) for name in NATIVE_KEYWORD_COLUMNS]

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: Any) -> None:
            if failure == "exit":
                raise RuntimeError("private_lookup_error")

        def execute(self, sql: str, binds: dict[str, str]) -> None:
            calls.append((sql, binds))
            assert keyword not in sql
            assert binds == {"keyword": keyword}
            if failure == "execute":
                raise RuntimeError("private_lookup_error")

        def fetchmany(self, size: int) -> list[tuple[str, ...]]:
            calls.append(size)
            assert size == 17
            if failure == "fetch":
                raise RuntimeError("private_lookup_error")
            return [(keyword, *ROW[1:])]

    @contextmanager
    def connection(*, read_only: bool = False) -> Iterator[Any]:
        assert read_only
        if failure == "acquire":
            raise RuntimeError("private_lookup_error")
        yield SimpleNamespace(cursor=Cursor)

    monkeypatch.setattr(adapter, "user_data_connection", connection)
    result = adapter.read_native_keyword_metadata(keyword)
    assert result["availability"] == ("available" if failure is None else "unavailable")
    assert "private_lookup_error" not in str(result) + caplog.text
    if failure != "acquire":
        assert len(calls) == (1 if failure == "execute" else 2)


@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("auxiliary_failure", [False, True])
def test_only_first_native_failure_gets_lookup_and_existing_repair_budget(
    monkeypatch: pytest.MonkeyPatch,
    repair: bool,
    auxiliary_failure: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = _repository()
    service = _worker(repository)
    generated: list[dict[str, Any]] = []
    looked_up: list[str] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        generated.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=GOOD if repair and len(generated) == 2 else BAD,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(sql: str) -> None:
        if sql == BAD:
            raise OracleSelectPreparationError(
                "ORA-00923",
                code=923,
                offset=sql.index("LEVEL"),
                offset_basis="utf8_bytes_zero_based",
            )

    def lookup(keyword: str) -> NativeKeywordMetadata:
        looked_up.append(keyword)
        if auxiliary_failure:
            raise RuntimeError("private_lookup_error")
        return positive()

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    monkeypatch.setattr(service._oracle_adapter, "read_native_keyword_metadata", lookup)

    def run() -> GeneratedSql:
        return service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            repository.list_profiles(include_archived=False)[0],
            AllowedObjects(table_names=["APP.ORDERS"]),
            10,
            [],
            prepare_for_execution=True,
        )

    if repair:
        assert run().generated_sql == GOOD
    else:
        with pytest.raises(SqlGenerationValidationError, match="ORA-00923"):
            run()
    assert len(generated) == 2 and looked_up == ["LEVEL"]
    assert generated[0]["generation_feedback"] is None
    feedback = generated[1]["generation_feedback"]
    assert feedback["operation"] == "repair_rejected_sql"
    diagnostic = feedback["oracle_diagnostics"][0]
    assert diagnostic["location"]["token"]["lexer_source"] == "sqlglot.OracleConversion"
    metadata = diagnostic["native_keyword_metadata"]
    if auxiliary_failure:
        assert metadata["availability"] == "unavailable"
    else:
        assert metadata == positive()
    assert "private_lookup_error" not in str(feedback) + caplog.text


@pytest.mark.parametrize("cancel_at", [3, 4])
def test_cancel_before_lookup_or_next_model_cannot_be_swallowed(
    monkeypatch: pytest.MonkeyPatch, cancel_at: int
) -> None:
    repository = _repository()
    service = _worker(repository)
    calls: list[str] = []
    checks = 0

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append("model")
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=BAD,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(sql: str) -> None:
        calls.append("parse")
        raise OracleSelectPreparationError(
            "ORA-00923", code=923, offset=sql.index("LEVEL"), offset_basis="utf8_bytes_zero_based"
        )

    def lookup(keyword: str) -> NativeKeywordMetadata:
        calls.append("lookup")
        return positive()

    def check() -> None:
        nonlocal checks
        checks += 1
        if checks == cancel_at:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    monkeypatch.setattr(service._oracle_adapter, "read_native_keyword_metadata", lookup)
    with pytest.raises(RuntimeError, match="cancelled"):
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
    assert calls == (["model", "parse"] if cancel_at == 3 else ["model", "parse", "lookup"])


@pytest.mark.parametrize(
    "scenario", ["success", "last_only", "unknown_offset", "truncated", "denied"]
)
def test_lookup_requires_reliable_token_native_failure_and_remaining_attempt(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    repository = _repository()
    service = _worker(repository)
    calls = 0
    sql = BAD if scenario != "truncated" else "SELECT ID AS " + "X" * 200 + " FROM APP.ORDERS"

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        nonlocal calls
        calls += 1
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql="SELECT MISSING FROM APP.ORDERS"
            if scenario == "last_only" and calls == 1
            else sql,
            explanation="",
            engine_meta={},
            schema_catalog=repository.load_catalog(),
        )

    def prepare(actual: str) -> None:
        if scenario != "success":
            raise OracleSelectPreparationError(
                "ORA-00942" if scenario == "denied" else "ORA-00923",
                code=942 if scenario == "denied" else 923,
                offset=0 if scenario == "unknown_offset" else actual.index(" AS ") + 4,
                offset_basis="utf8_bytes_zero_based",
            )

    def lookup(keyword: str) -> NativeKeywordMetadata:
        pytest.fail("次の修正に使用できない原生情報を取得してはならない")

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service, "_use_oracle_runtime", lambda: True)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", prepare)
    monkeypatch.setattr(service._oracle_adapter, "read_native_keyword_metadata", lookup)

    def run() -> GeneratedSql:
        return service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "一覧",
            repository.list_profiles(include_archived=False)[0],
            AllowedObjects(table_names=["APP.ORDERS"]),
            10,
            [],
            prepare_for_execution=True,
        )

    if scenario == "success":
        run()
    elif scenario == "denied":
        with pytest.raises(OracleSelectPreparationError):
            run()
    else:
        with pytest.raises(SqlGenerationValidationError):
            run()
    assert calls == (1 if scenario in {"success", "denied"} else 2)


@pytest.mark.parametrize("is_admin", [False, True])
def test_metadata_connection_preserves_current_actor(
    monkeypatch: pytest.MonkeyPatch, is_admin: bool
) -> None:
    from app.security.request_actor import actor_scope, current_actor_context

    adapter = OracleNl2SqlAdapter(get_settings())
    observed: list[tuple[str, bool, bool]] = []

    class Cursor:
        description = [(name,) for name in NATIVE_KEYWORD_COLUMNS]

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, *args: Any) -> None:
            pass

        def fetchmany(self, size: int) -> list[tuple[str, ...]]:
            return [ROW]

    @contextmanager
    def connection(*, read_only: bool = False) -> Iterator[Any]:
        actor = current_actor_context()
        observed.append((actor.user_uuid, actor.is_system_admin, read_only))
        yield SimpleNamespace(cursor=Cursor)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("制御用の接続へ切り替えてはならない")

    monkeypatch.setattr(adapter, "user_data_connection", connection)
    monkeypatch.setattr(adapter, "connection", forbidden)
    with actor_scope("actor-a", is_system_admin=is_admin):
        assert adapter.read_native_keyword_metadata("LEVEL") == positive()
    assert observed == [("actor-a", is_admin, True)]
