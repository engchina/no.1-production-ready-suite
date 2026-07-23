"""業務 Profile から Oracle DBMS_CLOUD_AI asset へ反映する永続 job。"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.settings import get_settings

from .models import (
    ProfileSelectAiProfileRequest,
    ProfileSyncJobData,
    ProfileSyncJobPhase,
    ProfileSyncJobRequest,
    ProfileSyncJobStatus,
)
from .ontology_observability import record_job
from .ontology_store import OntologyStore, OntologyVersionConflict, canonical_json
from .service import Nl2SqlService, nl2sql_service

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    ProfileSyncJobStatus.SUCCEEDED,
    ProfileSyncJobStatus.FAILED,
    ProfileSyncJobStatus.CANCELLED,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ProfileSyncService:
    """Oracle Profile 同期を API process / external worker で共有する。"""

    def __init__(
        self,
        *,
        service: Nl2SqlService = nl2sql_service,
        store_provider: Callable[[], OntologyStore] | None = None,
    ) -> None:
        self._service = service
        self._store_provider = store_provider or self._default_store
        self._jobs: dict[str, ProfileSyncJobData] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _default_store() -> OntologyStore:
        # Router import cycle を避け、初回 API 呼び出し時に共有 store を解決する。
        from .ontology_router import ontology_runtime

        return ontology_runtime.store

    @property
    def store(self) -> OntologyStore:
        return self._store_provider()

    def start(
        self,
        profile_id: str,
        request: ProfileSyncJobRequest,
        *,
        idempotency_key: str,
    ) -> ProfileSyncJobData:
        if request.confirmation.strip() != "ADMIN_EXECUTE":
            raise ValueError("実行には confirmation=ADMIN_EXECUTE が必要です。")
        profile = self._service.get_profile(profile_id)
        request_hash = hashlib.sha256(
            canonical_json(
                {
                    "profile_id": profile.id,
                    "profile_etag": profile.etag,
                    "rebuild_agent_assets": request.rebuild_agent_assets,
                }
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            existing = self._idempotent_job(idempotency_key, request_hash)
            if existing is not None:
                return existing
            job = ProfileSyncJobData(
                job_id=f"profile_sync_{uuid4().hex}",
                profile_id=profile.id,
                profile_etag=profile.etag,
                rebuild_agent_assets=request.rebuild_agent_assets,
                created_at=_now(),
            )
            reservation = {
                "operation": "profile_sync",
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "resource_id": job.job_id,
                "status": "accepted",
                "created_at": job.created_at,
            }
            try:
                self.store.save_idempotency(reservation)
            except OntologyVersionConflict:
                # 別 worker が同じ unique key を先に確保した。勝者の job を返す。
                for _attempt in range(20):
                    existing = self._idempotent_job(idempotency_key, request_hash)
                    if existing is not None:
                        return existing
                    time.sleep(0.01)
                raise RuntimeError(
                    "同じ Oracle Profile 同期を別 worker が受付中です。再試行してください。"
                ) from None
            try:
                self._save(job)
            except Exception:
                try:
                    self.store.delete_documents(
                        "idempotency",
                        {
                            "operation": "profile_sync",
                            "idempotency_key": idempotency_key,
                        },
                    )
                except Exception:
                    logger.warning(
                        "profile_sync_idempotency_cleanup_failed",
                        exc_info=True,
                        extra={"job_id": job.job_id},
                    )
                raise
        if get_settings().nl2sql_ontology_worker_mode == "inprocess":
            threading.Thread(
                target=self._run_safely,
                args=(job.job_id,),
                daemon=True,
            ).start()
        return job.model_copy(deep=True)

    def _idempotent_job(
        self,
        idempotency_key: str,
        request_hash: str,
    ) -> ProfileSyncJobData | None:
        existing = self.store.get_idempotency("profile_sync", idempotency_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise ValueError(
                "同じ Idempotency-Key が別の Oracle Profile 同期に使用されています。"
            )
        return self.get(str(existing.get("resource_id") or ""))

    def get(self, job_id: str) -> ProfileSyncJobData | None:
        document = self.store.get_job(job_id)
        if document is None or document.get("job_type") != "profile_sync":
            return None
        job = ProfileSyncJobData.model_validate(document["payload"])
        with self._lock:
            self._jobs[job_id] = job
        return job.model_copy(deep=True)

    def retry(self, job_id: str) -> ProfileSyncJobData:
        previous = self.get(job_id)
        if previous is None:
            raise KeyError(job_id)
        if previous.status != ProfileSyncJobStatus.FAILED:
            raise ValueError("失敗した Oracle Profile 同期 job だけを再試行できます。")
        profile = self._service.get_profile(previous.profile_id)
        job = ProfileSyncJobData(
            job_id=f"profile_sync_{uuid4().hex}",
            profile_id=profile.id,
            profile_etag=profile.etag,
            rebuild_agent_assets=previous.rebuild_agent_assets,
            retry_of_job_id=previous.job_id,
            created_at=_now(),
        )
        self._save(job)
        if get_settings().nl2sql_ontology_worker_mode == "inprocess":
            threading.Thread(target=self._run_safely, args=(job.job_id,), daemon=True).start()
        return job.model_copy(deep=True)

    def cancel_for_profile(self, profile_id: str) -> int:
        cancelled = 0
        for document in self.store.list_documents("jobs", {"profile_id": profile_id}):
            if document.get("job_type") != "profile_sync":
                continue
            job = ProfileSyncJobData.model_validate(document["payload"])
            if job.status in _TERMINAL_STATUSES:
                continue
            cancelled_job = job.model_copy(
                update={
                    "status": ProfileSyncJobStatus.CANCELLED,
                    "phase": ProfileSyncJobPhase.CANCELLED,
                    "error_code": "PROFILE_DELETED",
                    "error_message_ja": "業務 Profile が削除されたため同期を中止しました。",
                    "finished_at": _now(),
                }
            )
            self._save(cancelled_job)
            cancelled += 1
        return cancelled

    def run_persisted(self, job_id: str) -> ProfileSyncJobData:
        """永続化済み job を安全に実行し、失敗も terminal status へ確定する。"""

        self._run_safely(job_id)
        result = self.get(job_id)
        if result is None:
            raise RuntimeError("Oracle Profile 同期 job の実行結果を取得できません。")
        return result

    def _execute(self, job_id: str) -> ProfileSyncJobData:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("Oracle Profile 同期 job が見つかりません。")
        if job.status == ProfileSyncJobStatus.CANCELLED:
            return job
        deadline = time.monotonic() + max(
            1.0,
            get_settings().nl2sql_profile_sync_job_timeout_seconds,
        )
        running = job.model_copy(
            update={
                "status": ProfileSyncJobStatus.RUNNING,
                "phase": ProfileSyncJobPhase.SYNCING_ORACLE_PROFILE,
                "started_at": job.started_at or _now(),
                "error_code": "",
                "error_message_ja": "",
            }
        )
        self._save(running)
        self._assert_current_profile(running)

        oracle_result = self._service.upsert_profile_select_ai_profile(
            running.profile_id,
            ProfileSelectAiProfileRequest(
                confirmation="ADMIN_EXECUTE",
                reason="profile-sync-job",
            ),
        )
        if not oracle_result.executed or oracle_result.status == "error":
            warning = " ".join(oracle_result.warnings).strip()
            raise RuntimeError(warning or "Oracle DBMS_CLOUD_AI Profile の反映に失敗しました。")
        running = running.model_copy(update={"oracle_result": oracle_result})
        self._assert_deadline(deadline)
        self._assert_not_cancelled(running.job_id)

        if running.rebuild_agent_assets:
            running = running.model_copy(
                update={"phase": ProfileSyncJobPhase.REBUILDING_AGENT_ASSETS}
            )
            self._save(running)
            agent_result = self._service.refresh_select_ai_agent_assets(
                running.profile_id,
                profile_already_synced=True,
            )
            if not agent_result.refreshed:
                raise RuntimeError(
                    agent_result.warning or "Select AI Agent asset の再構築に失敗しました。"
                )
            running = running.model_copy(update={"agent_result": agent_result})

        self._assert_deadline(deadline)
        self._assert_not_cancelled(running.job_id)
        running = running.model_copy(update={"phase": ProfileSyncJobPhase.VERIFYING})
        self._save(running)
        self._assert_current_profile(running)
        succeeded = running.model_copy(
            update={
                "status": ProfileSyncJobStatus.SUCCEEDED,
                "phase": ProfileSyncJobPhase.SUCCEEDED,
                "finished_at": _now(),
            }
        )
        self._save(succeeded)
        record_job(job_type="profile_sync", status="succeeded")
        return succeeded.model_copy(deep=True)

    def _run_safely(self, job_id: str) -> None:
        try:
            self._execute(job_id)
        except Exception as exc:  # pragma: no cover - 最終防壁は status で検証する
            logger.warning("profile_sync_job_failed", exc_info=True, extra={"job_id": job_id})
            current = self.get(job_id)
            if current is None or current.status == ProfileSyncJobStatus.CANCELLED:
                return
            failed = current.model_copy(
                update={
                    "status": ProfileSyncJobStatus.FAILED,
                    "phase": ProfileSyncJobPhase.FAILED,
                    "error_code": "PROFILE_SYNC_FAILED",
                    "error_message_ja": (
                        f"Oracle Profile の反映に失敗しました: {exc} 再試行してください。"
                    ),
                    "finished_at": _now(),
                }
            )
            self._save(failed)
            record_job(
                job_type="profile_sync",
                status="failed",
                error_code="PROFILE_SYNC_FAILED",
            )

    def _assert_current_profile(self, job: ProfileSyncJobData) -> None:
        profile = self._service.get_profile(job.profile_id)
        if job.profile_etag and profile.etag != job.profile_etag:
            raise RuntimeError(
                "業務 Profile が同期受付後に更新されました。最新版から再試行してください。"
            )

    def _assert_not_cancelled(self, job_id: str) -> None:
        current = self.get(job_id)
        if current is not None and current.status == ProfileSyncJobStatus.CANCELLED:
            raise RuntimeError("業務 Profile が削除されたため同期を中止しました。")

    @staticmethod
    def _assert_deadline(deadline: float) -> None:
        if time.monotonic() > deadline:
            seconds = max(1.0, get_settings().nl2sql_profile_sync_job_timeout_seconds)
            raise TimeoutError(f"Oracle Profile 同期 job が {seconds:g} 秒の期限を超えました。")

    def _save(self, job: ProfileSyncJobData) -> None:
        current = self.store.get_job(job.job_id)
        document: dict[str, Any] = {
            "job_id": job.job_id,
            "job_type": "profile_sync",
            "profile_id": job.profile_id,
            "status": job.status.value,
            "payload": job.model_dump(mode="json"),
        }
        if current is not None:
            for field in ("claimed_by", "claimed_at"):
                if current.get(field) is not None:
                    document[field] = current[field]
        self.store.save_job(
            document,
            expected_etag=str(current["etag"]) if current is not None else None,
        )
        with self._lock:
            self._jobs[job.job_id] = job.model_copy(deep=True)


profile_sync_service = ProfileSyncService()


__all__ = ["ProfileSyncService", "profile_sync_service"]
