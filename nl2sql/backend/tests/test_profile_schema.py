"""許可済みキャッシュ schema の有界ページと権限の合成回帰。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.profile_schema import (
    MAX_SCHEMA_PAGE_BYTES,
    ProfileSchemaInput,
    schema_page,
)
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


def table(name: str = "TABLE_A", count: int = 3, *, owner: str = "APP") -> SchemaTable:
    return SchemaTable(
        owner=owner,
        table_name=name,
        logical_name=name,
        table_type="view",
        comment="既存の説明",
        row_count=999,
        constraints=["SELECT secret"],
        columns=[
            SchemaColumn(
                column_name=f"COL_{n}",
                logical_name=f"列{n}",
                data_type="NUMBER",
                comment="既存の列説明",
                sample_values=["private-sample"],
            )
            for n in range(count)
        ],
    )


def service(
    monkeypatch: pytest.MonkeyPatch, tables: list[SchemaTable] | None = None
) -> Nl2SqlService:
    obj = Nl2SqlService(store=MemoryNl2SqlStore())
    obj._catalog = SchemaCatalog(refreshed_at="snapshot-a", tables=tables or [table()])
    monkeypatch.setattr(
        obj,
        "get_profile",
        lambda *a, **k: Nl2SqlProfile(id="p", name="P", allowed_views=["APP.TABLE_A"]),
    )
    return obj


def page(tables: list[SchemaTable]) -> Any:
    return schema_page(
        profile_id="p",
        tables=tables,
        refreshed_at="snapshot-a",
        scope_binding="scope-a",
        snapshot_binding="snapshot-a",
        cursor=None,
        page_size=200,
    )


def test_complete_cached_whitelist(monkeypatch: pytest.MonkeyPatch) -> None:
    result = service(monkeypatch).get_profile_schema(ProfileSchemaInput(profile_id="p"))
    assert result.status == "available" and result.page_complete and not result.truncated
    assert result.source == "cached" and result.total_columns == 3
    assert result.objects[0].columns_complete
    assert all(
        word not in result.model_dump_json()
        for word in ("sample_values", "private-sample", "row_count", "constraints", "SELECT secret")
    )
    assert [c.name for c in result.objects[0].columns] == ["COL_0", "COL_1", "COL_2"]


def test_thirteen_objects_126_columns_fit_one_page() -> None:
    result = page([table(f"TABLE_{i}", 10 if i < 9 else 9) for i in range(13)])
    assert result.total_columns == 126 and result.total_objects == 13
    assert result.page_complete and result.next_cursor is None
    assert len(result.model_dump_json().encode()) < MAX_SCHEMA_PAGE_BYTES


def test_filter_only_fetches_selected_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    obj = service(monkeypatch, [table(), table("TABLE_B")])
    seen: list[str] = []
    monkeypatch.setattr(
        obj,
        "get_profile",
        lambda *a, **k: Nl2SqlProfile(
            id="p", name="P", allowed_views=["APP.TABLE_A", "APP.TABLE_B"]
        ),
    )
    original = obj._generation_schema_catalog

    def snapshot(profile: Nl2SqlProfile, allowed: AllowedObjects, **kwargs: Any) -> SchemaCatalog:
        seen.extend(allowed.table_names)
        return original(profile, allowed, **kwargs)

    monkeypatch.setattr(obj, "_generation_schema_catalog", snapshot)
    result = obj.get_profile_schema(
        ProfileSchemaInput(profile_id="p", object_names=["APP.TABLE_B"])
    )
    assert seen == ["APP.TABLE_B"] and [o.name for o in result.objects] == ["TABLE_B"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"object_names": ["APP.TABLE_SECRET"]},
        {"allowed_objects": AllowedObjects(table_names=["APP.TABLE_SECRET"])},
        {"allowed_objects": AllowedObjects(enforce_table_scope=True)},
        {"object_names": ["TABLE_A"]},
    ],
)
def test_invalid_scope_does_not_load_catalog(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    obj = service(monkeypatch)
    monkeypatch.setattr(
        obj, "_generation_schema_catalog", lambda *a: pytest.fail("scope外を取得しない")
    )
    result = obj.get_profile_schema(ProfileSchemaInput(profile_id="p", **kwargs))
    assert result.status == "unavailable" and not result.objects and not result.page_complete


def test_column_scope_aliases_intersect(monkeypatch: pytest.MonkeyPatch) -> None:
    result = service(monkeypatch).get_profile_schema(
        ProfileSchemaInput(
            profile_id="p",
            allowed_objects=AllowedObjects(
                columns={"APP.TABLE_A": ["COL_0", "COL_1"], '"APP"."TABLE_A"': ["COL_1", "COL_2"]}
            ),
        )
    )
    assert result.status == "available" and [c.name for c in result.objects[0].columns] == ["COL_1"]


def test_empty_column_list_is_not_extra_restriction(monkeypatch: pytest.MonkeyPatch) -> None:
    result = service(monkeypatch).get_profile_schema(
        ProfileSchemaInput(
            profile_id="p", allowed_objects=AllowedObjects(columns={"APP.TABLE_A": []})
        )
    )
    assert result.total_columns == 3


def test_uninitialized_catalog_is_not_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    obj = service(monkeypatch)
    obj._catalog = SchemaCatalog(refreshed_at="", tables=[])
    result = obj.get_profile_schema(ProfileSchemaInput(profile_id="p"))
    assert result.status == "unavailable" and not result.page_complete


def test_missing_scoped_object_is_not_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    obj = service(monkeypatch)
    monkeypatch.setattr(
        obj,
        "get_profile",
        lambda *a, **k: Nl2SqlProfile(
            id="p", name="P", allowed_views=["APP.TABLE_A", "APP.TABLE_MISSING"]
        ),
    )
    assert obj.get_profile_schema(ProfileSchemaInput(profile_id="p")).status == "unavailable"


def test_head_change_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    obj = service(monkeypatch)
    head = obj.get_catalog_head()
    calls = 0

    def readhead() -> Any:
        nonlocal calls
        calls += 1
        return head if calls == 1 else head.model_copy(update={"catalog_version": 2})

    monkeypatch.setattr(obj, "get_catalog_head", readhead)
    assert obj.get_profile_schema(ProfileSchemaInput(profile_id="p")).status == "stale"


def test_column_paging_no_duplicates_and_no_false_complete() -> None:
    tabs = [table(count=7)]
    cursor = None
    seen: list[str] = []
    for n in range(4):
        result = schema_page(
            profile_id="p",
            tables=tabs,
            refreshed_at="a",
            scope_binding="s",
            snapshot_binding="a",
            cursor=cursor,
            page_size=2,
        )
        seen.extend(c.name for o in result.objects for c in o.columns)
        assert not result.page_complete and not result.objects[0].columns_complete
        cursor = result.next_cursor
        if n < 3:
            assert cursor is not None and len(cursor) < 1024
    assert cursor is None and seen == [f"COL_{i}" for i in range(7)]


@pytest.mark.parametrize("changed", ["scope", "snapshot", "profile", "column"])
def test_cursor_rejects_changes(changed: str) -> None:
    tabs = [table()]
    first = schema_page(
        profile_id="p",
        tables=tabs,
        refreshed_at="a",
        scope_binding="s",
        snapshot_binding="a",
        cursor=None,
        page_size=1,
    )
    if changed == "column":
        tabs[0].columns[0].comment = "changed"
    result = schema_page(
        profile_id="other" if changed == "profile" else "p",
        tables=tabs,
        refreshed_at="a",
        scope_binding="other" if changed == "scope" else "s",
        snapshot_binding="other" if changed == "snapshot" else "a",
        cursor=first.next_cursor,
        page_size=1,
    )
    assert result.status == "stale" and not result.objects


@pytest.mark.parametrize("cursor", ["?", "e30=", "/not-a-cursor"])
def test_invalid_cursor(cursor: str) -> None:
    result = schema_page(
        profile_id="p",
        tables=[table()],
        refreshed_at="a",
        scope_binding="s",
        snapshot_binding="a",
        cursor=cursor,
        page_size=1,
    )
    assert result.status == "unavailable" and not result.objects


def test_comments_truncate_identifiers_do_not() -> None:
    tab = table("Mixed_日本語", owner="Owner.Mixed")
    tab.comment = "あ" * 300
    tab.columns[0].comment = "x" * 300
    obj = page([tab]).objects[0]
    assert obj.name == "Mixed_日本語" and obj.owner == "Owner.Mixed"
    assert (
        obj.description_truncated
        and len(obj.description) == 256
        and obj.columns[0].description_truncated
    )


def test_oversized_name_not_changed() -> None:
    result = page([table("a" * 513)])
    assert (
        result.status == "unavailable"
        and result.unavailable_reason == "metadata_limit"
        and not result.objects
    )


def test_byte_limit_has_continuation() -> None:
    tabs = [table(count=500)]
    for col in tabs[0].columns:
        col.comment = "あ" * 256
        col.logical_name = "あ" * 512
    result = schema_page(
        profile_id="p",
        tables=tabs,
        refreshed_at="a",
        scope_binding="s",
        snapshot_binding="a",
        cursor=None,
        page_size=500,
    )
    assert len(result.model_dump_json().encode()) <= MAX_SCHEMA_PAGE_BYTES
    assert result.next_cursor and result.truncated and not result.page_complete


@pytest.mark.parametrize(
    "arguments",
    [
        {"page_size": True},
        {"page_size": 0},
        {"page_size": 501},
        {"cursor": "x" * 1025},
        {"object_names": ["A"] * 21},
    ],
)
def test_input_limits(arguments: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProfileSchemaInput(profile_id="p", **arguments)


def test_physical_owner_and_quoted_case_stay_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    tables = [table("Mixed", owner="Owner"), table("Mixed", owner="Other")]
    obj = service(monkeypatch, tables)
    monkeypatch.setattr(
        obj,
        "get_profile",
        lambda *a, **kw: Nl2SqlProfile(id="p", name="P", allowed_views=['"Owner"."Mixed"']),
    )
    result = obj.get_profile_schema(ProfileSchemaInput(profile_id="p"))
    assert [(o.owner, o.name) for o in result.objects] == [("Owner", "Mixed")]


def test_hidden_object_never_returns_its_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    obj = service(monkeypatch, [table(), table("AGENT_INTERNAL")])
    result = obj.get_profile_schema(ProfileSchemaInput(profile_id="p"))
    assert [o.name for o in result.objects] == ["TABLE_A"]
    forbidden = obj.get_profile_schema(
        ProfileSchemaInput(profile_id="p", object_names=["APP.AGENT_INTERNAL"])
    )
    assert not forbidden.objects


def test_profile_scope_change_during_read_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    obj = service(monkeypatch)
    calls = 0

    def profile(*args: Any, **kwargs: Any) -> Nl2SqlProfile:
        nonlocal calls
        calls += 1
        return Nl2SqlProfile(id="p", name="P", allowed_views=["APP.TABLE_A"], version=calls)

    monkeypatch.setattr(obj, "get_profile", profile)
    assert obj.get_profile_schema(ProfileSchemaInput(profile_id="p")).status == "stale"


def test_full_mcp_response_is_byte_bounded() -> None:
    from pr_backend_core.mcp import tool_result

    tabs = [table(count=500)]
    for col in tabs[0].columns:
        col.comment = "あ" * 256
        col.logical_name = "あ" * 512
    result = schema_page(
        profile_id="p",
        tables=tabs,
        refreshed_at="a",
        scope_binding="s",
        snapshot_binding="a",
        cursor=None,
        page_size=500,
    )
    wire = tool_result({"schema_version": 4, **result.model_dump(mode="json")})
    assert len(json.dumps(wire, ensure_ascii=False).encode()) <= MAX_SCHEMA_PAGE_BYTES


def test_strict_cache_lookup_does_not_search_another_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from typing import cast

    from app.features.nl2sql.models import SchemaCatalogHead

    obj = service(monkeypatch)
    looked: list[tuple[str, str]] = []

    def get_object(owner: str, name: str) -> None:
        looked.append((owner, name))
        return None

    repo = SimpleNamespace(
        get_schema_object=get_object,
        search_schema_objects=lambda **kw: pytest.fail("別所有者へ検索を拡張しない"),
    )
    monkeypatch.setattr(obj, "_incremental_repository", cast(Any, repo))
    monkeypatch.setattr(
        obj, "get_catalog_head", lambda: SchemaCatalogHead(catalog_version=1, refreshed_at="a")
    )
    monkeypatch.setattr(obj, "_refresh_cache_token", lambda *a, **kw: None)
    result = obj.get_profile_schema(ProfileSchemaInput(profile_id="p"))
    assert result.status == "unavailable" and looked == [("APP", "TABLE_A")]
