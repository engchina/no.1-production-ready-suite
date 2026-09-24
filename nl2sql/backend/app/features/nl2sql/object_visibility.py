"""用户可见的 Oracle schema object 规则。"""

from __future__ import annotations

from .models import SchemaCatalog, SchemaObjectPage

_SYSTEM_OBJECT_NAME_MARKERS = frozenset({"$", "#"})
_SYSTEM_OBJECT_NAME_PREFIXES = ("NL2SQL_",)


def _split_identifier_parts(value: str) -> list[str]:
    """Owner-qualified name を、二重引用符内の dot を保って分割する。"""

    parts: list[str] = []
    buffer: list[str] = []
    in_double = False
    for char in str(value or "").strip():
        if char == '"':
            in_double = not in_double
            buffer.append(char)
            continue
        if char == "." and not in_double:
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer = []
            continue
        buffer.append(char)
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_identifier_part(value: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1].replace('""', '"')
    return normalized.upper()


def _is_user_visible_owner_name(owner_name: str) -> bool:
    normalized = _normalize_identifier_part(owner_name)
    return bool(normalized) and not any(
        marker in normalized for marker in _SYSTEM_OBJECT_NAME_MARKERS
    )


def _is_user_visible_object_part(object_name: str) -> bool:
    normalized = _normalize_identifier_part(object_name)
    return (
        bool(normalized)
        and not any(marker in normalized for marker in _SYSTEM_OBJECT_NAME_MARKERS)
        and not normalized.startswith(_SYSTEM_OBJECT_NAME_PREFIXES)
    )


def is_user_visible_object_name(object_name: str) -> bool:
    """系统生成对象不进入业务用户使用的对象目录。"""

    parts = _split_identifier_parts(object_name)
    if not parts:
        return False
    return all(_is_user_visible_owner_name(part) for part in parts[:-1]) and (
        _is_user_visible_object_part(parts[-1])
    )


def is_user_visible_owner_name(owner_name: str) -> bool:
    """schema owner 名は Oracle system marker だけを隠し、NL2SQL_ prefix は許可する。"""

    return _is_user_visible_owner_name(owner_name)


def is_user_visible_schema_object(owner: str, object_name: str) -> bool:
    """owner/object が分離した schema metadata 用の可視性判定。"""

    normalized_owner = owner.strip()
    owner_visible = not normalized_owner or _is_user_visible_owner_name(normalized_owner)
    return owner_visible and is_user_visible_object_name(object_name)


def filter_user_visible_catalog(catalog: SchemaCatalog) -> SchemaCatalog:
    """过滤旧 snapshot/cache 中残留的系统对象及其依赖。"""

    tables = [
        table
        for table in catalog.tables
        if is_user_visible_schema_object(table.owner, table.table_name)
    ]
    dependencies = [
        dependency
        for dependency in catalog.view_dependencies
        if is_user_visible_schema_object(dependency.owner, dependency.view_name)
        and is_user_visible_schema_object(
            dependency.referenced_owner,
            dependency.referenced_name,
        )
    ]
    if len(tables) == len(catalog.tables) and len(dependencies) == len(catalog.view_dependencies):
        return catalog
    return catalog.model_copy(
        deep=True,
        update={"tables": tables, "view_dependencies": dependencies},
    )


def filter_user_visible_object_page(page: SchemaObjectPage) -> SchemaObjectPage:
    """自定义 repository 的异常响应也不会泄露系统对象。"""

    items = [
        item for item in page.items if is_user_visible_schema_object(item.owner, item.object_name)
    ]
    hidden_items = [item for item in page.items if item not in items]
    hidden_tables = sum(
        item.object_type.upper() not in {"VIEW", "MATERIALIZED VIEW"} for item in hidden_items
    )
    hidden_views = len(hidden_items) - hidden_tables
    return page.model_copy(
        update={
            "items": items,
            "total": (max(0, page.total - len(hidden_items)) if page.total is not None else None),
            "table_count": max(0, page.table_count - hidden_tables),
            "view_count": max(0, page.view_count - hidden_views),
        }
    )
