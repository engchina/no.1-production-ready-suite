"""サービス起動/停止の制御層。

driver 抽象で実環境を切り替えられるようにしつつ、今回は ``DockerComposeDriver``
(ローカル開発)のみ実装する。将来 OKE/Container Instances 用 driver を足せる。

セキュリティ要件:
- ``rag_service_control_enabled`` が False の間は呼び出し側が 409 で拒否する(本層は実行しない)。
- service 名は **カタログの allowlist** に限定し、任意コマンド・任意引数は受けない。
- compose のベースコマンドのみ設定で差し替え可能(``rag_service_control_command``)。
- subprocess は timeout 付きで実行し、失敗は exit code/stderr 付きで構造化返却する。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.services.catalog import ServiceCatalogEntry, is_dev_mode

logger = logging.getLogger(__name__)

ServiceAction = Literal["start", "stop", "restart"]

# backend/app/services/control.py → parents[3] = リポジトリ root(services/<…> を解決する基点)。
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ControlResult:
    """制御コマンドの実行結果(非機密)。"""

    ok: bool
    action: ServiceAction
    service_id: str
    exit_code: int | None = None
    detail: str | None = None


class ServiceControlError(Exception):
    """制御コマンドの実行に失敗したことを表す(API は 502 へ正規化)。"""

    def __init__(self, result: ControlResult) -> None:
        super().__init__(result.detail or f"{result.action} failed: {result.service_id}")
        self.result = result


def _compose_args(
    settings: Settings,
    entry: ServiceCatalogEntry,
    action: ServiceAction,
) -> list[str]:
    """compose コマンドの引数配列を組み立てる(shell 補間なし)。

    GPU サービスは compose の profile gate を越えるため ``--profile gpu`` を付ける。
    dev(backend がホスト)では、コンテナの 8000 を localhost の dev_port へ公開する
    override(``docker-compose.dev.yml``)を重ね、ホスト backend が /health・/parse を
    叩けるようにする。prod(backend もコンテナ)は base compose のみ。
    service 名は allowlist 済みエントリからのみ採る。
    """
    base = shlex.split(settings.rag_service_control_command)
    if not base:
        base = ["docker", "compose"]
    file_args = (
        ["-f", "docker-compose.yml", "-f", "docker-compose.dev.yml"]
        if is_dev_mode(settings)
        else []
    )
    profile_args = ["--profile", "gpu"] if entry.profile == "gpu" else []
    if action == "start":
        # --no-build: 数 GB のイメージ build を制御 HTTP リクエスト内で走らせない。
        # 未ビルドなら compose が即エラーを返し、ユーザに事前 build を促す(timeout 回避)。
        return [*base, *file_args, *profile_args, "up", "-d", "--no-build", entry.service_id]
    if action == "stop":
        # stop は profile gate の影響を受けない(既存コンテナを止めるだけ)。
        return [*base, *file_args, "stop", entry.service_id]
    # restart も build しない(既存イメージを使う)。
    return [*base, *file_args, *profile_args, "restart", entry.service_id]


class DockerComposeDriver:
    """``docker compose`` CLI を subprocess で叩く driver(parser 系 / prod 全般)。"""

    async def run(
        self,
        settings: Settings,
        entry: ServiceCatalogEntry,
        action: ServiceAction,
    ) -> ControlResult:
        args = _compose_args(settings, entry, action)
        # dev はホストのリポジトリ root から compose ファイル群を解決する。
        # prod(コンテナ)は cwd を変えず、マウント済み compose を既定の cwd から解決する。
        cwd = str(REPO_ROOT) if is_dev_mode(settings) else None
        timeout = float(settings.rag_service_control_timeout_seconds)
        logger.info(
            "service_control_exec",
            extra={"service_id": entry.service_id, "action": action, "argv": args},
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            return ControlResult(
                ok=False,
                action=action,
                service_id=entry.service_id,
                detail=f"compose コマンドが見つかりません: {exc}",
            )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            with _suppress_process_cleanup():
                await process.wait()
            return ControlResult(
                ok=False,
                action=action,
                service_id=entry.service_id,
                detail=f"timeout({timeout}s)で打ち切りました。",
            )
        if process.returncode == 0:
            return ControlResult(ok=True, action=action, service_id=entry.service_id, exit_code=0)
        detail = (stderr or stdout or b"").decode("utf-8", "replace").strip()
        return ControlResult(
            ok=False,
            action=action,
            service_id=entry.service_id,
            exit_code=process.returncode,
            detail=detail or None,
        )


class _suppress_process_cleanup:
    """kill 後の wait() で出る例外を握り潰す軽量コンテキストマネージャ。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> bool:
        return True


