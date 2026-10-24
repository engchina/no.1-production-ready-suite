"""実行履歴（チャット・RAG 検索の 1 回の実行。#1890）の記録。

チャットと RAG 検索の画面の実行（回答を作った・作らなかった・安全チェックで止めた・失敗した）を、
`RunRecorder` が 1 行の要約として `rag_run_history` に残す。回答を作った実行の回答・引用・診断は
回答の記録（`rag_answer_records`）が正本で、ここには一覧の絞り込みと詳細の「処理の経過」に使う値だけを
持つ。詳細の「処理の経過」は実行の trace の span のイベント
（`stages_json.progress_events`。#3226）。記録は補助なので、失敗しても検索・回答は止めない。

記録しない実行: 根拠の検索だけ（`retrieval_only`。ナレッジベースの検索テスト・レシピの検索比較）、
業務ガイドの下書きで試した回答、品質評価、MCP（Agent が呼ぶ `rag_search`。Agent の実行履歴に残る）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal, Protocol, cast

from pr_backend_core.logging import safe_error_code, safe_exception_fields
from pr_backend_core.observability.tracing import TraceSummary, finish_operation_trace

from app.config import Settings
from app.rag.stored_answer import stored_citations
from app.schemas.search import RetrievedChunk, StorageFailure

logger = logging.getLogger(__name__)

RunSurface = Literal["search", "chat"]
RunStatus = Literal["success", "blocked", "error"]
RunVerification = Literal["verified", "partial", "unverified"]

# 期限切れの削除は、プロセスごとにこの間隔で 1 回だけ行う（実行のたびに全件を走査しない）。
PURGE_INTERVAL_SECONDS = 3600.0
_last_purge_at: float | None = None


class _StageProgress(Protocol):
    @property
    def stage(self) -> str: ...

    @property
    def outcome(self) -> str: ...


type _ProgressCallback = Callable[[Any], Awaitable[None]]


class _RunStore(Protocol):
    async def save_run(self, record: Mapping[str, object]) -> None: ...

    async def purge_runs(self, retention_days: int) -> int: ...


def answer_verification(diagnostics: Mapping[str, Any] | None) -> RunVerification | None:
    """最終の資料との照合を優先し、旧記録は要求ごとの監査から検証状態を読む。

    - verified: 監査した要求がすべて根拠で答えられている。
    - partial: 一部の要求が答えられていない・確かめられていない。
    - unverified: 監査の記録が無い（承認済み FAQ・確認の質問だけの応答・古い記録など）。
    回答の構造（`envelope.requests`）が無ければ None ではなく unverified にする（回答はある）。
    """
    if diagnostics is None:
        return None
    validation = diagnostics.get("answer_validation")
    if isinstance(validation, Mapping):
        if validation.get("valid") is True:
            return "verified"
        counts = validation.get("counts")
        supported = counts.get("supported") if isinstance(counts, Mapping) else 0
        return "partial" if isinstance(supported, int) and supported > 0 else "unverified"
    envelope = diagnostics.get("envelope")
    requests = envelope.get("requests") if isinstance(envelope, Mapping) else None
    statuses = [
        str(item.get("status") or "unknown")
        for item in (requests if isinstance(requests, list) else [])
        if isinstance(item, Mapping)
    ]
    if not statuses or all(status == "unknown" for status in statuses):
        return "unverified"
    if all(status == "addressed" for status in statuses):
        return "verified"
    return "partial"


@dataclass
class RunRecorder:
    """1 回の実行の記録。終わったときに 1 行を保存する。

    処理の経過は `progress_source`（実行の span のイベントの JSON を返す関数。#3226）から
    保存する。止まった段（`error_stage`）は pipeline の段の知らせ（`observe`）の最後の失敗から
    決める。
    """

    surface: RunSurface
    settings: Settings
    store: _RunStore | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    model_id: str | None = None
    progress_source: Callable[[], list[dict[str, Any]]] | None = None
    # 終わった段（止まった段を決める。保存しない）。
    stages: list[dict[str, object]] = field(default_factory=list)
    # 安全チェックの後の質問（pipeline が受け取った時点で入れる。失敗の記録に使う）。
    question: str | None = None
    trace_summary: TraceSummary | None = None
    # 最後に保存した記録（後から保存の失敗を足して保存し直す。#2342）。
    _last_record: dict[str, object] | None = field(default=None, repr=False)

    def observe(self, callback: _ProgressCallback | None) -> _ProgressCallback:
        """段の進捗の callback を包み、終わった段（成功・失敗・停止）を集める。

        集めた段は止まった段（`error_stage`）に使い、保存しない。
        """

        async def collect(progress: _StageProgress) -> None:
            if progress.outcome != "started":
                self.stages.append({"stage": progress.stage, "outcome": progress.outcome})
            if callback is not None:
                await callback(progress)

        return collect

    async def record(
        self,
        *,
        run_id: str,
        question: str | None,
        search_answer_profile_id: str | None,
        answer_generated: bool,
        status: RunStatus,
        elapsed_ms: float | None,
        citations: Iterable[RetrievedChunk] = (),
        answer_diagnostics: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
        error_stage: str | None = None,
        storage_failures: Iterable[StorageFailure] = (),
    ) -> None:
        """実行を保存する。失敗してもログだけにする（検索・回答の返却を止めない）。

        失敗した実行は、止まった段（渡されなければ集めた段の最後の失敗）とエラーコード（``ORA-03146``
        など）を残す。``storage_failures`` は回答を作ったが保存できなかった記録（#2342）。
        """
        cited = list(citations)
        diagnostics = answer_diagnostics if answer_generated else None
        if error is not None and error_stage is None:
            error_stage = _failed_stage(self.stages)
        error_code = safe_error_code(error) if error is not None else None
        failures = list(storage_failures)
        summary = finish_operation_trace(
            run_id,
            status=(
                "cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "failed"
                if error is not None
                else "done"
            ),
        )
        summary = summary or self.trace_summary
        record: dict[str, object] = {
            "run_id": run_id,
            "surface": self.surface,
            "search_answer_profile_id": search_answer_profile_id,
            "question": question,
            "answer_generated": answer_generated,
            "status": status,
            "answer_outcome": (diagnostics or {}).get("outcome") or None,
            "verification": (
                answer_verification(diagnostics)
                if status == "success" and diagnostics is not None
                else None
            ),
            "confidence": str((diagnostics or {}).get("confidence") or "") or None,
            "needs_human_review": (diagnostics or {}).get("needs_human_review"),
            "citation_count": len(cited),
            "elapsed_ms": elapsed_ms,
            "error_stage": error_stage,
            "error_type": type(error).__name__ if error is not None else None,
            "model_id": self.model_id,
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "stages": {
                # 詳細の処理の経過（実行の trace の span のイベント。#3226）。
                "progress_events": self._progress_events(),
                **({"trace_summary": summary.model_dump(mode="json")} if summary else {}),
                # 失敗のエラーコードと保存できなかった記録（処理の段と一緒に持つ。#2342）。
                **({"error_code": str(error_code)} if error_code is not None else {}),
                **(
                    {"storage_failures": [item.model_dump() for item in failures]}
                    if failures
                    else {}
                ),
            },
            # 回答を作った実行の根拠は回答の記録にある。回答を作らない RAG 検索の結果だけを持つ。
            "citations": None if answer_generated else stored_citations(cited),
        }
        self._last_record = record
        await self._save(record)

    def _progress_events(self) -> list[dict[str, Any]]:
        if self.progress_source is None:
            return []
        try:
            return self.progress_source()
        except Exception as exc:  # noqa: BLE001 - 処理の経過は補助。実行の記録は残す。
            logger.warning("run history progress dump failed", extra=safe_exception_fields(exc))
            return []

    async def add_storage_failure(self, failure: StorageFailure) -> None:
        """保存した実行に、後から分かった保存の失敗（会話の回答の保存など）を足して保存し直す（#2342）。"""
        record = self._last_record
        if record is None:
            return
        stages = dict(cast(Mapping[str, object], record.get("stages") or {}))
        existing = stages.get("storage_failures")
        stages["storage_failures"] = [
            *(existing if isinstance(existing, list) else []),
            failure.model_dump(),
        ]
        self._last_record = {**record, "stages": stages}
        await self._save(self._last_record)

    async def _save(self, record: Mapping[str, object]) -> None:
        store = self.store
        run_id = record.get("run_id")
        try:
            if store is None:
                from app.clients.oracle_run_history import RunHistoryStore

                store = RunHistoryStore()
            await store.save_run(record)
            await _purge_if_due(store, self.settings)
        except Exception as exc:  # noqa: BLE001 - 実行履歴は補助。検索・回答の返却を止めない。
            logger.warning(
                "run history record failed",
                extra={"trace_id": run_id, **safe_exception_fields(exc)},
            )


def _failed_stage(stages: Iterable[Mapping[str, object]]) -> str | None:
    """集めた段の最後の失敗・中断（止まった段。時間切れ以外の失敗でも空にしない。#2342）。"""
    failed = [
        str(stage.get("stage") or "")
        for stage in stages
        if stage.get("outcome") in {"error", "cancelled"} and stage.get("stage")
    ]
    return failed[-1] if failed else None


async def _purge_if_due(store: _RunStore, settings: Settings) -> None:
    """保持期間（回答の記録と同じ `rag_answer_record_retention_days`）を過ぎた実行を消す。"""
    global _last_purge_at
    retention_days = settings.rag_answer_record_retention_days
    if retention_days <= 0:
        return
    now = monotonic()
    if _last_purge_at is not None and now - _last_purge_at < PURGE_INTERVAL_SECONDS:
        return
    _last_purge_at = now
    await store.purge_runs(retention_days)
