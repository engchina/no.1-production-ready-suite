"""業務プロファイルの対象表を object_identity の引用規則で保存・照合する (#561)。"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlProfile,
    ProfileSelectAiProfileRequest,
    SchemaCatalog,
    SchemaTable,
)
from app.features.nl2sql.object_identity import sql_identifier_token
from app.features.nl2sql.ontology_build import _selected_schema_objects
from app.features.nl2sql.ontology_catalog import (
    build_schema_ontology,
    migrate_profile_ontology_view,
)
from app.features.nl2sql.ontology_models import PhysicalObjectRef, ProfileOntologyView
from app.features.nl2sql.ontology_router import OntologyApiRuntime
from app.features.nl2sql.oracle_adapter import (
    OracleAdapterError,
    OracleNl2SqlAdapter,
    SelectAiObjectListUnsupportedError,
)
from app.features.nl2sql.profile_sync import _profile_sync_public_error
from app.features.nl2sql.service import Nl2SqlService, _graph_with_resolved_table_owners
from app.features.nl2sql.sql_semantics import parse_oracle_sql
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.settings import get_settings

UPPER = "SALES.MIXED_CASE"
QUOTED = 'SALES."Mixed_Case"'


def _table(owner: str, name: str, table_type: str = "TABLE") -> SchemaTable:
    return SchemaTable(
        owner=owner,
        table_name=name,
        logical_name=f"{owner} {name}",
        table_type=table_type,
    )


def _service(*tables: SchemaTable, current_owner: str = "SALES") -> Nl2SqlService:
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._catalog = SchemaCatalog(  # noqa: SLF001 - scope resolver unit boundary
        refreshed_at="2026-09-14T00:00:00+00:00",
        current_owner=current_owner,
        tables=list(tables),
    )
    return service


def _both_tables_service() -> Nl2SqlService:
    return _service(_table("SALES", "MIXED_CASE"), _table("SALES", "Mixed_Case"))


# --- 保存（Profile の対象表の正規化） -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (QUOTED, QUOTED),
        ('"SALES"."Mixed_Case"', QUOTED),
        ('"Mixed_Case"', QUOTED),
        ("mixed_case", UPPER),
        ("Mixed_Case", UPPER),
        ("sales.mixed_case", UPPER),
        ('"SALES"."MIXED_CASE"', UPPER),
        (UPPER, UPPER),
        ('SALES."売上"', 'SALES."売上"'),
    ],
)
def test_resolve_profile_object_name_follows_oracle_quoting(raw: str, expected: str) -> None:
    service = _both_tables_service()

    assert service._resolve_profile_object_name(raw) == expected  # noqa: SLF001


@pytest.mark.parametrize("raw", ['SALES."Mixed_Case', "SALES.my table", "SALES.DEPT@REMOTE", ""])
def test_resolve_profile_object_name_rejects_invalid_identifiers(raw: str) -> None:
    service = _both_tables_service()

    with pytest.raises(ValueError):
        service._resolve_profile_object_name(raw)  # noqa: SLF001


def test_profile_keeps_quoted_and_upper_same_name_tables_distinct() -> None:
    service = _both_tables_service()

    quoted_only = service.create_profile(
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED])
    )
    both = service.create_profile(
        Nl2SqlProfile(
            id="both", name="両方", allowed_tables=[UPPER, QUOTED, '"SALES"."MIXED_CASE"']
        )
    )

    assert quoted_only.allowed_tables == [QUOTED]
    assert service.get_profile("quoted").allowed_tables == [QUOTED]
    assert service.profile_allowed_object_names(quoted_only) == [QUOTED]
    assert both.allowed_tables == [UPPER, QUOTED]


def test_existing_unquoted_profile_values_are_unchanged() -> None:
    service = _service(_table("APP", "ORDERS"), _table("SH", "ORDERS"), current_owner="APP")

    profile = service.create_profile(
        Nl2SqlProfile(
            id="orders",
            name="受注",
            allowed_tables=["ORDERS", "SH.ORDERS", "app.orders", '"APP"."ORDERS"'],
        )
    )

    assert profile.allowed_tables == ["APP.ORDERS", "SH.ORDERS"]
    assert service.build_select_ai_profile_attributes(profile)["object_list"] == [
        {"owner": "APP", "name": "ORDERS"},
        {"owner": "SH", "name": "ORDERS"},
    ]


def test_bare_unquoted_name_does_not_match_quoted_catalog_table() -> None:
    quoted_only = _service(_table("SH", "Mixed_Case"), current_owner="APP")

    # 引用なしの `mixed_case` は Oracle では `MIXED_CASE`。引用名の表 SH."Mixed_Case" に解決しない。
    assert (
        quoted_only._resolve_profile_object_name("mixed_case") == "APP.MIXED_CASE"
    )  # noqa: SLF001
    assert (
        quoted_only._resolve_profile_object_name('"Mixed_Case"') == 'SH."Mixed_Case"'
    )  # noqa: SLF001


# --- 許可表チェック（SQL 安全性検査） --------------------------------------------


@pytest.mark.parametrize(
    ("allowed_name", "sql", "safe", "referenced"),
    [
        (UPPER, 'SELECT * FROM SALES."Mixed_Case"', False, QUOTED),
        (UPPER, 'SELECT * FROM "Mixed_Case"', False, QUOTED),
        (UPPER, "SELECT * FROM sales.mixed_case", True, UPPER),
        (UPPER, 'SELECT * FROM "SALES"."MIXED_CASE"', True, UPPER),
        (QUOTED, 'SELECT * FROM SALES."Mixed_Case"', True, QUOTED),
        (QUOTED, 'SELECT m.* FROM "Mixed_Case" m', True, QUOTED),
        (QUOTED, "SELECT * FROM SALES.MIXED_CASE", False, UPPER),
        (QUOTED, "SELECT * FROM sales.Mixed_Case", False, UPPER),
    ],
)
def test_sql_safety_distinguishes_quoted_and_upper_same_name_tables(
    allowed_name: str, sql: str, safe: bool, referenced: str
) -> None:
    service = _both_tables_service()
    allowed = AllowedObjects(table_names=[allowed_name], enforce_table_scope=True)

    analyzed = service.analyze_sql(sql, allowed, None)

    assert analyzed.safety.referenced_tables == [referenced]
    assert analyzed.safety.is_safe is safe
    if not safe:
        assert "許可されていない表" in (analyzed.safety.blocked_reason or "")


def test_profile_scope_resolution_does_not_widen_quoted_profile_to_upper_table() -> None:
    service = _both_tables_service()
    service.create_profile(Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED]))

    requested_upper = service.resolve_allowed_objects(
        "quoted", AllowedObjects(table_names=[UPPER], enforce_table_scope=True)
    )
    requested_quoted = service.resolve_allowed_objects(
        "quoted", AllowedObjects(table_names=['"SALES"."Mixed_Case"'], enforce_table_scope=True)
    )
    direct_sql = service.resolve_direct_sql_allowed_objects(
        AllowedObjects(table_names=[UPPER, QUOTED], enforce_table_scope=True),
        profile_ids={"quoted"},
    )

    assert requested_upper.table_names == []
    assert requested_quoted.table_names == [QUOTED]
    assert direct_sql.table_names == [QUOTED]


def test_sql_semantic_graph_records_identifier_quoting() -> None:
    graph = parse_oracle_sql(
        'SELECT * FROM sales."Mixed_Case" m JOIN "SALES".orders o ON 1 = 1'
    ).graph

    assert graph is not None
    tables = [(t.owner, t.owner_quoted, t.name, t.name_quoted) for t in graph.tables]
    assert tables == [("sales", False, "Mixed_Case", True), ("SALES", True, "orders", False)]
    assert sql_identifier_token("Mixed_Case", quoted=True) == '"Mixed_Case"'
    assert sql_identifier_token("mixed_case", quoted=False) == "MIXED_CASE"


def test_resolved_table_owner_keeps_quoted_name_for_display() -> None:
    graph = _graph_with_resolved_table_owners(
        parse_oracle_sql('SELECT * FROM "Mixed_Case" JOIN sales.mixed_case ON 1 = 1').graph,
        "SALES",
    )

    assert graph is not None
    assert [(t.resolved_owner, t.resolved_qualified_name) for t in graph.tables] == [
        ("SALES", QUOTED),
        ("SALES", UPPER),
    ]


def test_generation_catalog_does_not_use_upper_table_detail_for_quoted_profile_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql.models import SchemaObjectDetail, SchemaObjectPage

    upper = _table("SALES", "MIXED_CASE")

    class Repository:
        def get_schema_object(self, owner: str, object_name: str) -> SchemaObjectDetail | None:
            # schema catalog の詳細取得は大文字小文字を区別しない（既存の制約を再現する）。
            if (owner.upper(), object_name.upper()) == ("SALES", "MIXED_CASE"):
                return SchemaObjectDetail(table=upper)
            return None

        def search_schema_objects(self, **_kwargs: Any) -> SchemaObjectPage:
            return SchemaObjectPage()

    service = _both_tables_service()
    profile = service.create_profile(
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED])
    )
    service._incremental_repository = Repository()  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(service, "_refresh_cache_token", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "get_catalog_head", lambda: service._catalog)  # noqa: SLF001
    monkeypatch.setattr(service, "_profile_scope_for_read", lambda value, **_kwargs: value)

    with pytest.raises(ValueError):
        service._generation_schema_catalog(  # noqa: SLF001
            profile, AllowedObjects(table_names=[QUOTED], enforce_table_scope=True)
        )


# --- Select AI の object_list --------------------------------------------------


def test_select_ai_object_list_rejects_names_that_require_quoting() -> None:
    service = _both_tables_service()
    profile = service.create_profile(
        Nl2SqlProfile(id="both", name="両方", allowed_tables=[UPPER, QUOTED])
    )

    with pytest.raises(SelectAiObjectListUnsupportedError) as exc_info:
        service.build_select_ai_profile_attributes(profile)

    assert exc_info.value.object_names == [QUOTED]
    assert "Select AI Profile の object_list に反映できません" in str(exc_info.value)
    assert "MIXED_CASE" not in str(exc_info.value).replace('"Mixed_Case"', "")


def test_select_ai_refresh_records_unsynchronized_scope_for_quoted_profile() -> None:
    service = _both_tables_service()
    profile = service.create_profile(
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED])
    )

    refreshed = service.refresh_select_ai_profile("quoted")

    assert refreshed.refreshed is False
    assert refreshed.status == "error"
    assert "Select AI Profile の object_list に反映できません" in refreshed.warning
    assert refreshed.engine_meta["unsupported_object_list_names"] == [QUOTED]
    with pytest.raises(OracleAdapterError, match="未同期"):
        service._assert_select_ai_scope_ready(profile)  # noqa: SLF001


def test_profile_select_ai_upsert_returns_explicit_error_for_quoted_profile() -> None:
    service = _both_tables_service()
    profile = service.create_profile(
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED])
    )

    result = service.upsert_profile_select_ai_profile(
        "quoted", ProfileSelectAiProfileRequest(confirmation="ADMIN_EXECUTE")
    )

    assert result.executed is False
    assert result.status == "error"
    assert result.engine_meta["unsupported_object_list_names"] == [QUOTED]
    assert "Select AI Profile の object_list に反映できません" in result.warnings[0]
    with pytest.raises(OracleAdapterError, match="未同期"):
        service._assert_select_ai_scope_ready(profile)  # noqa: SLF001

    code, message = _profile_sync_public_error(SelectAiObjectListUnsupportedError([QUOTED]))
    assert code == "PROFILE_OBJECT_LIST_UNSUPPORTED"
    assert QUOTED in message
    assert "再試行" not in message


def test_adapter_object_list_uses_quoting_rules() -> None:
    settings = get_settings().model_copy(update={"oracle_user": "APP"})
    adapter = OracleNl2SqlAdapter(settings)

    assert adapter._object_list(
        ["ORDERS", "sh.orders", '"SALES"."MIXED_CASE"']
    ) == [  # noqa: SLF001
        {"owner": "APP", "name": "ORDERS"},
        {"owner": "SH", "name": "ORDERS"},
        {"owner": "SALES", "name": "MIXED_CASE"},
    ]
    with pytest.raises(OracleAdapterError, match=r'SALES\."Mixed_Case"'):
        adapter._object_list([UPPER, QUOTED])  # noqa: SLF001


# --- オントロジー --------------------------------------------------------------


def test_ontology_build_selects_exact_catalog_object_and_skips_quoted_profile_object() -> None:
    catalog = SchemaCatalog(
        refreshed_at="2026-09-14T00:00:00+00:00",
        current_owner="SALES",
        tables=[_table("SALES", "MIXED_CASE"), _table("SALES", "Mixed_Case")],
    )

    upper_selected, upper_warnings, upper_errors = _selected_schema_objects(
        Nl2SqlProfile(id="upper", name="大文字", allowed_tables=[UPPER]), catalog
    )
    quoted_selected, quoted_warnings, quoted_errors = _selected_schema_objects(
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED]), catalog
    )

    assert [(t.owner, t.table_name) for t in upper_selected] == [("SALES", "MIXED_CASE")]
    assert upper_warnings == [] and upper_errors == []
    assert quoted_selected == []
    assert quoted_errors == []
    assert "オントロジー構築の対象にできません" in quoted_warnings[0]


def test_profile_ontology_view_does_not_map_quoted_profile_object_to_upper_node() -> None:
    ontology = build_schema_ontology(
        SchemaCatalog(
            refreshed_at="2026-09-14T00:00:00+00:00",
            current_owner="SALES",
            tables=[_table("SALES", "MIXED_CASE"), _table("SALES", "売上")],
        )
    )

    quoted = migrate_profile_ontology_view(
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED]), ontology
    )
    upper = migrate_profile_ontology_view(
        Nl2SqlProfile(id="upper", name="大文字", allowed_tables=[UPPER, 'SALES."売上"']), ontology
    )

    assert quoted.physical_objects == []
    assert sorted(item.object_name for item in upper.physical_objects) == ["MIXED_CASE", "売上"]
    with pytest.raises(ValueError, match="Ontology に存在しません"):
        migrate_profile_ontology_view(
            Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED]),
            ontology,
            strict=True,
        )


def test_profile_view_warning_reports_unresolved_quoted_profile_object() -> None:
    view = ProfileOntologyView(
        id="view",
        profile_id="quoted",
        ontology_revision_id="rev",
        physical_objects=[PhysicalObjectRef(owner="SALES", object_name="MIXED_CASE")],
    )

    warnings = OntologyApiRuntime._profile_view_unresolved_object_warnings(  # noqa: SLF001
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED, UPPER]), view
    )

    assert len(warnings) == 1
    assert QUOTED in warnings[0]


# --- DeepSec の関連テーブル候補 ---------------------------------------------------


def test_deepsec_relation_catalog_accepts_quoted_profile_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.nl2sql import service as service_module
    from app.security import scope_relations

    service = _service(
        _table("SALES", "MIXED_CASE"), _table("SALES", "Mixed_Case"), _table("SALES", "Regions")
    )
    service.create_profile(
        Nl2SqlProfile(id="quoted", name="引用名", allowed_tables=[QUOTED, 'SALES."Regions"', UPPER])
    )
    monkeypatch.setattr(service_module, "nl2sql_service", service)
    monkeypatch.setattr(scope_relations, "_ontology_relations", lambda *args: [])

    class Cursor:
        def __init__(self) -> None:
            self.executed: list[tuple[str, dict[str, Any]]] = []

        def execute(self, sql: str, binds: dict[str, Any] | None = None) -> None:
            self.executed.append((sql, binds or {}))

        def fetchall(self) -> list[tuple[Any, ...]]:
            if "ALL_CONSTRAINTS" in self.executed[-1][0]:
                return [("SALES", "FK_Region", "Mixed_Case", "SALES", "Regions", "Region", "Id", 1)]
            return []

    profiles = scope_relations.scope_profiles()
    cursor = Cursor()
    catalog = scope_relations.relation_catalog(object(), "quoted", QUOTED, cursor=cursor)

    assert profiles[0]["objects"] == [QUOTED, 'SALES."Regions"', UPPER]
    assert cursor.executed[0][1] == {"owner": "SALES", "object_name": "Mixed_Case"}
    assert [relation["target"] for relation in catalog["relations"]] == ['SALES."Regions"']