def _runtime_dir() -> Path:
    """dev プロセスの pidfile / logfile 置き場(``<repo_root>/.run/services``)。"""
    path = REPO_ROOT / ".run" / "services"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_pid(pidfile: Path) -> int | None:
    """pidfile から pid を読む。欠如/不正は None。"""
    try:
        return int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """pid のプロセスが生存しているか(signal 0 で確認)。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class UvProcessDriver:
    """``uv run uvicorn`` でサービスをホスト上の detached プロセスとして起動/停止する driver。

    dev(ENVIRONMENT != production)向け。各サービスを ``catalog`` の ``working_dir`` /
    ``dev_port`` に従い ``127.0.0.1`` で起動し、pid を ``.run/services/<id>.pid`` に記録する。
    起動ログは同ディレクトリの ``<id>.log`` へ追記する。
    """

    async def run(
        self,
        settings: Settings,
        entry: ServiceCatalogEntry,
        action: ServiceAction,
    ) -> ControlResult:
        if action == "start":
            return self._start(settings, entry)
        if action == "stop":
            return self._stop(settings, entry)
        stop_result = self._stop(settings, entry)
        if not stop_result.ok:
            return stop_result
        return self._start(settings, entry)

    def _start(self, settings: Settings, entry: ServiceCatalogEntry) -> ControlResult:
        runtime = _runtime_dir()
        pidfile = runtime / f"{entry.service_id}.pid"
        existing = _read_pid(pidfile)
        if existing is not None and _pid_alive(existing):
            # 既に起動済み: 冪等に成功扱い。
            return ControlResult(ok=True, action="start", service_id=entry.service_id, exit_code=0)

        workdir = REPO_ROOT / entry.working_dir
        if not workdir.is_dir():
            return ControlResult(
                ok=False,
                action="start",
                service_id=entry.service_id,
                detail=f"サービスのディレクトリが見つかりません: {entry.working_dir}",
            )
        argv = [
            "uv",
            "run",
            "--directory",
            str(workdir),
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(entry.dev_port),
        ]
        logfile = runtime / f"{entry.service_id}.log"
        logger.info(
            "service_control_exec",
            extra={"service_id": entry.service_id, "action": "start", "argv": argv},
        )
        try:
            log = logfile.open("ab")
        except OSError as exc:
            return ControlResult(
                ok=False,
                action="start",
                service_id=entry.service_id,
                detail=f"ログファイルを開けません: {exc}",
            )
        try:
            # start_new_session=True で独立プロセスグループにし、backend と寿命を切り離す。
            process = subprocess.Popen(  # noqa: S603 - argv は固定 + allowlist 済みエントリのみ
                argv,
                cwd=str(REPO_ROOT),
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return ControlResult(
                ok=False,
                action="start",
                service_id=entry.service_id,
                detail=f"uv コマンドが見つかりません: {exc}",
            )
        finally:
            log.close()
        pidfile.write_text(str(process.pid))
        return ControlResult(ok=True, action="start", service_id=entry.service_id, exit_code=0)

    def _stop(self, settings: Settings, entry: ServiceCatalogEntry) -> ControlResult:
        runtime = _runtime_dir()
        pidfile = runtime / f"{entry.service_id}.pid"
        pid = _read_pid(pidfile)
        if pid is None or not _pid_alive(pid):
            pidfile.unlink(missing_ok=True)
            return ControlResult(ok=True, action="stop", service_id=entry.service_id, exit_code=0)
        logger.info(
            "service_control_exec",
            extra={"service_id": entry.service_id, "action": "stop", "pid": pid},
        )
        timeout = float(settings.rag_service_control_timeout_seconds)
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pidfile.unlink(missing_ok=True)
            return ControlResult(ok=True, action="stop", service_id=entry.service_id, exit_code=0)
        deadline = time.monotonic() + timeout
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _pid_alive(pid):
            # graceful 終了しなければ強制終了する。
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        pidfile.unlink(missing_ok=True)
        return ControlResult(ok=True, action="stop", service_id=entry.service_id, exit_code=0)


class ServiceControlClient:
    """カタログ allowlist と feature flag を front に、mode 別 driver へ委譲する。

    driver の選択は mode と **エントリ単位の ``dev_runner``** で決まる:
    - prod: 常に ``DockerComposeDriver``。
    - dev かつ ``dev_runner == "uv"``(軽量な前処理): ``UvProcessDriver``(ホストプロセス)。
    - dev かつ ``dev_runner == "docker"``(重い ML 依存の parser): ``DockerComposeDriver``
      (dev override でポート公開)。host への巨大依存 sync を避ける。
    """

    def __init__(
        self,
        docker_driver: DockerComposeDriver | None = None,
        uv_driver: UvProcessDriver | None = None,
    ) -> None:
        self._docker_driver = docker_driver or DockerComposeDriver()
        self._uv_driver = uv_driver or UvProcessDriver()

    async def control(
        self,
        settings: Settings,
        entry: ServiceCatalogEntry,
        action: ServiceAction,
    ) -> ControlResult:
        """allowlist 済みエントリに対し action を実行する。失敗は例外で送出する。"""
        use_uv = is_dev_mode(settings) and entry.dev_runner == "uv"
        driver: UvProcessDriver | DockerComposeDriver = (
            self._uv_driver if use_uv else self._docker_driver
        )
        result = await driver.run(settings, entry, action)
        if not result.ok:
            raise ServiceControlError(result)
        return result
