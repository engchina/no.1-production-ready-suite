from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.clients.oracle_runtime import OraclePoolManager
from app.security.deepsec import (
    _DEEPSEC_APP_USER_CONTEXT_EXPR,
    DeepSecService,
    build_data_entitlement_statements,
)
from app.security.domain import DataEntitlementRecord, scope_expression_from_json
from app.security.schemas import DataEntitlementInput, ScopeExpression
from app.security.scope_expression import compile_expression
from app.security.service import SecurityApiError, SecurityService
from app.settings import Settings


def condition(column: str = "STATUS", value: str = "ACTIVE", **extra: Any) -> dict[str, Any]:
    return {
        "kind": "condition",
        "filter": {
            "column_name": column,
            "operator": "EQ",
            "value_type": "TEXT",
            "value": value,
            **extra,
        },
    }


def group(*children: dict[str, Any], operator: str = "AND") -> dict[str, Any]:
    return {"kind": "group", "operator": operator, "children": list(children)}


def related(*conditions: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "related_exists",
        "profile_id": "p",
        "object_scope_version": 1,
        "target_owner": "HR",
        "target_object": "DEPARTMENTS",
        "join_keys": [{"source_column": "DEPT", "target_column": "ID"}],
        "condition": group(*(conditions or (condition("LOCATION", "TOKYO"),))),
    }


def record(root: dict[str, Any]) -> DataEntitlementRecord:
    return DataEntitlementInput.model_validate(
        {
            "entitlement_id": "e",
            "resource_code": "HR.EMPLOYEES",
            "capability": "SELECT",
            "target_owner": "HR",
            "target_object": "EMPLOYEES",
            "column_names": ["ID", "STATUS"],
            "scope_mode": "EXPRESSION",
            "scope_expression": {"version": 1, "root": root},
        }
    ).to_record("r")


def test_grouping_and_same_related_row_are_executed() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS HR")
    db.execute("CREATE TABLE HR.EMPLOYEES(ID TEXT, STATUS TEXT, DEPT TEXT)")
    db.execute("CREATE TABLE HR.DEPARTMENTS(ID TEXT, LOCATION TEXT, ENABLED TEXT)")
    db.executemany(
        "INSERT INTO HR.EMPLOYEES VALUES (?, ?, ?)",
        [
            ("1", "ACTIVE", "D1"),
            ("2", "ACTIVE", "D2"),
            ("3", "INACTIVE", "D3"),
            ("4", "ACTIVE", None),
        ],
    )
    db.executemany(
        "INSERT INTO HR.DEPARTMENTS VALUES (?, ?, ?)",
        [
            ("D1", "TOKYO", "YES"),
            ("D1", "TOKYO", "YES"),
            ("D2", "TOKYO", "NO"),
            ("D2", "OSAKA", "YES"),
        ],
    )
    a, b, c = (
        condition(),
        related(condition("LOCATION", "TOKYO"), condition("ENABLED", "YES")),
        condition("ID", "3"),
    )
    predicate = compile_expression(record(group(group(a, b), c, operator="OR")), {})
    assert [row[0] for row in db.execute("SELECT ID FROM HR.EMPLOYEES WHERE " + predicate)] == [
        "1",
        "3",
    ]
    predicate = compile_expression(record(group(a, group(b, c, operator="OR"))), {})
    assert [row[0] for row in db.execute("SELECT ID FROM HR.EMPLOYEES WHERE " + predicate)] == ["1"]


