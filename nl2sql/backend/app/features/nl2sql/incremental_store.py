"""NL2SQL の増分永続化 Repository。

起動時に業務データを hydrate しないことを最優先にし、profile、schema catalog、
operation document を entity 単位で読み書きする。Oracle 実装は DDL を一切実行せず、
versioned migration が適用済みであることだけを確認する。
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol, cast

from .incremental_observability import record_cache, record_repository
from .models import (
    Nl2SqlProfile,
    ProfileSummary,
    ProfileSummaryPage,
    SchemaCatalog,
    SchemaCatalogHead,
    SchemaColumn,
    SchemaConstraintDetail,
    SchemaObjectDetail,
    SchemaObjectPage,
    SchemaObjectSummary,
    SchemaRefreshJob,
    SchemaRefreshJobStatus,
    SchemaRefreshMode,
    SchemaRefreshTargetObject,
    SchemaTable,
    SchemaViewDependency,
)
from .object_visibility import (
    filter_user_visible_catalog,
    is_user_visible_schema_object,
)
from .oracle_lob import configure_clob_fetch_as_text

PROFILE_NAMESPACE = "profiles"
SCHEMA_NAMESPACE = "schema"
STATE_NAMESPACE = "state"
REQUIRED_MIGRATION_VERSIONS = (3, 5, 6)


class IncrementalStoreError(RuntimeError):
    """増分 store の公開可能な基底例外。"""


class IncrementalStoreNotMigrated(IncrementalStoreError):
    """必要な versioned migration が未適用。"""


class IncrementalVersionConflict(IncrementalStoreError):
    """ETag が現在値と一致しない。"""

    def __init__(self, current_etag: str = "") -> None:
        super().__init__("保存対象が別のリクエストで更新されています。")
        self.current_etag = current_etag


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _merge_refresh_targets(
    left: Sequence[SchemaRefreshTargetObject],
    right: Sequence[SchemaRefreshTargetObject],
) -> list[SchemaRefreshTargetObject]:
    merged: dict[tuple[str, str], SchemaRefreshTargetObject] = {}
    for target in [*left, *right]:
        key = (target.owner.upper(), target.object_name.upper())
        current = merged.get(key)
        if current is None:
            merged[key] = target.model_copy(update={"owner": key[0], "object_name": key[1]})
            continue
        expected_state = current.expected_state
        if target.expected_state != "unknown":
            expected_state = target.expected_state
        object_type = current.object_type
        if target.object_type != "unknown":
            object_type = target.object_type
        merged[key] = current.model_copy(
            update={"object_type": object_type, "expected_state": expected_state}
        )
    return list(merged.values())


def _coalesce_pending_refresh_job(
    active: SchemaRefreshJob,
    candidate: SchemaRefreshJob,
) -> SchemaRefreshJob:
    if active.status != SchemaRefreshJobStatus.PENDING:
        return active
    if active.mode == SchemaRefreshMode.FULL:
        return active
    if candidate.mode == SchemaRefreshMode.FULL:
        return active.model_copy(
            update={
                "mode": SchemaRefreshMode.FULL,
                "source": candidate.source,
                "target_objects": [],
                "requires_full_refresh": False,
            }
        )
    return active.model_copy(
        update={
            "target_objects": _merge_refresh_targets(
                active.target_objects,
                candidate.target_objects,
            ),
            "source": active.source if active.source == candidate.source else "coalesced",
        }
    )


def _memory_document_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Memory store の索引用 metadata を Oracle と同じ公開 payload から除外する。"""

    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"_entity_id", "_profile_id", "_status", "_updated_at"}
    }


_HISTORY_QUERY_FIELDS = ("question", "generated_sql", "feedback_comment")


def _state_document_sort_value(collection: str, value: Mapping[str, Any]) -> str:
    if collection == "history":
        return str(value.get("created_at") or value.get("_updated_at") or "")
    return str(value.get("_updated_at") or "")


def _state_document_matches_query(
    collection: str,
    value: Mapping[str, Any],
    query_key: str,
) -> bool:
    if not query_key:
        return True
    if collection == "history":
        haystack = " ".join(str(value.get(field) or "") for field in _HISTORY_QUERY_FIELDS)
        return query_key in haystack.casefold()
    return query_key in _canonical_json(value).casefold()


def _state_document_status_value(
    collection: str,
    payload: Mapping[str, Any],
    fallback: str,
) -> str:
    if collection == "history":
        return str(payload.get("feedback_rating") or "unrated")
    return fallback


def _state_document_claimable(payload: Mapping[str, Any], now: datetime) -> bool:
    status = str(payload.get("status") or "")
    if status == "pending":
        return True
    if status != "running":
        return False
    raw_expires_at = str(payload.get("lease_expires_at") or "")
    if not raw_expires_at:
        return True
    try:
        expires_at = datetime.fromisoformat(raw_expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now


def _claimed_state_document(
    payload: Mapping[str, Any],
    *,
    worker_id: str,
    lease_seconds: float,
    now: datetime,
) -> dict[str, Any]:
    started_at = str(payload.get("started_at") or now.isoformat())
    try:
        attempt = int(payload.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    lease_expires_at = datetime.fromtimestamp(
        now.timestamp() + max(30.0, lease_seconds), UTC
    ).isoformat()
    return {
        **copy.deepcopy(dict(payload)),
        "status": "running",
        "started_at": started_at,
        "worker_id": worker_id,
        "heartbeat_at": now.isoformat(),
        "lease_expires_at": lease_expires_at,
        "attempt": attempt + 1,
    }


def _state_document_sort_sql(collection: str) -> str:
    updated_at_expr = "TO_CHAR(UPDATED_AT, 'SYYYY-MM-DD\"T\"HH24:MI:SS.FF6TZH:TZM')"
    if collection != "history":
        return updated_at_expr
    return (
        "COALESCE(JSON_VALUE(PAYLOAD_JSON, '$.created_at' "
        "RETURNING VARCHAR2(128) NULL ON ERROR), "
        f"{updated_at_expr})"
    )


def _read_lob(value: Any) -> str:
    read = getattr(value, "read", None)
    raw = read() if callable(read) else value
    if isinstance(raw, (Mapping, list, tuple)):
        # Oracle 26ai may return JSON columns as native Python values instead
        # of LOB locators depending on the column/driver configuration.
        return _canonical_json(raw)
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw or "")


def _set_clob_bind(cursor: Any, bind_name: str) -> None:
    """大きい JSON 配列を Oracle の SQL VARCHAR2 上限に依存せず bind する。"""

    setter = getattr(cursor, "setinputsizes", None)
    if not callable(setter):
        return
    try:
        db_type_clob = import_module("oracledb").DB_TYPE_CLOB
    except Exception:  # pragma: no cover - optional driver boundary
        return
    setter(**{bind_name: db_type_clob})


def _etag(payload: Mapping[str, Any], version: int) -> str:
    data = dict(payload)
    data.pop("etag", None)
    data["version"] = version
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def _encode_cursor(*parts: str) -> str:
    raw = _canonical_json(list(parts)).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, expected_parts: int) -> tuple[str, ...] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor が不正です。") from exc
    if not isinstance(decoded, list) or len(decoded) != expected_parts:
        raise ValueError("cursor が不正です。")
    return tuple(str(value) for value in decoded)


def _profile_search_key(value: str) -> str:
    return str(value or "").upper()


PROFILE_SORT_KEYS = ("name", "tables", "views")
PROFILE_SORT_DIRECTIONS = ("asc", "desc")
# 件数 sort でも cursor を文字列 tuple のまま扱えるようゼロ埋めする(辞書順=数値順)。
_PROFILE_COUNT_SORT_WIDTH = 12


def normalize_profile_sort(sort: str | None, direction: str | None) -> tuple[str, str]:
    """一覧 sort 指定を検証して (key, direction) に正規化する。"""

    sort_key = (sort or "name").strip().lower()
    if sort_key not in PROFILE_SORT_KEYS:
        raise ValueError("sort は name / tables / views のいずれかで指定してください。")
    sort_direction = (direction or "asc").strip().lower()
    if sort_direction not in PROFILE_SORT_DIRECTIONS:
        raise ValueError("direction は asc / desc のいずれかで指定してください。")
    return sort_key, sort_direction


def _profile_count_sort_key(count: int) -> str:
    return f"{max(0, int(count)):0{_PROFILE_COUNT_SORT_WIDTH}d}"


def _profile_summary_sort_key(summary: ProfileSummary, sort_key: str) -> str:
    if sort_key == "tables":
        return _profile_count_sort_key(summary.allowed_table_count)
    if sort_key == "views":
        return _profile_count_sort_key(summary.allowed_view_count)
    return _profile_search_key(summary.name)


def profile_sort_key(profile: Nl2SqlProfile, sort_key: str) -> str:
    """cursor と in-memory sort が共有する並び替えキー。"""

    if sort_key == "tables":
        return _profile_count_sort_key(len(profile.allowed_tables))
    if sort_key == "views":
        return _profile_count_sort_key(len(profile.allowed_views))
    return _profile_search_key(profile.name)


def paginate_sorted_profiles(
    profiles: list[Nl2SqlProfile],
    *,
    after: tuple[str, ...] | None,
    limit: int,
    sort_key: str,
    direction: str,
) -> tuple[list[Nl2SqlProfile], str | None]:
    """sort 指定に沿って keyset ページングする(memory / legacy 経路の共通処理)。"""

    descending = direction == "desc"
    profiles.sort(
        key=lambda item: (profile_sort_key(item, sort_key), item.id),
        reverse=descending,
    )
    if after:
        after_key = (after[0], after[1])
        profiles = [
            item
            for item in profiles
            if (
                (profile_sort_key(item, sort_key), item.id) < after_key
                if descending
                else (profile_sort_key(item, sort_key), item.id) > after_key
            )
        ]
    selected = profiles[: limit + 1]
    has_more = len(selected) > limit
    selected = selected[:limit]
    next_cursor = None
    if has_more and selected:
        last = selected[-1]
        next_cursor = _encode_cursor(profile_sort_key(last, sort_key), last.id)
    return selected, next_cursor


def _profile_matches_query(profile: Nl2SqlProfile, query_key: str) -> bool:
    if not query_key:
        return True
    return any(
        query_key in _profile_search_key(value)
        for value in (profile.name, profile.category, profile.description)
    )


