"""用户可见的 Oracle schema object 规则。"""

from __future__ import annotations

from .models import SchemaCatalog, SchemaObjectPage

_SYSTEM_OBJECT_NAME_MARKERS = frozenset({"$", "#"})


def is_user_visible_object_name(object_name: str) -> bool:
    """系统生成对象不进入业务用户使用的对象目录。"""

    normalized = object_name.strip()
    return bool(normalized) and not any(
        marker in normalized for marker in _SYSTEM_OBJECT_NAME_MARKERS
    )


def filter_user_visible_catalog(catalog: SchemaCatalog) -> SchemaCatalog:
    """过滤旧 snapshot/cache 中残留的系统对象及其依赖。"""

    tables = [
        table for table in catalog.tables if is_user_visible_object_name(table.table_name)
    ]
    dependencies = [
        dependency
        for dependency in catalog.view_dependencies
        if is_user_visible_object_name(dependency.view_name)
        and is_user_visible_object_name(dependency.referenced_name)
    ]
    if len(tables) == len(catalog.tables) and len(dependencies) == len(
        catalog.view_dependencies
    ):
        return catalog
    return catalog.model_copy(
        deep=True,
        update={"tables": tables, "view_dependencies": dependencies},
    )


def filter_user_visible_object_page(page: SchemaObjectPage) -> SchemaObjectPage:
    """自定义 repository 的异常响应也不会泄露系统对象。"""

    items = [
        item for item in page.items if is_user_visible_object_name(item.object_name)
    ]
    hidden_items = [item for item in page.items if item not in items]
    hidden_tables = sum(
        item.object_type.upper() not in {"VIEW", "MATERIALIZED VIEW"}
        for item in hidden_items
    )
    hidden_views = len(hidden_items) - hidden_tables
    return page.model_copy(
        update={
            "items": items,
            "total": (
                max(0, page.total - len(hidden_items))
                if page.total is not None
                else None
            ),
            "table_count": max(0, page.table_count - hidden_tables),
            "view_count": max(0, page.view_count - hidden_views),
        }
    )
