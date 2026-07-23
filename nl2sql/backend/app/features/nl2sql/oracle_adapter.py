"""Optional Oracle runtime adapter for NL2SQL.

この module は `oracledb` を import-time dependency にしない。local / CI は deterministic
adapter のまま動き、`NL2SQL_RUNTIME_MODE=oracle` のときだけ runtime import する。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.settings import Settings

from .models import (
    CsvImportColumn,
    ExplainPlanData,
    ExplainPlanOperation,
    QueryResults,
    SchemaCatalog,
    SchemaColumn,
    SchemaConstraintDetail,
    SchemaOwnersData,
    SchemaOwnerSummary,
    SchemaTable,
    SchemaViewDependency,
)
from .object_identity import parse_object_identity, qualified_object_name
from .object_visibility import (
    filter_user_visible_catalog,
    is_user_visible_object_name,
)


class OracleAdapterError(RuntimeError):
    """Oracle adapter の実行時エラー。"""


WALLET_PASSWORD_REQUIRED_ERROR = (
    "Oracle Wallet がパスワードを必要としています。"  # nosec B105
    "ORACLE_WALLET_PASSWORD または ORACLE_PASSWORD を設定してください。"
)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    read = getattr(value, "read", None)
    if callable(read):
        return str(read())
    return str(value)


def _coerce_result_value(value: Any) -> Any:
    read = getattr(value, "read", None)
    if callable(read):
        return _coerce_text(value)
    return value


def _select_ai_object_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    for key in (
        "name",
        "NAME",
        "object_name",
        "OBJECT_NAME",
        "table_name",
        "TABLE_NAME",
        "objectName",
        "tableName",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _select_ai_object_list_items(
    value: Any, *, candidate_scope: bool = False, depth: int = 0
) -> list[Any]:
    if value is None or depth > 6:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        with suppress(json.JSONDecodeError):
            return _select_ai_object_list_items(
                json.loads(text), candidate_scope=candidate_scope, depth=depth + 1
            )
        return [text] if candidate_scope else []
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            items.extend(_select_ai_object_list_items(item, candidate_scope=True, depth=depth + 1))
        return items
    if not isinstance(value, dict):
        return []
    if candidate_scope and _select_ai_object_name(value):
        return [value]

    collected: list[Any] = []
    for key in (
        "object_list",
        "OBJECT_LIST",
        "objectList",
        "objects",
        "OBJECTS",
        "tables",
        "TABLES",
    ):
        if key in value:
            collected.extend(
                _select_ai_object_list_items(value[key], candidate_scope=True, depth=depth + 1)
            )
    for key in (
        "attributes",
        "ATTRIBUTES",
        "profile_attributes",
        "PROFILE_ATTRIBUTES",
        "profileAttributes",
        "params",
        "PARAMS",
    ):
        if key in value:
            collected.extend(
                _select_ai_object_list_items(value[key], candidate_scope=False, depth=depth + 1)
            )
    return collected


def _normalize_select_ai_object_list(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _select_ai_object_list_items(value, candidate_scope=False):
        name = _select_ai_object_name(item)
        if not is_user_visible_object_name(name):
            continue
        record = dict(item) if isinstance(item, dict) else {"name": name}
        record.setdefault("name", name)
        for owner_key in ("owner", "OWNER", "schema", "SCHEMA"):
            owner = record.get(owner_key)
            if isinstance(owner, str) and owner.strip():
                record.setdefault("owner", owner.strip())
                break
        owner = str(record.get("owner") or "").strip().upper()
        key = f"{owner}.{name.upper()}"
        if key in seen:
            continue
        seen.add(key)
        normalized.append(record)
    return normalized


_FLEXIBLE_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y%m%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y年%m月%d日",
)


def _flexible_date_value(value: str) -> datetime | None:
    """CSV セル値を柔軟に datetime へ変換する(SQL Assist の _convert_to_date 再マップ)。"""
    text = str(value or "").strip()
    if not text:
        return None
    # 'YYYY-MM-DD HH:MM:SS.ffffff' のマイクロ秒は落として解釈する
    text = re.sub(r"\.\d+$", "", text)
    for fmt in _FLEXIBLE_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)  # noqa: DTZ007
        except ValueError:
            continue
    # Excel シリアル日付(1899-12-30 起点、9999-12-31 まで)
    try:
        serial = float(text)
    except ValueError:
        return None
    if 1 <= serial <= 2958465:
        return datetime(1899, 12, 30) + timedelta(days=serial)  # noqa: DTZ001
    return None


def _extract_select_statement(text: str) -> str:
    """LLM/Select AI response から最初の SELECT/WITH statement を抽出する。"""
    cleaned = text.strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("sql", "generated_sql", "query", "result"):
            candidate = str(payload.get(key) or "").strip()
            if candidate:
                extracted = _extract_select_statement(candidate)
                if extracted:
                    return extracted
    candidates: list[str] = []
    for match in re.finditer(r"\b(with|select)\b", cleaned, flags=re.IGNORECASE):
        candidate = cleaned[match.start() :].strip()
        if match.group(1).lower() == "with" or re.search(
            r"\bfrom\b", candidate, flags=re.IGNORECASE
        ):
            candidates.append(candidate)
    if not candidates:
        return ""
    statement = candidates[-1]
    error_match = re.search(r"\bException encountered\s*:", statement, flags=re.IGNORECASE)
    if error_match:
        statement = statement[: error_match.start()].strip()
    return statement.split(";", 1)[0].strip()


def _quote_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _strict_sql_name(value: str) -> str:
    normalized = value.strip().strip('"').upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", normalized):
        raise OracleAdapterError(f"安全でない Oracle object name です: {value}")
    return normalized


def _select_ai_feedback_index_names(profile_name: str) -> tuple[str, str, str]:
    safe_profile = _strict_sql_name(profile_name)
    index_name = f"{safe_profile}_FEEDBACK_VECINDEX"
    table_name = f"{index_name}$VECTAB"
    if len(index_name) > 128 or len(table_name) > 128:
        raise OracleAdapterError(f"feedback index 名が長すぎます: {profile_name}")
    return safe_profile, index_name, table_name


def oracle_connect_kwargs(
    settings: Settings,
    *,
    user: str | None = None,
    password: str | None = None,
) -> dict[str, object]:
    """python-oracledb connect に渡す共通 kwargs を作る。"""
    kwargs: dict[str, object] = {
        "user": user if user is not None else settings.oracle_user,
        "dsn": _oracle_connection_test_dsn(settings),
        "tcp_connect_timeout": settings.nl2sql_oracle_connect_timeout_seconds,
    }
    resolved_password = password if password is not None else settings.oracle_password
    if resolved_password.strip():
        kwargs["password"] = resolved_password
    _add_wallet_kwargs(settings, kwargs)
    return kwargs


def _oracle_connect_kwargs(settings: Settings) -> dict[str, object]:
    """後方互換 wrapper。"""
    return oracle_connect_kwargs(settings)


def _oracle_connection_test_dsn(settings: Settings) -> str:
    """Wallet alias の descriptor が取れれば、長い retry 設定を外して接続テストする。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return settings.oracle_dsn
    descriptor = _tns_alias_descriptor(Path(wallet_dir).expanduser(), settings.oracle_dsn)
    if not descriptor:
        return settings.oracle_dsn
    return _strip_tns_retry_settings(descriptor)


def _tns_alias_descriptor(wallet_path: Path, alias: str) -> str | None:
    """tnsnames.ora から指定 alias の connect descriptor を抜き出す。"""
    tnsnames = wallet_path / "tnsnames.ora"
    if not alias.strip() or not tnsnames.is_file():
        return None
    try:
        content = tnsnames.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for match in re.finditer(r"(?im)^\s*([A-Za-z0-9_.-]+)\s*=\s*", content):
        if match.group(1).lower() != alias.lower():
            continue
        descriptor_start = content.find("(", match.end())
        if descriptor_start < 0:
            return None
        return _balanced_parenthesized_text(content, descriptor_start)
    return None


def _balanced_parenthesized_text(content: str, start: int) -> str | None:
    """start 位置から始まる括弧式を top-level まで読み取る。"""
    depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
        if depth < 0:
            return None
    return None


def _strip_tns_retry_settings(descriptor: str) -> str:
    """ADB Wallet の長い retry 設定を接続テスト用に取り除く。"""
    without_retry_count = re.sub(r"\(\s*retry_count\s*=\s*\d+\s*\)", "", descriptor, flags=re.I)
    return re.sub(r"\(\s*retry_delay\s*=\s*\d+\s*\)", "", without_retry_count, flags=re.I)


def _add_wallet_kwargs(settings: Settings, kwargs: dict[str, object]) -> None:
    """Wallet 設定を kwargs に追加する。"""
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    if not wallet_dir:
        return

    wallet_path = Path(wallet_dir).expanduser()
    if not wallet_path.is_dir():
        return

    wallet_password = settings.oracle_wallet_password.strip() or settings.oracle_password.strip()
    if not wallet_password and _wallet_requires_password(wallet_path):
        raise OracleAdapterError(WALLET_PASSWORD_REQUIRED_ERROR)

    resolved_wallet_path = str(wallet_path)
    kwargs["config_dir"] = resolved_wallet_path
    kwargs["wallet_location"] = resolved_wallet_path
    if wallet_password:
        kwargs["wallet_password"] = wallet_password


def _wallet_dir_exists(settings: Settings) -> bool:
    wallet_dir = settings.resolved_oracle_wallet_dir.strip()
    return bool(wallet_dir and Path(wallet_dir).expanduser().is_dir())


def _wallet_requires_password(wallet_path: Path) -> bool:
    """自動ログイン Wallet がなく、秘密鍵が暗号化されていればパスワード必須。"""
    try:
        files = [path for path in wallet_path.iterdir() if path.is_file()]
    except OSError:
        return False
    names = {path.name.lower() for path in files}
    if "ewallet.p12" in names:
        return True
    encrypted_pem_exists = any(
        path.suffix.lower() == ".pem" and _pem_file_is_encrypted(path) for path in files
    )
    if encrypted_pem_exists:
        return True
    return "cwallet.sso" not in names


def _pem_file_is_encrypted(path: Path) -> bool:
    """暗号化 PEM の代表的な marker だけを少量読み取って判定する。"""
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    text = head.decode("utf-8", errors="ignore").upper()
    return "BEGIN ENCRYPTED PRIVATE KEY" in text or "PROC-TYPE: 4,ENCRYPTED" in text


