"""合成データの受理、実行、回復。実行不明の attempt は自動再送しない。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from app.security.domain import Principal
from app.security.request_actor import actor_scope
from app.security.service import get_security_service
from app.settings import Settings, get_settings

from .models import SyntheticDataGenerateRequest
from .oracle_adapter import OracleNl2SqlAdapter
from .synthetic_models import TERMINAL, SyntheticRun, SyntheticRunRequest, SyntheticTarget, now
from .synthetic_oracle import capture_session, inspect_operation
from .synthetic_store import SyntheticConflict, SyntheticStore

logger = logging.getLogger(__name__)


def context_id(settings: Settings) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                settings.nl2sql_runtime_mode,
                settings.nl2sql_persistence_mode,
                settings.oracle_dsn,
                settings.oracle_user,
                settings.oracle_wallet_dir,
            ]
        ).encode()
    ).hexdigest()


def authorize(principal: Principal | None) -> Principal:
    if (
        not principal
        or principal.status != "ACTIVE"
        or principal.force_password_change
        or not principal.has_any_permission({"menu.data_management", "menu.sample_data"})
    ):
        raise HTTPException(403, "合成データ生成を利用する権限がありません。")
    return principal


class SyntheticService:
    def __init__(
        self,
        settings: Settings,
        *,
        store: SyntheticStore | None = None,
        adapter: OracleNl2SqlAdapter | None = None,
        resolve_actor: Callable[[str], Principal] | None = None,
    ):
        self.context = context_id(settings)
        self.settings = settings
        self.adapter = adapter or OracleNl2SqlAdapter(settings)
        self.store = store or SyntheticStore(
            self.adapter.connection if settings.nl2sql_persistence_mode == "oracle" else None
        )
        self.resolve_actor = resolve_actor or (
            lambda actor: get_security_service().principal_for_worker(actor)
        )
        self._threads: dict[str, threading.Thread] = {}
        self._thread_lock = threading.Lock()

    def preflight(self, names: list[str], profile_name: str) -> dict[str, int]:
        if self.settings.nl2sql_persistence_mode != "oracle":
            raise HTTPException(409, "生成状況を保存するため Oracle persistence が必要です。")
        if self.settings.nl2sql_runtime_mode != "oracle":
            raise HTTPException(409, "合成データ生成には Oracle runtime が必要です。")
        identities = [self.adapter._db_admin_identity(name) for name in names]
        result: dict[str, int] = {}
        with self.adapter.connection() as conn, conn.cursor() as cur:
            for identity in identities:
                binds = {"owner": identity.owner, "name": identity.object_name}
                cur.execute(
                    "SELECT OBJECT_ID FROM ALL_OBJECTS WHERE OWNER=:owner "
                    "AND OBJECT_NAME=:name AND OBJECT_TYPE='TABLE'",
                    binds,
                )
                row = cur.fetchone()
                if not row:
                    raise HTTPException(
                        400,
                        f"{identity.qualified_name}: "
                        "対象テーブルが存在しないか参照権限がありません。",
                    )
                result[identity.qualified_name] = int(row[0])
                cur.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS "
                    "WHERE OWNER=:owner AND TABLE_NAME=:name",
                    binds,
                )
                unsupported = [
                    f"{c}({t})"
                    for c, t in cur.fetchall()
                    if str(t).upper()
                    in {
                        "BLOB",
                        "CLOB",
                        "NCLOB",
                        "RAW",
                        "LONG",
                        "LONG RAW",
                        "VECTOR",
                        "XMLTYPE",
                        "BFILE",
                    }
                ]
                if unsupported:
                    raise HTTPException(
                        400,
                        f"{identity.qualified_name}: 生成非対応の列があります: "
                        + ", ".join(unsupported)
                        + "。"
                        "対象から除外して確認し直してください。",
                    )
            cur.execute(
                "SELECT COUNT(*) FROM USER_CLOUD_AI_PROFILES WHERE PROFILE_NAME=:name",
                {"name": profile_name.upper()},
            )
            if not cur.fetchone()[0]:
                raise HTTPException(400, "選択した Select AI Profile が存在しません。")
        return result

    def create(self, request: SyntheticRunRequest, principal: Principal | None) -> SyntheticRun:
        actor = authorize(principal)
        if request.profile_id and not actor.can_use_profile(request.profile_id):
            raise HTTPException(403, "この Profile を利用する権限がありません。")
        source = [request.table_name] if request.table_name.strip() else request.object_list
        names = list(
            dict.fromkeys(
                self.adapter._db_admin_identity(n).qualified_name for n in source if n.strip()
            )
        )
        if not names or len(names) > 100 or not request.profile_name.strip():
            raise HTTPException(400, "Profile と 1〜100 件の対象テーブルを指定してください。")
        expected = (
            {names[0], names[0].split(".", 1)[-1]}
            if request.table_name.strip()
            else {"ADMIN_EXECUTE"}
        )
        # 非 current-schema の単一表では owner-qualified 確認が必要。
        if names[0].split(".", 1)[0] != self.settings.oracle_user.upper():
            expected.discard(names[0].split(".", 1)[-1])
        if request.confirmation.strip() not in expected:
            raise HTTPException(
                400, "実行確認語が対象と一致しません。確認して入力し直してください。"
            )
        payload = request.model_dump(exclude={"confirmation", "idempotency_key"})
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        old = self.store.by_key(actor.user_uuid, self.context, request.idempotency_key)
        if old:
            if old.request_hash != digest:
                raise HTTPException(409, "同じ受付キーの条件が異なります。")
            return old
        payload["_object_ids"] = self.preflight(names, request.profile_name.strip())
        run = SyntheticRun(
            run_id=str(uuid4()),
            actor_id=actor.user_uuid,
            context_id=self.context,
            idempotency_key=request.idempotency_key,
            request_hash=digest,
            request=payload,
            targets=[
                SyntheticTarget(
                    table_name=n, requested_rows=request.rows_per_table or request.row_count
                )
                for n in names
            ],
        )
        try:
            return self.store.create(run)
        except SyntheticConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    def get(self, run_id: str, principal: Principal | None) -> SyntheticRun:
        actor = authorize(principal)
        run = self.store.get(run_id)
        if not run or run.actor_id != actor.user_uuid or run.context_id != self.context:
            raise HTTPException(404, "生成記録が見つかりません。")
        return run

    def list(self, principal: Principal | None) -> list[SyntheticRun]:
        actor = authorize(principal)
        return self.store.list(self.context, actor.user_uuid)

    def update(self, run_id: str, fn: Callable[[SyntheticRun], None]) -> None:
        for _ in range(10):
            run = self.store.get(run_id)
            if not run or run.status in TERMINAL:
                return
            fn(run)
            if self.store.save(run):
                return
        raise RuntimeError("生成状態の競合により更新できません。")

    def execute(self, run_id: str) -> None:
        try:
            run = self.store.get(run_id)
            if not run:
                return
            actor = authorize(self.resolve_actor(run.actor_id))
            if run.context_id != context_id(get_settings()):
                raise HTTPException(409, "受付時から DB 接続先が変更されました。")
            req = SyntheticDataGenerateRequest.model_validate(
                {k: v for k, v in run.request.items() if k != "_object_ids"}
            )
            if req.profile_id and not actor.can_use_profile(req.profile_id):
                raise HTTPException(403, "Profile の利用権限が変更されました。")
            ids = self.preflight([t.table_name for t in run.targets], req.profile_name)
            if ids != run.request.get("_object_ids"):
                raise HTTPException(
                    409, "受付後に対象テーブルが作り直されました。再確認が必要です。"
                )

            def record_session(conn: Any) -> None:
                session = capture_session(conn)
                self.update(run_id, lambda r: setattr(r, "session", session))
                # Callback の永続化を確認してから、初めて Oracle の mutation を許可する。
                stored = self.store.get(run_id)
                if not stored or stored.session != session or stored.status != "running":
                    raise RuntimeError("実行セッションの保存を確認できません。")

            with actor_scope(actor.user_uuid, is_system_admin=actor.is_system_admin):
                self.adapter.generate_synthetic_data(
                    table_name=run.targets[0].table_name if len(run.targets) == 1 else "",
                    object_list=[t.table_name for t in run.targets] if len(run.targets) > 1 else [],
                    row_count=req.rows_per_table or req.row_count,
                    profile_name=req.profile_name,
                    user_prompt="\n".join(p for p in [req.user_prompt, req.extra_prompt] if p),
                    sample_rows=req.sample_rows,
                    use_comments=req.use_comments,
                    on_connection=record_session,
                )

            def returned(r: SyntheticRun) -> None:
                r.execution_returned = True
                r.status = "verifying"

            self.update(run_id, returned)
        except Exception as exc:
            message = str(getattr(exc, "detail", getattr(exc, "public_message", str(exc))))[:2000]

            def failed(r: SyntheticRun) -> None:
                r.message = message
                r.execution_returned = True
                r.status = "unknown" if r.session else "failed"
                if not r.session:
                    r.finished_at = now()
                    for t in r.targets:
                        t.status, t.loaded_rows, t.error = "failed", 0, message

            self.update(run_id, failed)

    def reconcile(self, run: SyntheticRun) -> None:
        if run.status == "pending" or run.status in TERMINAL:
            return
        try:
            latest = inspect_operation(self.adapter, run)
            latest.checked_at = now()
            if latest.status in TERMINAL:
                latest.finished_at = now()
            elif latest.status == "verifying" or (
                not latest.operation_ids
                and latest.started_at
                and (datetime.now(UTC) - datetime.fromisoformat(latest.started_at)).total_seconds()
                > 120
            ):
                latest.status = "unknown"
                latest.message = (
                    latest.message
                    or "対応する Oracle 実行結果を確認できません。"
                    "再生成せず状態を再確認してください。"
                )
            self.store.save(latest)
        except Exception:
            logger.warning("synthetic_run_reconciliation_failed", extra={"run_id": run.run_id})
            run.status = "unknown"
            run.message = "Oracle の生成状況を取得できません。処理を再送せず確認を続けています。"
            self.store.save(run)

    def tick(self) -> None:
        # CAS で pending→running を一度だけ claim。期限切れでも生成を再実行しない。
        with self._thread_lock:
            self._threads = {k: t for k, t in self._threads.items() if t.is_alive()}
            for run in reversed(self.store.list(self.context, active=True)):
                if run.status == "pending" and len(self._threads) < 2:
                    run.status, run.started_at = "running", now()
                    if self.store.save(run):
                        thread = threading.Thread(
                            target=self.execute, args=(run.run_id,), daemon=True
                        )
                        self._threads[run.run_id] = thread
                        thread.start()
                else:
                    self.reconcile(run)


_services: dict[str, SyntheticService] = {}
_services_lock = threading.Lock()


def get_synthetic_service() -> SyntheticService:
    settings = get_settings()
    key = context_id(settings)
    with _services_lock:
        if key not in _services or _services[key].settings != settings:
            _services[key] = SyntheticService(settings)
        return _services[key]


def worker_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            get_synthetic_service().tick()
        except Exception:
            logger.warning("synthetic_worker_poll_failed")
        stop.wait(2)
