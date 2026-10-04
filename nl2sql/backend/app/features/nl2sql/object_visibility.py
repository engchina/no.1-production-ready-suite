"""用户可见的 Oracle schema object 规则。"""

from __future__ import annotations

from .models import SchemaCatalog, SchemaObjectPage

_SYSTEM_OBJECT_NAME_MARKERS = frozenset({"$", "#"})
# 3 製品は同じ schema を共有する（#212）。共通基盤（PLATFORM_: 認証テーブルなど）と他製品の
# テーブル（RAG_ / AGENT_）も業務データではないため、業務ユーザーの対象一覧に出さない。
_SYSTEM_OBJECT_NAME_PREFIXES = ("NL2SQL_", "PLATFORM_", "RAG_", "AGENT_")


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


# 業務データとして扱わない object を対象にしたときの拒否の文（#943）。種類ごとに理由を変える。
HIDDEN_OBJECT_MESSAGES = {
    "nl2sql": (
        "NL2SQL_ で始まる表/VIEW は NL2SQL システム object です。"
        "システムテーブル管理からのみ管理できます。"
    ),
    "platform": (
        "PLATFORM_ で始まる表/VIEW は 3 製品で共通の基盤（ユーザー・ロールなど）のテーブルです。"
        "業務データとして参照・変更できません。"
    ),
    "product": (
        "RAG_ / AGENT_ で始まる表/VIEW は他の製品（RAG・Agent）のテーブルです。"
        "業務データとして参照・変更できません。"
    ),
    "oracle": (
        "名前に $ や # を含む object は Oracle のシステム object です。"
        "業務データとして参照・変更できません。"
    ),
}
HIDDEN_OBJECT_GENERIC_MESSAGE = (
    "システムの表/VIEW（NL2SQL_・PLATFORM_・RAG_・AGENT_ で始まるものと、名前に $ や # を含む "
    "Oracle のシステム object）は業務データとして参照・変更できません。"
    "NL2SQL_ の表はシステムテーブル管理から管理します。"
)
_HIDDEN_OBJECT_KIND_ORDER = ("nl2sql", "platform", "product", "oracle")


def hidden_object_kind(name: str) -> str:
    """業務データとして扱わない object の種類（`HIDDEN_OBJECT_MESSAGES` の key）を返す。"""

    parts = [_normalize_identifier_part(part) for part in _split_identifier_parts(name)]
    if any(marker in part for part in parts for marker in _SYSTEM_OBJECT_NAME_MARKERS):
        return "oracle"
    object_name = parts[-1] if parts else ""
    if object_name.startswith("NL2SQL_"):
        return "nl2sql"
    if object_name.startswith("PLATFORM_"):
        return "platform"
    if object_name.startswith(("RAG_", "AGENT_")):
        return "product"
    return "oracle"


def hidden_object_blocked_message(names: list[str] | tuple[str, ...] | None = None) -> str:
    """対象名ごとに種類に合った拒否の文を返す（対象名が無ければ全種類を挙げる文）。"""

    unique = sorted({name for name in (names or []) if name})
    if not unique:
        return HIDDEN_OBJECT_GENERIC_MESSAGE
    grouped: dict[str, list[str]] = {}
    for name in unique:
        grouped.setdefault(hidden_object_kind(name), []).append(name)
    return " ".join(
        f"{', '.join(grouped[kind])}: {HIDDEN_OBJECT_MESSAGES[kind]}"
        for kind in _HIDDEN_OBJECT_KIND_ORDER
        if kind in grouped
    )


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