def test_or_cannot_escape_role_gate() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS HR")
    db.execute("ATTACH DATABASE ':memory:' AS APP")
    db.execute("CREATE TABLE HR.EMPLOYEES(ID TEXT, STATUS TEXT)")
    db.execute("INSERT INTO HR.EMPLOYEES VALUES ('3', 'ACTIVE')")
    db.execute("CREATE TABLE APP.NL2SQL_APP_USER_ROLES(USER_UUID TEXT, ROLE_ID TEXT)")
    db.execute("CREATE TABLE APP.NL2SQL_APP_ROLES(ROLE_ID TEXT, ARCHIVED INTEGER)")
    db.execute(
        "CREATE TABLE APP.NL2SQL_APP_DATA_ENTITLEMENTS(ROLE_ID TEXT, ENTITLEMENT_ID TEXT, "
        "CAPABILITY TEXT, APPLY_STATUS TEXT)"
    )
    db.execute("INSERT INTO APP.NL2SQL_APP_ROLES VALUES ('r', 0)")
    db.execute(
        "INSERT INTO APP.NL2SQL_APP_DATA_ENTITLEMENTS VALUES ('r', 'e', 'SELECT', 'APPLIED')"
    )
    grant = build_data_entitlement_statements(
        Settings(oracle_user="APP"), record(group(condition(), condition("ID", "3"), operator="OR"))
    )[2]
    predicate = (
        grant.split("WHERE ", 1)[1]
        .rsplit("TO ", 1)[0]
        .replace(_DEEPSEC_APP_USER_CONTEXT_EXPR, "'user'")
    )
    query = "SELECT ID FROM HR.EMPLOYEES WHERE " + predicate
    assert list(db.execute(query)) == []
    db.execute("INSERT INTO APP.NL2SQL_APP_USER_ROLES VALUES ('user', 'r')")
    assert list(db.execute(query)) == [("3",)]
    db.execute("UPDATE APP.NL2SQL_APP_ROLES SET ARCHIVED = 1")
    assert list(db.execute(query)) == []
    db.execute("UPDATE APP.NL2SQL_APP_ROLES SET ARCHIVED = 0")
    db.execute("UPDATE APP.NL2SQL_APP_DATA_ENTITLEMENTS SET APPLY_STATUS = 'PENDING'")
    assert list(db.execute(query)) == []