def _escape_oracle_like_pattern(value: str) -> str:
    return _profile_search_key(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _profile_payload(profile: Nl2SqlProfile) -> dict[str, Any]:
    payload = profile.model_dump(mode="json")
    payload.pop("etag", None)
    payload.pop("version", None)
    payload.pop("updated_at", None)
    return payload


def _profile_from_payload(
    payload: Mapping[str, Any],
    *,
    version: int,
    etag: str,
    updated_at: str,
) -> Nl2SqlProfile:
    return Nl2SqlProfile.model_validate(
        {**payload, "version": version, "etag": etag, "updated_at": updated_at}
    )


def _profile_summary(profile: Nl2SqlProfile) -> ProfileSummary:
    return ProfileSummary(
        id=profile.id,
        name=profile.name,
        category=profile.category,
        description=profile.description,
        archived=profile.archived,
        allowed_table_count=len(profile.allowed_tables),
        allowed_view_count=len(profile.allowed_views),
        glossary_count=len(profile.glossary),
        few_shot_count=len(profile.few_shot_examples),
        version=profile.version,
        etag=profile.etag,
        updated_at=profile.updated_at,
    )


class IncrementalNl2SqlRepository(Protocol):
    """Service が依存する増分 store contract。"""

    mode: str

    def check(self) -> tuple[bool, str]: ...

    def get_change_token(self, namespace: str) -> int: ...

    def search_profiles(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        include_archived: bool,
        sort_key: str = "name",
        direction: str = "asc",
    ) -> ProfileSummaryPage: ...

    def get_profile(self, profile_id: str) -> Nl2SqlProfile | None: ...

    def list_profiles(self, *, include_archived: bool) -> list[Nl2SqlProfile]: ...

    def save_profile(
        self,
        profile: Nl2SqlProfile,
        *,
        expected_etag: str | None,
    ) -> Nl2SqlProfile: ...

    def delete_profile(self, profile_id: str, *, expected_etag: str | None) -> None: ...

    def get_catalog_head(self) -> SchemaCatalogHead: ...

    def load_catalog(self) -> SchemaCatalog: ...

    def search_schema_objects(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        owner: str,
        owner_prefix: str = "",
        query_scope: str = "all",
        object_type: str,
        allowed_names: set[str] | None,
        row_state: str = "",
        include_counts: bool = True,
    ) -> SchemaObjectPage: ...

    def get_schema_object(self, owner: str, object_name: str) -> SchemaObjectDetail | None: ...

    def schema_manifest(self) -> dict[tuple[str, str], str]: ...

    def apply_schema_refresh(
        self,
        *,
        catalog: SchemaCatalog,
        manifest: Mapping[tuple[str, str], str],
        changed_keys: set[tuple[str, str]],
        deleted_keys: set[tuple[str, str]],
    ) -> SchemaCatalogHead: ...

    def save_refresh_job(self, job: SchemaRefreshJob) -> SchemaRefreshJob: ...

    def get_refresh_job(self, job_id: str) -> SchemaRefreshJob | None: ...

    def find_active_refresh_job(self) -> SchemaRefreshJob | None: ...

    def submit_refresh_job(self, job: SchemaRefreshJob) -> SchemaRefreshJob: ...

    def claim_refresh_job(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        job_id: str | None = None,
    ) -> SchemaRefreshJob | None: ...

    def claim_document(
        self,
        collection: str,
        *,
        worker_id: str,
        lease_seconds: float,
        entity_id: str | None = None,
    ) -> dict[str, Any] | None: ...

    def put_document(
        self,
        collection: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        profile_id: str = "",
        status: str = "",
    ) -> None: ...

    def patch_document(
        self,
        collection: str,
        entity_id: str,
        updates: Mapping[str, Any],
        *,
        profile_id: str = "",
        status: str = "",
    ) -> dict[str, Any] | None: ...

    def replace_documents(
        self,
        collection: str,
        documents: Sequence[tuple[str, Mapping[str, Any], str, str]],
    ) -> None: ...

    def get_document(self, collection: str, entity_id: str) -> dict[str, Any] | None: ...

    def delete_document(self, collection: str, entity_id: str) -> None: ...

    def list_documents(
        self,
        collection: str,
        *,
        limit: int,
        profile_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]: ...

    def list_documents_page(
        self,
        collection: str,
        *,
        cursor: str | None,
        limit: int,
        profile_id: str = "",
        status: str = "",
        query: str = "",
        payload_filters: Mapping[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, int]: ...


class VersionedTtlCache:
    """外部 cache を追加せずに使う bounded LRU + TTL cache。"""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        name: str = "generic",
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._ttl_seconds = max(0.001, ttl_seconds)
        self._name = name
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                record_cache(self._name, "miss")
                return None
            expires_at, value = item
            if expires_at <= time.monotonic():
                self._items.pop(key, None)
                record_cache(self._name, "expired")
                return None
            self._items.move_to_end(key)
            record_cache(self._name, "hit")
            return copy.deepcopy(value)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (time.monotonic() + self._ttl_seconds, copy.deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    def discard(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class MemoryIncrementalNl2SqlRepository:
    """Oracle 実装と同じ lazy contract を持つ local / unit-test store。"""

    mode = "memory"

    def __init__(self, *, seed_default: bool = True) -> None:
        self._lock = threading.RLock()
        self._profiles: dict[str, Nl2SqlProfile] = {}
        self._catalog = SchemaCatalog(refreshed_at=_utc_now(), tables=[])
        self._catalog_version = 0
        self._tokens = {PROFILE_NAMESPACE: 0, SCHEMA_NAMESPACE: 0, STATE_NAMESPACE: 0}
        self._manifest: dict[tuple[str, str], str] = {}
        self._refresh_jobs: dict[str, SchemaRefreshJob] = {}
        self._documents: dict[tuple[str, str], dict[str, Any]] = {}
        if seed_default:
            self.save_profile(
                Nl2SqlProfile(id="default", name="標準プロファイル"),
                expected_etag=None,
            )

    def check(self) -> tuple[bool, str]:
        return True, "incremental memory store"

    def get_change_token(self, namespace: str) -> int:
        with self._lock:
            return self._tokens.get(namespace, 0)

    def search_profiles(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        include_archived: bool,
        sort_key: str = "name",
        direction: str = "asc",
    ) -> ProfileSummaryPage:
        after = _decode_cursor(cursor, 2)
        query_key = _profile_search_key(query.strip())
        with self._lock:
            profiles = [
                profile
                for profile in self._profiles.values()
                if (include_archived or not profile.archived)
                and _profile_matches_query(profile, query_key)
            ]
            total = len(profiles)
            selected, next_cursor = paginate_sorted_profiles(
                profiles,
                after=after,
                limit=limit,
                sort_key=sort_key,
                direction=direction,
            )
            return ProfileSummaryPage(
                items=[_profile_summary(item) for item in selected],
                next_cursor=next_cursor,
                total=total,
                change_token=self._tokens[PROFILE_NAMESPACE],
            )

    def get_profile(self, profile_id: str) -> Nl2SqlProfile | None:
        with self._lock:
            value = self._profiles.get(profile_id)
            return value.model_copy(deep=True) if value else None

    def list_profiles(self, *, include_archived: bool) -> list[Nl2SqlProfile]:
        with self._lock:
            return [
                item.model_copy(deep=True)
                for item in sorted(
                    self._profiles.values(),
                    key=lambda profile: (profile.name.casefold(), profile.id),
                )
                if include_archived or not item.archived
            ]

    def save_profile(
        self,
        profile: Nl2SqlProfile,
        *,
        expected_etag: str | None,
    ) -> Nl2SqlProfile:
        with self._lock:
            current = self._profiles.get(profile.id)
            if current is not None and expected_etag != current.etag:
                raise IncrementalVersionConflict(current.etag)
            if current is None and expected_etag:
                raise IncrementalVersionConflict("")
            version = (current.version + 1) if current else 1
            updated_at = _utc_now()
            payload = _profile_payload(profile)
            stored = _profile_from_payload(
                payload,
                version=version,
                etag=_etag(payload, version),
                updated_at=updated_at,
            )
            self._profiles[stored.id] = stored
            self._tokens[PROFILE_NAMESPACE] += 1
            return stored.model_copy(deep=True)

    def delete_profile(self, profile_id: str, *, expected_etag: str | None) -> None:
        with self._lock:
            current = self._profiles.get(profile_id)
            if current is None:
                raise KeyError(profile_id)
            if expected_etag != current.etag:
                raise IncrementalVersionConflict(current.etag)
            del self._profiles[profile_id]
            self._tokens[PROFILE_NAMESPACE] += 1

    def get_catalog_head(self) -> SchemaCatalogHead:
        with self._lock:
            visible_catalog = filter_user_visible_catalog(self._catalog)
            etag = self._catalog.schema_fingerprint or f"schema-{self._catalog_version}"
            return SchemaCatalogHead(
                catalog_version=self._catalog_version,
                schema_fingerprint=self._catalog.schema_fingerprint,
                refreshed_at=self._catalog.refreshed_at,
                object_count=len(visible_catalog.tables),
                column_count=sum(len(table.columns) for table in visible_catalog.tables),
                change_token=self._tokens[SCHEMA_NAMESPACE],
                etag=etag,
            )

    def load_catalog(self) -> SchemaCatalog:
        with self._lock:
            return filter_user_visible_catalog(self._catalog).model_copy(deep=True)

    def search_schema_objects(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        owner: str,
        owner_prefix: str = "",
        query_scope: str = "all",
        object_type: str,
        allowed_names: set[str] | None,
        row_state: str = "",
        include_counts: bool = True,
    ) -> SchemaObjectPage:
        after = _decode_cursor(cursor, 2)
        query_key = query.casefold().strip()
        query_scope_key = query_scope.lower().strip()
        owner_key = owner.upper().strip()
        owner_prefix_key = owner_prefix.upper().strip()
        type_key = object_type.upper().strip()
        row_state_key = row_state.lower().strip()
        with self._lock:
            tables = [
                table
                for table in self._catalog.tables
                if is_user_visible_schema_object(table.owner, table.table_name)
                and (not owner_key or table.owner.upper() == owner_key)
                and (not owner_prefix_key or table.owner.upper().startswith(owner_prefix_key))
                and (
                    not type_key
                    or table.table_type.upper() == type_key
                    or (type_key == "VIEW" and table.table_type.upper() == "MATERIALIZED VIEW")
                )
                and (
                    not row_state_key
                    or row_state_key == "all"
                    or (row_state_key == "with_rows" and (table.row_count or 0) > 0)
                    or (row_state_key == "empty_rows" and table.row_count == 0)
                    or (row_state_key == "unknown_rows" and table.row_count is None)
                )
                and (
                    allowed_names is None
                    or table.table_name.upper() in allowed_names
                    or f"{table.owner}.{table.table_name}".upper() in allowed_names
                )
                and (
                    not query_key
                    or query_key
                    in " ".join(
                        [table.table_name, table.comment]
                        if query_scope_key == "name_comment"
                        else [
                            table.owner,
                            table.table_name,
                            table.logical_name,
                            table.comment,
                            *(column.column_name for column in table.columns),
                            *(column.logical_name for column in table.columns),
                        ]
                    ).casefold()
                )
            ]
            tables.sort(key=lambda item: (item.owner.upper(), item.table_name.upper()))
            total = len(tables) if include_counts else None
            table_count = (
                sum(item.table_type.upper() not in {"VIEW", "MATERIALIZED VIEW"} for item in tables)
                if include_counts
                else 0
            )
            view_count = (total - table_count) if total is not None else 0
            if after:
                tables = [
                    item for item in tables if (item.owner.upper(), item.table_name.upper()) > after
                ]
            selected = tables[: limit + 1]
            has_more = len(selected) > limit
            selected = selected[:limit]
            next_cursor = None
            if has_more and selected:
                last = selected[-1]
                next_cursor = _encode_cursor(last.owner.upper(), last.table_name.upper())
            return SchemaObjectPage(
                items=[self._schema_summary(table) for table in selected],
                next_cursor=next_cursor,
                total=total,
                table_count=table_count,
                view_count=view_count,
                counts_included=include_counts,
                refreshed_at=self._catalog.refreshed_at,
                catalog_version=self._catalog_version,
            )

    def get_schema_object(self, owner: str, object_name: str) -> SchemaObjectDetail | None:
        if not is_user_visible_schema_object(owner, object_name):
            return None
        owner_key = owner.upper()
        name_key = object_name.upper()
        with self._lock:
            table = next(
                (
                    item
                    for item in self._catalog.tables
                    if item.owner.upper() == owner_key and item.table_name.upper() == name_key
                ),
                None,
            )
            if table is None:
                return None
            dependencies = [
                item
                for item in self._catalog.view_dependencies
                if item.owner.upper() == owner_key and item.view_name.upper() == name_key
            ]
            return SchemaObjectDetail(
                table=table.model_copy(deep=True),
                dependencies=copy.deepcopy(dependencies),
                catalog_version=self._catalog_version,
                etag=self._catalog.schema_fingerprint or f"schema-{self._catalog_version}",
            )

    def schema_manifest(self) -> dict[tuple[str, str], str]:
        with self._lock:
            return dict(self._manifest)

    def apply_schema_refresh(
        self,
        *,
        catalog: SchemaCatalog,
        manifest: Mapping[tuple[str, str], str],
        changed_keys: set[tuple[str, str]],
        deleted_keys: set[tuple[str, str]],
    ) -> SchemaCatalogHead:
        del changed_keys, deleted_keys
        with self._lock:
            self._catalog = filter_user_visible_catalog(catalog).model_copy(deep=True)
            self._manifest = {
                key: value
                for key, value in manifest.items()
                if is_user_visible_schema_object(key[0], key[1])
            }
            self._catalog_version += 1
            self._tokens[SCHEMA_NAMESPACE] += 1
            return self.get_catalog_head()

    def save_refresh_job(self, job: SchemaRefreshJob) -> SchemaRefreshJob:
        with self._lock:
            self._refresh_jobs[job.job_id] = job.model_copy(deep=True)
            return job.model_copy(deep=True)

    def get_refresh_job(self, job_id: str) -> SchemaRefreshJob | None:
        with self._lock:
            job = self._refresh_jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def find_active_refresh_job(self) -> SchemaRefreshJob | None:
        with self._lock:
            active = [
                job
                for job in self._refresh_jobs.values()
                if job.status in {SchemaRefreshJobStatus.PENDING, SchemaRefreshJobStatus.RUNNING}
            ]
            if not active:
                return None
            return min(active, key=lambda item: (item.created_at, item.job_id)).model_copy(
                deep=True
            )

    def submit_refresh_job(self, job: SchemaRefreshJob) -> SchemaRefreshJob:
        """process 間の submit 契約と同じく、active job の確認と作成を原子的に行う。"""

        with self._lock:
            active = [
                item
                for item in self._refresh_jobs.values()
                if item.status in {SchemaRefreshJobStatus.PENDING, SchemaRefreshJobStatus.RUNNING}
            ]
            if active:
                current = min(active, key=lambda item: (item.created_at, item.job_id))
                coalesced = _coalesce_pending_refresh_job(current, job)
                self._refresh_jobs[coalesced.job_id] = coalesced.model_copy(deep=True)
                return coalesced.model_copy(deep=True)
            self._refresh_jobs[job.job_id] = job.model_copy(deep=True)
            return job.model_copy(deep=True)

    def claim_refresh_job(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        job_id: str | None = None,
    ) -> SchemaRefreshJob | None:
        now = datetime.now(UTC)
        with self._lock:
            candidates = [
                item
                for item in self._refresh_jobs.values()
                if (job_id is None or item.job_id == job_id)
                and (
                    item.status == SchemaRefreshJobStatus.PENDING
                    or (
                        item.status == SchemaRefreshJobStatus.RUNNING
                        and (
                            not item.lease_expires_at
                            or datetime.fromisoformat(item.lease_expires_at) <= now
                        )
                    )
                )
            ]
            if not candidates:
                return None
            current = min(candidates, key=lambda item: (item.created_at, item.job_id))
            claimed = current.model_copy(
                update={
                    "status": SchemaRefreshJobStatus.RUNNING,
                    "started_at": current.started_at or now.isoformat(),
                    "worker_id": worker_id,
                    "heartbeat_at": now.isoformat(),
                    "lease_expires_at": datetime.fromtimestamp(
                        now.timestamp() + max(30.0, lease_seconds), UTC
                    ).isoformat(),
                    "attempt": current.attempt + 1,
                }
            )
            self._refresh_jobs[claimed.job_id] = claimed
            return claimed.model_copy(deep=True)

    def claim_document(
        self,
        collection: str,
        *,
        worker_id: str,
        lease_seconds: float,
        entity_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        with self._lock:
            candidates: list[tuple[tuple[str, str], dict[str, Any], dict[str, Any]]] = []
            for key, value in self._documents.items():
                item_collection, item_entity_id = key
                if item_collection != collection:
                    continue
                if entity_id is not None and item_entity_id != entity_id:
                    continue
                payload = _memory_document_payload(value)
                if _state_document_claimable(payload, now):
                    candidates.append((key, value, payload))
            if not candidates:
                return None
            key, current, payload = min(
                candidates,
                key=lambda item: (
                    str(item[2].get("created_at") or item[1].get("_updated_at") or ""),
                    item[0][1],
                ),
            )
            claimed = _claimed_state_document(
                payload,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                now=now,
            )
            self._documents[key] = {
                **claimed,
                "_entity_id": key[1],
                "_profile_id": str(current.get("_profile_id") or ""),
                "_status": "running",
                "_updated_at": now.isoformat(),
            }
            self._tokens[STATE_NAMESPACE] += 1
            return copy.deepcopy(claimed)

    def put_document(
        self,
        collection: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        profile_id: str = "",
        status: str = "",
    ) -> None:
        with self._lock:
            self._documents[(collection, entity_id)] = {
                **copy.deepcopy(dict(payload)),
                "_entity_id": entity_id,
                "_profile_id": profile_id,
                "_status": status,
                "_updated_at": _utc_now(),
            }
            self._tokens[STATE_NAMESPACE] += 1

    def patch_document(
        self,
        collection: str,
        entity_id: str,
        updates: Mapping[str, Any],
        *,
        profile_id: str = "",
        status: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            key = (collection, entity_id)
            current = self._documents.get(key)
            if current is None:
                return None
            payload = _memory_document_payload(current)
            payload.update(copy.deepcopy(dict(updates)))
            self._documents[key] = {
                **payload,
                "_entity_id": entity_id,
                "_profile_id": profile_id or str(current.get("_profile_id") or ""),
                "_status": status
                or _state_document_status_value(
                    collection,
                    payload,
                    str(current.get("_status") or ""),
                ),
                "_updated_at": _utc_now(),
            }
            self._tokens[STATE_NAMESPACE] += 1
            return copy.deepcopy(payload)

    def replace_documents(
        self,
        collection: str,
        documents: Sequence[tuple[str, Mapping[str, Any], str, str]],
    ) -> None:
        with self._lock:
            for key in [key for key in self._documents if key[0] == collection]:
                self._documents.pop(key, None)
            for entity_id, payload, profile_id, status in documents:
                self._documents[(collection, entity_id)] = {
                    **copy.deepcopy(dict(payload)),
                    "_entity_id": entity_id,
                    "_profile_id": profile_id,
                    "_status": status,
                    "_updated_at": _utc_now(),
                }
            self._tokens[STATE_NAMESPACE] += 1

    def get_document(self, collection: str, entity_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._documents.get((collection, entity_id))
            return _memory_document_payload(value) if value else None

    def delete_document(self, collection: str, entity_id: str) -> None:
        with self._lock:
            self._documents.pop((collection, entity_id), None)
            self._tokens[STATE_NAMESPACE] += 1

    def list_documents(
        self,
        collection: str,
        *,
        limit: int,
        profile_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        with self._lock:
            values = [
                value
                for (item_collection, _entity_id), value in self._documents.items()
                if item_collection == collection
                and (not profile_id or value.get("_profile_id") == profile_id)
                and (not status or value.get("_status") == status)
            ]
            values.sort(
                key=lambda item: (
                    _state_document_sort_value(collection, item),
                    str(item.get("_entity_id") or ""),
                ),
                reverse=True,
            )
            return [_memory_document_payload(value) for value in values[:limit]]

    def list_documents_page(
        self,
        collection: str,
        *,
        cursor: str | None,
        limit: int,
        profile_id: str = "",
        status: str = "",
        query: str = "",
        payload_filters: Mapping[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        decoded = _decode_cursor(cursor, 2)
        query_key = query.casefold().strip()
        filters = {key: value for key, value in (payload_filters or {}).items() if value}
        with self._lock:
            values = [
                value
                for (item_collection, _entity_id), value in self._documents.items()
                if item_collection == collection
                and (not profile_id or value.get("_profile_id") == profile_id)
                and (not status or value.get("_status") == status)
                and _state_document_matches_query(collection, value, query_key)
                and all(str(value.get(key) or "") == expected for key, expected in filters.items())
            ]
            values.sort(
                key=lambda item: (
                    _state_document_sort_value(collection, item),
                    str(item.get("_entity_id") or ""),
                ),
                reverse=True,
            )
            total = len(values)
            if decoded:
                values = [
                    value
                    for value in values
                    if (
                        _state_document_sort_value(collection, value),
                        str(value.get("_entity_id") or ""),
                    )
                    < decoded
                ]
            page = values[: limit + 1]
            has_more = len(page) > limit
            selected = page[:limit]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = _encode_cursor(
                _state_document_sort_value(collection, last),
                str(last.get("_entity_id") or ""),
            )
        return [_memory_document_payload(value) for value in selected], next_cursor, total

    def _schema_summary(self, table: SchemaTable) -> SchemaObjectSummary:
        return SchemaObjectSummary(
            owner=table.owner,
            object_name=table.table_name,
            object_type=table.table_type.upper(),
            logical_name=table.logical_name,
            comment=table.comment,
            row_count=table.row_count,
            column_count=len(table.columns),
            last_ddl_at=self._manifest.get((table.owner.upper(), table.table_name.upper()), ""),
        )


class OracleIncrementalNl2SqlRepository:
    """Oracle 26ai backed incremental repository。DDL は migration の責務。"""

    mode = "oracle"

    def __init__(
        self,
        *,
        connection_factory: Callable[[], AbstractContextManager[Any]],
    ) -> None:
        self._connection_factory = connection_factory

    def check(self) -> tuple[bool, str]:
        try:
            with self._connection_factory() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT VERSION_NO FROM NL2SQL_SCHEMA_MIGRATIONS "
                    "WHERE VERSION_NO IN (3, 5, 6)"
                )
                rows = cursor.fetchall()
        except Exception as exc:
            if "ORA-00942" in str(exc).upper():
                return False, "incremental migration is not applied"
            raise
        record_repository("migration_check", rows=len(rows))
        applied = {int(row[0]) for row in rows}
        missing = [version for version in REQUIRED_MIGRATION_VERSIONS if version not in applied]
        if missing:
            return False, f"migration {', '.join(map(str, missing))} is required"
        return True, "incremental migrations 3,5,6"

    def get_change_token(self, namespace: str) -> int:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT CHANGE_SEQ FROM NL2SQL_CHANGE_TOKENS WHERE NAMESPACE = :namespace",
                {"namespace": namespace},
            )
            row = cursor.fetchone()
        record_repository("change_token", rows=1 if row else 0)
        return int(row[0]) if row else 0

    # sort key を文字列式で統一し、cursor(文字列 tuple)を memory 実装と共通化する。
    # NL2SQL_PROFILES は小規模のため LPAD による index 非利用は許容する。
    _PROFILE_SORT_EXPRESSIONS = {
        "name": "UPPER(NAME)",
        "tables": "LPAD(TO_CHAR(NVL(ALLOWED_TABLE_COUNT, 0)), 12, '0')",
        "views": "LPAD(TO_CHAR(NVL(ALLOWED_VIEW_COUNT, 0)), 12, '0')",
    }

    def search_profiles(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        include_archived: bool,
        sort_key: str = "name",
        direction: str = "asc",
    ) -> ProfileSummaryPage:
        after = _decode_cursor(cursor, 2)
        sort_key, direction = normalize_profile_sort(sort_key, direction)
        sort_expression = self._PROFILE_SORT_EXPRESSIONS[sort_key]
        descending = direction == "desc"
        order = "DESC" if descending else "ASC"
        comparison = "<" if descending else ">"
        where = ["(:include_archived = 1 OR ARCHIVED = 0)"]
        binds: dict[str, Any] = {
            "include_archived": 1 if include_archived else 0,
            "limit": limit + 1,
        }
        if query.strip():
            where.append(
                "(UPPER(NAME) LIKE :query ESCAPE '\\' "
                "OR UPPER(CATEGORY) LIKE :query ESCAPE '\\' "
                "OR UPPER(DESCRIPTION) LIKE :query ESCAPE '\\')"
            )
            binds["query"] = f"%{_escape_oracle_like_pattern(query.strip())}%"
        if after:
            where.append(
                f"({sort_expression} {comparison} :after_sort OR "
                f"({sort_expression} = :after_sort AND PROFILE_ID {comparison} :after_id))"
            )
            binds.update(after_sort=after[0], after_id=after[1])
        sql = (
            "SELECT PROFILE_ID, NAME, CATEGORY, DESCRIPTION, ARCHIVED, "  # nosec B608
            "ALLOWED_TABLE_COUNT, ALLOWED_VIEW_COUNT, GLOSSARY_COUNT, FEW_SHOT_COUNT, "
            "VERSION_NO, ETAG, UPDATED_AT FROM NL2SQL_PROFILES WHERE "
            + " AND ".join(where)
            + f" ORDER BY {sort_expression} {order}, PROFILE_ID {order}"
            + " FETCH FIRST :limit ROWS ONLY"
        )
        with self._connection_factory() as connection, connection.cursor() as db_cursor:
            db_cursor.execute(sql, binds)
            rows = db_cursor.fetchall()
            count_where = ["(:include_archived = 1 OR ARCHIVED = 0)"]
            count_binds: dict[str, Any] = {"include_archived": binds["include_archived"]}
            if query.strip():
                count_where.append(
                    "(UPPER(NAME) LIKE :query ESCAPE '\\' "
                    "OR UPPER(CATEGORY) LIKE :query ESCAPE '\\' "
                    "OR UPPER(DESCRIPTION) LIKE :query ESCAPE '\\')"
                )
                count_binds["query"] = binds["query"]
            db_cursor.execute(
                "SELECT COUNT(*) FROM NL2SQL_PROFILES WHERE "
                + " AND ".join(count_where),  # nosec B608
                count_binds,
            )
            count_row = db_cursor.fetchone()
            token = self._change_token_with_cursor(db_cursor, PROFILE_NAMESPACE)
        record_repository("profile_search", statements=3, rows=len(rows) + 2)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [self._summary_from_row(row) for row in rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(_profile_summary_sort_key(last, sort_key), last.id)
        return ProfileSummaryPage(
            items=items,
            next_cursor=next_cursor,
            total=int(count_row[0]) if count_row else 0,
            change_token=token,
        )

    def get_profile(self, profile_id: str) -> Nl2SqlProfile | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT PAYLOAD_JSON, VERSION_NO, ETAG, UPDATED_AT "
                "FROM NL2SQL_PROFILES WHERE PROFILE_ID = :profile_id",
                {"profile_id": profile_id},
            )
            row = cursor.fetchone()
            raw = _read_lob(row[0]) if row else ""
        if not row:
            return None
        record_repository(
            "profile_detail",
            rows=1,
            clob_collection="profiles",
            clob_bytes=len(raw.encode("utf-8")),
        )
        payload = json.loads(raw)
        return _profile_from_payload(
            payload,
            version=int(row[1]),
            etag=str(row[2]),
            updated_at=str(row[3]),
        )

    def list_profiles(self, *, include_archived: bool) -> list[Nl2SqlProfile]:
        where = "" if include_archived else " WHERE ARCHIVED = 0"
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT PAYLOAD_JSON, VERSION_NO, ETAG, UPDATED_AT FROM NL2SQL_PROFILES"  # nosec B608
                + where
                + " ORDER BY UPPER(NAME), PROFILE_ID"
            )
            rows = cursor.fetchall()
            materialized_rows = [(_read_lob(row[0]), *row[1:]) for row in rows]
        return [
            _profile_from_payload(
                json.loads(row[0]),
                version=int(row[1]),
                etag=str(row[2]),
                updated_at=str(row[3]),
            )
            for row in materialized_rows
        ]

    def save_profile(
        self,
        profile: Nl2SqlProfile,
        *,
        expected_etag: str | None,
    ) -> Nl2SqlProfile:
        payload = _profile_payload(profile)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    "SELECT VERSION_NO, ETAG FROM NL2SQL_PROFILES "
                    "WHERE PROFILE_ID = :profile_id FOR UPDATE",
                    {"profile_id": profile.id},
                )
                current = cursor.fetchone()
                if current and expected_etag != str(current[1]):
                    raise IncrementalVersionConflict(str(current[1]))
                if not current and expected_etag:
                    raise IncrementalVersionConflict("")
                version = int(current[0]) + 1 if current else 1
                etag = _etag(payload, version)
                updated_at = _utc_now()
                binds = self._profile_binds(profile, payload, version, etag, updated_at)
                if current:
                    cursor.execute(self._profile_update_sql(), binds)
                else:
                    cursor.execute(self._profile_insert_sql(), binds)
                self._bump_token(cursor, PROFILE_NAMESPACE)
                connection.commit()
            except Exception:
                rollback = getattr(connection, "rollback", None)
                if callable(rollback):
                    rollback()
                raise
        return _profile_from_payload(
            payload,
            version=version,
            etag=etag,
            updated_at=updated_at,
        )

    def delete_profile(self, profile_id: str, *, expected_etag: str | None) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    "SELECT ETAG FROM NL2SQL_PROFILES WHERE PROFILE_ID = :profile_id " "FOR UPDATE",
                    {"profile_id": profile_id},
                )
                current = cursor.fetchone()
                if not current:
                    raise KeyError(profile_id)
                if expected_etag != str(current[0]):
                    raise IncrementalVersionConflict(str(current[0]))
                # view revision と旧互換 row を parent と同じ transaction で削除する。
                # migration の ON DELETE CASCADE は競合時の最終防壁として残す。
                cursor.execute(
                    "DELETE FROM NL2SQL_ONTOLOGY_PROFILE_VIEW_REVISIONS "
                    "WHERE PROFILE_ID = :profile_id",
                    {"profile_id": profile_id},
                )
                cursor.execute(
                    "DELETE FROM NL2SQL_ONTOLOGY_PROFILE_VIEWS WHERE PROFILE_ID = :profile_id",
                    {"profile_id": profile_id},
                )
                cursor.execute(
                    "DELETE FROM NL2SQL_PROFILES WHERE PROFILE_ID = :profile_id",
                    {"profile_id": profile_id},
                )
                self._bump_token(cursor, PROFILE_NAMESPACE)
                connection.commit()
            except Exception:
                rollback = getattr(connection, "rollback", None)
                if callable(rollback):
                    rollback()
                raise

    def get_catalog_head(self) -> SchemaCatalogHead:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT h.CATALOG_VERSION, h.SCHEMA_FINGERPRINT, h.REFRESHED_AT, "
                "h.OBJECT_COUNT, h.COLUMN_COUNT, h.ETAG, t.CHANGE_SEQ "
                "FROM NL2SQL_CHANGE_TOKENS t LEFT JOIN NL2SQL_SCHEMA_CATALOG_HEAD h "
                "ON h.HEAD_KEY = 'active' WHERE t.NAMESPACE = :namespace",
                {"namespace": SCHEMA_NAMESPACE},
            )
            row = cursor.fetchone()
        record_repository("schema_head", rows=1 if row else 0)
        if not row:
            return SchemaCatalogHead()
        return SchemaCatalogHead(
            catalog_version=int(row[0] or 0),
            schema_fingerprint=str(row[1] or ""),
            refreshed_at=str(row[2] or ""),
            object_count=int(row[3] or 0),
            column_count=int(row[4] or 0),
            change_token=int(row[6] or 0),
            etag=str(row[5] or ""),
        )

    def load_catalog(self) -> SchemaCatalog:
        head = self.get_catalog_head()
        with self._connection_factory() as connection, connection.cursor() as cursor:
            configure_clob_fetch_as_text(cursor)
            cursor.execute(
                "SELECT OWNER_NAME, OBJECT_NAME, OBJECT_TYPE, LOGICAL_NAME, COMMENTS, "
                "ROW_COUNT FROM NL2SQL_SCHEMA_OBJECTS ORDER BY OWNER_NAME, OBJECT_NAME"
            )
            object_rows = cursor.fetchall()
            cursor.execute(
                "SELECT OWNER_NAME, OBJECT_NAME, COLUMN_NAME, LOGICAL_NAME, DATA_TYPE, "
                "NULLABLE, COMMENTS, SAMPLE_VALUES_JSON FROM NL2SQL_SCHEMA_COLUMNS "
                "ORDER BY OWNER_NAME, OBJECT_NAME, COLUMN_POSITION"
            )
            column_rows = cursor.fetchall()
            cursor.execute(
                "SELECT OWNER_NAME, OBJECT_NAME, CONSTRAINT_TEXT, PAYLOAD_JSON "
                "FROM NL2SQL_SCHEMA_CONSTRAINTS ORDER BY OWNER_NAME, OBJECT_NAME, "
                "CONSTRAINT_NAME"
            )
            constraint_rows = cursor.fetchall()
            cursor.execute(
                "SELECT OWNER_NAME, VIEW_NAME, REFERENCED_OWNER, REFERENCED_NAME, "
                "REFERENCED_TYPE FROM NL2SQL_SCHEMA_DEPENDENCIES "
                "ORDER BY OWNER_NAME, VIEW_NAME, REFERENCED_OWNER, REFERENCED_NAME"
            )
            dependency_rows = cursor.fetchall()
            column_rows = [(*row[:7], _read_lob(row[7]) if row[7] else "") for row in column_rows]
            constraint_rows = [
                (*row[:3], _read_lob(row[3]) if row[3] else "") for row in constraint_rows
            ]
        schema_clob_bytes = sum(len(row[7].encode("utf-8")) for row in column_rows if row[7]) + sum(
            len(row[3].encode("utf-8")) for row in constraint_rows if row[3]
        )
        record_repository(
            "schema_catalog_load",
            statements=4,
            rows=(
                len(object_rows) + len(column_rows) + len(constraint_rows) + len(dependency_rows)
            ),
            clob_collection="schema",
            clob_bytes=schema_clob_bytes,
        )
        tables: dict[tuple[str, str], SchemaTable] = {}
        for row in object_rows:
            key = (str(row[0]), str(row[1]))
            tables[key] = SchemaTable(
                owner=key[0],
                table_name=key[1],
                table_type=str(row[2]).lower(),
                logical_name=str(row[3] or key[1]),
                comment=str(row[4] or ""),
                row_count=int(row[5]) if row[5] is not None else None,
            )
        for row in column_rows:
            table = tables.get((str(row[0]), str(row[1])))
            if table is None:
                continue
            samples = json.loads(row[7]) if row[7] else []
            table.columns.append(
                SchemaColumn(
                    column_name=str(row[2]),
                    logical_name=str(row[3] or row[2]),
                    data_type=str(row[4]),
                    nullable=bool(row[5]),
                    comment=str(row[6] or ""),
                    sample_values=samples,
                )
            )
        for row in constraint_rows:
            table = tables.get((str(row[0]), str(row[1])))
            if table is None:
                continue
            table.constraints.append(str(row[2] or ""))
            table.constraint_details.append(
                SchemaConstraintDetail.model_validate(json.loads(row[3]))
            )
        dependencies = [
            SchemaViewDependency(
                owner=str(row[0]),
                view_name=str(row[1]),
                referenced_owner=str(row[2]),
                referenced_name=str(row[3]),
                referenced_type=str(row[4]),
            )
            for row in dependency_rows
        ]
        return SchemaCatalog(
            refreshed_at=head.refreshed_at,
            schema_fingerprint=head.schema_fingerprint,
            tables=list(tables.values()),
            view_dependencies=dependencies,
        )

    def search_schema_objects(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        owner: str,
        owner_prefix: str = "",
        query_scope: str = "all",
        object_type: str,
        allowed_names: set[str] | None,
        row_state: str = "",
        include_counts: bool = True,
    ) -> SchemaObjectPage:
        after = _decode_cursor(cursor, 2)
        where = [
            "o.OWNER_NAME NOT LIKE '%$%'",
            "o.OWNER_NAME NOT LIKE '%#%'",
            "o.OBJECT_NAME NOT LIKE '%$%'",
            "o.OBJECT_NAME NOT LIKE '%#%'",
            "o.OBJECT_NAME NOT LIKE 'NL2SQL\\_%' ESCAPE '\\'",
        ]
        binds: dict[str, Any] = {"limit": limit + 1}
        if owner.strip():
            where.append("o.OWNER_NAME = :owner")
            binds["owner"] = owner.strip().upper()
        if owner_prefix.strip():
            where.append("SUBSTR(UPPER(o.OWNER_NAME), 1, LENGTH(:owner_prefix)) = :owner_prefix")
            binds["owner_prefix"] = owner_prefix.strip().upper()
        if object_type.strip():
            normalized_type = object_type.strip().upper()
            if normalized_type == "VIEW":
                where.append("o.OBJECT_TYPE IN ('VIEW', 'MATERIALIZED VIEW')")
            else:
                where.append("o.OBJECT_TYPE = :object_type")
                binds["object_type"] = normalized_type
        if query.strip():
            if query_scope.lower().strip() == "name_comment":
                where.append("(UPPER(o.OBJECT_NAME) LIKE :query OR UPPER(o.COMMENTS) LIKE :query)")
            else:
                where.append(
                    "(UPPER(o.OWNER_NAME) LIKE :query OR "
                    "UPPER(o.OWNER_NAME || '.' || o.OBJECT_NAME) LIKE :query OR "
                    "UPPER(o.OBJECT_NAME) LIKE :query OR UPPER(o.LOGICAL_NAME) LIKE :query "
                    "OR UPPER(o.COMMENTS) LIKE :query OR EXISTS (SELECT 1 "
                    "FROM NL2SQL_SCHEMA_COLUMNS c WHERE c.OWNER_NAME = o.OWNER_NAME "
                    "AND c.OBJECT_NAME = o.OBJECT_NAME AND "
                    "(UPPER(c.COLUMN_NAME) LIKE :query OR UPPER(c.LOGICAL_NAME) LIKE :query)))"
                )
            binds["query"] = f"%{query.strip().upper()}%"
        normalized_row_state = row_state.strip().lower()
        if normalized_row_state == "with_rows":
            where.append("o.ROW_COUNT > 0")
        elif normalized_row_state == "empty_rows":
            where.append("o.ROW_COUNT = 0")
        elif normalized_row_state == "unknown_rows":
            where.append("o.ROW_COUNT IS NULL")
        if after:
            where.append(
                "(o.OWNER_NAME > :after_owner OR "
                "(o.OWNER_NAME = :after_owner AND o.OBJECT_NAME > :after_name))"
            )
            binds.update(after_owner=after[0].upper(), after_name=after[1].upper())
        if allowed_names is not None:
            normalized = sorted({name.upper() for name in allowed_names})
            if not normalized:
                if include_counts:
                    return SchemaObjectPage(catalog_version=self.get_catalog_head().catalog_version)
                return SchemaObjectPage(total=None, counts_included=False)
            where.append(
                "EXISTS (SELECT 1 FROM JSON_TABLE(:allowed_names_json, '$[*]' "
                "COLUMNS (ALLOWED_NAME VARCHAR2(512) PATH '$')) allowed "
                "WHERE allowed.ALLOWED_NAME = UPPER(o.OBJECT_NAME) OR "
                "allowed.ALLOWED_NAME = UPPER(o.OWNER_NAME || '.' || o.OBJECT_NAME))"
            )
            binds["allowed_names_json"] = json.dumps(normalized, ensure_ascii=False)
        base_where = " AND ".join(where)
        sql = (
            "SELECT o.OWNER_NAME, o.OBJECT_NAME, o.OBJECT_TYPE, o.LOGICAL_NAME, "  # nosec B608
            "o.COMMENTS, o.ROW_COUNT, o.COLUMN_COUNT, o.LAST_DDL_AT "
            "FROM NL2SQL_SCHEMA_OBJECTS o WHERE "
            + base_where
            + " ORDER BY o.OWNER_NAME, o.OBJECT_NAME FETCH FIRST :limit ROWS ONLY"
        )
        with self._connection_factory() as connection, connection.cursor() as db_cursor:
            if "allowed_names_json" in binds:
                _set_clob_bind(db_cursor, "allowed_names_json")
            db_cursor.execute(sql, binds)
            rows = db_cursor.fetchall()
            count_row = None
            if include_counts:
                count_where = [part for part in where if not part.startswith("(o.OWNER_NAME >")]
                count_binds = {
                    key: value
                    for key, value in binds.items()
                    if key not in {"limit", "after_owner", "after_name"}
                }
                # Oracle は aggregate と scalar subquery を同じ SELECT level に混在させると
                # ORA-00937 を返す。件数を一行の derived table に確定してから head を結合する。
                db_cursor.execute(
                    "SELECT stats.TOTAL_COUNT, stats.TABLE_COUNT, stats.VIEW_COUNT, "  # nosec B608
                    "h.CATALOG_VERSION, h.REFRESHED_AT "
                    "FROM (SELECT COUNT(*) TOTAL_COUNT, "
                    "SUM(CASE WHEN o.OBJECT_TYPE IN ('VIEW', 'MATERIALIZED VIEW') "
                    "THEN 0 ELSE 1 END) TABLE_COUNT, "
                    "SUM(CASE WHEN o.OBJECT_TYPE IN ('VIEW', 'MATERIALIZED VIEW') "
                    "THEN 1 ELSE 0 END) VIEW_COUNT "
                    "FROM NL2SQL_SCHEMA_OBJECTS o WHERE "
                    + " AND ".join(count_where)
                    + ") stats LEFT JOIN NL2SQL_SCHEMA_CATALOG_HEAD h "
                    "ON h.HEAD_KEY = 'active'",
                    count_binds,
                )
                count_row = db_cursor.fetchone()
        record_repository(
            "schema_search",
            statements=2 if include_counts else 1,
            rows=len(rows) + (1 if include_counts else 0),
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [
            SchemaObjectSummary(
                owner=str(row[0]),
                object_name=str(row[1]),
                object_type=str(row[2]),
                logical_name=str(row[3] or row[1]),
                comment=str(row[4] or ""),
                row_count=int(row[5]) if row[5] is not None else None,
                column_count=int(row[6] or 0),
                last_ddl_at=str(row[7] or ""),
            )
            for row in rows
        ]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(last.owner.upper(), last.object_name.upper())
        return SchemaObjectPage(
            items=items,
            next_cursor=next_cursor,
            total=int(count_row[0]) if count_row else (0 if include_counts else None),
            table_count=int(count_row[1] or 0) if count_row else 0,
            view_count=int(count_row[2] or 0) if count_row else 0,
            counts_included=include_counts,
            refreshed_at=str(count_row[4] or "") if count_row else "",
            catalog_version=int(count_row[3] or 0) if count_row else 0,
        )

    def get_schema_object(self, owner: str, object_name: str) -> SchemaObjectDetail | None:
        if not is_user_visible_schema_object(owner, object_name):
            return None
        owner_key = owner.upper()
        object_key = object_name.upper()
        catalog = self._load_catalog_subset(owner_key, object_key)
        if not catalog.tables:
            return None
        head = self.get_catalog_head()
        return SchemaObjectDetail(
            table=catalog.tables[0],
            dependencies=catalog.view_dependencies,
            catalog_version=head.catalog_version,
            etag=head.etag,
        )

    def schema_manifest(self) -> dict[tuple[str, str], str]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT OWNER_NAME, OBJECT_NAME, LAST_DDL_AT FROM NL2SQL_SCHEMA_OBJECTS "
                "WHERE OWNER_NAME NOT LIKE '%$%' "
                "AND OWNER_NAME NOT LIKE '%#%' "
                "AND OBJECT_NAME NOT LIKE '%$%' "
                "AND OBJECT_NAME NOT LIKE '%#%' "
                "AND OBJECT_NAME NOT LIKE 'NL2SQL\\_%' ESCAPE '\\'"
            )
            rows = cursor.fetchall()
        record_repository("schema_manifest", rows=len(rows))
        return {(str(row[0]), str(row[1])): str(row[2] or "") for row in rows}

    def apply_schema_refresh(
        self,
        *,
        catalog: SchemaCatalog,
        manifest: Mapping[tuple[str, str], str],
        changed_keys: set[tuple[str, str]],
        deleted_keys: set[tuple[str, str]],
    ) -> SchemaCatalogHead:
        catalog = filter_user_visible_catalog(catalog)
        manifest = {
            key: value
            for key, value in manifest.items()
            if is_user_visible_schema_object(key[0], key[1])
        }
        changed_keys = {
            key for key in changed_keys if is_user_visible_schema_object(key[0], key[1])
        }
        table_by_key = {
            (table.owner.upper(), table.table_name.upper()): table for table in catalog.tables
        }
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    "SELECT CATALOG_VERSION FROM NL2SQL_SCHEMA_CATALOG_HEAD "
                    "WHERE HEAD_KEY = 'active' FOR UPDATE"
                )
                current = cursor.fetchone()
                version = int(current[0]) + 1 if current else 1
                for key in sorted(changed_keys | deleted_keys):
                    binds = {"owner": key[0], "object_name": key[1]}
                    cursor.execute(
                        "DELETE FROM NL2SQL_SCHEMA_COLUMNS WHERE OWNER_NAME = :owner "
                        "AND OBJECT_NAME = :object_name",
                        binds,
                    )
                    cursor.execute(
                        "DELETE FROM NL2SQL_SCHEMA_CONSTRAINTS WHERE OWNER_NAME = :owner "
                        "AND OBJECT_NAME = :object_name",
                        binds,
                    )
                    cursor.execute(
                        "DELETE FROM NL2SQL_SCHEMA_OBJECTS WHERE OWNER_NAME = :owner "
                        "AND OBJECT_NAME = :object_name",
                        binds,
                    )
                for key in sorted(changed_keys):
                    table = table_by_key.get(key)
                    if table is None:
                        continue
                    self._insert_schema_table(cursor, table, manifest.get(key, ""))
                cursor.execute("DELETE FROM NL2SQL_SCHEMA_DEPENDENCIES")
                for dependency in catalog.view_dependencies:
                    cursor.execute(
                        "INSERT INTO NL2SQL_SCHEMA_DEPENDENCIES (OWNER_NAME, VIEW_NAME, "
                        "REFERENCED_OWNER, REFERENCED_NAME, REFERENCED_TYPE) VALUES "
                        "(:owner, :view_name, :referenced_owner, :referenced_name, "
                        ":referenced_type)",
                        dependency.model_dump(mode="json"),
                    )
                cursor.execute("SELECT COUNT(*), SUM(COLUMN_COUNT) FROM NL2SQL_SCHEMA_OBJECTS")
                counts = cursor.fetchone() or (0, 0)
                head_etag = hashlib.sha256(
                    f"{version}:{catalog.schema_fingerprint}".encode()
                ).hexdigest()
                head_binds = {
                    "version": version,
                    "fingerprint": catalog.schema_fingerprint,
                    "refreshed_at": catalog.refreshed_at,
                    "object_count": int(counts[0] or 0),
                    "column_count": int(counts[1] or 0),
                    "etag": head_etag,
                }
                if current:
                    cursor.execute(
                        "UPDATE NL2SQL_SCHEMA_CATALOG_HEAD SET CATALOG_VERSION = :version, "
                        "SCHEMA_FINGERPRINT = :fingerprint, REFRESHED_AT = :refreshed_at, "
                        "OBJECT_COUNT = :object_count, COLUMN_COUNT = :column_count, "
                        "ETAG = :etag, UPDATED_AT = SYSTIMESTAMP WHERE HEAD_KEY = 'active'",
                        head_binds,
                    )
                else:
                    cursor.execute(
                        "INSERT INTO NL2SQL_SCHEMA_CATALOG_HEAD (HEAD_KEY, CATALOG_VERSION, "
                        "SCHEMA_FINGERPRINT, REFRESHED_AT, OBJECT_COUNT, COLUMN_COUNT, ETAG) "
                        "VALUES ('active', :version, :fingerprint, :refreshed_at, "
                        ":object_count, :column_count, :etag)",
                        head_binds,
                    )
                self._bump_token(cursor, SCHEMA_NAMESPACE)
                connection.commit()
            except Exception:
                rollback = getattr(connection, "rollback", None)
                if callable(rollback):
                    rollback()
                raise
        return self.get_catalog_head()

    def save_refresh_job(self, job: SchemaRefreshJob) -> SchemaRefreshJob:
        payload = _canonical_json(job.model_dump(mode="json"))
        heartbeat_at = datetime.fromisoformat(job.heartbeat_at) if job.heartbeat_at else None
        lease_expires_at = (
            datetime.fromisoformat(job.lease_expires_at) if job.lease_expires_at else None
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "MERGE INTO NL2SQL_SCHEMA_REFRESH_JOBS t USING (SELECT :job_id JOB_ID "
                "FROM DUAL) s ON (t.JOB_ID = s.JOB_ID) WHEN MATCHED THEN UPDATE SET "
                "t.STATUS = :status, t.WORKER_ID = :worker_id, "
                "t.HEARTBEAT_AT = :heartbeat_at, t.LEASE_EXPIRES_AT = :lease_expires_at, "
                "t.ATTEMPT_NO = :attempt, t.PAYLOAD_JSON = :payload, "
                "t.UPDATED_AT = SYSTIMESTAMP "
                "WHEN NOT MATCHED THEN INSERT (JOB_ID, STATUS, PAYLOAD_JSON) "
                "VALUES (:job_id, :status, :payload)",
                {
                    "job_id": job.job_id,
                    "status": job.status.value,
                    "worker_id": job.worker_id,
                    "heartbeat_at": heartbeat_at,
                    "lease_expires_at": lease_expires_at,
                    "attempt": job.attempt,
                    "payload": payload,
                },
            )
            connection.commit()
        return job.model_copy(deep=True)

    def get_refresh_job(self, job_id: str) -> SchemaRefreshJob | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT PAYLOAD_JSON FROM NL2SQL_SCHEMA_REFRESH_JOBS WHERE JOB_ID = :job_id",
                {"job_id": job_id},
            )
            row = cursor.fetchone()
            raw = _read_lob(row[0]) if row else ""
        return SchemaRefreshJob.model_validate_json(raw) if raw else None

    def find_active_refresh_job(self) -> SchemaRefreshJob | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT PAYLOAD_JSON FROM NL2SQL_SCHEMA_REFRESH_JOBS "
                "WHERE STATUS IN ('pending', 'running') "
                "ORDER BY CREATED_AT, JOB_ID FETCH FIRST 1 ROWS ONLY"
            )
            row = cursor.fetchone()
            raw = _read_lob(row[0]) if row else ""
        record_repository("schema_refresh_active", rows=1 if row else 0)
        return SchemaRefreshJob.model_validate_json(raw) if raw else None

    def submit_refresh_job(self, job: SchemaRefreshJob) -> SchemaRefreshJob:
        """複数 API process の同時 submit も1件へ合流させる。DDL 追加は不要。"""

        payload = _canonical_json(job.model_dump(mode="json"))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute("LOCK TABLE NL2SQL_SCHEMA_REFRESH_JOBS IN EXCLUSIVE MODE")
                cursor.execute(
                    "SELECT PAYLOAD_JSON FROM NL2SQL_SCHEMA_REFRESH_JOBS "
                    "WHERE STATUS IN ('pending', 'running') "
                    "ORDER BY CREATED_AT, JOB_ID FETCH FIRST 1 ROWS ONLY"
                )
                row = cursor.fetchone()
                if row is not None:
                    active = SchemaRefreshJob.model_validate_json(_read_lob(row[0]))
                    coalesced = _coalesce_pending_refresh_job(active, job)
                    if coalesced != active:
                        cursor.execute(
                            "UPDATE NL2SQL_SCHEMA_REFRESH_JOBS SET STATUS = :status, "
                            "PAYLOAD_JSON = :payload, UPDATED_AT = SYSTIMESTAMP "
                            "WHERE JOB_ID = :job_id",
                            {
                                "job_id": coalesced.job_id,
                                "status": coalesced.status.value,
                                "payload": _canonical_json(coalesced.model_dump(mode="json")),
                            },
                        )
                    connection.commit()
                    record_repository("schema_refresh_submit", statements=2, rows=1)
                    return coalesced
                cursor.execute(
                    "INSERT INTO NL2SQL_SCHEMA_REFRESH_JOBS "
                    "(JOB_ID, STATUS, PAYLOAD_JSON) VALUES (:job_id, :status, :payload)",
                    {
                        "job_id": job.job_id,
                        "status": job.status.value,
                        "payload": payload,
                    },
                )
                connection.commit()
            except Exception:
                rollback = getattr(connection, "rollback", None)
                if callable(rollback):
                    rollback()
                raise
        record_repository("schema_refresh_submit", statements=3, rows=1)
        return job.model_copy(deep=True)

    def claim_refresh_job(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        job_id: str | None = None,
    ) -> SchemaRefreshJob | None:
        now = datetime.now(UTC)
        lease_expires_at = datetime.fromtimestamp(now.timestamp() + max(30.0, lease_seconds), UTC)
        predicate = (
            "(STATUS = 'pending' OR (STATUS = 'running' AND "
            "(LEASE_EXPIRES_AT IS NULL OR LEASE_EXPIRES_AT <= SYSTIMESTAMP)))"
        )
        binds: dict[str, Any] = {
            "worker_id": worker_id,
            "heartbeat_at": now,
            "lease_expires_at": lease_expires_at,
        }
        if job_id is not None:
            predicate += " AND JOB_ID = :job_id"
            binds["job_id"] = job_id
        select_sql = (
            "SELECT JOB_ID, PAYLOAD_JSON FROM NL2SQL_SCHEMA_REFRESH_JOBS WHERE "  # nosec B608
            + predicate
            + " ORDER BY CREATED_AT, JOB_ID FOR UPDATE SKIP LOCKED"
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                # Oracle は row_limiting_clause と FOR UPDATE の併用を許可しない。
                # SKIP LOCKED は fetch 時に行を lock するため、driver の内部 fetch も
                # 1 行へ絞り、先頭の claim 対象だけを lock する。
                cursor.prefetchrows = 0
                cursor.arraysize = 1
                select_binds = {"job_id": job_id} if job_id is not None else {}
                cursor.execute(select_sql, select_binds)
                row = cursor.fetchone()
                if row is None:
                    connection.commit()
                    return None
                current = SchemaRefreshJob.model_validate_json(_read_lob(row[1]))
                claimed = current.model_copy(
                    update={
                        "status": SchemaRefreshJobStatus.RUNNING,
                        "started_at": current.started_at or now.isoformat(),
                        "worker_id": worker_id,
                        "heartbeat_at": now.isoformat(),
                        "lease_expires_at": lease_expires_at.isoformat(),
                        "attempt": current.attempt + 1,
                    }
                )
                cursor.execute(
                    "UPDATE NL2SQL_SCHEMA_REFRESH_JOBS SET STATUS = 'running', "
                    "WORKER_ID = :worker_id, HEARTBEAT_AT = :heartbeat_at, "
                    "LEASE_EXPIRES_AT = :lease_expires_at, ATTEMPT_NO = ATTEMPT_NO + 1, "
                    "PAYLOAD_JSON = :payload, UPDATED_AT = SYSTIMESTAMP WHERE JOB_ID = :job_id",
                    {
                        **binds,
                        "job_id": str(row[0]),
                        "payload": _canonical_json(claimed.model_dump(mode="json")),
                    },
                )
                connection.commit()
                return claimed
            except Exception:
                connection.rollback()
                raise

    def claim_document(
        self,
        collection: str,
        *,
        worker_id: str,
        lease_seconds: float,
        entity_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        predicate = "COLLECTION = :collection AND STATUS IN ('pending', 'running')"
        select_binds: dict[str, Any] = {"collection": collection}
        if entity_id is not None:
            predicate += " AND ENTITY_ID = :entity_id"
            select_binds["entity_id"] = entity_id
        select_sql = (
            "SELECT ENTITY_ID, PAYLOAD_JSON, PROFILE_ID FROM NL2SQL_STATE_DOCUMENTS "  # nosec B608
            "WHERE " + predicate + " ORDER BY CREATED_AT, ENTITY_ID FOR UPDATE SKIP LOCKED"
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                # Oracle は row_limiting_clause と FOR UPDATE の併用を許可しない。
                # SKIP LOCKED は fetch 時に行を lock するため、driver の内部 fetch も
                # 小さく絞り、claim 候補だけを短時間 lock する。
                cursor.prefetchrows = 0
                cursor.arraysize = 25
                cursor.execute(select_sql, select_binds)
                for row in cursor.fetchmany(25):
                    current = cast(dict[str, Any], json.loads(_read_lob(row[1])))
                    if not _state_document_claimable(current, now):
                        continue
                    claimed = _claimed_state_document(
                        current,
                        worker_id=worker_id,
                        lease_seconds=lease_seconds,
                        now=now,
                    )
                    payload_json = _canonical_json(claimed)
                    etag = hashlib.sha256(payload_json.encode()).hexdigest()
                    _set_clob_bind(cursor, "payload")
                    cursor.execute(
                        "UPDATE NL2SQL_STATE_DOCUMENTS SET PROFILE_ID = :profile_id, "
                        "STATUS = 'running', VERSION_NO = VERSION_NO + 1, ETAG = :etag, "
                        "PAYLOAD_JSON = :payload, UPDATED_AT = SYSTIMESTAMP "
                        "WHERE COLLECTION = :collection AND ENTITY_ID = :entity_id",
                        {
                            "collection": collection,
                            "entity_id": str(row[0]),
                            "profile_id": str(row[2] or ""),
                            "etag": etag,
                            "payload": payload_json,
                        },
                    )
                    self._bump_token(cursor, STATE_NAMESPACE)
                    connection.commit()
                    return claimed
                connection.commit()
                return None
            except Exception:
                connection.rollback()
                raise

    def put_document(
        self,
        collection: str,
        entity_id: str,
        payload: Mapping[str, Any],
        *,
        profile_id: str = "",
        status: str = "",
    ) -> None:
        payload_json = _canonical_json(dict(payload))
        etag = hashlib.sha256(payload_json.encode()).hexdigest()
        with self._connection_factory() as connection, connection.cursor() as cursor:
            _set_clob_bind(cursor, "payload")
            cursor.execute(
                "MERGE INTO NL2SQL_STATE_DOCUMENTS t USING (SELECT :collection COLLECTION, "
                ":entity_id ENTITY_ID FROM DUAL) s ON (t.COLLECTION = s.COLLECTION AND "
                "t.ENTITY_ID = s.ENTITY_ID) WHEN MATCHED THEN UPDATE SET "
                "t.PROFILE_ID = :profile_id, t.STATUS = :status, t.VERSION_NO = "
                "t.VERSION_NO + 1, t.ETAG = :etag, t.PAYLOAD_JSON = :payload, "
                "t.UPDATED_AT = SYSTIMESTAMP WHEN NOT MATCHED THEN INSERT "
                "(COLLECTION, ENTITY_ID, PROFILE_ID, STATUS, VERSION_NO, ETAG, PAYLOAD_JSON) "
                "VALUES (:collection, :entity_id, :profile_id, :status, 1, :etag, :payload)",
                {
                    "collection": collection,
                    "entity_id": entity_id,
                    "profile_id": profile_id,
                    "status": status,
                    "etag": etag,
                    "payload": payload_json,
                },
            )
            self._bump_token(cursor, STATE_NAMESPACE)
            connection.commit()

    def patch_document(
        self,
        collection: str,
        entity_id: str,
        updates: Mapping[str, Any],
        *,
        profile_id: str = "",
        status: str = "",
    ) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    "SELECT PAYLOAD_JSON, PROFILE_ID, STATUS FROM NL2SQL_STATE_DOCUMENTS "
                    "WHERE COLLECTION = :collection AND ENTITY_ID = :entity_id FOR UPDATE",
                    {"collection": collection, "entity_id": entity_id},
                )
                row = cursor.fetchone()
                if row is None:
                    connection.rollback()
                    return None
                current_payload = cast(dict[str, Any], json.loads(_read_lob(row[0])))
                current_payload.update(copy.deepcopy(dict(updates)))
                payload_json = _canonical_json(current_payload)
                etag = hashlib.sha256(payload_json.encode()).hexdigest()
                _set_clob_bind(cursor, "payload")
                cursor.execute(
                    "UPDATE NL2SQL_STATE_DOCUMENTS SET PROFILE_ID = :profile_id, "
                    "STATUS = :status, VERSION_NO = VERSION_NO + 1, ETAG = :etag, "
                    "PAYLOAD_JSON = :payload, UPDATED_AT = SYSTIMESTAMP "
                    "WHERE COLLECTION = :collection AND ENTITY_ID = :entity_id",
                    {
                        "collection": collection,
                        "entity_id": entity_id,
                        "profile_id": profile_id or str(row[1] or ""),
                        "status": status
                        or _state_document_status_value(
                            collection,
                            current_payload,
                            str(row[2] or ""),
                        ),
                        "etag": etag,
                        "payload": payload_json,
                    },
                )
                self._bump_token(cursor, STATE_NAMESPACE)
                connection.commit()
                return current_payload
            except Exception:
                connection.rollback()
                raise

    def replace_documents(
        self,
        collection: str,
        documents: Sequence[tuple[str, Mapping[str, Any], str, str]],
    ) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    "DELETE FROM NL2SQL_STATE_DOCUMENTS WHERE COLLECTION = :collection",
                    {"collection": collection},
                )
                _set_clob_bind(cursor, "payload")
                for entity_id, payload, profile_id, status in documents:
                    payload_json = _canonical_json(dict(payload))
                    etag = hashlib.sha256(payload_json.encode()).hexdigest()
                    cursor.execute(
                        "INSERT INTO NL2SQL_STATE_DOCUMENTS "
                        "(COLLECTION, ENTITY_ID, PROFILE_ID, STATUS, VERSION_NO, ETAG, "
                        "PAYLOAD_JSON) VALUES (:collection, :entity_id, :profile_id, "
                        ":status, 1, :etag, :payload)",
                        {
                            "collection": collection,
                            "entity_id": entity_id,
                            "profile_id": profile_id,
                            "status": status,
                            "etag": etag,
                            "payload": payload_json,
                        },
                    )
                self._bump_token(cursor, STATE_NAMESPACE)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def get_document(self, collection: str, entity_id: str) -> dict[str, Any] | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT PAYLOAD_JSON FROM NL2SQL_STATE_DOCUMENTS "
                "WHERE COLLECTION = :collection AND ENTITY_ID = :entity_id",
                {"collection": collection, "entity_id": entity_id},
            )
            row = cursor.fetchone()
            raw = _read_lob(row[0]) if row else ""
        record_repository(
            "state_document_detail",
            rows=1 if row else 0,
            clob_collection=collection,
            clob_bytes=len(raw.encode("utf-8")),
        )
        return cast(dict[str, Any], json.loads(raw)) if raw else None

    def delete_document(self, collection: str, entity_id: str) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM NL2SQL_STATE_DOCUMENTS WHERE COLLECTION = :collection "
                "AND ENTITY_ID = :entity_id",
                {"collection": collection, "entity_id": entity_id},
            )
            self._bump_token(cursor, STATE_NAMESPACE)
            connection.commit()

    def list_documents(
        self,
        collection: str,
        *,
        limit: int,
        profile_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        where = ["COLLECTION = :collection"]
        binds: dict[str, Any] = {"collection": collection, "limit": limit}
        if profile_id:
            where.append("PROFILE_ID = :profile_id")
            binds["profile_id"] = profile_id
        if status:
            where.append("STATUS = :status")
            binds["status"] = status
        sql = (
            "SELECT PAYLOAD_JSON FROM NL2SQL_STATE_DOCUMENTS WHERE "  # nosec B608
            + " AND ".join(where)
            + f" ORDER BY {_state_document_sort_sql(collection)} DESC, "  # nosec B608
            "ENTITY_ID DESC FETCH FIRST :limit ROWS ONLY"
        )
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(sql, binds)
            rows = cursor.fetchall()
            raw_rows = [_read_lob(row[0]) for row in rows]
        record_repository(
            "state_document_list",
            rows=len(rows),
            clob_collection=collection,
            clob_bytes=sum(len(raw.encode("utf-8")) for raw in raw_rows),
        )
        return [cast(dict[str, Any], json.loads(raw)) for raw in raw_rows]

    def list_documents_page(
        self,
        collection: str,
        *,
        cursor: str | None,
        limit: int,
        profile_id: str = "",
        status: str = "",
        query: str = "",
        payload_filters: Mapping[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, int]:
        decoded = _decode_cursor(cursor, 2)
        where = ["COLLECTION = :collection"]
        filter_binds: dict[str, Any] = {"collection": collection}
        if profile_id:
            where.append("PROFILE_ID = :profile_id")
            filter_binds["profile_id"] = profile_id
        if status:
            where.append("STATUS = :status")
            filter_binds["status"] = status
        if query.strip():
            if collection == "history":
                query_predicates = [
                    "INSTR(LOWER(JSON_VALUE(PAYLOAD_JSON, "
                    f"'$.{field}' RETURNING VARCHAR2(4000) NULL ON ERROR)), "
                    "LOWER(:query)) > 0"
                    for field in _HISTORY_QUERY_FIELDS
                ]
                where.append("(" + " OR ".join(query_predicates) + ")")
            else:
                where.append("DBMS_LOB.INSTR(LOWER(PAYLOAD_JSON), LOWER(:query)) > 0")
            filter_binds["query"] = query.strip()
        for index, (key, value) in enumerate((payload_filters or {}).items()):
            if not value:
                continue
            if not key.replace("_", "").isalnum():
                raise ValueError("payload filter key が不正です。")
            bind_name = f"payload_filter_{index}"
            where.append(
                "JSON_VALUE(PAYLOAD_JSON, "
                f"'$.{key}' RETURNING VARCHAR2(4000) NULL ON ERROR) = :{bind_name}"
            )
            filter_binds[bind_name] = value
        count_predicate = " AND ".join(where)
        count_sql = (
            f"SELECT COUNT(*) FROM NL2SQL_STATE_DOCUMENTS WHERE {count_predicate}"  # nosec B608
        )
        page_binds = dict(filter_binds)
        sort_expr = _state_document_sort_sql(collection)
        if decoded:
            where.append(
                f"({sort_expr} < :after_sort_key OR "  # nosec B608
                f"({sort_expr} = :after_sort_key AND ENTITY_ID < :after_entity_id))"
            )
            page_binds["after_sort_key"] = decoded[0]
            page_binds["after_entity_id"] = decoded[1]
        page_predicate = " AND ".join(where)
        page_sql = (
            f"SELECT PAYLOAD_JSON, {sort_expr} AS SORT_KEY, ENTITY_ID "  # nosec B608
            "FROM NL2SQL_STATE_DOCUMENTS WHERE "
            + page_predicate
            + f" ORDER BY {sort_expr} DESC, ENTITY_ID DESC FETCH FIRST :limit ROWS ONLY"  # nosec B608
        )
        with self._connection_factory() as connection, connection.cursor() as db_cursor:
            db_cursor.execute(count_sql, filter_binds)
            count_row = db_cursor.fetchone()
            total = int(count_row[0] or 0) if count_row else 0
            db_cursor.execute(
                page_sql,
                {**page_binds, "limit": limit + 1},
            )
            rows = db_cursor.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            raw_rows = [_read_lob(row[0]) for row in rows]
        items = [cast(dict[str, Any], json.loads(raw)) for raw in raw_rows]
        record_repository(
            "state_document_page",
            statements=2,
            rows=len(rows) + 1,
            clob_collection=collection,
            clob_bytes=sum(len(raw.encode("utf-8")) for raw in raw_rows),
        )
        next_cursor = None
        if has_more and rows:
            next_cursor = _encode_cursor(str(rows[-1][1]), str(rows[-1][2]))
        return items, next_cursor, total

    def _load_catalog_subset(self, owner: str, object_name: str) -> SchemaCatalog:
        binds = {"owner": owner, "object_name": object_name}
        with self._connection_factory() as connection, connection.cursor() as cursor:
            configure_clob_fetch_as_text(cursor)
            cursor.execute(
                "SELECT OWNER_NAME, OBJECT_NAME, OBJECT_TYPE, LOGICAL_NAME, COMMENTS, "
                "ROW_COUNT FROM NL2SQL_SCHEMA_OBJECTS WHERE OWNER_NAME = :owner "
                "AND OBJECT_NAME = :object_name",
                binds,
            )
            object_row = cursor.fetchone()
            if object_row is None:
                return SchemaCatalog(refreshed_at="", tables=[])
            cursor.execute(
                "SELECT COLUMN_NAME, LOGICAL_NAME, DATA_TYPE, NULLABLE, COMMENTS, "
                "SAMPLE_VALUES_JSON FROM NL2SQL_SCHEMA_COLUMNS WHERE OWNER_NAME = :owner "
                "AND OBJECT_NAME = :object_name ORDER BY COLUMN_POSITION",
                binds,
            )
            column_rows = cursor.fetchall()
            cursor.execute(
                "SELECT CONSTRAINT_TEXT, PAYLOAD_JSON FROM NL2SQL_SCHEMA_CONSTRAINTS "
                "WHERE OWNER_NAME = :owner AND OBJECT_NAME = :object_name "
                "ORDER BY CONSTRAINT_NAME",
                binds,
            )
            constraint_rows = cursor.fetchall()
            cursor.execute(
                "SELECT OWNER_NAME, VIEW_NAME, REFERENCED_OWNER, REFERENCED_NAME, "
                "REFERENCED_TYPE FROM NL2SQL_SCHEMA_DEPENDENCIES WHERE OWNER_NAME = :owner "
                "AND VIEW_NAME = :object_name ORDER BY REFERENCED_OWNER, REFERENCED_NAME",
                binds,
            )
            dependency_rows = cursor.fetchall()
            column_rows = [(*row[:5], _read_lob(row[5]) if row[5] else "") for row in column_rows]
            constraint_rows = [
                (row[0], _read_lob(row[1]) if row[1] else "") for row in constraint_rows
            ]
        detail_clob_bytes = sum(len(row[5].encode("utf-8")) for row in column_rows if row[5]) + sum(
            len(row[1].encode("utf-8")) for row in constraint_rows if row[1]
        )
        record_repository(
            "schema_detail",
            statements=4,
            rows=1 + len(column_rows) + len(constraint_rows) + len(dependency_rows),
            clob_collection="schema",
            clob_bytes=detail_clob_bytes,
        )
        table = SchemaTable(
            owner=str(object_row[0]),
            table_name=str(object_row[1]),
            table_type=str(object_row[2]).lower(),
            logical_name=str(object_row[3] or object_row[1]),
            comment=str(object_row[4] or ""),
            row_count=int(object_row[5]) if object_row[5] is not None else None,
            columns=[
                SchemaColumn(
                    column_name=str(row[0]),
                    logical_name=str(row[1] or row[0]),
                    data_type=str(row[2]),
                    nullable=bool(row[3]),
                    comment=str(row[4] or ""),
                    sample_values=json.loads(row[5]) if row[5] else [],
                )
                for row in column_rows
            ],
            constraints=[str(row[0] or "") for row in constraint_rows],
            constraint_details=[
                SchemaConstraintDetail.model_validate(json.loads(row[1])) for row in constraint_rows
            ],
        )
        dependencies = [
            SchemaViewDependency(
                owner=str(row[0]),
                view_name=str(row[1]),
                referenced_owner=str(row[2]),
                referenced_name=str(row[3]),
                referenced_type=str(row[4]),
            )
            for row in dependency_rows
        ]
        return SchemaCatalog(refreshed_at="", tables=[table], view_dependencies=dependencies)

    def _insert_schema_table(self, cursor: Any, table: SchemaTable, last_ddl_at: str) -> None:
        cursor.execute(
            "INSERT INTO NL2SQL_SCHEMA_OBJECTS (OWNER_NAME, OBJECT_NAME, OBJECT_TYPE, "
            "LOGICAL_NAME, COMMENTS, ROW_COUNT, COLUMN_COUNT, LAST_DDL_AT) VALUES "
            "(:owner, :object_name, :object_type, :logical_name, :comments, :row_count, "
            ":column_count, :last_ddl_at)",
            {
                "owner": table.owner.upper(),
                "object_name": table.table_name.upper(),
                "object_type": table.table_type.upper(),
                "logical_name": table.logical_name,
                "comments": table.comment,
                "row_count": table.row_count,
                "column_count": len(table.columns),
                "last_ddl_at": last_ddl_at,
            },
        )
        for position, column in enumerate(table.columns, start=1):
            cursor.execute(
                "INSERT INTO NL2SQL_SCHEMA_COLUMNS (OWNER_NAME, OBJECT_NAME, COLUMN_NAME, "
                "COLUMN_POSITION, LOGICAL_NAME, DATA_TYPE, NULLABLE, COMMENTS, "
                "SAMPLE_VALUES_JSON) VALUES (:owner, :object_name, :column_name, :position, "
                ":logical_name, :data_type, :nullable, :comments, :samples)",
                {
                    "owner": table.owner.upper(),
                    "object_name": table.table_name.upper(),
                    "column_name": column.column_name,
                    "position": position,
                    "logical_name": column.logical_name,
                    "data_type": column.data_type,
                    "nullable": 1 if column.nullable else 0,
                    "comments": column.comment,
                    "samples": _canonical_json(column.sample_values),
                },
            )
        for detail_index, detail in enumerate(table.constraint_details):
            constraint_text = (
                table.constraints[detail_index]
                if detail_index < len(table.constraints)
                else detail.constraint_name
            )
            cursor.execute(
                "INSERT INTO NL2SQL_SCHEMA_CONSTRAINTS (OWNER_NAME, OBJECT_NAME, "
                "CONSTRAINT_NAME, CONSTRAINT_TEXT, PAYLOAD_JSON) VALUES "
                "(:owner, :object_name, :constraint_name, :constraint_text, :payload)",
                {
                    "owner": table.owner.upper(),
                    "object_name": table.table_name.upper(),
                    "constraint_name": detail.constraint_name,
                    "constraint_text": constraint_text,
                    "payload": _canonical_json(detail.model_dump(mode="json")),
                },
            )

    @staticmethod
    def _summary_from_row(row: Sequence[Any]) -> ProfileSummary:
        return ProfileSummary(
            id=str(row[0]),
            name=str(row[1]),
            category=str(row[2] or ""),
            description=str(row[3] or ""),
            archived=bool(row[4]),
            allowed_table_count=int(row[5] or 0),
            allowed_view_count=int(row[6] or 0),
            glossary_count=int(row[7] or 0),
            few_shot_count=int(row[8] or 0),
            version=int(row[9]),
            etag=str(row[10]),
            updated_at=str(row[11]),
        )

    @staticmethod
    def _profile_binds(
        profile: Nl2SqlProfile,
        payload: Mapping[str, Any],
        version: int,
        etag: str,
        updated_at: str,
    ) -> dict[str, Any]:
        return {
            "profile_id": profile.id,
            "name": profile.name,
            "category": profile.category,
            "description": profile.description,
            "archived": 1 if profile.archived else 0,
            "allowed_table_count": len(profile.allowed_tables),
            "allowed_view_count": len(profile.allowed_views),
            "glossary_count": len(profile.glossary),
            "few_shot_count": len(profile.few_shot_examples),
            "version": version,
            "etag": etag,
            "payload": _canonical_json(payload),
            # Bind a native datetime so TIMESTAMP WITH TIME ZONE writes never
            # depend on the Oracle session's NLS timestamp format.
            "updated_at": datetime.fromisoformat(updated_at),
        }

    @staticmethod
    def _profile_insert_sql() -> str:
        return (
            "INSERT INTO NL2SQL_PROFILES (PROFILE_ID, NAME, CATEGORY, DESCRIPTION, ARCHIVED, "
            "ALLOWED_TABLE_COUNT, ALLOWED_VIEW_COUNT, GLOSSARY_COUNT, FEW_SHOT_COUNT, "
            "VERSION_NO, ETAG, PAYLOAD_JSON, UPDATED_AT) VALUES (:profile_id, :name, "
            ":category, :description, :archived, :allowed_table_count, :allowed_view_count, "
            ":glossary_count, :few_shot_count, :version, :etag, :payload, :updated_at)"
        )

    @staticmethod
    def _profile_update_sql() -> str:
        return (
            "UPDATE NL2SQL_PROFILES SET NAME = :name, CATEGORY = :category, "
            "DESCRIPTION = :description, ARCHIVED = :archived, "
            "ALLOWED_TABLE_COUNT = :allowed_table_count, "
            "ALLOWED_VIEW_COUNT = :allowed_view_count, GLOSSARY_COUNT = :glossary_count, "
            "FEW_SHOT_COUNT = :few_shot_count, VERSION_NO = :version, ETAG = :etag, "
            "PAYLOAD_JSON = :payload, UPDATED_AT = :updated_at WHERE PROFILE_ID = :profile_id"
        )

    @staticmethod
    def _bump_token(cursor: Any, namespace: str) -> None:
        cursor.execute(
            "MERGE INTO NL2SQL_CHANGE_TOKENS t USING (SELECT :namespace NAMESPACE FROM DUAL) s "
            "ON (t.NAMESPACE = s.NAMESPACE) WHEN MATCHED THEN UPDATE SET "
            "t.CHANGE_SEQ = t.CHANGE_SEQ + 1, t.UPDATED_AT = SYSTIMESTAMP "
            "WHEN NOT MATCHED THEN INSERT (NAMESPACE, CHANGE_SEQ) VALUES (:namespace, 1)",
            {"namespace": namespace},
        )

    @staticmethod
    def _change_token_with_cursor(cursor: Any, namespace: str) -> int:
        cursor.execute(
            "SELECT CHANGE_SEQ FROM NL2SQL_CHANGE_TOKENS WHERE NAMESPACE = :namespace",
            {"namespace": namespace},
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0


__all__ = [
    "IncrementalNl2SqlRepository",
    "IncrementalStoreError",
    "IncrementalStoreNotMigrated",
    "IncrementalVersionConflict",
    "MemoryIncrementalNl2SqlRepository",
    "OracleIncrementalNl2SqlRepository",
    "PROFILE_NAMESPACE",
    "SCHEMA_NAMESPACE",
    "STATE_NAMESPACE",
    "VersionedTtlCache",
]
