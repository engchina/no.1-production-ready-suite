"""許可済みキャッシュ schema の読み取り専用・有界ページ。"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal

from pr_backend_core.mcp import tool_result
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.features.nl2sql.models import AllowedObjects, SchemaColumn, SchemaTable
from app.features.nl2sql.object_identity import qualified_object_name

MAX_SCHEMA_PAGE_BYTES = 128 * 1024
MAX_SCHEMA_DESCRIPTION = 256
MAX_SCHEMA_CURSOR = 1024
MAX_SCHEMA_NAME = 512


class ProfileSchemaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(
        min_length=1, max_length=128, description="nl2sql_list_profiles の業務プロファイル ID"
    )
    object_names: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(
        default_factory=list,
        max_length=20,
        description="所有者付きの正確な表・ビュー名。省略すると許可範囲全体。",
    )
    allowed_objects: AllowedObjects = Field(
        default_factory=AllowedObjects,
        description="必要ならプロファイル内の表・列をさらに限定する。",
    )
    cursor: str | None = Field(
        default=None,
        max_length=MAX_SCHEMA_CURSOR,
        description="同じ条件の前ページの next_cursor。初回は省略。",
    )
    page_size: int = Field(default=200, ge=1, le=500, strict=True)

    @model_validator(mode="after")
    def bounded_scope(self) -> ProfileSchemaInput:
        scope = self.allowed_objects
        names = [*scope.table_names, *scope.columns]
        columns = [column for values in scope.columns.values() for column in values]
        if (
            len(names) > 4096
            or len(columns) > 20000
            or any(len(name) > 512 for name in [*names, *columns])
        ):
            raise ValueError("対象の範囲が大きすぎます。表・列を限定してください。")
        return self


class ProfileSchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    logical_name: str
    description: str
    description_truncated: bool = False


class ProfileSchemaObject(BaseModel):
    owner: str
    name: str
    object_type: str
    logical_name: str
    description: str
    description_truncated: bool = False
    columns: list[ProfileSchemaColumn] = Field(default_factory=list)
    columns_complete: bool = False


class ProfileSchemaPage(BaseModel):
    profile_id: str
    source: Literal["cached"] = "cached"
    status: Literal["available", "unavailable", "stale"] = "unavailable"
    unavailable_reason: (
        Literal[
            "schema_unavailable",
            "scope_unresolved",
            "snapshot_changed",
            "invalid_cursor",
            "metadata_limit",
        ]
        | None
    ) = "schema_unavailable"
    refreshed_at: str = ""
    objects: list[ProfileSchemaObject] = Field(default_factory=list)
    page_complete: bool = False
    next_cursor: str | None = None
    truncated: bool = False
    total_objects: int = 0
    total_columns: int = 0


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _column(column: SchemaColumn) -> ProfileSchemaColumn:
    return ProfileSchemaColumn(
        name=column.column_name,
        data_type=column.data_type,
        nullable=column.nullable,
        logical_name=column.logical_name,
        description=column.comment[:MAX_SCHEMA_DESCRIPTION],
        description_truncated=len(column.comment) > MAX_SCHEMA_DESCRIPTION,
    )


def _object(table: SchemaTable) -> ProfileSchemaObject:
    return ProfileSchemaObject(
        owner=table.owner,
        name=table.table_name,
        object_type=table.table_type,
        logical_name=table.logical_name,
        description=table.comment[:MAX_SCHEMA_DESCRIPTION],
        description_truncated=len(table.comment) > MAX_SCHEMA_DESCRIPTION,
    )


def schema_page(
    *,
    profile_id: str,
    tables: list[SchemaTable],
    refreshed_at: str,
    scope_binding: object,
    snapshot_binding: object,
    cursor: str | None,
    page_size: int,
) -> ProfileSchemaPage:
    """名前を省略せず、列位置の cursor を同じ scope/snapshot に結び付ける。

    cursor は認可の代わりではない。呼び出し側は毎回許可集合を構築し直す。
    page_complete はこの応答だけで選択範囲を全て含むことを示す。
    """
    unavailable = ProfileSchemaPage(profile_id=profile_id)
    if not tables or not refreshed_at:
        return unavailable
    if len(tables) > 2048 or sum(len(t.columns) for t in tables) > 20000:
        return unavailable.model_copy(update={"unavailable_reason": "metadata_limit"})
    objects = [_object(table) for table in tables]
    columns = [[_column(column) for column in table.columns] for table in tables]
    names = [
        part for obj in objects for part in (obj.owner, obj.name, obj.object_type, obj.logical_name)
    ]
    names += [
        part
        for cols in columns
        for col in cols
        for part in (col.name, col.data_type, col.logical_name)
    ]
    if any(len(part) > MAX_SCHEMA_NAME for part in names):
        return unavailable.model_copy(update={"unavailable_reason": "metadata_limit"})
    identities = [qualified_object_name(t.owner, t.table_name) for t in tables]
    if len(set(identities)) != len(identities) or any(
        len({c.name for c in cols}) != len(cols) for cols in columns
    ):
        return unavailable.model_copy(update={"unavailable_reason": "scope_unresolved"})
    # 応答に出さない sample/row count/自由 SQL は cursor にも含めない。
    scope = _digest([profile_id, scope_binding])
    snapshot = _digest(
        [
            snapshot_binding,
            refreshed_at,
            [o.model_dump() for o in objects],
            [[c.model_dump() for c in cols] for cols in columns],
        ]
    )
    offset = 0
    if cursor is not None:
        try:
            raw = json.loads(
                base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
            )
            if not isinstance(raw, dict) or set(raw) != {"scope", "snapshot", "offset"}:
                raise ValueError
            if raw["scope"] != scope or raw["snapshot"] != snapshot:
                return unavailable.model_copy(
                    update={"status": "stale", "unavailable_reason": "snapshot_changed"}
                )
            if type(raw["offset"]) is not int or raw["offset"] < 0:
                raise ValueError
            offset = raw["offset"]
        except (ValueError, UnicodeError, TypeError):
            return unavailable.model_copy(update={"unavailable_reason": "invalid_cursor"})
    entries = [
        (i, j) for i, cols in enumerate(columns) for j in (range(len(cols)) if cols else [-1])
    ]
    if offset >= len(entries):
        return unavailable.model_copy(update={"unavailable_reason": "invalid_cursor"})

    def build(end: int) -> ProfileSchemaPage:
        selected: dict[int, ProfileSchemaObject] = {}
        for i, j in entries[offset:end]:
            if i not in selected:
                selected[i] = objects[i].model_copy(deep=True)
            if j >= 0:
                selected[i].columns.append(columns[i][j])
        for i, obj in selected.items():
            obj.columns_complete = len(obj.columns) == len(columns[i])
        next_cursor = None
        if end < len(entries):
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {"scope": scope, "snapshot": snapshot, "offset": end}, separators=(",", ":")
                ).encode()
            ).decode()
        return ProfileSchemaPage(
            profile_id=profile_id,
            status="available",
            unavailable_reason=None,
            refreshed_at=refreshed_at,
            objects=list(selected.values()),
            page_complete=offset == 0 and end == len(entries),
            next_cursor=next_cursor,
            truncated=offset != 0 or end < len(entries),
            total_objects=len(objects),
            total_columns=sum(len(cols) for cols in columns),
        )

    # 小さい段階から増やすため、巨大な単一 object でも上限を超える応答を作らない。
    result: ProfileSchemaPage | None = None
    for end in range(offset + 1, min(len(entries), offset + page_size) + 1):
        candidate = build(end)
        # MCP の schema_version/envelope 用の余裕を残す。
        body = {"schema_version": 0, **candidate.model_dump(mode="json")}
        if (
            len(json.dumps(tool_result(body), ensure_ascii=False).encode())
            > MAX_SCHEMA_PAGE_BYTES - 1024
        ):
            break
        result = candidate
    return result or unavailable.model_copy(update={"unavailable_reason": "metadata_limit"})
