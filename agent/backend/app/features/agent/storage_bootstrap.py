"""Run の repository・画面で変えた定義・履歴の読み込み（#1212）。

DB に接続できないあいだも backend は起動する（RAG / NL2SQL と同じ。画面は DB ゲートで案内する）。
読み込みは起動時にバックグラウンドで始めて一定の間隔で再試行し、業務の API は読み込みが済むまで
最初の要求で読み込みを試みる（`require_agent_storage`）。DB が起動すれば、再起動せずに読み込む。

- Run の repository（`runtime.get_runtime_repository`）→ 定義の復元（`restore_control_plane`）→
  履歴の準備（評価の整理と Run の事実の backfill）の順に 1 回だけ行い、済んだら `is_ready()`。
- DB に接続できない（接続のエラー）ときは `AgentRuntimeStorageUnavailableError`（API は 503）。
  済んだ段階は持ち越し、次の試行は残りから行う。
- 定義の読み込みの接続以外のエラー（権限など）は、今までどおり記録して先へ進む（画面を止めない）。
- 失敗の直後（`FAILURE_COOLDOWN_SECONDS`）は接続を試さずに同じ理由を返す。DB の停止中に要求ごとに
  接続を待たない（wallet の接続記述子の再試行で、1 回の接続に 1 分以上かかることがある）。
- 別のスレッドが読み込んでいるあいだの要求は `WAIT_SECONDS` まで待ち、超えたら 503 にする（要求の
  スレッドを塞ぎ続けない）。
- DB の状態・システム設定・認証・ユーザーとロールの API は読み込みを待たない（`requires_storage`）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from pr_backend_core.oracle_errors import is_oracle_connection_error, oracle_error_codes

from app.features.agent import control_plane_store, runtime, storage_backend
from app.features.agent.runtime import AgentRuntimeStorageUnavailableError

logger = logging.getLogger(__name__)

# 起動時のバックグラウンドの読み込みの再試行の間隔（秒）。
RETRY_INTERVAL_SECONDS = 15.0
# 失敗の直後に、接続を試さずに同じ理由を返す時間（秒）。
FAILURE_COOLDOWN_SECONDS = 5.0
# 別のスレッドが読み込んでいるあいだ、要求が待つ上限（秒）。
WAIT_SECONDS = 10.0

# 読み込みを待たない API（DB ゲート・システム設定・認証・ユーザーとロール）。`/` 区切りの前方一致。
STORAGE_INDEPENDENT_PATHS = (
    "/api/health",
    "/api/ready",
    "/api/auth",
    "/api/settings/database",
    "/api/settings/model",
    "/api/settings/oci",
    "/api/settings/upload-storage",
    "/api/security/users",
    "/api/security/roles",
    "/api/security/permissions",
)

_LOADING_MESSAGE = (
    "Agent の保存データを読み込んでいます。しばらくしてから、もう一度実行してください。"
)
_LOAD_FAILED_MESSAGE = (
    "Agent の保存データを読み込めません。バックエンドのログ（agent_storage_load_failed）を"
    "確認してください。"
)

_lock = threading.Lock()
_ready = False
_control_plane_restored = False
# 直前の失敗（時刻・利用者に返す理由）。
_failure: tuple[float, str] | None = None
_monotonic: Callable[[], float] = time.monotonic
_stop = threading.Event()
_thread: threading.Thread | None = None


def requires_storage(path: str) -> bool:
    """この API が Run の repository と定義の読み込みを待つか。"""
    return not any(
        path == prefix or path.startswith(f"{prefix}/") for prefix in STORAGE_INDEPENDENT_PATHS
    )


def is_ready() -> bool:
    return _ready


def ensure_ready() -> None:
    """読み込む（済んでいれば何もしない）。

    読み込めなければ `AgentRuntimeStorageUnavailableError`。
    """
    global _ready, _failure
    if _ready:
        return
    if not _lock.acquire(timeout=WAIT_SECONDS):
        raise AgentRuntimeStorageUnavailableError(_LOADING_MESSAGE)
    try:
        if _ready:
            return
        if _failure is not None and _monotonic() - _failure[0] < FAILURE_COOLDOWN_SECONDS:
            raise AgentRuntimeStorageUnavailableError(_failure[1])
        try:
            _load_locked()
        except AgentRuntimeStorageUnavailableError as exc:
            _failure = (_monotonic(), str(exc))
            raise
        except Exception as exc:
            # 接続以外のエラー（明示した Oracle の checkpoint の破損・SQL・権限）。直し方を書いた
            # 例外（checkpoint の破損）はその文を返し、それ以外はログへ案内する。
            logger.exception("agent_storage_load_failed")
            message = (
                str(exc)
                if isinstance(exc, runtime.AgentRuntimeSnapshotCorruptError)
                else _LOAD_FAILED_MESSAGE
            )
            _failure = (_monotonic(), message)
            raise AgentRuntimeStorageUnavailableError(message) from exc
        _failure = None
        _ready = True
    finally:
        _lock.release()
    logger.info("agent_storage_ready", extra={"backend": storage_backend.resolved_backend()})


def _load_locked() -> None:
    global _control_plane_restored
    runtime.get_runtime_repository()
    if storage_backend.fell_back_to_memory():
        # checkpoint が読めず memory にした。定義も同じ保存先にそろえる（作った store を捨てる）。
        control_plane_store.set_control_plane_store(None)
    if not _control_plane_restored:
        _restore_control_plane()
        _control_plane_restored = True
    _prepare_history()


def _restore_control_plane() -> None:
    """画面・API で変えた定義（Skill・プラグイン・MCP 接続・ツール権限）を読み込む（#764）。"""
    try:
        restored = control_plane_store.restore_control_plane()
    except Exception as exc:
        if is_oracle_connection_error(exc):
            codes = ", ".join(oracle_error_codes(exc)) or type(exc).__name__
            raise AgentRuntimeStorageUnavailableError(
                f"Agent の定義の保存先のデータベースに接続できません（{codes}）。"
                "接続できるようになると、再起動せずに読み込みます。"
            ) from exc
        # 接続以外の障害で画面を止めない（今までどおり記録して先へ進む）。
        logger.exception("agent_control_plane_restore_failed")
        return
    logger.info("agent_control_plane_restored", extra={"restored": restored})


