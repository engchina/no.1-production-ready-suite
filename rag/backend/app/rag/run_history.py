"""実行履歴（チャット・RAG 検索の 1 回の実行。#1890）の記録。

チャットと RAG 検索の画面の実行（回答を作った・作らなかった・安全チェックで止めた・失敗した）を、
`RunRecorder` が 1 行の要約として `rag_run_history` に残す。回答を作った実行の回答・引用・診断は
回答の記録（`rag_answer_records`）が正本で、ここには一覧の絞り込みと詳細の「処理の経過」に使う値だけを
持つ。記録は補助なので、失敗しても検索・回答は止めない。

記録しない実行: 根拠の検索だけ（`retrieval_only`。ナレッジベースの検索テスト・レシピの検索比較）、
業務ガイドの下書きで試した回答、品質評価、MCP（Agent が呼ぶ `rag_search`。Agent の実行履歴に残る）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal, Protocol

from pr_backend_core.logging import safe_exception_fields

from app.config import Settings
from app.rag.stored_answer import stored_citations
from app.schemas.search import RetrievedChunk

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

    @property
    def elapsed_ms(self) -> float: ...


type _ProgressCallback = Callable[[Any], Awaitable[None]]


class _RunStore(Protocol):
    async def save_run(self, record: Mapping[str, object]) -> None: ...

    async def purge_runs(self, retention_days: int) -> int: ...


def answer_verification(diagnostics: Mapping[str, Any] | None) -> RunVerification | None:
    """回答の検証（回答の監査で、質問の要求ごとに根拠で答えられたかを確かめた結果）。

    - verified: 監査した要求がすべて根拠で答えられている。
    - partial: 一部の要求が答えられていない・確かめられていない。
    - unverified: 監査の記録が無い（承認済み FAQ・確認の質問だけの応答・古い記録など）。
    回答の構造（`envelope.requests`）が無ければ None ではなく unverified にする（回答はある）。
    """
    if diagnostics is None:
        return None
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


def _answer_steps(diagnostics: Mapping[str, Any] | None) -> list[dict[str, object]]:
    """回答フローの各工程（名前・状態・秒・LLM の呼び出し回数）。既存の記録（execution_steps）。"""
    steps = diagnostics.get("execution_steps") if diagnostics else None
    result: list[dict[str, object]] = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, Mapping):
            continue
        result.append(
            {
                "name": str(step.get("name") or ""),
                "status": str(step.get("status") or ""),
                "elapsed_seconds": step.get("elapsed_seconds"),
                "llm_calls": step.get("llm_calls"),
            }
        )
    return result


@dataclass
class RunRecorder:
    """1 回の実行の記録。pipeline の段（`observe`）を集め、終わったときに 1 行を保存する。"""

    surface: RunSurface
    settings: Settings
    store: _RunStore | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    model_id: str | None = None
    stages: list[dict[str, object]] = field(default_factory=list)
    # 安全チェックの後の質問（pipeline が受け取った時点で入れる。失敗の記録に使う）。
    question: str | None = None

    def observe(self, callback: _ProgressCallback | None) -> _ProgressCallback:
        """段の進捗の callback を包み、終わった段（成功・失敗・停止）と時間を集める。"""

        async def collect(progress: _StageProgress) -> None:
            if progress.outcome != "started":
                self.stages.append(
                    {
                        "stage": progress.stage,
                        "outcome": progress.outcome,
                        "elapsed_ms": progress.elapsed_ms,
                    }
                )
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
    ) -> None:
        """実行を保存する。失敗してもログだけにする（検索・回答の返却を止めない）。"""
        cited = list(citations)
        diagnostics = answer_diagnostics if answer_generated else None
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
                "stages": list(self.stages),
                "steps": _answer_steps(answer_diagnostics),
            },
            # 回答を作った実行の根拠は回答の記録にある。回答を作らない RAG 検索の結果だけを持つ。
            "citations": None if answer_generated else stored_citations(cited),
        }
        store = self.store
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
