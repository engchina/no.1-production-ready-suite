"""同回の許可済み schema の完全一致だけを修正データにする。"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.nl2sql import sql_binding
from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlEngine,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.service import GeneratedSql, Nl2SqlService
from app.features.nl2sql.sql_binding import (
    SqlGenerationValidationError,
    column_binding_findings,
    column_binding_violations,
)
from app.features.nl2sql.store import MemoryNl2SqlStore


def table(name: str, columns: tuple[str, ...], *, owner: str = "APP") -> SchemaTable:
    return SchemaTable(
        owner=owner,
        table_name=name,
        logical_name=name,
        columns=[SchemaColumn(column_name=c, logical_name=c, data_type="NUMBER") for c in columns],
    )


def catalog() -> SchemaCatalog:
    return SchemaCatalog(
        refreshed_at="snapshot-a",
        tables=[
            table("TABLE_A", ("ID",)),
            table("TABLE_B", ("ID", "VALUE_DIFF")),
            table("OTHER_SCOPE", ("ID", "VALUE_DIFF")),
            table("AGENT_INTERNAL", ("VALUE_DIFF",)),
        ],
    )


def profile(*names: str) -> Nl2SqlProfile:
    return Nl2SqlProfile(id="p", name="P", allowed_tables=list(names), object_scope_version=2)


def test_typed_finding_preserves_legacy_error_and_exact_candidate_identity() -> None:
    tables = catalog().tables[:2]
    sql = "SELECT a.VALUE_DIFF AS value_diff FROM APP.TABLE_A a"
    found = column_binding_findings(sql, tables, owner="APP", candidate_tables=tables)
    assert [f.message for f in found] == column_binding_violations(sql, tables, owner="APP")
    assert found[0].message == "SQL_COLUMN_BINDING_INVALID: Unknown column: VALUE_DIFF"
    assert found[0].diagnostics == [
        {
            "source": "schema_catalog",
            "reference": {"qualifier": "A", "column": "VALUE_DIFF", "quoted": False},
            "exact_candidates": [
                {
                    "owner": "APP",
                    "table": "TABLE_B",
                    "column": "VALUE_DIFF",
                    "sql_identifier": '"APP"."TABLE_B"."VALUE_DIFF"',
                }
            ],
            "truncated": False,
        }
    ]
    assert column_binding_findings(sql, tables, owner="APP")[0].diagnostics == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT VALUE_DIFF FROM APP.TABLE_A",
        "WITH x AS (SELECT ID FROM APP.TABLE_A) SELECT x.VALUE_DIFF FROM x",
        "SELECT x.VALUE_DIFF FROM (SELECT ID FROM APP.TABLE_A) x",
        "SELECT a.ID FROM APP.TABLE_A a WHERE EXISTS "
        "(SELECT 1 FROM APP.TABLE_B b WHERE b.ID=a.VALUE_DIFF)",
        "SELECT x.VALUE_DIFF FROM APP.NOT_IN_SNAPSHOT x",
    ],
)
def test_uncertain_derived_unqualified_external_or_unknown_source_has_no_candidates(
    sql: str,
) -> None:
    tables = catalog().tables[:2]
    findings = column_binding_findings(sql, tables, owner="APP", candidate_tables=tables)
    assert all(not finding.diagnostics for finding in findings)


def test_physical_column_within_cte_is_still_a_certain_local_source() -> None:
    tables = catalog().tables[:2]
    found = column_binding_findings(
        "WITH x AS (SELECT a.VALUE_DIFF FROM APP.TABLE_A a) SELECT * FROM x",
        tables,
        owner="APP",
        candidate_tables=tables,
    )
    assert found[0].diagnostics[0]["exact_candidates"][0]["table"] == "TABLE_B"


@pytest.mark.parametrize(
    "column,expected",
    [('"Value_Diff"', ["Value_Diff"]), ("VALUE_DIFF", ["VALUE_DIFF"]), ('"value_diff"', [])],
)
def test_quoted_identifiers_are_not_folded_or_fuzzily_matched(
    column: str, expected: list[str]
) -> None:
    tables = [
        table("Mixed_A", ("ID",), owner="MixedOwner"),
        table("Mixed_B", ("VALUE_DIFF", "Value_Diff"), owner="MixedOwner"),
    ]
    sql = f'SELECT a.{column} FROM "MixedOwner"."Mixed_A" a'
    found = column_binding_findings(sql, tables, owner="APP", candidate_tables=tables)
    candidates = found[0].diagnostics[0]["exact_candidates"]
    assert [c["column"] for c in candidates] == expected
    assert all(c["owner"] == "MixedOwner" and c["table"] == "Mixed_B" for c in candidates)
    assert found[0].diagnostics[0]["reference"]["quoted"] == column.startswith('"')


def test_all_exact_matches_are_bounded_and_not_ranked_by_semantics() -> None:
    tables = [table("TABLE_A", ("ID",))] + [
        table(f"TABLE_{i:02}", ("VALUE_DIFF",)) for i in range(17)
    ]
    found = column_binding_findings(
        "SELECT a.VALUE_DIFF FROM APP.TABLE_A a", tables, owner="APP", candidate_tables=tables
    )
    diagnostic = found[0].diagnostics[0]
    assert len(diagnostic["exact_candidates"]) == 16 and diagnostic["truncated"]
    assert [c["table"] for c in diagnostic["exact_candidates"]] == [
        f"TABLE_{i:02}" for i in range(16)
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a.ID FROM APP.TABLE_A a",
        "SELECT COUNT(*) FROM APP.TABLE_A",
        "SELECT a.ID FROM APP.TABLE_A a UNION ALL SELECT b.ID FROM APP.TABLE_B b",
        "SELECT LEVEL FROM DUAL CONNECT BY LEVEL <= 2",
    ],
)
def test_legal_sql_gets_neither_rejection_nor_suggestions(sql: str) -> None:
    tables = catalog().tables[:2]
    assert column_binding_findings(sql, tables, owner="APP", candidate_tables=tables) == []


def test_candidate_failure_cannot_mask_original_binding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private helper error")

    tables = catalog().tables[:2]
    sql = "SELECT a.VALUE_DIFF FROM APP.TABLE_A a"
    original = column_binding_violations(sql, tables, owner="APP")
    monkeypatch.setattr(sql_binding, "_missing_physical_columns", broken)
    found = column_binding_findings(sql, tables, owner="APP", candidate_tables=tables)
    assert [f.message for f in found] == original
    assert found[0].diagnostics == [] and "private" not in found[0].message


@pytest.mark.parametrize(
    "restriction", ["profile", "request", "column", "hidden", "empty", "invalid", "legacy_empty"]
)
def test_service_filters_candidates_by_profile_request_columns_and_hidden_objects_without_reads(
    monkeypatch: pytest.MonkeyPatch,
    restriction: str,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = catalog()
    names = ["APP.TABLE_A", "APP.TABLE_B", "APP.AGENT_INTERNAL"]
    selected = profile(*names)
    allowed = AllowedObjects(table_names=names, enforce_table_scope=True)
    if restriction == "profile":
        selected = profile("APP.TABLE_A")
    if restriction == "request":
        allowed = AllowedObjects(table_names=["APP.TABLE_A"], enforce_table_scope=True)
    if restriction == "column":
        allowed.columns = {"APP.TABLE_B": ["ID"]}
    if restriction == "empty":
        allowed = AllowedObjects(enforce_table_scope=True)
    if restriction == "invalid":
        allowed.table_names = ['APP."unterminated']
    if restriction == "legacy_empty":
        selected = Nl2SqlProfile(id="p", name="P")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("別の catalog や DB を読み直してはならない")

    for name in [
        "get_catalog",
        "_current_schema_owner",
        "_resolve_profile_object_name",
        "profile_allowed_object_names",
    ]:
        monkeypatch.setattr(service, name, forbidden)
    visible = service._binding_candidate_tables(selected, allowed, snap)
    assert all(t.table_name not in {"OTHER_SCOPE", "AGENT_INTERNAL"} for t in visible)
    findings = column_binding_findings(
        "SELECT a.VALUE_DIFF FROM APP.TABLE_A a", snap.tables, owner="APP", candidate_tables=visible
    )
    candidates = [c for f in findings for d in f.diagnostics for c in d["exact_candidates"]]
    assert [c["table"] for c in candidates] == (["TABLE_B"] if restriction == "hidden" else [])
    assert len(snap.tables[1].columns) == 2  # 元 snapshot を変更しない。


def test_scopes_from_different_actors_do_not_share_candidate_state() -> None:
    from app.security.request_actor import actor_scope

    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = catalog()
    with actor_scope("actor-a"):
        visible_a = service._binding_candidate_tables(
            profile("APP.TABLE_A", "APP.TABLE_B"), AllowedObjects(), snap
        )
    with actor_scope("actor-b"):
        visible_b = service._binding_candidate_tables(
            profile("APP.TABLE_A"), AllowedObjects(), snap
        )
    sql = "SELECT a.VALUE_DIFF FROM APP.TABLE_A a"
    assert column_binding_findings(sql, snap.tables, owner="APP", candidate_tables=visible_a)[
        0
    ].diagnostics[0]["exact_candidates"]
    assert (
        column_binding_findings(sql, snap.tables, owner="APP", candidate_tables=visible_b)[
            0
        ].diagnostics[0]["exact_candidates"]
        == []
    )


@pytest.mark.parametrize("repair", [False, True])
def test_existing_two_attempts_receive_typed_candidates_without_rewrite_or_model_extra_calls(
    monkeypatch: pytest.MonkeyPatch,
    repair: bool,
) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = catalog()
    bad = "SELECT a.VALUE_DIFF AS requested_diff FROM APP.TABLE_A a"
    good = "SELECT b.VALUE_DIFF AS requested_diff FROM APP.TABLE_B b"
    calls: list[dict[str, Any]] = []

    def generate(*args: Any, **kwargs: Any) -> GeneratedSql:
        calls.append(kwargs)
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=good if repair and len(calls) == 2 else bad,
            explanation="",
            engine_meta={},
            schema_catalog=snap,
        )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("候補生成のために DB を呼んではならない")

    monkeypatch.setattr(service, "_generate_sql_with_engine", generate)
    monkeypatch.setattr(service._oracle_adapter, "execute_select", forbidden)
    monkeypatch.setattr(service._oracle_adapter, "prepare_select", forbidden)

    def run() -> GeneratedSql:
        return service._generate_sql(
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            "指定した差を取得",
            profile("APP.TABLE_A", "APP.TABLE_B"),
            AllowedObjects(table_names=["APP.TABLE_A", "APP.TABLE_B"], enforce_table_scope=True),
            10,
            [],
        )

    if repair:
        assert run().generated_sql == good
    else:
        with pytest.raises(SqlGenerationValidationError):
            run()
    assert len(calls) == 2 and calls[0]["generation_feedback"] is None
    feedback = calls[1]["generation_feedback"]
    assert feedback["rejected_sql"] == bad and feedback["operation"] == "repair_rejected_sql"
    assert feedback["binding_diagnostics"][0]["exact_candidates"][0]["table"] == "TABLE_B"
    assert feedback["violations"] == column_binding_violations(bad, snap.tables, owner="APP")


@pytest.mark.parametrize("reverse", [False, True])
def test_equivalent_scope_keys_intersect_nonempty_column_restrictions(reverse: bool) -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = catalog()
    entries = [("APP.TABLE_B", ["ID", "VALUE_DIFF"]), ("TABLE_B", ["ID"]), ('"APP"."TABLE_B"', [])]
    if reverse:
        entries.reverse()
    visible = service._binding_candidate_tables(
        profile("APP.TABLE_A", "APP.TABLE_B"),
        AllowedObjects(columns=dict(entries)),
        snap,
    )
    assert [
        c.column_name for c in next(t for t in visible if t.table_name == "TABLE_B").columns
    ] == ["ID"]
    found = column_binding_findings(
        "SELECT a.VALUE_DIFF FROM APP.TABLE_A a", snap.tables, owner="APP", candidate_tables=visible
    )
    assert found[0].diagnostics[0]["exact_candidates"] == []


def test_disjoint_column_restrictions_do_not_become_unrestricted() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = catalog()
    visible = service._binding_candidate_tables(
        profile("APP.TABLE_A", "APP.TABLE_B"),
        AllowedObjects(columns={"APP.TABLE_B": ["ID"], "TABLE_B": ["VALUE_DIFF"]}),
        snap,
    )
    assert next(t for t in visible if t.table_name == "TABLE_B").columns == []


def test_hidden_existing_column_is_not_reclassified_as_missing_by_another_failure() -> None:
    tables = [table("TABLE_A", ("ID",)), table("TABLE_B", ("ID", "VALUE_DIFF"))]
    restricted = [table("TABLE_A", ()), tables[1]]
    found = column_binding_findings(
        "SELECT a.ID,a.VALUE_DIFF FROM APP.TABLE_A a",
        tables,
        owner="APP",
        candidate_tables=restricted,
    )
    assert [d["reference"]["column"] for d in found[0].diagnostics] == ["VALUE_DIFF"]


def test_same_table_name_in_other_owner_has_exact_physical_identity() -> None:
    tables = [table("TABLE_A", ("ID",)), table("TABLE_A", ("VALUE_DIFF",), owner="OTHER")]
    found = column_binding_findings(
        "SELECT a.VALUE_DIFF FROM APP.TABLE_A a", tables, owner="APP", candidate_tables=tables
    )
    assert found[0].diagnostics[0]["exact_candidates"][0]["owner"] == "OTHER"
    assert not column_binding_findings(
        "SELECT a.VALUE_DIFF FROM OTHER.TABLE_A a", tables, owner="APP", candidate_tables=tables
    )


def test_non_ascii_quoted_catalog_roundtrip_and_scoped_visibility() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    tables = [table("表A", ("ID",), owner="所有者"), table("表B", ("値A",), owner="所有者")]
    snap = SchemaCatalog(refreshed_at="snapshot-b", tables=tables)
    selected = profile('"所有者"."表A"', '"所有者"."表B"')
    visible = service._binding_candidate_tables(
        selected, AllowedObjects(columns={'"所有者"."表B"': ['"値A"']}), snap
    )
    found = column_binding_findings(
        'SELECT a."値A" FROM "所有者"."表A" a', tables, owner="APP", candidate_tables=visible
    )
    candidate = found[0].diagnostics[0]["exact_candidates"][0]
    assert candidate == {
        "owner": "所有者",
        "table": "表B",
        "column": "値A",
        "sql_identifier": '"所有者"."表B"."値A"',
    }


def test_ambiguous_bare_object_scope_and_missing_owner_offer_no_candidates() -> None:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    snap = SchemaCatalog(
        refreshed_at="s",
        tables=[
            table("TABLE_A", ("ID",)),
            table("TABLE_A", ("VALUE_DIFF",), owner="OTHER"),
            table("TABLE_B", ("VALUE_DIFF",), owner=""),
        ],
    )
    assert service._binding_candidate_tables(profile("TABLE_A"), AllowedObjects(), snap) == []
    visible = service._binding_candidate_tables(
        profile("APP.TABLE_A", "TABLE_B"), AllowedObjects(), snap
    )
    assert visible == []  # OWNER 不明の候補を current owner で補完しない。