class OracleNl2SqlAdapter:
    """Thin python-oracledb wrapper.

    実 SQL 生成・実行はここへ閉じ込める。呼び出し側 service は同じ API shape のまま
    deterministic / oracle runtime を切り替える。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._oracledb: Any | None = None
        self._client_initialized = False

    def is_configured(self) -> bool:
        if not self.settings.oracle_user.strip() or not self.settings.oracle_dsn.strip():
            return False
        return bool(self.settings.oracle_password.strip() or _wallet_dir_exists(self.settings))

    def module_available(self) -> bool:
        try:
            self._load_oracledb()
        except OracleAdapterError:
            return False
        return True

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "Oracle 接続情報が不足しています。"
        try:
            with self.connection() as conn, conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM DUAL")
                cursor.fetchone()
            return True, "Oracle 接続に成功しました。"
        except Exception as exc:
            return False, f"Oracle 接続に失敗しました: {exc}"

    @contextmanager
    def connection(self) -> Iterator[Any]:
        oracledb = self._load_oracledb()
        self._init_client(oracledb)
        if not self.is_configured():
            raise OracleAdapterError("Oracle 接続情報が不足しています。")
        try:
            conn = oracledb.connect(**_oracle_connect_kwargs(self.settings))
        except OracleAdapterError:
            raise
        except Exception as exc:  # oracledb.DatabaseError/OperationalError 等を統一契約に変換
            raise OracleAdapterError(f"Oracle 接続に失敗しました: {exc}") from exc
        try:
            # python-oracledb call_timeout は 1 round-trip 単位の millisecond。
            # 0 (無期限) を避け、DBMS_CLOUD_AI/Agent PL/SQL が worker を占有し続けないようにする。
            call_timeout_ms = int(max(1.0, self.settings.nl2sql_oracle_call_timeout_seconds) * 1000)
            if hasattr(conn, "call_timeout"):
                conn.call_timeout = call_timeout_ms
            try:
                yield conn
            except Exception:
                rollback = getattr(conn, "rollback", None)
                if callable(rollback):
                    rollback()
                raise
        finally:
            conn.close()

    @contextmanager
    def user_data_connection(self) -> Iterator[Any]:
        """認証済み actor のデータ処理にだけ共有 DeepSec END USER を使う。"""
        if not self.settings.oracle_deepsec_enabled:
            with self.connection() as connection:
                yield connection
            return
        from app.clients.oracle_runtime import get_oracle_pool_manager
        from app.security.request_actor import current_actor_user_id

        actor_user_id = current_actor_user_id()
        if not actor_user_id:
            raise OracleAdapterError(
                "DeepSec データ接続には認証済み application user が必要です。"
            )
        with get_oracle_pool_manager().data_connection(actor_user_id) as connection:
            yield connection

    def fetch_catalog(
        self,
        *,
        include_samples: bool = True,
        object_keys: set[tuple[str, str]] | None = None,
    ) -> SchemaCatalog:
        owner_filter, owner_binds = self._schema_owner_filter("c.owner")
        target_filter = ""
        target_binds: dict[str, str] = {}
        if object_keys:
            target_parts: list[str] = []
            for index, (owner, object_name) in enumerate(sorted(object_keys)):
                owner_key = f"target_owner_{index}"
                name_key = f"target_name_{index}"
                target_parts.append(f"(c.owner = :{owner_key} AND c.table_name = :{name_key})")
                target_binds[owner_key] = owner.upper()
                target_binds[name_key] = object_name.upper()
            target_filter = " AND (" + " OR ".join(target_parts) + ")"
        sql = f"""
            SELECT
                c.owner,
                c.table_name,
                NVL(tc.comments, c.table_name) AS table_comment,
                c.column_name,
                NVL(cc.comments, c.column_name) AS column_comment,
                c.data_type,
                c.nullable,
                c.column_id,
                t.num_rows,
                NVL(o.object_type, 'TABLE') AS object_type
            FROM all_tab_columns c
            LEFT JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name
            LEFT JOIN all_objects o
              ON o.owner = c.owner
             AND o.object_name = c.table_name
             AND o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
            LEFT JOIN all_tab_comments tc ON tc.owner = c.owner AND tc.table_name = c.table_name
            LEFT JOIN all_col_comments cc
              ON cc.owner = c.owner
             AND cc.table_name = c.table_name
             AND cc.column_name = c.column_name
            WHERE {owner_filter}
              AND c.table_name NOT LIKE '%$%'
              AND c.table_name NOT LIKE '%#%'
              {target_filter}
            ORDER BY c.owner, c.table_name, c.column_id
        """
        tables: dict[str, SchemaTable] = {}
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql, {**owner_binds, **target_binds})
            for row in cursor:
                owner = str(row[0] or "APP")
                table_name = str(row[1])
                table_comment = str(row[2] or table_name)
                column_name = str(row[3])
                column_comment = str(row[4] or column_name)
                data_type = str(row[5])
                nullable = str(row[6]).upper() == "Y"
                row_count = int(row[8]) if row[8] is not None else None
                table_type = str(row[9]).lower() if len(row) > 9 and row[9] else "table"
                table_key = f"{owner.upper()}.{table_name.upper()}"
                table = tables.setdefault(
                    table_key,
                    SchemaTable(
                        table_name=table_name,
                        logical_name=table_comment,
                        owner=owner,
                        table_type=table_type,
                        comment=table_comment,
                        row_count=row_count,
                    ),
                )
                table.columns.append(
                    SchemaColumn(
                        column_name=column_name,
                        logical_name=column_comment,
                        data_type=data_type,
                        nullable=nullable,
                    )
                )
            self._load_constraints(cursor, tables, object_keys=object_keys)
            view_dependencies = self._load_view_dependencies(cursor, object_keys=object_keys)
            # 全 catalog refresh で全表を走査しない。sample は profile 選択時または
            # object detail 展開時に fetch_metadata_sample_values から遅延取得する。
            if include_samples:
                self._load_sample_values(cursor, tables)
        catalog = filter_user_visible_catalog(SchemaCatalog(
            refreshed_at=datetime.now(UTC).isoformat(),
            tables=list(tables.values()),
            view_dependencies=view_dependencies,
        ))
        catalog.schema_fingerprint = self._schema_fingerprint(catalog)
        return catalog

    def fetch_schema_owners(self) -> SchemaOwnersData:
        """ALL_* から現在ユーザーが実際に参照できる業務 schema を列挙する。"""

        allowlist = self._configured_schema_owner_allowlist()
        sql = """
            SELECT
                o.owner,
                NVL(u.oracle_maintained, 'N') AS oracle_maintained,
                MAX(CASE WHEN o.owner = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
                         THEN 1 ELSE 0 END) AS is_current,
                COUNT(DISTINCT CASE WHEN o.object_type = 'TABLE'
                                    THEN o.object_name END) AS table_count,
                COUNT(DISTINCT CASE WHEN o.object_type IN ('VIEW', 'MATERIALIZED VIEW')
                                    THEN o.object_name END) AS view_count
            FROM all_objects o
            JOIN all_users u ON u.username = o.owner
            WHERE o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
              AND o.status = 'VALID'
              AND o.object_name NOT LIKE '%$%'
              AND o.object_name NOT LIKE '%#%'
            GROUP BY o.owner, NVL(u.oracle_maintained, 'N')
            ORDER BY o.owner
        """
        owners: list[SchemaOwnerSummary] = []
        excluded_maintained: set[str] = set()
        current_owner = self.settings.oracle_user.strip().upper()
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql)
            for row in cursor:
                owner = str(row[0] or "").upper()
                oracle_maintained = str(row[1] or "N").upper() == "Y"
                is_current = bool(int(row[2] or 0))
                if is_current:
                    current_owner = owner
                if oracle_maintained:
                    excluded_maintained.add(owner)
                    continue
                if allowlist and owner not in allowlist:
                    continue
                owners.append(
                    SchemaOwnerSummary(
                        owner=owner,
                        is_current=is_current,
                        table_count=int(row[3] or 0),
                        view_count=int(row[4] or 0),
                    )
                )
        return SchemaOwnersData(
            current_owner=current_owner,
            owners=owners,
            excluded_oracle_maintained_count=len(excluded_maintained),
        )

    def fetch_catalog_objects(self, object_keys: set[tuple[str, str]]) -> SchemaCatalog:
        """変更された object だけを詳細取得する（Oracle bind 数を bounded に保つ）。"""

        if not object_keys:
            return SchemaCatalog(refreshed_at=datetime.now(UTC).isoformat(), tables=[])
        tables: list[SchemaTable] = []
        dependencies: dict[tuple[str, str, str, str], SchemaViewDependency] = {}
        ordered = sorted(object_keys)
        for offset in range(0, len(ordered), 250):
            partial = self.fetch_catalog(
                include_samples=False,
                object_keys=set(ordered[offset : offset + 250]),
            )
            tables.extend(partial.tables)
            for dependency in partial.view_dependencies:
                key = (
                    dependency.owner,
                    dependency.view_name,
                    dependency.referenced_owner,
                    dependency.referenced_name,
                )
                dependencies[key] = dependency
        catalog = SchemaCatalog(
            refreshed_at=datetime.now(UTC).isoformat(),
            tables=tables,
            view_dependencies=list(dependencies.values()),
        )
        catalog.schema_fingerprint = self._schema_fingerprint(catalog)
        return catalog

    def fetch_schema_manifest(self) -> dict[tuple[str, str], str]:
        """ALL_OBJECTS から軽量 change manifest だけを取得する。"""

        owner_filter, owner_binds = self._schema_owner_filter("o.owner")
        sql = f"""
            SELECT o.owner, o.object_name, o.last_ddl_time
            FROM all_objects o
            WHERE {owner_filter}
              AND o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
              AND o.status = 'VALID'
              AND o.object_name NOT LIKE '%$%'
              AND o.object_name NOT LIKE '%#%'
            ORDER BY o.owner, o.object_name
        """
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql, owner_binds)
            return {
                (str(owner).upper(), str(object_name).upper()): (
                    last_ddl_time.isoformat()
                    if hasattr(last_ddl_time, "isoformat")
                    else str(last_ddl_time or "")
                )
                for owner, object_name, last_ddl_time in cursor
            }

    def catalog_fingerprint(self, catalog: SchemaCatalog) -> str:
        """Refresh worker 用の public deterministic fingerprint。"""

        return self._schema_fingerprint(catalog)

    def _configured_schema_owner_allowlist(self) -> set[str]:
        return {
            owner.strip().upper()
            for owner in self.settings.nl2sql_schema_owner_allowlist
            if owner.strip()
        }

    def _schema_owner_filter(self, column_sql: str) -> tuple[str, dict[str, str]]:
        owners = self._configured_schema_owner_allowlist()
        business_owner_filter = (
            "EXISTS (SELECT 1 FROM all_users nl2sql_owner "
            f"WHERE nl2sql_owner.username = {column_sql} "
            "AND NVL(nl2sql_owner.oracle_maintained, 'N') = 'N')"
        )
        if not owners:
            return business_owner_filter, {}
        binds = {f"owner_{index}": owner for index, owner in enumerate(sorted(owners))}
        placeholders = ", ".join(f":{name}" for name in binds)
        return f"{business_owner_filter} AND {column_sql} IN ({placeholders})", binds

    def _load_constraints(
        self,
        cursor: Any,
        tables: dict[str, SchemaTable],
        *,
        object_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        owner_filter, owner_binds = self._schema_owner_filter("uc.owner")
        target_filter, target_binds = self._object_key_filter(
            "uc.owner", "uc.table_name", object_keys, prefix="constraint_target"
        )
        cursor.execute(
            f"""
            SELECT
                uc.table_name,
                uc.constraint_name,
                uc.constraint_type,
                LISTAGG(ucc.column_name, ', ') WITHIN GROUP (ORDER BY ucc.position) AS columns,
                uc.owner AS owner_name,
                ruc.owner AS referenced_owner,
                ruc.table_name AS referenced_table,
                LISTAGG(rucc.column_name, ', ') WITHIN GROUP (ORDER BY rucc.position)
                    AS referenced_columns,
                uc.delete_rule,
                uc.status,
                uc.deferrable
            FROM all_constraints uc
            LEFT JOIN all_cons_columns ucc
              ON ucc.owner = uc.owner
             AND ucc.constraint_name = uc.constraint_name
             AND ucc.table_name = uc.table_name
            LEFT JOIN all_constraints ruc
              ON ruc.owner = uc.r_owner
             AND ruc.constraint_name = uc.r_constraint_name
            LEFT JOIN all_cons_columns rucc
              ON rucc.owner = ruc.owner
             AND rucc.constraint_name = ruc.constraint_name
             AND rucc.table_name = ruc.table_name
             AND rucc.position = ucc.position
            WHERE {owner_filter}
              {target_filter}
              AND uc.constraint_type IN ('P', 'R', 'U', 'C')
            GROUP BY uc.table_name, uc.constraint_name, uc.constraint_type,
                     uc.owner, ruc.owner, ruc.table_name,
                     uc.delete_rule, uc.status, uc.deferrable
            ORDER BY uc.table_name, uc.constraint_name
            """,
            {**owner_binds, **target_binds},
        )
        for row in cursor:
            table_name, constraint_name, constraint_type, columns = row[:4]
            owner = str(row[4]) if len(row) > 4 and row[4] else "APP"
            table = tables.get(f"{owner.upper()}.{str(table_name).upper()}")
            if not table:
                continue
            column_text = str(columns or "").strip()
            suffix = f"({column_text})" if column_text else ""
            table.constraints.append(f"{constraint_name} {constraint_type}{suffix}")
            referenced_owner = str(row[5]) if len(row) > 5 and row[5] else None
            referenced_table = str(row[6]) if len(row) > 6 and row[6] else None
            referenced_columns_text = str(row[7] or "") if len(row) > 7 else ""
            table.constraint_details.append(
                SchemaConstraintDetail(
                    constraint_name=str(constraint_name),
                    constraint_type=str(constraint_type),
                    owner=owner,
                    table_name=str(table_name),
                    columns=[value.strip() for value in column_text.split(",") if value.strip()],
                    referenced_owner=referenced_owner,
                    referenced_table=referenced_table,
                    referenced_columns=[
                        value.strip()
                        for value in referenced_columns_text.split(",")
                        if value.strip()
                    ],
                    delete_rule=str(row[8]) if len(row) > 8 and row[8] else "NO ACTION",
                    status=str(row[9]) if len(row) > 9 and row[9] else "ENABLED",
                    deferrable=(str(row[10]) if len(row) > 10 and row[10] else "NOT DEFERRABLE"),
                )
            )

    def _load_view_dependencies(
        self,
        cursor: Any,
        *,
        object_keys: set[tuple[str, str]] | None = None,
    ) -> list[SchemaViewDependency]:
        owner_filter, owner_binds = self._schema_owner_filter("d.owner")
        target_filter, target_binds = self._object_key_filter(
            "d.owner", "d.name", object_keys, prefix="dependency_target"
        )
        cursor.execute(
            f"""
            SELECT
                d.owner AS owner_name,
                d.name AS view_name,
                d.referenced_owner,
                d.referenced_name,
                d.referenced_type
            FROM all_dependencies d
            WHERE d.type IN ('VIEW', 'MATERIALIZED VIEW')
              AND d.referenced_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
              AND {owner_filter}
              {target_filter}
            ORDER BY d.name, d.referenced_owner, d.referenced_name
            """,
            {**owner_binds, **target_binds},
        )
        return [
            SchemaViewDependency(
                owner=str(owner),
                view_name=str(view_name),
                referenced_owner=str(referenced_owner),
                referenced_name=str(referenced_name),
                referenced_type=str(referenced_type),
            )
            for owner, view_name, referenced_owner, referenced_name, referenced_type in cursor
        ]

    @staticmethod
    def _object_key_filter(
        owner_column: str,
        name_column: str,
        object_keys: set[tuple[str, str]] | None,
        *,
        prefix: str,
    ) -> tuple[str, dict[str, str]]:
        """変更 object の metadata 関連 query を同じ bounded batch に限定する。"""

        if not object_keys:
            return "", {}
        binds: dict[str, str] = {}
        clauses: list[str] = []
        for index, (owner, object_name) in enumerate(sorted(object_keys)):
            owner_key = f"{prefix}_owner_{index}"
            name_key = f"{prefix}_name_{index}"
            binds[owner_key] = owner.upper()
            binds[name_key] = object_name.upper()
            clauses.append(
                f"({owner_column} = :{owner_key} AND {name_column} = :{name_key})"
            )
        return "AND (" + " OR ".join(clauses) + ")", binds

    def _schema_fingerprint(self, catalog: SchemaCatalog) -> str:
        payload = {
            "tables": [
                {
                    "owner": table.owner.upper(),
                    "name": table.table_name.upper(),
                    "type": table.table_type.upper(),
                    "columns": [
                        (column.column_name.upper(), column.data_type.upper(), column.nullable)
                        for column in table.columns
                    ],
                    "constraints": [
                        detail.model_dump(mode="json") for detail in table.constraint_details
                    ],
                }
                for table in catalog.tables
            ],
            "view_dependencies": [
                dependency.model_dump(mode="json") for dependency in catalog.view_dependencies
            ],
        }
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _load_sample_values(self, cursor: Any, tables: dict[str, SchemaTable]) -> None:
        sample_rows = max(self.settings.nl2sql_schema_sample_rows, 0)
        sample_columns = max(self.settings.nl2sql_schema_sample_columns_per_table, 0)
        if sample_rows == 0 or sample_columns == 0:
            return
        for table in tables.values():
            quoted_table = (
                f"{_quote_identifier(table.owner)}.{_quote_identifier(table.table_name)}"
                if table.owner
                else _quote_identifier(table.table_name)
            )
            for column in table.columns[:sample_columns]:
                quoted_column = _quote_identifier(column.column_name)
                try:
                    # Safe: identifiers come from Oracle catalog metadata and are quoted.
                    sample_sql = (
                        f"SELECT DISTINCT {quoted_column} "  # nosec B608
                        f"FROM {quoted_table} "
                        f"WHERE {quoted_column} IS NOT NULL "
                        "FETCH FIRST :sample_rows ROWS ONLY"
                    )
                    cursor.execute(
                        sample_sql,
                        {"sample_rows": sample_rows},
                    )
                    column.sample_values = [
                        _coerce_text(row[0]) for row in cursor if _coerce_text(row[0])
                    ]
                except Exception:
                    column.sample_values = []

    def fetch_metadata_sample_values(
        self, targets: list[dict[str, Any]], sample_limit: int
    ) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
        """選択済み table/view の列代表値を、生成時の指定件数で取得する。"""
        if sample_limit <= 0:
            return {}, []

        samples: dict[str, dict[str, list[str]]] = {}
        warnings: list[str] = []
        try:
            with self.user_data_connection() as conn, conn.cursor() as cursor:
                for target in targets:
                    raw_object_name = str(target.get("object_name") or "")
                    raw_owner = str(target.get("owner") or "")
                    identity = parse_object_identity(
                        raw_object_name,
                        default_owner=raw_owner or self.settings.oracle_user,
                    )
                    owner = _strict_sql_name(identity.owner)
                    object_name = _strict_sql_name(identity.object_name)
                    requested_columns = [
                        _strict_sql_name(str(column))
                        for column in target.get("columns", [])
                        if str(column).strip()
                    ]
                    cursor.execute(
                        """
                        SELECT column_name FROM all_tab_columns
                        WHERE owner = :owner AND table_name = :object_name
                        ORDER BY column_id
                        """,
                        {"owner": owner, "object_name": object_name},
                    )
                    available_columns = {str(row[0]).upper() for row in cursor}
                    columns = requested_columns or list(available_columns)
                    columns = [column for column in columns if column in available_columns]
                    skipped_columns = set(requested_columns) - available_columns
                    if skipped_columns:
                        warnings.append(
                            f"{object_name}: 存在しない列を除外しました: "
                            + ", ".join(sorted(skipped_columns))
                        )
                    if not columns:
                        warnings.append(f"{object_name}: サンプル取得可能な列がありません。")
                        continue
                    object_samples: dict[str, list[str]] = {}
                    qualified_name = qualified_object_name(owner, object_name)
                    quoted_object = f"{_quote_identifier(owner)}.{_quote_identifier(object_name)}"
                    for column in columns:
                        try:
                            quoted_column = _quote_identifier(column)
                            cursor.execute(
                                (
                                    f"SELECT DISTINCT {quoted_column} "  # nosec B608
                                    f"FROM {quoted_object} "
                                    f"WHERE {quoted_column} IS NOT NULL "
                                    "FETCH FIRST :sample_rows ROWS ONLY"
                                ),
                                {"sample_rows": sample_limit},
                            )
                            values = [_coerce_text(row[0]) for row in cursor]
                            values = [value for value in values if value]
                            if values:
                                object_samples[column] = values
                        except Exception as exc:
                            warnings.append(
                                f"{qualified_name}.{column}: サンプル取得に失敗しました: {exc}"
                            )
                    if object_samples:
                        samples[qualified_name] = object_samples
        except Exception as exc:
            if isinstance(exc, OracleAdapterError):
                raise
            raise OracleAdapterError(f"生成用サンプルの取得に失敗しました: {exc}") from exc
        return samples, warnings

    def execute_select(self, sql: str, max_rows: int | None) -> QueryResults:
        try:
            with self.user_data_connection() as conn, conn.cursor() as cursor:
                cursor.execute(sql)
                columns = [description[0] for description in cursor.description or []]
                rows: list[dict[str, Any]] = []
                fetched_rows = cursor.fetchmany(max_rows) if max_rows else cursor.fetchall()
                for row in fetched_rows:
                    rows.append(
                        {
                            columns[index]: _coerce_result_value(value)
                            for index, value in enumerate(row)
                        }
                    )
            return QueryResults(columns=columns, rows=rows, total=len(rows))
        except OracleAdapterError:
            raise
        except Exception as exc:
            raise OracleAdapterError(f"SELECT の実行に失敗しました: {exc}") from exc

    def explain_select(self, sql: str) -> ExplainPlanData:
        """Oracle PLAN_TABLE から cost/cardinality と full scan を要約する。"""

        statement_id = f"NL2SQL_{hashlib.sha256(sql.encode()).hexdigest()[:20].upper()}"
        normalized = sql.strip().rstrip(";")
        try:
            with self.user_data_connection() as conn, conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM PLAN_TABLE WHERE statement_id = :statement_id",
                    {"statement_id": statement_id},
                )
                # EXPLAIN PLAN は DDL のため bind 変数を受け付けない。statement_id は
                # SHA-256 由来の英数字だけに限定し、SQL 本体は sqlglot の read-only
                # gate 通過後だけ渡される。
                cursor.execute(  # nosec B608
                    f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {normalized}",
                )
                cursor.execute(
                    """
                    SELECT operation, options, object_owner, object_name,
                           cost, cardinality, bytes
                    FROM plan_table
                    WHERE statement_id = :statement_id
                    ORDER BY id
                    """,
                    {"statement_id": statement_id},
                )
                operations = [
                    ExplainPlanOperation(
                        operation=str(row[0] or ""),
                        options=str(row[1] or ""),
                        owner=str(row[2] or ""),
                        object_name=str(row[3] or ""),
                        cost=int(row[4]) if row[4] is not None else None,
                        cardinality=int(row[5]) if row[5] is not None else None,
                        bytes=int(row[6]) if row[6] is not None else None,
                    )
                    for row in cursor
                ]
                cursor.execute(
                    "DELETE FROM PLAN_TABLE WHERE statement_id = :statement_id",
                    {"statement_id": statement_id},
                )
                conn.commit()
        except Exception as exc:
            return ExplainPlanData(
                available=False,
                warning=f"Oracle EXPLAIN PLAN を利用できません: {exc}",
            )
        root = operations[0] if operations else None
        full_scans = sorted(
            {
                operation.object_name
                for operation in operations
                if operation.operation.upper() == "TABLE ACCESS"
                and "FULL" in operation.options.upper()
                and operation.object_name
            }
        )
        return ExplainPlanData(
            available=bool(operations),
            total_cost=root.cost if root else None,
            estimated_cardinality=root.cardinality if root else None,
            full_table_scans=full_scans,
            operations=operations,
            warning="" if operations else "PLAN_TABLE に実行計画が生成されませんでした。",
        )

    def list_db_admin_objects(self, object_type: str) -> list[dict[str, Any]]:
        """List user tables or views for the DB admin console."""
        normalized_type = "view" if object_type.lower() == "view" else "table"
        if normalized_type == "view":
            sql = """
                SELECT v.view_name, USER, NULL, NVL(c.comments, ' ')
                FROM user_views v
                LEFT JOIN user_tab_comments c ON c.table_name = v.view_name
                WHERE v.view_name NOT LIKE '%$%'
                  AND v.view_name NOT LIKE '%#%'
                ORDER BY v.view_name
            """
        else:
            sql = """
                SELECT t.table_name, USER, t.num_rows, NVL(c.comments, ' ')
                FROM user_tables t
                LEFT JOIN user_tab_comments c ON c.table_name = t.table_name
                WHERE t.table_name NOT LIKE '%$%'
                  AND t.table_name NOT LIKE '%#%'
                ORDER BY t.table_name
            """
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        return [
            {
                "name": str(row[0] or ""),
                "owner": str(row[1] or ""),
                "object_type": normalized_type,
                "row_count": int(row[2]) if row[2] is not None else None,
                "comment": _coerce_text(row[3]) if len(row) > 3 else "",
            }
            for row in rows
            if is_user_visible_object_name(str(row[0] or ""))
        ]

    def get_db_admin_object_detail(
        self,
        *,
        object_name: str,
        object_type: str,
        include_ddl: bool = True,
        exact_count: bool = False,
    ) -> dict[str, Any]:
        """Return columns and DBMS_METADATA DDL for a table/view.

        include_ddl=False のときは重い DBMS_METADATA.GET_DDL を実行せず ddl="" を返す
        (列一覧の初期表示を高速化。DDL は DDL タブ表示時に別途取得する)。
        exact_count=False のときは全表スキャンの COUNT(*) を実行せず row_count=None を返す
        (件数は呼び出し側が num_rows 統計で補完。正確件数は exact_count=True 時のみ)。
        """
        safe_name = _strict_sql_name(object_name)
        normalized_type = "VIEW" if object_type.lower() == "view" else "TABLE"
        columns: list[SchemaColumn] = []
        warnings: list[str] = []
        comment = ""
        row_count: int | None = None
        ddl = ""
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.column_name,
                       c.data_type ||
                       CASE
                         WHEN c.data_type IN ('VARCHAR2','CHAR','NVARCHAR2','NCHAR')
                         THEN '(' || c.data_length || ')'
                         WHEN c.data_type = 'NUMBER' AND c.data_precision IS NOT NULL
                         THEN '(' || c.data_precision ||
                              CASE WHEN c.data_scale > 0 THEN ',' || c.data_scale ELSE '' END || ')'
                         ELSE ''
                       END AS data_type,
                       c.nullable,
                       NVL(cc.comments, ' ')
                FROM user_tab_columns c
                LEFT JOIN user_col_comments cc
                  ON cc.table_name = c.table_name AND cc.column_name = c.column_name
                WHERE c.table_name = :object_name
                ORDER BY c.column_id
                """,
                {"object_name": safe_name},
            )
            for column_name, data_type, nullable, column_comment in cursor:
                columns.append(
                    SchemaColumn(
                        column_name=str(column_name or ""),
                        logical_name=_coerce_text(column_comment) or str(column_name or ""),
                        data_type=str(data_type or ""),
                        nullable=str(nullable or "Y").upper() == "Y",
                        comment=_coerce_text(column_comment),
                    )
                )
            cursor.execute(
                "SELECT comments FROM user_tab_comments WHERE table_name = :object_name",
                {"object_name": safe_name},
            )
            row = cursor.fetchone()
            comment = _coerce_text(row[0]) if row and row[0] else ""
            if normalized_type == "TABLE" and exact_count:
                try:
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {_quote_identifier(safe_name)}"  # nosec B608
                    )
                    row = cursor.fetchone()
                    row_count = int(row[0] or 0) if row else None
                except Exception as exc:
                    warnings.append(f"row count の取得に失敗しました: {exc}")
            if include_ddl:
                ddl_type = normalized_type
                if normalized_type == "VIEW":
                    # MView/実体 TABLE を VIEW として GET_DDL すると ORA-31603 になるため
                    # user_objects で実種別を判定する
                    with suppress(Exception):
                        cursor.execute(
                            """
                            SELECT object_type FROM user_objects
                            WHERE object_name = :object_name
                            ORDER BY CASE object_type
                              WHEN 'MATERIALIZED VIEW' THEN 0 WHEN 'VIEW' THEN 1 ELSE 2 END
                            """,
                            {"object_name": safe_name},
                        )
                        row = cursor.fetchone()
                        actual = str(row[0] or "").upper() if row else ""
                        if actual == "MATERIALIZED VIEW":
                            ddl_type = "MATERIALIZED_VIEW"
                        elif actual == "TABLE":
                            ddl_type = "TABLE"
                try:
                    cursor.execute(
                        "SELECT DBMS_METADATA.GET_DDL(:object_type, :object_name) FROM DUAL",
                        {"object_type": ddl_type, "object_name": safe_name},
                    )
                    row = cursor.fetchone()
                    ddl = _coerce_text(row[0]) if row and row[0] else ""
                except Exception as exc:
                    warnings.append(f"DBMS_METADATA.GET_DDL に失敗しました: {exc}")
        if ddl:
            ddl = ddl.rstrip()
            if not ddl.endswith(";"):
                ddl += ";"
            if comment:
                escaped_comment = comment.replace("'", "''")
                ddl += (
                    f"\nCOMMENT ON {normalized_type} {_quote_identifier(safe_name)} "
                    f"IS '{escaped_comment}';"
                )
            for column in columns:
                if column.comment:
                    escaped_column_comment = column.comment.replace("'", "''")
                    ddl += (
                        f"\nCOMMENT ON COLUMN {_quote_identifier(safe_name)}."
                        f"{_quote_identifier(column.column_name)} "
                        f"IS '{escaped_column_comment}';"
                    )
        return {
            "name": safe_name,
            "owner": self.settings.oracle_user.strip().upper(),
            "object_type": normalized_type.lower(),
            "row_count": row_count,
            "comment": comment,
            "columns": [column.model_dump(mode="json") for column in columns],
            "ddl": ddl,
            "warnings": warnings,
        }

    def execute_admin_statements(
        self, statements: list[str], *, atomic: bool = True
    ) -> list[dict[str, Any]]:
        """Execute non-SELECT admin SQL statements.

        atomic=True は all-or-nothing、atomic=False は SQL Assist 互換の部分成功
        (成功が 1 件でもあれば commit、全滅なら rollback)。
        """
        from app.clients.oracle_statement_executor import oracle_statement_executor

        with self.connection() as conn:
            return oracle_statement_executor.execute(
                conn,
                statements,
                atomic=atomic,
                normalize=self._normalize_admin_statement,
                statement_type=self._admin_statement_type,
                output_reader=self._fetch_dbms_output,
                success_message=self._admin_success_message,
            )

    def import_tabular_table(
        self,
        *,
        table_name: str,
        columns: list[CsvImportColumn],
        rows: list[dict[str, str | None]],
        mode: str,
    ) -> dict[str, Any]:
        """Import parsed tabular rows into Oracle using create/replace/append/truncate mode."""
        safe_table = _strict_sql_name(table_name)
        quoted_table = _quote_identifier(safe_table)
        column_defs = ", ".join(
            f"{_quote_identifier(column.column_name)} {column.data_type}" for column in columns
        )
        ddl = f"CREATE TABLE {quoted_table} ({column_defs})"
        bind_names = [f"c{index}" for index, _column in enumerate(columns)]
        insert_sql = (
            f"INSERT INTO {quoted_table} "  # nosec B608
            f"({', '.join(_quote_identifier(column.column_name) for column in columns)}) "
            f"VALUES ({', '.join(':' + name for name in bind_names)})"
        )
        bind_rows = [
            {
                bind_names[index]: self._coerce_csv_value(row.get(column.column_name), column)
                for index, column in enumerate(columns)
            }
            for row in rows
        ]
        normalized_mode = mode.strip().lower()
        with self.connection() as conn, conn.cursor() as cursor:
            if normalized_mode in {"replace", "create"}:
                if normalized_mode == "replace":
                    self._drop_best_effort(cursor, f"DROP TABLE {quoted_table} PURGE", {})
                self._execute_plsql_like(cursor, ddl, {})
            elif normalized_mode == "truncate":
                self._execute_plsql_like(cursor, f"TRUNCATE TABLE {quoted_table}", {})
            elif normalized_mode != "append":
                raise OracleAdapterError(f"未対応 import mode です: {mode}")
            if bind_rows:
                cursor.executemany(insert_sql, bind_rows)
            conn.commit()
        return {
            "runtime": "oracle",
            "table_name": safe_table,
            "row_count": len(bind_rows),
            "mode": normalized_mode,
            "ddl": ddl,
            "insert_sql": insert_sql,
        }

    def upload_csv_to_existing_table(
        self,
        *,
        table_name: str,
        columns: list[CsvImportColumn],
        rows: list[dict[str, str | None]],
        truncate: bool,
    ) -> dict[str, Any]:
        """既存テーブルへ CSV 行を投入する(SQL Assist upload_csv_data の再マップ)。

        CSV 列名とテーブル列名を大文字比較でマッチングし、行ごとに INSERT して
        エラー先頭 5 件を収集する。成功 1 件以上で commit。
        """
        safe_table = _strict_sql_name(table_name)
        quoted_table = _quote_identifier(safe_table)
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type
                FROM user_tab_columns
                WHERE table_name = :table_name
                ORDER BY column_id
                """,
                {"table_name": safe_table},
            )
            table_columns = [(str(row[0] or ""), str(row[1] or "")) for row in cursor.fetchall()]
            if not table_columns:
                raise OracleAdapterError(f"{safe_table}: テーブルが見つからないか列がありません。")
            csv_by_upper: dict[str, CsvImportColumn] = {}
            for column in columns:
                for key in (column.source_name.strip().upper(), column.column_name.upper()):
                    if key and key not in csv_by_upper:
                        csv_by_upper[key] = column
            matched: list[tuple[str, str, CsvImportColumn]] = []
            for name, data_type in table_columns:
                csv_column = csv_by_upper.get(name.upper())
                if csv_column is not None:
                    matched.append((name, data_type, csv_column))
            if not matched:
                raise OracleAdapterError(
                    "CSV の列名がテーブルの列名と一致しません。ヘッダ行を確認してください。"
                )
            matched_csv_names = {column.column_name for _name, _type, column in matched}
            unmatched_csv = [
                column.source_name
                for column in columns
                if column.column_name not in matched_csv_names
            ]
            if truncate:
                self._execute_plsql_like(cursor, f"TRUNCATE TABLE {quoted_table}", {})
            bind_names = [f"c{index}" for index in range(len(matched))]
            insert_sql = (
                f"INSERT INTO {quoted_table} "  # nosec B608
                f"({', '.join(_quote_identifier(name) for name, _type, _column in matched)}) "
                f"VALUES ({', '.join(':' + bind for bind in bind_names)})"
            )
            success_count = 0
            row_errors: list[str] = []
            date_error = False
            for row_index, row in enumerate(rows, start=1):
                binds = {
                    bind_names[bind_index]: self._csv_upload_value(
                        row.get(csv_column.column_name), data_type
                    )
                    for bind_index, (_name, data_type, csv_column) in enumerate(matched)
                }
                try:
                    cursor.execute(insert_sql, binds)
                    success_count += 1
                except Exception as exc:
                    message = str(exc)
                    if "ORA-01861" in message or "ORA-01843" in message:
                        date_error = True
                    if len(row_errors) < 5:
                        row_errors.append(f"行{row_index}: {message}")
            if success_count > 0:
                conn.commit()
            else:
                conn.rollback()
        hint = (
            "日付列のフォーマットを解釈できませんでした。"
            "YYYY-MM-DD(例: 2026-01-31)形式を推奨します。"
            if date_error
            else ""
        )
        return {
            "runtime": "oracle",
            "table_name": safe_table,
            "matched_columns": [name for name, _type, _column in matched],
            "unmatched_csv_columns": unmatched_csv,
            "row_count": len(rows),
            "success_count": success_count,
            "error_count": len(rows) - success_count,
            "row_errors": row_errors,
            "hint": hint,
            "insert_sql": insert_sql,
        }

    def _csv_upload_value(self, value: str | None, data_type: str) -> Any:
        if value is None or str(value).strip() == "":
            return None
        upper_type = data_type.upper()
        if upper_type == "DATE" or upper_type.startswith("TIMESTAMP"):
            converted = _flexible_date_value(str(value))
            # 変換不能な値はそのまま渡し、Oracle 側の行エラー(ORA-01861 等)として報告する
            return converted if converted is not None else str(value)
        if upper_type == "NUMBER":
            text = str(value).strip()
            try:
                return int(text)
            except ValueError:
                try:
                    return float(text)
                except ValueError:
                    return text
        return value

    def apply_comment_statements(self, statements: list[str]) -> dict[str, Any]:
        """Execute generated COMMENT ON statements.

        Callers must generate the statements from validated catalog metadata; this method
        intentionally does not accept arbitrary DDL from API clients.
        """
        with self.connection() as conn, conn.cursor() as cursor:
            for statement in statements:
                self._execute_plsql_like(cursor, statement.strip().rstrip(";"), {})
            conn.commit()
        return {
            "runtime": "oracle",
            "statement_count": len(statements),
        }

    def apply_safe_statements(self, statements: list[str]) -> dict[str, Any]:
        """Execute generated catalog-validated metadata statements."""
        with self.connection() as conn, conn.cursor() as cursor:
            for statement in statements:
                self._execute_plsql_like(cursor, statement.strip().rstrip(";"), {})
            conn.commit()
        return {
            "runtime": "oracle",
            "statement_count": len(statements),
        }

    def list_select_ai_profiles(self) -> list[dict[str, Any]]:
        """List DBMS_CLOUD_AI profiles from the Oracle data dictionary when available."""
        candidates = [
            """
            SELECT PROFILE_NAME, NVL(STATUS, 'UNKNOWN'), OWNER, CREATED
            FROM USER_CLOUD_AI_PROFILES
            ORDER BY PROFILE_NAME
            """,
            """
            SELECT PROFILE_NAME, 'UNKNOWN', USER, NULL
            FROM USER_CLOUD_AI_PROFILES
            ORDER BY PROFILE_NAME
            """,
        ]
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql in candidates:
                try:
                    cursor.execute(sql)
                    rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
                    return [
                        {
                            "name": str(row[0] or ""),
                            "status": str(row[1] or "unknown").lower(),
                            "owner": str(row[2] or ""),
                            "created_at": _coerce_text(row[3]) if len(row) > 3 else "",
                        }
                        for row in rows
                    ]
                except Exception as exc:
                    errors.append(str(exc))
                    continue
        raise OracleAdapterError(
            "Oracle Select AI profile 一覧を取得できませんでした: " + "; ".join(errors)
        )

    def get_select_ai_profile_detail(self, *, profile_name: str) -> dict[str, Any]:
        """Fetch one DBMS_CLOUD_AI profile detail with best-effort attribute decoding.

        Autonomous DB の ``USER_CLOUD_AI_PROFILES`` は ``ATTRIBUTES``/``OWNER``/``CREATED``
        列を持たない環境がある(それらを select すると ORA-00904)。存在が保証される
        ``PROFILE_NAME``/``STATUS`` のみを主ビューから読み、属性・object_list は 1 属性 = 1 行の
        ``USER_CLOUD_AI_PROFILE_ATTRIBUTES`` から best-effort で組み立てる。
        """
        safe_name = profile_name.strip()
        if not safe_name:
            raise OracleAdapterError("profile_name が空です。")
        candidates = [
            """
            SELECT PROFILE_NAME, NVL(STATUS, 'UNKNOWN')
            FROM USER_CLOUD_AI_PROFILES
            WHERE UPPER(PROFILE_NAME) = UPPER(:profile_name)
            """,
            """
            SELECT PROFILE_NAME, 'UNKNOWN'
            FROM USER_CLOUD_AI_PROFILES
            WHERE UPPER(PROFILE_NAME) = UPPER(:profile_name)
            """,
        ]
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            row: Any = None
            for sql in candidates:
                try:
                    cursor.execute(sql, {"profile_name": safe_name})
                    row = cursor.fetchone()
                    break
                except Exception as exc:  # noqa: BLE001 - 環境差の列欠落を吸収
                    errors.append(str(exc))
                    continue
            else:
                raise OracleAdapterError(
                    "Oracle Select AI profile 詳細を取得できませんでした: " + "; ".join(errors)
                )
            if not row:
                raise OracleAdapterError(f"{profile_name}: profile が見つかりません。")

            attributes = self._fetch_cloud_ai_profile_attributes(cursor, safe_name)
            object_list = _normalize_select_ai_object_list(attributes)
            owner = ""
            if object_list:
                owner = str(object_list[0].get("owner") or "")
            if not owner:
                owner = self.settings.oracle_user.strip().upper()
            return {
                "name": str(row[0] or safe_name),
                "status": str(row[1] or "unknown").lower(),
                "owner": owner,
                "created_at": str(attributes.get("created") or ""),
                "attributes": attributes,
                "description": str(attributes.get("description") or ""),
                "object_list": object_list,
            }

    @staticmethod
    def _fetch_cloud_ai_profile_attributes(cursor: Any, profile_name: str) -> dict[str, Any]:
        """``USER_CLOUD_AI_PROFILE_ATTRIBUTES`` を 1 属性 = 1 行で読み dict へ復元する。

        値は LOB のことがあるため ``_coerce_text`` で文字列化し、JSON として解釈できれば
        構造化して格納する(``object_list`` など)。参照失敗時は空 dict で縮退する。
        """
        try:
            cursor.execute(
                """
                SELECT ATTRIBUTE_NAME, ATTRIBUTE_VALUE
                FROM USER_CLOUD_AI_PROFILE_ATTRIBUTES
                WHERE UPPER(PROFILE_NAME) = UPPER(:profile_name)
                """,
                {"profile_name": profile_name},
            )
            rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        except Exception:  # noqa: BLE001 - ビュー未提供環境では attributes 無しで縮退
            return {}
        attributes: dict[str, Any] = {}
        for attr_row in rows:
            name = str(attr_row[0] or "").strip()
            if not name:
                continue
            text = _coerce_text(attr_row[1]) if len(attr_row) > 1 else ""
            try:
                attributes[name] = json.loads(text) if text else text
            except json.JSONDecodeError:
                attributes[name] = text
        return attributes

    def list_select_ai_feedback_entries(
        self, *, profile_name: str, limit: int = 50
    ) -> dict[str, Any]:
        """List entries from a DBMS_CLOUD_AI profile feedback vector table."""
        safe_profile, index_name, table_name = _select_ai_feedback_index_names(profile_name)
        quoted_table = _quote_identifier(table_name)
        query = (
            "SELECT CONTENT, "
            "JSON_VALUE(ATTRIBUTES, '$.sql_id' RETURNING VARCHAR2(128)) AS SQL_ID, "
            "JSON_VALUE(ATTRIBUTES, '$.sql_text' RETURNING CLOB) AS SQL_TEXT, "
            f"ATTRIBUTES FROM {quoted_table} "  # nosec B608
            "FETCH FIRST :limit ROWS ONLY"
        )
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(query, {"limit": max(1, min(int(limit), 50))})
                rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
                # python-oracledb の CLOB は接続に紐づく locator なので、接続を閉じる前に
                # 文字列へ materialize する。接続 context 外で read() すると
                # DPY-1001 / DPI-1010 になる。
                materialized_rows = [
                    tuple(_coerce_result_value(value) for value in row) for row in rows
                ]
            except Exception as exc:
                message = str(exc)
                if "ORA-00942" in message or "ORA-04043" in message:
                    raise OracleAdapterError(
                        f"{table_name}: Select AI feedback vector table が未作成です。"
                        "feedback vector index を再構築してください。"
                    ) from exc
                raise OracleAdapterError(
                    f"Select AI feedback entries の取得に失敗しました: {message}"
                ) from exc
        items: list[dict[str, Any]] = []
        for row in materialized_rows:
            attributes_text = _coerce_text(row[3] if len(row) > 3 else "")
            try:
                attributes = json.loads(attributes_text) if attributes_text.strip() else {}
            except json.JSONDecodeError:
                attributes = {"raw": attributes_text}
            items.append(
                {
                    "content": _coerce_text(row[0] if len(row) > 0 else ""),
                    "sql_id": _coerce_text(row[1] if len(row) > 1 else ""),
                    "sql_text": _coerce_text(row[2] if len(row) > 2 else ""),
                    "attributes": (
                        attributes if isinstance(attributes, dict) else {"raw": attributes}
                    ),
                    "raw_attributes": attributes_text,
                }
            )
        return {
            "runtime": "oracle",
            "profile_name": safe_profile,
            "index_name": index_name,
            "table_name": table_name,
            "items": items,
            "total": len(items),
        }

    def delete_select_ai_feedback(self, *, profile_name: str, sql_text: str) -> dict[str, Any]:
        """Delete one DBMS_CLOUD_AI feedback entry by SQL text."""
        safe_profile, index_name, table_name = _select_ai_feedback_index_names(profile_name)
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    BEGIN
                        DBMS_CLOUD_AI.FEEDBACK(
                            profile_name => :profile_name,
                            sql_text => :sql_text,
                            operation => 'DELETE'
                        );
                    END;
                    """,
                    {"profile_name": safe_profile, "sql_text": sql_text},
                )
                conn.commit()
            except Exception as exc:
                raise OracleAdapterError(f"Select AI feedback の削除に失敗しました: {exc}") from exc
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": safe_profile,
            "index_name": index_name,
            "table_name": table_name,
        }

    def add_select_ai_feedback(
        self,
        *,
        profile_name: str,
        sql_text: str,
        feedback_type: str,
        response: str,
        feedback_content: str,
    ) -> dict[str, Any]:
        """Add one DBMS_CLOUD_AI feedback entry."""
        safe_profile, index_name, table_name = _select_ai_feedback_index_names(profile_name)
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    BEGIN
                        DBMS_CLOUD_AI.FEEDBACK(
                            profile_name => :profile_name,
                            sql_text => :sql_text,
                            feedback_type => :feedback_type,
                            response => :response,
                            feedback_content => :feedback_content,
                            operation => 'ADD'
                        );
                    END;
                    """,
                    {
                        "profile_name": safe_profile,
                        "sql_text": sql_text,
                        "feedback_type": feedback_type,
                        "response": response,
                        "feedback_content": feedback_content,
                    },
                )
                conn.commit()
            except Exception as exc:
                raise OracleAdapterError(f"Select AI feedback の追加に失敗しました: {exc}") from exc
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": safe_profile,
            "index_name": index_name,
            "table_name": table_name,
            "sql_text": sql_text,
            "feedback_type": feedback_type,
        }

    def update_select_ai_feedback_vector_index(
        self, *, profile_name: str, similarity_threshold: float, match_limit: int
    ) -> dict[str, Any]:
        """Update DBMS_CLOUD_AI feedback vector index attributes."""
        safe_profile, index_name, table_name = _select_ai_feedback_index_names(profile_name)
        attributes = json.dumps(
            {
                "similarity_threshold": float(similarity_threshold),
                "match_limit": int(match_limit),
            },
            ensure_ascii=False,
        )
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    BEGIN
                        DBMS_CLOUD_AI.UPDATE_VECTOR_INDEX(
                            index_name => :index_name,
                            attributes => :attributes
                        );
                    END;
                    """,
                    {"index_name": index_name, "attributes": attributes},
                )
                conn.commit()
            except Exception as exc:
                raise OracleAdapterError(
                    f"Select AI feedback vector index の更新に失敗しました: {exc}"
                ) from exc
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": safe_profile,
            "index_name": index_name,
            "table_name": table_name,
            "attributes": attributes,
        }

    def upsert_select_ai_profile_low_level(
        self,
        *,
        profile_name: str,
        attributes: dict[str, Any],
        description: str = "",
        original_name: str = "",
    ) -> dict[str, Any]:
        """Create or replace DBMS_CLOUD_AI profile from raw attributes JSON."""
        safe_name = profile_name.strip()
        if not safe_name:
            raise OracleAdapterError("profile_name が空です。")
        attrs = json.dumps(attributes or {}, ensure_ascii=False)
        desc = description or ""
        with self.connection() as conn, conn.cursor() as cursor:
            if original_name.strip() and original_name.strip().upper() != safe_name.upper():
                self._drop_cloud_ai_profile_best_effort(cursor, original_name.strip())
            self._drop_cloud_ai_profile_best_effort(cursor, safe_name)
            self._execute_first_supported_plsql(
                cursor,
                [
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.CREATE_PROFILE(
                                profile_name => :name,
                                attributes => :attrs,
                                description => :description
                            );
                        END;
                        """,
                        {"name": safe_name, "attrs": attrs, "description": desc},
                    ),
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.CREATE_PROFILE(
                                profile_name => :name,
                                attributes => :attrs
                            );
                        END;
                        """,
                        {"name": safe_name, "attrs": attrs},
                    ),
                ],
            )
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": safe_name,
            "attributes": attributes,
            "description": desc,
        }

    def list_agent_conversations(
        self, *, team_name: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Fetch recent Select AI Agent conversation prompts when dictionary view exists."""
        filters = []
        binds: dict[str, Any] = {"limit": max(limit, 1)}
        if team_name:
            filters.append("UPPER(TEAM_NAME) = UPPER(:team_name)")
            binds["team_name"] = team_name
        where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""
        conversation_sql = (
            "SELECT CONVERSATION_ID, PROMPT, RESPONSE, CREATED, TEAM_NAME "  # nosec B608
            "FROM USER_CLOUD_AI_CONVERSATION_PROMPTS "
            f"{where_clause} "
            "ORDER BY CREATED DESC "
            "FETCH FIRST :limit ROWS ONLY"
        )
        prompt_sql = (
            "SELECT CONVERSATION_ID, PROMPT, NULL, CREATED, NULL "  # nosec B608
            "FROM USER_CLOUD_AI_CONVERSATION_PROMPTS "
            f"{where_clause} "
            "ORDER BY CREATED DESC "
            "FETCH FIRST :limit ROWS ONLY"
        )
        candidates = [
            (conversation_sql, binds),
            (prompt_sql, binds),
        ]
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql, params in candidates:
                try:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
                    return [
                        {
                            "conversation_id": str(row[0] or ""),
                            "prompt": _coerce_text(row[1]),
                            "response": _coerce_text(row[2]) if len(row) > 2 else "",
                            "created_at": _coerce_text(row[3]) if len(row) > 3 else "",
                            "team_name": str(row[4] or "") if len(row) > 4 else "",
                        }
                        for row in rows
                    ]
                except Exception as exc:
                    errors.append(str(exc))
                    continue
        raise OracleAdapterError(
            "Select AI Agent conversation 履歴を取得できませんでした: " + "; ".join(errors)
        )

    def check_select_ai_agent_privileges(self) -> list[dict[str, str]]:
        """Run side-effect-free Select AI Agent privilege checks."""

        def check_count(
            cursor: Any,
            *,
            name: str,
            sql: str,
            params: dict[str, Any] | None = None,
            ok_message: str,
            warning_message: str,
        ) -> dict[str, str]:
            try:
                cursor.execute(sql, params or {})
                row = cursor.fetchone()
                count = int(row[0] or 0) if row else 0
                return {
                    "name": name,
                    "status": "ok" if count > 0 else "warning",
                    "message": ok_message if count > 0 else warning_message,
                }
            except Exception as exc:
                return {
                    "name": name,
                    "status": "warning",
                    "message": f"{warning_message}: {exc}",
                }

        def check_access(
            cursor: Any,
            *,
            name: str,
            sql: str,
            ok_message: str,
            warning_message: str,
        ) -> dict[str, str]:
            try:
                cursor.execute(sql)
                cursor.fetchone()
                return {"name": name, "status": "ok", "message": ok_message}
            except Exception as exc:
                return {
                    "name": name,
                    "status": "warning",
                    "message": f"{warning_message}: {exc}",
                }

        with self.connection() as conn, conn.cursor() as cursor:
            checks: list[dict[str, str]] = []
            try:
                cursor.execute("SELECT 1 FROM DUAL")
                checks.append(
                    {
                        "name": "oracle_connection",
                        "status": "ok",
                        "message": "Oracle へ接続できます。",
                    }
                )
            except Exception as exc:
                raise OracleAdapterError(f"Oracle 接続確認に失敗しました: {exc}") from exc
            checks.append(
                check_count(
                    cursor,
                    name="dbms_cloud_ai_package",
                    sql="""
                    SELECT COUNT(*)
                    FROM ALL_PROCEDURES
                    WHERE OBJECT_NAME = 'DBMS_CLOUD_AI'
                    """,
                    ok_message="DBMS_CLOUD_AI package が参照可能です。",
                    warning_message="DBMS_CLOUD_AI package を参照できません。",
                )
            )
            checks.append(
                check_count(
                    cursor,
                    name="dbms_cloud_ai_agent_package",
                    sql="""
                    SELECT COUNT(*)
                    FROM ALL_PROCEDURES
                    WHERE OBJECT_NAME = 'DBMS_CLOUD_AI_AGENT'
                    """,
                    ok_message="DBMS_CLOUD_AI_AGENT package が参照可能です。",
                    warning_message="DBMS_CLOUD_AI_AGENT package を参照できません。",
                )
            )
            checks.append(
                check_access(
                    cursor,
                    name="user_cloud_ai_profiles",
                    sql="SELECT PROFILE_NAME FROM USER_CLOUD_AI_PROFILES WHERE 1 = 0",
                    ok_message="USER_CLOUD_AI_PROFILES を参照できます。",
                    warning_message="USER_CLOUD_AI_PROFILES を参照できません。",
                )
            )
            checks.append(
                check_access(
                    cursor,
                    name="user_cloud_ai_conversation_prompts",
                    sql=(
                        "SELECT CONVERSATION_ID FROM USER_CLOUD_AI_CONVERSATION_PROMPTS WHERE 1 = 0"
                    ),
                    ok_message="USER_CLOUD_AI_CONVERSATION_PROMPTS を参照できます。",
                    warning_message="USER_CLOUD_AI_CONVERSATION_PROMPTS を参照できません。",
                )
            )
            return checks

    def generate_synthetic_data(
        self,
        *,
        table_name: str,
        row_count: int,
        profile_name: str = "",
        object_list: list[str] | None = None,
        user_prompt: str = "",
        sample_rows: int = 0,
        use_comments: bool = True,
    ) -> dict[str, Any]:
        """Call DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA for a validated table."""
        safe_table = _strict_sql_name(table_name) if table_name.strip() else ""
        safe_objects = [_strict_sql_name(item) for item in object_list or [] if item.strip()]
        if not safe_table and not safe_objects:
            raise OracleAdapterError("synthetic data 対象 table/object_list が空です。")
        params_json = json.dumps(
            {
                "comments": bool(use_comments),
                "sample_rows": max(int(sample_rows), 0),
            },
            ensure_ascii=False,
        )
        candidates = [
            (
                """
                SELECT DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                    table_name => :table_name,
                    row_count => :row_count,
                    profile_name => :profile_name
                )
                FROM DUAL
                """,
                {
                    "table_name": safe_table or safe_objects[0],
                    "row_count": int(row_count),
                    "profile_name": profile_name or None,
                },
            ),
            (
                """
                SELECT DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                    table_name => :table_name,
                    row_count => :row_count
                )
                FROM DUAL
                """,
                {"table_name": safe_table or safe_objects[0], "row_count": int(row_count)},
            ),
        ]
        procedure_candidates: list[tuple[str, dict[str, Any]]] = []
        if profile_name.strip():
            if safe_objects and not safe_table:
                procedure_candidates.append(
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                                profile_name => :profile_name,
                                object_list => :object_list,
                                params => :params
                            );
                        END;
                        """,
                        {
                            "profile_name": profile_name,
                            "object_list": json.dumps(
                                [
                                    {
                                        "owner": self.settings.oracle_user.strip().upper(),
                                        "name": object_name,
                                        "record_count": int(row_count),
                                    }
                                    for object_name in safe_objects
                                ],
                                ensure_ascii=False,
                            ),
                            "params": params_json,
                        },
                    )
                )
            else:
                procedure_candidates.append(
                    (
                        """
                        BEGIN
                            DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA(
                                profile_name => :profile_name,
                                object_name => :object_name,
                                owner_name => :owner_name,
                                record_count => :row_count,
                                user_prompt => :user_prompt,
                                params => :params
                            );
                        END;
                        """,
                        {
                            "profile_name": profile_name,
                            "object_name": safe_table or safe_objects[0],
                            "owner_name": self.settings.oracle_user.strip().upper(),
                            "row_count": int(row_count),
                            "user_prompt": user_prompt or None,
                            "params": params_json,
                        },
                    )
                )
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql, params in candidates:
                try:
                    cursor.execute(sql, params)
                    row = cursor.fetchone()
                    conn.commit()
                    operation_id = _coerce_text(row[0] if row else "").strip()
                    if not operation_id:
                        operation_id = self._latest_load_operation_id(cursor)
                    return {
                        "runtime": "oracle",
                        "package": "DBMS_CLOUD_AI",
                        "operation_id": operation_id,
                        "table_name": safe_table or (safe_objects[0] if safe_objects else ""),
                        "object_list": safe_objects,
                        "row_count": int(row_count),
                    }
                except Exception as exc:
                    message = str(exc)
                    if self._looks_like_signature_error(message):
                        errors.append(message)
                        continue
                    raise OracleAdapterError(
                        f"DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA に失敗しました: {message}"
                    ) from exc
            for sql, params in procedure_candidates:
                try:
                    cursor.execute(sql, params)
                    conn.commit()
                    return {
                        "runtime": "oracle",
                        "package": "DBMS_CLOUD_AI",
                        "mode": "procedure",
                        "operation_id": self._latest_load_operation_id(cursor),
                        "table_name": safe_table or (safe_objects[0] if safe_objects else ""),
                        "object_list": safe_objects,
                        "row_count": int(row_count),
                    }
                except Exception as exc:
                    message = str(exc)
                    if self._looks_like_signature_error(message):
                        errors.append(message)
                        continue
                    raise OracleAdapterError(
                        f"DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA に失敗しました: {message}"
                    ) from exc
        raise OracleAdapterError(
            "DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA の対応 signature が見つかりません: "
            + "; ".join(errors)
        )

    def _latest_load_operation_id(self, cursor: Any) -> str:
        # GENERATE_SYNTHETIC_DATA は戻り値のない同期プロシージャのため、同一 session の直後に
        # USER_LOAD_OPERATIONS.MAX(ID) を引いて operation id を得る。
        # ponytail: 直近 1 件を返すだけ。並行実行時は取り違えの可能性あり(単一管理者運用前提)。
        try:
            cursor.execute("SELECT MAX(ID) FROM USER_LOAD_OPERATIONS")
            row = cursor.fetchone()
        except Exception:
            return ""
        return _coerce_text(row[0] if row else "").strip()

    def synthetic_data_operation_status(self, *, operation_id: str) -> dict[str, Any]:
        safe = operation_id.strip()
        if not safe:
            raise OracleAdapterError("operation_id が空です。")
        if not safe.isdigit():
            # 動的テーブル名に埋め込むため digits 限定(識別子はバインド不可・injection 防止)
            raise OracleAdapterError("operation_id が不正です。")
        table = f'"SYNTHETIC_DATA${safe}_STATUS"'
        sql = (
            f"SELECT NAME, ROWS_LOADED, STATUS, ERROR_MESSAGE "
            f"FROM {table} FETCH FIRST 200 ROWS ONLY"
        )
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(sql)
                rows = cursor.fetchall() or []
            except Exception as exc:
                # status テーブル未生成(ORA-00942)等 → not_found で安全に縮退
                return {
                    "runtime": "oracle",
                    "status": "not_found",
                    "message": _coerce_text(str(exc)),
                    "result": {},
                }
        if not rows:
            return {"runtime": "oracle", "status": "not_found", "message": "", "result": {}}
        entries = [
            {
                "name": _coerce_text(row[0]),
                "rows_loaded": _coerce_result_value(row[1]),
                "status": _coerce_text(row[2]),
                "error_message": _coerce_text(row[3]) if len(row) > 3 else "",
            }
            for row in rows
        ]
        statuses = [entry["status"].upper() for entry in entries if entry["status"]]
        if any(status in {"FAILED", "ERROR"} for status in statuses):
            overall = "failed"
        elif statuses and all(status in {"COMPLETED", "SUCCEEDED"} for status in statuses):
            overall = "completed"
        elif statuses:
            overall = statuses[-1].lower()
        else:
            overall = "unknown"
        message = next((entry["error_message"] for entry in entries if entry["error_message"]), "")
        return {
            "runtime": "oracle",
            "status": overall,
            "message": message,
            "result": {"operations": entries},
        }

    def rebuild_feedback_vector_index(
        self,
        *,
        table_name: str,
        index_name: str,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Recreate the NL2SQL feedback VECTOR table and vector index."""
        safe_table = _strict_sql_name(table_name)
        safe_index = _strict_sql_name(index_name)
        quoted_table = _quote_identifier(safe_table)
        quoted_index = _quote_identifier(safe_index)
        create_table = (
            f"CREATE TABLE {quoted_table} ("  # nosec B608
            "HISTORY_ID VARCHAR2(64) PRIMARY KEY, "
            "PROFILE_ID VARCHAR2(128), "
            "QUESTION CLOB, "
            "GENERATED_SQL CLOB, "
            "FEEDBACK_RATING VARCHAR2(32), "
            "EMBEDDING VECTOR(1536, FLOAT32), "
            "CREATED_AT TIMESTAMP WITH TIME ZONE)"
        )
        insert_sql = (
            f"INSERT INTO {quoted_table} "  # nosec B608
            "(HISTORY_ID, PROFILE_ID, QUESTION, GENERATED_SQL, "
            "FEEDBACK_RATING, EMBEDDING, CREATED_AT) "
            "VALUES (:history_id, :profile_id, :question, :generated_sql, :feedback_rating, "
            "TO_VECTOR(:embedding_json), SYSTIMESTAMP)"
        )
        create_index = (
            f"CREATE VECTOR INDEX {quoted_index} "  # nosec B608
            f"ON {quoted_table} (EMBEDDING) "
            "ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE"
        )
        bind_rows = [
            {
                "history_id": str(row["history_id"]),
                "profile_id": str(row.get("profile_id") or ""),
                "question": str(row.get("question") or ""),
                "generated_sql": str(row.get("generated_sql") or ""),
                "feedback_rating": str(row.get("feedback_rating") or ""),
                "embedding_json": json.dumps(row.get("embedding") or []),
            }
            for row in rows
        ]
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_best_effort(cursor, f"DROP INDEX {quoted_index}", {})
            self._drop_best_effort(cursor, f"DROP TABLE {quoted_table} PURGE", {})
            self._execute_plsql_like(cursor, create_table, {})
            if bind_rows:
                cursor.executemany(insert_sql, bind_rows)
            self._execute_plsql_like(cursor, create_index, {})
            conn.commit()
        return {
            "runtime": "oracle",
            "table_name": safe_table,
            "index_name": safe_index,
            "row_count": len(bind_rows),
        }

    def clear_feedback_vector_index(self, *, table_name: str, index_name: str) -> dict[str, Any]:
        """Drop the NL2SQL feedback VECTOR index/table if present."""
        safe_table = _strict_sql_name(table_name)
        safe_index = _strict_sql_name(index_name)
        quoted_table = _quote_identifier(safe_table)
        quoted_index = _quote_identifier(safe_index)
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_best_effort(cursor, f"DROP INDEX {quoted_index}", {})
            self._drop_best_effort(cursor, f"DROP TABLE {quoted_table} PURGE", {})
            conn.commit()
        return {
            "runtime": "oracle",
            "table_name": safe_table,
            "index_name": safe_index,
        }

    def search_feedback_vector_index(
        self,
        *,
        table_name: str,
        embedding: list[float],
        profile_id: str | None,
        include_bad: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search feedback history with Oracle 26ai vector similarity."""
        safe_table = _strict_sql_name(table_name)
        quoted_table = _quote_identifier(safe_table)
        filters = ["1 = 1"]
        binds: dict[str, Any] = {
            "embedding_json": json.dumps(embedding),
            "limit": max(limit, 1),
        }
        if profile_id:
            filters.append("PROFILE_ID = :profile_id")
            binds["profile_id"] = profile_id
        if not include_bad:
            filters.append("(FEEDBACK_RATING IS NULL OR FEEDBACK_RATING <> 'bad')")
        where_clause = " AND ".join(filters)
        query = (
            "SELECT HISTORY_ID, PROFILE_ID, QUESTION, GENERATED_SQL, FEEDBACK_RATING, "
            "VECTOR_DISTANCE(EMBEDDING, TO_VECTOR(:embedding_json), COSINE) AS DISTANCE "
            f"FROM {quoted_table} "  # nosec B608
            f"WHERE {where_clause} "
            "ORDER BY DISTANCE FETCH FIRST :limit ROWS ONLY"
        )
        with self.connection() as conn, conn.cursor() as cursor:
            cursor.execute(query, binds)
            rows = cursor.fetchall() if hasattr(cursor, "fetchall") else list(cursor)
        results: list[dict[str, Any]] = []
        for row in rows:
            distance = float(row[5] or 0)
            results.append(
                {
                    "history_id": str(row[0] or ""),
                    "profile_id": str(row[1] or ""),
                    "question": _coerce_text(row[2]),
                    "generated_sql": _coerce_text(row[3]),
                    "feedback_rating": str(row[4] or ""),
                    "distance": distance,
                    "score": round(max(0.0, 1.0 - distance), 3),
                }
            )
        return results

    def generate_select_ai_sql(
        self,
        *,
        profile_name: str,
        question: str,
        action: str = "showsql",
        attributes: dict[str, str] | None = None,
    ) -> str:
        """Oracle Select AI profile で SQL を生成する。

        DBMS_CLOUD_AI.GENERATE の属性は環境差があるため、呼び出しは adapter 内に限定する。
        """
        with self.connection() as conn, conn.cursor() as cursor:
            binds: dict[str, str] = {
                "prompt": question,
                "profile_name": profile_name,
                "action": action,
            }
            if attributes:
                binds["attributes"] = json.dumps(attributes, ensure_ascii=False)
                cursor.execute(
                    """
                    SELECT DBMS_CLOUD_AI.GENERATE(
                        prompt => :prompt,
                        profile_name => :profile_name,
                        action => :action,
                        attributes => :attributes
                    )
                    FROM DUAL
                    """,
                    binds,
                )
            else:
                cursor.execute(
                    """
                    SELECT DBMS_CLOUD_AI.GENERATE(
                        prompt => :prompt,
                        profile_name => :profile_name,
                        action => :action
                    )
                    FROM DUAL
                    """,
                    binds,
                )
            row = cursor.fetchone()
            text = _coerce_text(row[0] if row else "")
        return _extract_select_statement(text)

    def refresh_select_ai_profile(
        self,
        *,
        profile_name: str,
        allowed_tables: list[str],
        row_limit: int | None,
        description: str = "",
    ) -> dict[str, Any]:
        """Create or replace an Oracle Select AI profile."""
        attributes = self._select_ai_profile_attributes(
            allowed_tables=allowed_tables,
            row_limit=row_limit,
            description=description,
        )
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_cloud_ai_profile_best_effort(cursor, profile_name)
            self._execute_plsql(
                cursor,
                """
                BEGIN
                    DBMS_CLOUD_AI.CREATE_PROFILE(
                        profile_name => :profile_name,
                        attributes => :attributes
                    );
                END;
                """,
                {"profile_name": profile_name, "attributes": json.dumps(attributes)},
            )
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": profile_name,
            "profile_attributes": attributes,
        }

    def drop_select_ai_profile(self, *, profile_name: str) -> dict[str, Any]:
        """Drop an Oracle Select AI profile if it exists."""
        with self.connection() as conn, conn.cursor() as cursor:
            self._drop_cloud_ai_profile_best_effort(cursor, profile_name)
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI",
            "profile_name": profile_name,
        }

    def refresh_select_ai_agent_assets(
        self,
        *,
        profile_name: str,
        tool_name: str,
        agent_name: str,
        task_name: str,
        team_name: str,
        allowed_tables: list[str],
        row_limit: int | None,
        description: str = "",
        refresh_profile: bool = True,
    ) -> dict[str, Any]:
        """Create or replace Oracle Select AI Agent profile/tool/agent/task/team assets."""
        profile_meta = (
            self.refresh_select_ai_profile(
                profile_name=profile_name,
                allowed_tables=allowed_tables,
                row_limit=row_limit,
                description=description,
            )
            if refresh_profile
            else {
                "runtime": "oracle",
                "package": "DBMS_CLOUD_AI",
                "profile_name": profile_name,
                "reused": True,
            }
        )
        profile_attributes = self._select_ai_profile_attributes(
            allowed_tables=allowed_tables,
            row_limit=row_limit,
            description=description,
        )
        tool_attributes = {
            "tool_type": "SQL",
            "tool_params": {"profile_name": profile_name},
            "instruction": (
                "Use this tool to generate Oracle SELECT/WITH SQL from natural language. "
                "Use SHOWSQL behavior and do not execute DML, DDL, PL/SQL, or multi-statement SQL."
            ),
        }
        agent_attributes = {
            "profile_name": profile_name,
            "role": description.strip() or "Oracle SQL による業務データ分析を支援します。",
            "tools": [tool_name],
        }
        task_attributes = {
            "instruction": (
                "Use the SQL tool to create exactly one Oracle SELECT statement for the "
                f"user's request. Invoke tool {tool_name} with JSON keys TOOL_NAME, QUERY, "
                "and ACTION. TOOL_NAME must be the SQL tool name, QUERY must be the user's "
                "natural language request, and ACTION must be SHOWSQL. "
                "Return strict JSON only with keys sql and explanation. "
                "sql must be a single SELECT/WITH statement without markdown, comments, "
                "or trailing narration. explanation must be concise and written in Japanese."
            ),
            "tools": [tool_name],
            "enable_human_tool": False,
        }
        team_attributes = {
            "agents": [{"name": agent_name, "task": task_name}],
            "process": "sequential",
        }
        with self.connection() as conn, conn.cursor() as cursor:
            for procedure, name_param, name in [
                ("DROP_TEAM", "team_name", team_name),
                ("DROP_TASK", "task_name", task_name),
                ("DROP_AGENT", "agent_name", agent_name),
                ("DROP_TOOL", "tool_name", tool_name),
            ]:
                self._drop_best_effort(
                    cursor,
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}({name_param} => :name, force => TRUE);
                    END;
                    """,
                    {"name": name},
                )
            self._drop_cloud_ai_profile_best_effort(cursor, f"AGENT${team_name}")
            self._drop_sql_translator_profile_best_effort(cursor, f"AGENT${team_name}")
            conn.commit()
            self._execute_agent_create(
                cursor,
                procedure="CREATE_TOOL",
                name_param="tool_name",
                name=tool_name,
                attributes=tool_attributes,
            )
            self._execute_agent_create(
                cursor,
                procedure="CREATE_AGENT",
                name_param="agent_name",
                name=agent_name,
                attributes=agent_attributes,
            )
            self._execute_agent_create(
                cursor,
                procedure="CREATE_TASK",
                name_param="task_name",
                name=task_name,
                attributes=task_attributes,
            )
            try:
                self._execute_agent_create(
                    cursor,
                    procedure="CREATE_TEAM",
                    name_param="team_name",
                    name=team_name,
                    attributes=team_attributes,
                )
            except OracleAdapterError as exc:
                if not self._looks_like_profile_already_exists(str(exc)):
                    raise
                self._drop_cloud_ai_profile_best_effort(cursor, f"AGENT${team_name}")
                self._drop_sql_translator_profile_best_effort(cursor, f"AGENT${team_name}")
                conn.commit()
                self._execute_agent_create(
                    cursor,
                    procedure="CREATE_TEAM",
                    name_param="team_name",
                    name=team_name,
                    attributes=team_attributes,
                )
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI_AGENT",
            "select_ai_profile_meta": profile_meta,
            "profile_attributes": profile_attributes,
            "tool_attributes": tool_attributes,
            "agent_attributes": agent_attributes,
            "task_attributes": task_attributes,
            "team_attributes": team_attributes,
        }

    def drop_select_ai_agent_assets(
        self,
        *,
        profile_name: str,
        tool_name: str,
        agent_name: str,
        task_name: str,
        team_name: str,
    ) -> dict[str, Any]:
        """Drop Oracle Select AI Agent assets if they exist."""
        with self.connection() as conn, conn.cursor() as cursor:
            for procedure, name_param, name in [
                ("DROP_TEAM", "team_name", team_name),
                ("DROP_TASK", "task_name", task_name),
                ("DROP_AGENT", "agent_name", agent_name),
                ("DROP_TOOL", "tool_name", tool_name),
            ]:
                self._drop_best_effort(
                    cursor,
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}({name_param} => :name, force => TRUE);
                    END;
                    """,
                    {"name": name},
                )
            for name in [f"AGENT${team_name}", profile_name]:
                self._drop_cloud_ai_profile_best_effort(cursor, name)
            self._drop_sql_translator_profile_best_effort(cursor, f"AGENT${team_name}")
            conn.commit()
        return {
            "runtime": "oracle",
            "package": "DBMS_CLOUD_AI_AGENT",
            "profile_name": profile_name,
            "tool_name": tool_name,
            "agent_name": agent_name,
            "task_name": task_name,
            "team_name": team_name,
        }

    def run_select_ai_agent_team(
        self, *, team_name: str, question: str, tool_name: str | None = None
    ) -> tuple[str, str]:
        conversation_id = ""
        try:
            conversation_id = self.create_agent_conversation()
        except OracleAdapterError:
            conversation_id = ""
        params = json.dumps(
            {"conversation_id": conversation_id} if conversation_id else {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        candidates = [
            (
                """
                SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(
                    team_name => :team_name,
                    user_prompt => :user_prompt,
                    conversation_id => :conversation_id,
                    params => :params
                )
                FROM DUAL
                """,
                {
                    "team_name": team_name,
                    "user_prompt": question,
                    "conversation_id": conversation_id,
                    "params": params,
                },
            )
        ]
        candidates.append(
            (
                """
                SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(
                    team_name => :team_name,
                    user_prompt => :user_prompt,
                    params => :params
                )
                FROM DUAL
                """,
                {"team_name": team_name, "user_prompt": question, "params": params},
            )
        )
        candidates.extend(
            [
                (
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(:team_name, :user_prompt, :params)
                    FROM DUAL
                    """,
                    {"team_name": team_name, "user_prompt": question, "params": params},
                ),
                (
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(
                        :team_name,
                        :user_prompt,
                        :conversation_id,
                        :params
                    )
                    FROM DUAL
                    """,
                    {
                        "team_name": team_name,
                        "user_prompt": question,
                        "conversation_id": conversation_id,
                        "params": params,
                    },
                ),
                (
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TEAM(:team_name, :user_prompt)
                    FROM DUAL
                    """,
                    {"team_name": team_name, "user_prompt": question},
                ),
            ]
        )
        errors: list[str] = []
        with self.connection() as conn, conn.cursor() as cursor:
            for sql, bindings in candidates:
                try:
                    cursor.execute(sql, bindings)
                    row = cursor.fetchone()
                    text = _coerce_text(row[0] if row else "")
                    return _extract_select_statement(text), conversation_id
                except Exception as exc:
                    message = str(exc)
                    if self._looks_like_agent_profile_loss(message):
                        return self.run_select_ai_agent_tool(
                            tool_name=tool_name or self._tool_name_from_team_name(team_name),
                            question=question,
                        )
                    if self._looks_like_signature_error(message):
                        errors.append(message)
                        continue
                    raise self._agent_runtime_error(exc) from exc
        if errors:
            raise self._agent_runtime_error(RuntimeError("; ".join(errors)))
        raise OracleAdapterError("Select AI Agent team の実行結果を取得できませんでした。")

    def run_select_ai_agent_tool(self, *, tool_name: str, question: str) -> tuple[str, str]:
        payload = json.dumps(
            {"TOOL_NAME": tool_name, "QUERY": question, "ACTION": "SHOWSQL"},
            ensure_ascii=False,
        )
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    SELECT DBMS_CLOUD_AI_AGENT.RUN_TOOL(
                        tool_name => :tool_name,
                        input => :input
                    )
                    FROM DUAL
                    """,
                    {"tool_name": tool_name, "input": payload},
                )
            except Exception as exc:
                raise self._agent_runtime_error(exc) from exc
            row = cursor.fetchone()
            text = _coerce_text(row[0] if row else "")
        return _extract_select_statement(text), f"run_tool:{tool_name}"

    def create_agent_conversation(self) -> str:
        with self.connection() as conn, conn.cursor() as cursor:
            try:
                cursor.execute("SELECT DBMS_CLOUD_AI_AGENT.CREATE_CONVERSATION() FROM DUAL")
            except Exception as exc:
                raise self._agent_runtime_error(exc) from exc
            row = cursor.fetchone()
        conversation_id = _coerce_text(row[0] if row else "").strip()
        if not conversation_id:
            raise OracleAdapterError("Select AI Agent conversation_id を作成できませんでした。")
        return conversation_id

    def _select_ai_profile_attributes(
        self, *, allowed_tables: list[str], row_limit: int | None, description: str
    ) -> dict[str, Any]:
        del row_limit, description
        attributes: dict[str, Any] = {
            "provider": self.settings.nl2sql_select_ai_provider,
            "enforce_object_list": True,
            "annotations": True,
            "comments": True,
            "constraints": True,
            "object_list": self._object_list(allowed_tables),
        }
        if self.settings.nl2sql_select_ai_credential_name:
            attributes["credential_name"] = self.settings.nl2sql_select_ai_credential_name
        if self.settings.nl2sql_select_ai_model:
            attributes["model"] = self.settings.nl2sql_select_ai_model
        if self.settings.oci_region:
            attributes["region"] = self.settings.oci_region
        if self.settings.oci_compartment_id:
            attributes["oci_compartment_id"] = self.settings.oci_compartment_id
        return attributes

    def _object_list(self, allowed_tables: list[str]) -> list[dict[str, str]]:
        owner = self.settings.oracle_user.upper() if self.settings.oracle_user else ""
        objects: list[dict[str, str]] = []
        for table_name in allowed_tables:
            normalized = table_name.strip().upper()
            if not normalized:
                continue
            if "." in normalized:
                object_owner, object_name = normalized.split(".", 1)
                objects.append({"owner": object_owner, "name": object_name})
            elif owner:
                objects.append({"owner": owner, "name": normalized})
            else:
                objects.append({"name": normalized})
        return objects

    def _execute_agent_create(
        self,
        cursor: Any,
        *,
        procedure: str,
        name_param: str,
        name: str,
        attributes: dict[str, Any],
    ) -> None:
        attributes_json = json.dumps(attributes, ensure_ascii=False)
        self._execute_first_supported_plsql(
            cursor,
            [
                (
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}(
                            {name_param} => :name,
                            attributes => :attributes
                        );
                    END;
                    """,
                    {"name": name, "attributes": attributes_json},
                ),
                (
                    f"""
                    BEGIN
                        DBMS_CLOUD_AI_AGENT.{procedure}(
                            name => :name,
                            attributes => :attributes
                        );
                    END;
                    """,
                    {"name": name, "attributes": attributes_json},
                ),
            ],
        )

    def _execute_first_supported_plsql(
        self, cursor: Any, candidates: list[tuple[str, dict[str, Any]]]
    ) -> None:
        errors: list[str] = []
        for sql, params in candidates:
            try:
                self._execute_plsql(cursor, sql, params)
                return
            except OracleAdapterError as exc:
                if not self._looks_like_signature_error(str(exc)):
                    raise
                errors.append(str(exc))
        raise OracleAdapterError("; ".join(errors) or "Oracle PL/SQL 呼び出しに失敗しました。")

    def _execute_plsql(self, cursor: Any, sql: str, params: dict[str, Any]) -> None:
        try:
            cursor.execute(sql, params)
        except Exception as exc:
            raise OracleAdapterError(f"Oracle PL/SQL 実行に失敗しました: {exc}") from exc

    def _execute_plsql_like(self, cursor: Any, sql: str, params: dict[str, Any]) -> None:
        try:
            cursor.execute(sql, params)
        except Exception as exc:
            raise OracleAdapterError(f"Oracle SQL 実行に失敗しました: {exc}") from exc

    def _drop_best_effort(self, cursor: Any, sql: str, params: dict[str, Any]) -> bool:
        try:
            cursor.execute(sql, params)
        except Exception:
            return False
        return True

    def _drop_cloud_ai_profile_best_effort(self, cursor: Any, profile_name: str) -> None:
        for sql, params in [
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(
                        profile_name => :name,
                        force => TRUE
                    );
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(profile_name => :name);
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(:name, TRUE);
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_CLOUD_AI.DROP_PROFILE(:name);
                END;
                """,
                {"name": profile_name},
            ),
        ]:
            if self._drop_best_effort(cursor, sql, params):
                break

    def _drop_sql_translator_profile_best_effort(self, cursor: Any, profile_name: str) -> None:
        for sql, params in [
            (
                """
                BEGIN
                    DBMS_SQL_TRANSLATOR.DROP_PROFILE(profile_name => :name);
                END;
                """,
                {"name": profile_name},
            ),
            (
                """
                BEGIN
                    DBMS_SQL_TRANSLATOR.DROP_PROFILE(:name);
                END;
                """,
                {"name": profile_name},
            ),
        ]:
            self._drop_best_effort(cursor, sql, params)

    def _looks_like_signature_error(self, message: str) -> bool:
        normalized = message.upper()
        return (
            "PLS-00306" in normalized
            or "PLS-306" in normalized
            or "PLS-00302" in normalized
            or "ORA-00904" in normalized
            or "ORA-06550" in normalized
        )

    def _looks_like_profile_already_exists(self, message: str) -> bool:
        normalized = message.upper()
        return "PROFILE" in normalized and "ALREADY EXISTS" in normalized

    def _agent_runtime_error(self, exc: Exception) -> OracleAdapterError:
        message = str(exc)
        if self._looks_like_signature_error(message):
            return OracleAdapterError(
                "Oracle Select AI Agent runtime API がこの database では利用できません。"
                f"DBMS_CLOUD_AI_AGENT の version / 権限を確認してください: {message}"
            )
        return OracleAdapterError(f"Oracle Select AI Agent 実行に失敗しました: {message}")

    def _looks_like_agent_profile_loss(self, message: str) -> bool:
        normalized = message.upper()
        return "INVALID PROFILE" in normalized or "ORA-20046" in normalized

    def _tool_name_from_team_name(self, team_name: str) -> str:
        if team_name.endswith("_TEAM"):
            return f"{team_name[: -len('_TEAM')]}_TOOL"
        return f"{team_name}_TOOL"

    def _admin_statement_type(self, statement: str) -> str:
        stripped = str(statement or "").strip()
        while stripped.startswith("--") or stripped.startswith("/*"):
            if stripped.startswith("--"):
                newline = stripped.find("\n")
                stripped = "" if newline < 0 else stripped[newline + 1 :].lstrip()
            else:
                end = stripped.find("*/")
                stripped = "" if end < 0 else stripped[end + 2 :].lstrip()
        if re.match(r"^comment\s+on\b", stripped, flags=re.IGNORECASE):
            return "COMMENT"
        if re.match(r"^(select|with)\b", stripped, flags=re.IGNORECASE):
            return "SELECT"
        if re.match(r"^(begin|declare|exec|execute)\b", stripped, flags=re.IGNORECASE):
            return "PLSQL"
        for keyword in (
            "insert",
            "update",
            "delete",
            "merge",
            "create",
            "drop",
            "alter",
            "truncate",
            "grant",
            "revoke",
        ):
            if re.match(rf"^{keyword}\b", stripped, flags=re.IGNORECASE):
                return keyword.upper()
        return "UNKNOWN"

    def _normalize_admin_statement(self, statement: str) -> str:
        stripped = str(statement or "").strip()
        if re.match(r"^(exec|execute)\b", stripped, flags=re.IGNORECASE):
            body = re.sub(r"^(exec|execute)\s+", "", stripped, flags=re.IGNORECASE).strip()
            return f"BEGIN {body.rstrip(';')}; END;"
        return stripped

    def _fetch_dbms_output(self, cursor: Any, batch: int = 1000) -> str:
        lines: list[str] = []
        try:
            line_var = cursor.var(str)
            status_var = cursor.var(int)
            for _ in range(batch):
                cursor.callproc("dbms_output.get_line", (line_var, status_var))
                if int(status_var.getvalue() or 0) != 0:
                    break
                lines.append(str(line_var.getvalue() or ""))
        except Exception:
            return ""
        return "\n".join(line for line in lines if line)

    def _admin_success_message(self, statement_type: str, row_count: int | None) -> str:
        if statement_type in {"INSERT", "UPDATE", "DELETE", "MERGE"}:
            return f"RowsAffected={row_count if row_count is not None else 0}"
        if statement_type == "PLSQL":
            return "PL/SQL executed"
        if statement_type == "COMMENT":
            return "Comment applied"
        return "OK"

    def _load_oracledb(self) -> Any:
        if self._oracledb is not None:
            return self._oracledb
        try:
            self._oracledb = importlib.import_module("oracledb")
        except ModuleNotFoundError as exc:
            raise OracleAdapterError("python-oracledb がインストールされていません。") from exc
        return self._oracledb

    def _init_client(self, oracledb: Any) -> None:
        if self._client_initialized or self.settings.oracle_driver_mode == "thin":
            return
        if not self.settings.oracle_client_lib_dir:
            return
        init_oracle_client = getattr(oracledb, "init_oracle_client", None)
        if callable(init_oracle_client):
            init_oracle_client(lib_dir=self.settings.oracle_client_lib_dir)
        self._client_initialized = True

    def _coerce_csv_value(self, value: str | None, column: CsvImportColumn) -> Any:
        if value is None or value == "":
            return None
        if column.data_type == "NUMBER":
            try:
                return int(value)
            except ValueError:
                return float(value)
        return value