@pytest.mark.parametrize(
    "root",
    [
        group(),
        group(group(group(group(condition())))),
        group(*[condition() for _ in range(21)]),
        group(*[related() for _ in range(4)]),
        group(related(related())),
    ],
)
def test_limits_fail_closed(root: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        record(root)


def test_root_boolean_is_not_arbitrary_sql() -> None:
    root = group(condition())
    root["operator"] = "OR 1=1 --"
    with pytest.raises(ValidationError):
        record(root)
    node = related()
    node["target_object"] = "DEPARTMENTS@REMOTE"
    with pytest.raises(ValidationError):
        record(group(node))
    with pytest.raises(ValidationError):
        record(group(condition(raw_sql="1=1")))


def test_self_relation_and_unknown_columns_rejected() -> None:
    node = related()
    node["target_object"] = "EMPLOYEES"
    with pytest.raises(SecurityApiError):
        compile_expression(record(group(node)), {})
    with pytest.raises(SecurityApiError):
        compile_expression(record(group(condition())), {"OTHER": "NUMBER"})


def test_composite_keys_preserved_and_literals_escaped() -> None:
    node = related(condition("LOCATION", "O'Reilly"))
    node["join_keys"].append({"source_column": "TENANT", "target_column": "TENANT"})
    sql = compile_expression(record(group(node)), {})
    assert "HR.EMPLOYEES.DEPT = dsr1.ID AND HR.EMPLOYEES.TENANT = dsr1.TENANT" in sql
    assert "O''Reilly" in sql
    assert sql.count("EXISTS") == 1


def test_full_predicate_limit_counts_role_gate() -> None:
    entitlement = record(group(*[condition(value="x" * 512) for _ in range(8)]))
    with pytest.raises(SecurityApiError, match="4000"):
        build_data_entitlement_statements(Settings(oracle_user="APP"), entitlement)


def test_expression_roundtrip_and_unmodified_apply_state() -> None:
    entitlement = replace(
        record(group(condition())),
        data_grant_name="SAVED",
        sql_checksum="checksum",
        apply_status="APPLIED",
    )
    records = SecurityService._data_entitlement_records(
        "r", [deepcopy(entitlement)], current_entitlements=[entitlement]
    )
    assert records == [entitlement]
    assert records[0].scope_expression is not entitlement.scope_expression
    assert (
        scope_expression_from_json(json.dumps(entitlement.scope_expression))
        == entitlement.scope_expression
    )
    changed = replace(
        entitlement, scope_expression=record(group(condition(value="OTHER"))).scope_expression
    )
    result = SecurityService._data_entitlement_records(
        "r", [changed], current_entitlements=[entitlement]
    )[0]
    assert result.entitlement_id == "e" and result.data_grant_name == "SAVED"
    assert result.apply_status == "PENDING" and not result.sql_checksum
    with pytest.raises(SecurityApiError):
        SecurityService._data_entitlement_records(
            "r",
            [replace(entitlement, scope_mode="FILTERS", scope_expression=None)],
            current_entitlements=[entitlement],
        )


def test_extra_expression_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        ScopeExpression.model_validate({"version": 2, "root": group(condition())})


class MetadataCursor:
    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []
        self.denied = False
        self.dependencies: list[tuple[Any, ...]] = []
        self.parent_type = "VARCHAR2"

    def execute(self, sql: str, params: Any = None) -> None:
        self.rows = []
        if "FROM ALL_CONSTRAINTS" in sql:
            self.rows = [("HR", "FK_DEPT", "EMPLOYEES", "HR", "DEPARTMENTS", "DEPT", "ID", 1)]
        elif "FROM ALL_OBJECTS" in sql:
            self.rows = [("TABLE",)]
        elif "FROM ALL_TAB_COLUMNS" in sql:
            self.rows = [("ID", self.parent_type), ("LOCATION", "VARCHAR2")]
        elif "FROM ALL_DEPENDENCIES" in sql:
            self.rows = self.dependencies
        elif sql.startswith("SELECT 1 FROM HR.DEPARTMENTS") and self.denied:
            raise RuntimeError("ORA-00942")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


@pytest.fixture
def relation_validation(monkeypatch: pytest.MonkeyPatch) -> tuple[DeepSecService, MetadataCursor]:
    from app.security import scope_relations
    from app.security.deepsec import DeepSecService
    from app.security.store import InMemorySecurityStore

    monkeypatch.setattr(
        scope_relations,
        "scope_profiles",
        lambda: [
            {"id": "p", "object_scope_version": 1, "objects": ["HR.EMPLOYEES", "HR.DEPARTMENTS"]}
        ],
    )
    monkeypatch.setattr(scope_relations, "_ontology_relations", lambda *args: [])
    settings = Settings(oracle_user="APP")
    security = SecurityService(InMemorySecurityStore(), settings)
    return DeepSecService(settings, security, cast(OraclePoolManager, None)), MetadataCursor()


def test_relation_metadata_and_composite_catalog(
    relation_validation: tuple[DeepSecService, MetadataCursor],
) -> None:
    from app.security.scope_expression import validate_expression_metadata
    from app.security.scope_relations import relation_catalog

    service, cursor = relation_validation
    catalog = relation_catalog(service, "p", "HR.EMPLOYEES", cursor=cursor)
    candidate = catalog["relations"][0]
    assert candidate["join_keys"] == [{"source_column": "DEPT", "target_column": "ID"}]
    node = related()
    node.update(
        relation_source="FOREIGN_KEY",
        relation_id=candidate["id"],
        relation_version=candidate["version"],
    )
    columns = {"ID": "VARCHAR2", "STATUS": "VARCHAR2", "DEPT": "VARCHAR2"}
    validate_expression_metadata(service, cursor, record(group(node)), columns)
    assert columns["HR.DEPARTMENTS.LOCATION"] == "VARCHAR2"


@pytest.mark.parametrize(
    "change",
    [
        {"profile_id": "deleted"},
        {"object_scope_version": 2},
        {"target_object": "OUT_OF_SCOPE"},
        {"join_keys": [{"source_column": "MISSING", "target_column": "ID"}]},
        {"relation_source": "FOREIGN_KEY", "relation_id": "HR.FK_DEPT", "relation_version": "old"},
    ],
)
def test_invalid_relation_metadata_is_rejected(
    relation_validation: tuple[DeepSecService, MetadataCursor], change: dict[str, Any]
) -> None:
    from app.security.scope_expression import validate_expression_metadata

    service, cursor = relation_validation
    node = related()
    node.update(change)
    with pytest.raises(SecurityApiError):
        validate_expression_metadata(service, cursor, record(group(node)), {"DEPT": "VARCHAR2"})


@pytest.mark.parametrize("issue", ["denied", "cycle", "remote", "type"])
def test_relation_permissions_dependencies_and_types_fail_closed(
    relation_validation: tuple[DeepSecService, MetadataCursor], issue: str
) -> None:
    from app.security.scope_expression import validate_expression_metadata

    service, cursor = relation_validation
    if issue == "denied":
        cursor.denied = True
    if issue == "cycle":
        cursor.dependencies = [("HR", "EMPLOYEES", None)]
    if issue == "remote":
        cursor.dependencies = [("HR", "OTHER", "REMOTE")]
    if issue == "type":
        cursor.parent_type = "NUMBER"
    with pytest.raises(SecurityApiError):
        validate_expression_metadata(
            service, cursor, record(group(related())), {"DEPT": "VARCHAR2"}
        )


def test_expression_storage_and_api_preserve_full_tree() -> None:
    from app.security.domain import RoleRecord
    from app.security.schemas import DataEntitlementData
    from app.security.store import OracleSecurityStore

    entitlement = record(group(condition(), related(), operator="OR"))
    calls = []

    class Cursor:
        def execute(self, sql: str, binds: dict[str, Any]) -> None:
            calls.append((sql, binds))

    role = RoleRecord("r", "R", "role", "", False, False, 1, entitlements=[entitlement])
    OracleSecurityStore._replace_role_access(Cursor(), role)
    sql, binds = next(
        call for call in calls if "INSERT INTO NL2SQL_APP_DATA_ENTITLEMENTS" in call[0]
    )
    assert "SCOPE_EXPRESSION" in sql
    assert json.loads(binds["scope_expression"]) == entitlement.scope_expression
    response = DataEntitlementData.from_record(entitlement)
    assert response.scope_expression is not None
    assert response.scope_expression.model_dump() == entitlement.scope_expression


def test_old_client_cannot_turn_expression_into_all_rows() -> None:
    original = record(group(condition()))
    all_rows = replace(original, scope_mode="ALL", scope_expression=None)
    with pytest.raises(SecurityApiError):
        SecurityService._data_entitlement_records("r", [all_rows], current_entitlements=[original])
    explicit = replace(all_rows, scope_expression_version=1)
    result = SecurityService._data_entitlement_records(
        "r", [explicit], current_entitlements=[original]
    )
    assert result[0].scope_mode == "ALL" and result[0].apply_status == "PENDING"


def test_ontology_candidates_require_published_approved_profile_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.features.nl2sql import ontology_router
    from app.security.scope_relations import _ontology_relations

    edge: dict[str, Any] = {
        "id": "edge",
        "revision_id": "rev",
        "kind": "foreign_key",
        "source_node_id": "employee",
        "target_node_id": "department",
        "relationship_name_ja": "所属部署",
        "provenance": {"source_kind": "manual"},
        "review_status": "approved",
        "join_conditions": [
            {
                "left": {"owner": "HR", "object_name": "EMPLOYEES", "column_name": "DEPT"},
                "right": {"owner": "HR", "object_name": "DEPARTMENTS", "column_name": "ID"},
            }
        ],
    }
    view: dict[str, Any] = {
        "id": "view",
        "profile_id": "p",
        "ontology_revision_id": "rev",
        "view_version": 2,
        "status": "published",
        "edge_ids": ["edge"],
    }
    revision = {"status": "published"}

    class Store:
        def list_documents(self, collection: str, identity: dict[str, str]) -> list[dict[str, Any]]:
            return [{"payload": view if collection == "profile_views" else edge}]

        def get_document(self, collection: str, identity: dict[str, str]) -> dict[str, Any]:
            return {"payload": revision}

    monkeypatch.setattr(
        ontology_router,
        "ontology_runtime",
        SimpleNamespace(
            store=Store(),
            _stored_payload=lambda document, **kwargs: document["payload"],
        ),
    )

    def candidates() -> list[dict[str, Any]]:
        return _ontology_relations("p", "HR.EMPLOYEES", ["HR.EMPLOYEES", "HR.DEPARTMENTS"])

    assert candidates()[0]["version"] == "rev:2"
    assert candidates()[0]["join_keys"] == [{"source_column": "DEPT", "target_column": "ID"}]
    edge["review_status"] = "proposed"
    assert candidates() == []
    edge["review_status"] = "approved"
    revision["status"] = "draft"
    assert candidates() == []
    revision["status"] = "published"
    view["archived"] = True
    assert candidates() == []
