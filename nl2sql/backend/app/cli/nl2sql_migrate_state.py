"""Legacy CLOB snapshot から incremental NL2SQL store への idempotent migrator。"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.features.nl2sql.incremental_observability import record_outbox_lag
from app.features.nl2sql.incremental_store import OracleIncrementalNl2SqlRepository
from app.features.nl2sql.models import Nl2SqlProfile, SchemaCatalog
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.features.nl2sql.store import OracleJsonNl2SqlStore
from app.features.settings.system_schema import system_schema_manager
from app.settings import get_settings

logger = logging.getLogger(__name__)

_COLLECTION_IDENTITIES: dict[str, str] = {
    "jobs": "job_id",
    "history": "id",
    "classifier_examples": "id",
    "admin_audit": "id",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _snapshot_checksum(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()


def _read_lob(value: Any) -> str:
    read = getattr(value, "read", None)
    raw = read() if callable(read) else value
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw or "")


def _decode_snapshot_value(value: Any) -> dict[str, Any]:
    """Decode Oracle JSON values returned either as mappings or CLOB locators."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        parsed = json.loads(json.dumps(value, ensure_ascii=False))
        return parsed if isinstance(parsed, dict) else {}
    raw = _read_lob(value)
    parsed = json.loads(raw) if raw else {}
    return parsed if isinstance(parsed, dict) else {}


def _split_ddl(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip().rstrip(";").strip())
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return statements


def apply_ddl(adapter: OracleNl2SqlAdapter, migration_path: Path) -> int:
    """Migration artifact を再適用可能な形で実行する。"""

    statements = _split_ddl(migration_path.read_text(encoding="utf-8"))
    applied = 0
    with adapter.connection() as connection, connection.cursor() as cursor:
        try:
            for statement in statements:
                try:
                    cursor.execute(statement)
                    applied += 1
                except Exception as exc:
                    normalized = str(exc).upper()
                    if any(
                        code in normalized
                        for code in (
                            "ORA-00955",
                            "ORA-00001",
                            "ORA-01430",
                            "ORA-01442",
                            "ORA-01451",
                        )
                    ):
                        continue
                    raise
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return applied


def _migration_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    catalog = SchemaCatalog.model_validate(
        snapshot.get("catalog") or {"refreshed_at": "", "tables": []}
    )
    profiles = [Nl2SqlProfile.model_validate(item) for item in snapshot.get("profiles", [])]
    return {
        "checksum": _snapshot_checksum(snapshot),
        "profiles": len(profiles),
        "schema_objects": len(catalog.tables),
        "schema_columns": sum(len(table.columns) for table in catalog.tables),
        **{
            collection: len(snapshot.get(collection, []))
            for collection in _COLLECTION_IDENTITIES
        },
    }


