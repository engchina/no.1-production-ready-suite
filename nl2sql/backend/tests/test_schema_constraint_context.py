"""SQL 生成の制約メタデータは、現在可視の列だけに閉じる。"""

import json
from typing import Any, Literal

import pytest

from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaConstraintDetail,
    SchemaTable,
)
from app.features.nl2sql.schema_constraint_context import schema_constraint_context
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


def table(
    name: str,
    columns: list[str],
    constraints: list[SchemaConstraintDetail] | None = None,
    owner: str = "APP",
) -> SchemaTable:
    return SchemaTable(
        table_name=name,
        logical_name=name,
        owner=owner,
        columns=[SchemaColumn(column_name=c, logical_name=c, data_type="NUMBER") for c in columns],
        constraint_details=constraints or [],
    )


def constraint(
    kind: Literal["P", "U", "R", "C"], columns: list[str], **kwargs: Any
) -> SchemaConstraintDetail:
    return SchemaConstraintDetail(
        constraint_name="SYNTHETIC", constraint_type=kind, table_name="B", columns=columns, **kwargs
    )


def records(tables: list[SchemaTable]) -> list[dict[str, Any]]:
    return [json.loads(line[2:]) for line in schema_constraint_context(tables)[1:]]


def fixture() -> list[SchemaTable]:
    return [
        table("A", ["ID", "PART"], [constraint("P", ["ID", "PART"])]),
        table(
            "B",
            ["ID", "A_ID", "A_PART"],
            [
                constraint("U", ["ID"]),
                constraint(
                    "R",
                    ["A_ID", "A_PART"],
                    referenced_owner="APP",
                    referenced_table="A",
                    referenced_columns=["ID", "PART"],
                ),
            ],
        ),
        table("C", ["ID"], [constraint("P", ["ID"])]),
    ]


def test_composite_key_order_and_distinct_same_named_ids() -> None:
    result = records(fixture())
    fk = next(r for r in result if r["type"] == "foreign_key")
    assert fk["columns"] == ["A_ID", "A_PART"]
    assert fk["referenced_table"] == "APP.A"
    assert fk["referenced_columns"] == ["ID", "PART"]
    assert not any(r.get("referenced_table") == "APP.C" for r in result)
    assert {r["type"] for r in result} == {"primary_key", "unique_key", "foreign_key"}


@pytest.mark.parametrize("missing", ["target_table", "target_column", "local_column", "arity"])
def test_foreign_key_is_not_partially_rendered_or_leaked(missing: str) -> None:
    tables = fixture()
    if missing == "target_table":
        tables = tables[1:]
    elif missing == "target_column":
        tables[0].columns = tables[0].columns[:1]
    elif missing == "local_column":
        tables[1].columns = tables[1].columns[:2]
    else:
        tables[1].constraint_details[1].referenced_columns = ["ID"]
    assert not any(r["type"] == "foreign_key" for r in records(tables))


def test_serializer_preserves_quoted_identifiers_and_status() -> None:
    a = table("Mixed A", ["Mixed Id"], owner="Mixed Owner")
    b = table(
        "Mixed B",
        ["Ref Id"],
        [
            constraint(
                "R",
                ["Ref Id"],
                referenced_owner="Mixed Owner",
                referenced_table="Mixed A",
                referenced_columns=["Mixed Id"],
                status="DISABLED",
            )
        ],
        owner="Mixed Owner",
    )
    fk = records([a, b])[0]
    assert fk["referenced_table"] == '"Mixed Owner"."Mixed A"'
    assert fk["columns"] == ['"Ref Id"'] and fk["referenced_columns"] == ['"Mixed Id"']
    assert fk["status"] == "DISABLED"


def test_no_legacy_constraint_text_or_check_expression_leaks() -> None:
    t = table("A", ["ID"], [constraint("C", ["ID"])])
    t.constraints = ["secret outside target"]
    assert schema_constraint_context([t]) == []


def test_direct_context_filters_constraints_with_table_and_column_scope() -> None:
    service = Nl2SqlService(MemoryNl2SqlStore())
    profile = Nl2SqlProfile(id="synthetic", name="synthetic", allowed_tables=["APP.A", "APP.B"])
    catalog = SchemaCatalog(refreshed_at="2026-01-01T00:00:00Z", tables=fixture())
    allowed = AllowedObjects(table_names=["APP.B"], columns={"APP.B": ["ID", "A_ID", "A_PART"]})
    context = service._enterprise_ai_schema_context(
        profile=profile, allowed=allowed, catalog=catalog
    )
    assert '"type": "unique_key"' in context
    assert "referenced_table" not in context and "APP.A" not in context and "APP.C" not in context
    allowed = AllowedObjects(
        table_names=["APP.A", "APP.B"], columns={"APP.A": ["ID"], "APP.B": ["ID", "A_ID", "A_PART"]}
    )
    context = service._enterprise_ai_schema_context(
        profile=profile, allowed=allowed, catalog=catalog
    )
    assert "referenced_columns" not in context
    allowed = AllowedObjects(table_names=["APP.A", "APP.B"])
    context = service._enterprise_ai_schema_context(
        profile=profile, allowed=allowed, catalog=catalog
    )
    assert '"referenced_columns": ["ID", "PART"]' in context