def _prepare_history() -> None:
    """評価の履歴の整理（保持期間）と、Run の事実の backfill（#794）。

    Run の事実は Oracle の構成だけ保存する。Runtime repository にある Run の事実をすべて
    キューに入れ、バックグラウンドで MERGE する（今見えている Run の集計を消さない）。
    """
    from app.features.agent import run_facts_store
    from app.features.agent.evaluation import evaluation_store

    try:
        evaluation_store.prune()
    except Exception:  # noqa: BLE001 - 整理の失敗で読み込みを止めない
        logger.exception("agent_evaluation_prune_failed")
    try:
        run_facts_store.backfill(runtime.get_runtime_repository())
    except Exception:  # noqa: BLE001 - 集計用の事実の失敗で読み込みを止めない
        logger.exception("agent_run_facts_backfill_failed")


def start_background_load() -> None:
    """起動時の読み込みをバックグラウンドで始める（済むまで一定の間隔で再試行する）。

    daemon thread にする（DB の停止中の接続の待ちで、終了・`--reload` の再起動を待たせない）。
    """
    global _thread
    if _ready or (_thread is not None and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_load_until_ready, name="agent-storage-load", daemon=True)
    _thread.start()


def stop_background_load() -> None:
    _stop.set()


def _load_until_ready() -> None:
    attempt = 0
    while not _stop.is_set() and not _ready:
        try:
            ensure_ready()
            return
        except AgentRuntimeStorageUnavailableError as exc:
            attempt += 1
            logger.warning(
                "agent_storage_not_ready",
                extra={
                    "attempt": attempt,
                    "retry_seconds": RETRY_INTERVAL_SECONDS,
                    "reason": str(exc),
                },
            )
        except Exception:  # noqa: BLE001 - 再試行を止めない
            attempt += 1
            logger.exception("agent_storage_load_failed")
        _stop.wait(RETRY_INTERVAL_SECONDS)


def reset(*, ready: bool = False) -> None:
    """状態をやり直す（テスト。`ready=True` は読み込み済みにする）。"""
    global _ready, _control_plane_restored, _failure
    with _lock:
        _ready = ready
        _control_plane_restored = ready
        _failure = None