def migrate_snapshot(
    repository: OracleIncrementalNl2SqlRepository,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Snapshot を entity 単位に展開し、同一 payload は再書込みしない。"""

    for raw in snapshot.get("profiles", []):
        profile = Nl2SqlProfile.model_validate(raw)
        current = repository.get_profile(profile.id)
        if current is not None:
            current_payload = current.model_dump(
                mode="json", exclude={"version", "etag", "updated_at"}
            )
            next_payload = profile.model_dump(
                mode="json", exclude={"version", "etag", "updated_at"}
            )
            if current_payload == next_payload:
                continue
        repository.save_profile(profile, expected_etag=current.etag if current else None)

    catalog = SchemaCatalog.model_validate(
        snapshot.get("catalog") or {"refreshed_at": "", "tables": []}
    )
    if catalog.tables:
        manifest = {
            (table.owner.upper(), table.table_name.upper()): catalog.refreshed_at
            for table in catalog.tables
        }
        current_manifest = repository.schema_manifest()
        incoming = set(manifest)
        repository.apply_schema_refresh(
            catalog=catalog,
            manifest=manifest,
            changed_keys={key for key in incoming if current_manifest.get(key) != manifest[key]},
            deleted_keys=set(current_manifest) - incoming,
        )

    for collection, identity_field in _COLLECTION_IDENTITIES.items():
        for index, raw in enumerate(snapshot.get(collection, [])):
            if not isinstance(raw, dict):
                continue
            entity_id = str(raw.get(identity_field) or f"legacy-{index}")
            repository.put_document(
                collection,
                entity_id,
                raw,
                profile_id=str(raw.get("profile_id") or ""),
                status=str(
                    raw.get("feedback_rating")
                    or ("unrated" if collection == "history" else raw.get("status") or "")
                ),
            )
    for key in (
        "feedback_search_config",
        "classifier_artifact",
        "asset_meta",
        "legacy_learning_material",
    ):
        value = snapshot.get(key)
        if value is not None:
            repository.put_document("singletons", key, {"value": value})
    return _migration_summary(snapshot)


def validate_migrated_snapshot(
    repository: OracleIncrementalNl2SqlRepository,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """ID、canonical payload、Pydantic contract を切替前に照合する。"""

    mismatches: list[str] = []
    for raw in snapshot.get("profiles", []):
        expected = Nl2SqlProfile.model_validate(raw)
        actual = repository.get_profile(expected.id)
        if actual is None:
            mismatches.append(f"profile:{expected.id}:missing")
            continue
        expected_payload = expected.model_dump(
            mode="json", exclude={"version", "etag", "updated_at"}
        )
        actual_payload = actual.model_dump(
            mode="json", exclude={"version", "etag", "updated_at"}
        )
        if _canonical_json(expected_payload) != _canonical_json(actual_payload):
            mismatches.append(f"profile:{expected.id}:checksum")

    expected_catalog = SchemaCatalog.model_validate(
        snapshot.get("catalog") or {"refreshed_at": "", "tables": []}
    )
    if expected_catalog.tables:
        actual_keys = set(repository.schema_manifest())
        expected_keys = {
            (table.owner.upper(), table.table_name.upper())
            for table in expected_catalog.tables
        }
        if actual_keys != expected_keys:
            mismatches.append("schema:object_ids")

    for collection, identity_field in _COLLECTION_IDENTITIES.items():
        for index, raw in enumerate(snapshot.get(collection, [])):
            if not isinstance(raw, dict):
                continue
            entity_id = str(raw.get(identity_field) or f"legacy-{index}")
            actual_document = repository.get_document(collection, entity_id)
            if actual_document is None:
                mismatches.append(f"{collection}:{entity_id}:missing")
            elif _canonical_json(actual_document) != _canonical_json(raw):
                mismatches.append(f"{collection}:{entity_id}:checksum")
    if mismatches:
        preview = ", ".join(mismatches[:20])
        raise RuntimeError(f"incremental migration validation failed: {preview}")
    return {**_migration_summary(snapshot), "validated": True}


def _load_snapshot_cut(
    adapter: OracleNl2SqlAdapter,
    *,
    table_name: str,
) -> tuple[dict[str, Any], int]:
    """Legacy snapshot と outbox high-water を同一 read-only transaction で取得する。"""

    with adapter.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            f"SELECT STATE_JSON FROM {table_name} WHERE STATE_KEY = :state_key",  # nosec B608
            {"state_key": "default"},
        )
        snapshot_row = cursor.fetchone()
        cursor.execute("SELECT COALESCE(MAX(OUTBOX_ID), 0) FROM NL2SQL_MIGRATION_OUTBOX")
        high_water_row = cursor.fetchone()
        # Oracle LOB locators are connection-bound.  Materialize the CLOB while
        # the read-only transaction is still open so the snapshot and outbox
        # high-water remain one consistent cut.
        snapshot = _decode_snapshot_value(snapshot_row[0]) if snapshot_row else {}
        high_water = int(high_water_row[0] or 0) if high_water_row else 0
    return snapshot, high_water


def _mark_outbox_through(adapter: OracleNl2SqlAdapter, high_water: int) -> None:
    if high_water <= 0:
        return
    with adapter.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE NL2SQL_MIGRATION_OUTBOX SET PROCESSED_AT = SYSTIMESTAMP "
            "WHERE OUTBOX_ID <= :high_water AND PROCESSED_AT IS NULL",
            {"high_water": high_water},
        )
        connection.commit()


def replay_migration_outbox(
    adapter: OracleNl2SqlAdapter,
    repository: OracleIncrementalNl2SqlRepository,
) -> tuple[int, int]:
    """高水位後の dual-write outbox を version 順に idempotent replay する。"""

    replayed = 0
    while True:
        with adapter.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT OUTBOX_ID, SNAPSHOT_CHECKSUM, STATE_JSON "
                "FROM NL2SQL_MIGRATION_OUTBOX WHERE PROCESSED_AT IS NULL "
                "ORDER BY OUTBOX_ID FETCH FIRST 1 ROWS ONLY"
            )
            row = cursor.fetchone()
        if row is None:
            break
        outbox_id = int(row[0])
        expected_checksum = str(row[1])
        raw = _read_lob(row[2])
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected_checksum:
            raise RuntimeError(f"migration outbox checksum mismatch: {outbox_id}")
        snapshot = json.loads(raw)
        migrate_snapshot(repository, snapshot)
        validate_migrated_snapshot(repository, snapshot)
        with adapter.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE NL2SQL_MIGRATION_OUTBOX SET PROCESSED_AT = SYSTIMESTAMP "
                "WHERE OUTBOX_ID = :outbox_id AND PROCESSED_AT IS NULL",
                {"outbox_id": outbox_id},
            )
            connection.commit()
        replayed += 1
    with adapter.connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM NL2SQL_MIGRATION_OUTBOX WHERE PROCESSED_AT IS NULL"
        )
        row = cursor.fetchone()
    lag = int(row[0] or 0) if row else 0
    record_outbox_lag(lag)
    return replayed, lag


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="validate and print counts only")
    parser.add_argument("--apply", action="store_true", help="migrate snapshot data")
    parser.add_argument(
        "--apply-ddl",
        action="store_true",
        help="apply versioned Ontology/incremental state migrations first",
    )
    args = parser.parse_args()
    if not any((args.dry_run, args.apply, args.apply_ddl)):
        parser.error("one of --dry-run, --apply, or --apply-ddl is required")

    settings = get_settings()
    adapter = OracleNl2SqlAdapter(settings)
    if args.apply_ddl:
        schema_result = system_schema_manager.initialize()
        print(  # noqa: T201
            _canonical_json(
                {
                    "operation": schema_result["operation"],
                    "applied_versions": schema_result["applied_versions"],
                    "schema_head": schema_result["schema_head"],
                }
            )
        )

    if args.dry_run or args.apply:
        legacy = OracleJsonNl2SqlStore(
            connection_factory=adapter.connection,
            table_name=settings.nl2sql_oracle_state_table,
        )
        if args.dry_run:
            snapshot = legacy.load_snapshot() or {}
            print(_canonical_json(_migration_summary(snapshot)))  # noqa: T201
        else:
            repository = OracleIncrementalNl2SqlRepository(
                connection_factory=adapter.connection
            )
            ok, detail = repository.check()
            if not ok:
                raise RuntimeError(detail)
            snapshot, high_water = _load_snapshot_cut(
                adapter,
                table_name=legacy.table_name,
            )
            migrate_snapshot(repository, snapshot)
            summary = validate_migrated_snapshot(repository, snapshot)
            _mark_outbox_through(adapter, high_water)
            replayed, lag = replay_migration_outbox(adapter, repository)
            print(  # noqa: T201
                _canonical_json(
                    {
                        **summary,
                        "outbox_high_water": high_water,
                        "outbox_replayed": replayed,
                        "outbox_lag": lag,
                    }
                )
            )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI boundary
    raise SystemExit(main())
