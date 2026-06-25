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
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.services.catalog import ServiceCatalogEntry, is_dev_mode
from app.services.service_env import oci_service_env

logger = logging.getLogger(__name__)

ServiceAction = Literal["start", "stop", "restart"]
ServiceLogsSource = Literal["docker", "uv"]

# backend/app/services/control.py → parents[3] = リポジトリ root(services/<…> を解決する基点)。
REPO_ROOT = Path(__file__).resolve().parents[3]

# uv プロセス起動直後に「即死していないか」を確認するまでの待機秒数。
_START_VERIFY_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class ControlResult:
    """制御コマンドの実行結果(非機密)。"""

    ok: bool
    action: ServiceAction
    service_id: str
    exit_code: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ServiceLogsResult:
    """サービスログの末尾(非機密メタデータ + 本文)。"""

    service_id: str
    source: ServiceLogsSource
    lines: int
    content: str


class ServiceControlError(Exception):
    """制御コマンドの実行に失敗したことを表す(API は 502 へ正規化)。"""

    def __init__(self, result: ControlResult) -> None:
        super().__init__(result.detail or f"{result.action} failed: {result.service_id}")
        self.result = result


class ServiceLogsError(Exception):
    """サービスログ取得に失敗したことを表す(API は 502 へ正規化)。"""


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
    profile_args = _compose_profile_args(entry)
    if action == "start":
        # --no-build: 数 GB のイメージ build を制御 HTTP リクエスト内で走らせない。
        # 未ビルドなら compose が即エラーを返し、ユーザに事前 build を促す(timeout 回避)。
        return [*base, *file_args, *profile_args, "up", "-d", "--no-build", entry.service_id]
    if action == "stop":
        # GPU サービスは profile gate に隠れるため stop でも --profile gpu を付ける
        # (付けても既存コンテナを止めるだけで無害)。
        return [*base, *file_args, *profile_args, "stop", entry.service_id]
    # restart も build しない(既存イメージを使う)。
    return [*base, *file_args, *profile_args, "restart", entry.service_id]


def _compose_logs_args(
    settings: Settings,
    entry: ServiceCatalogEntry,
    lines: int,
) -> list[str]:
    """docker compose logs の引数配列を組み立てる(shell 補間なし)。"""
    base = shlex.split(settings.rag_service_control_command)
    if not base:
        base = ["docker", "compose"]
    file_args = (
        ["-f", "docker-compose.yml", "-f", "docker-compose.dev.yml"]
        if is_dev_mode(settings)
        else []
    )
    return [
        *base,
        *file_args,
        *_compose_profile_args(entry),
        "logs",
        "--no-color",
        "--tail",
        str(lines),
        entry.service_id,
    ]


def _compose_profile_args(entry: ServiceCatalogEntry) -> list[str]:
    """compose profile 引数を service catalog から組み立てる。"""
    profiles: list[str] = []
    if entry.profile == "gpu":
        profiles.append("gpu")
    if entry.service_id.endswith("-vllm"):
        profiles.append("gpu-vllm")
    return [arg for profile in profiles for arg in ("--profile", profile)]


def _build_command_hint(settings: Settings, entry: ServiceCatalogEntry) -> str:
    """未ビルド時にユーザへ案内する build コマンド(dev は override・GPU は profile 付き)。"""
    files = "-f docker-compose.yml -f docker-compose.dev.yml " if is_dev_mode(settings) else ""
    profile = " ".join(_compose_profile_args(entry))
    if profile:
        profile = f"{profile} "
    return f"docker compose {files}{profile}build {entry.service_id}"


def _friendly_compose_error(detail: str, settings: Settings, entry: ServiceCatalogEntry) -> str:
    """compose の生エラーを、実行可能な案内付きの分かりやすい文言へ正規化する。"""
    low = detail.lower()
    if "no such image" in low or "image not found" in low:
        # --no-build のため未ビルドのイメージで up すると発生する。事前 build を促す。
        return (
            f"{entry.service_id} のイメージが未ビルドです。先にビルドしてください: "
            f"{_build_command_hint(settings, entry)}"
        )
    return detail


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
        raw = (stderr or stdout or b"").decode("utf-8", "replace").strip()
        detail = _friendly_compose_error(raw, settings, entry)
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


def _read_log_tail(logfile: Path, max_chars: int = 600) -> str:
    """起動失敗時の原因提示用に、ログファイル末尾を返す(取得不可は空文字)。"""
    try:
        text = logfile.read_text("utf-8", "replace").strip()
    except OSError:
        return ""
    return text[-max_chars:]


def _read_log_tail_lines(logfile: Path, lines: int) -> str:
    """ログファイル末尾を行数指定で読む(ファイル欠如は空文字)。"""
    try:
        with logfile.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=lines)).strip()
    except OSError:
        return ""


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


def _proc_cmdline(pid: int) -> str | None:
    """``/proc/<pid>/cmdline`` を空白区切り文字列で返す(取得不可・非 Linux は None)。"""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace")


def _pid_is_service(pid: int, entry: ServiceCatalogEntry) -> bool:
    """pid が生存し、かつ当該サービスの uvicorn プロセスであることを確認する。

    pidfile の PID が OS により無関係なプロセスへ再利用された場合に、誤って稼働中と
    判定したり無関係なプロセスグループを kill するのを防ぐ。``/proc`` が無い環境では
    cmdline 照合を諦め、生存判定にフォールバックする(従来挙動)。
    """
    if not _pid_alive(pid):
        return False
    cmdline = _proc_cmdline(pid)
    if cmdline is None:
        return True
    return "uvicorn" in cmdline and f"--port {entry.dev_port}" in cmdline


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
            return await self._start_and_verify(settings, entry)
        if action == "stop":
            return self._stop(settings, entry)
        stop_result = self._stop(settings, entry)
        if not stop_result.ok:
            return stop_result
        return await self._start_and_verify(settings, entry)

    async def _start_and_verify(
        self, settings: Settings, entry: ServiceCatalogEntry
    ) -> ControlResult:
        """起動を試み、新規 spawn 時は短時間後に生存を確認する。

        ポート競合や import エラーで即死した場合、``start`` 段階で失敗を返し、ログ末尾を
        添えて原因を提示する(冪等な「既に起動済み」では検証しない)。
        """
        result, spawned = self._start(settings, entry)
        if not result.ok or not spawned:
            return result
        await asyncio.sleep(_START_VERIFY_DELAY_SECONDS)
        runtime = _runtime_dir()
        pidfile = runtime / f"{entry.service_id}.pid"
        pid = _read_pid(pidfile)
        if pid is not None and _pid_alive(pid):
            return result
        pidfile.unlink(missing_ok=True)
        tail = _read_log_tail(runtime / f"{entry.service_id}.log")
        detail = "起動直後にプロセスが終了しました(ポート競合や依存エラーの可能性)。"
        if tail:
            detail = f"{detail}\n{tail}"
        return ControlResult(ok=False, action="start", service_id=entry.service_id, detail=detail)

    def _start(self, settings: Settings, entry: ServiceCatalogEntry) -> tuple[ControlResult, bool]:
        """起動処理本体。``(結果, 新規 spawn したか)`` を返す。"""
        runtime = _runtime_dir()
        pidfile = runtime / f"{entry.service_id}.pid"
        existing = _read_pid(pidfile)
        if existing is not None and _pid_is_service(existing, entry):
            # 既に起動済み: 冪等に成功扱い(spawn していない)。
            return (
                ControlResult(ok=True, action="start", service_id=entry.service_id, exit_code=0),
                False,
            )

        workdir = REPO_ROOT / entry.working_dir
        if not workdir.is_dir():
            return (
                ControlResult(
                    ok=False,
                    action="start",
                    service_id=entry.service_id,
                    detail=f"サービスのディレクトリが見つかりません: {entry.working_dir}",
                ),
                False,
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
            return (
                ControlResult(
                    ok=False,
                    action="start",
                    service_id=entry.service_id,
                    detail=f"ログファイルを開けません: {exc}",
                ),
                False,
            )
        # microservice は os.environ(from_env)から設定を読むが、backend の有効設定は
        # Settings(.env + UI 上書き)で os.environ に載らない。profile=oci のサービスには
        # backend の OCI 設定を env で渡し、UI/.env で設定した値を子プロセスへ届ける
        # (API キー等は OCI サービスにのみ渡す)。
        child_env = os.environ.copy()
        # uv run --directory 先の project .venv を使わせる。backend の active venv は継承しない。
        child_env.pop("VIRTUAL_ENV", None)
        child_env.pop("VIRTUAL_ENV_PROMPT", None)
        if entry.profile == "oci":
            child_env.update(oci_service_env(settings))
        try:
            # start_new_session=True で独立プロセスグループにし、backend と寿命を切り離す。
            process = subprocess.Popen(  # noqa: S603 - argv は固定 + allowlist 済みエントリのみ
                argv,
                cwd=str(REPO_ROOT),
                env=child_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return (
                ControlResult(
                    ok=False,
                    action="start",
                    service_id=entry.service_id,
                    detail=f"uv コマンドが見つかりません: {exc}",
                ),
                False,
            )
        finally:
            log.close()
        pidfile.write_text(str(process.pid))
        return (
            ControlResult(ok=True, action="start", service_id=entry.service_id, exit_code=0),
            True,
        )

    def _stop(self, settings: Settings, entry: ServiceCatalogEntry) -> ControlResult:
        runtime = _runtime_dir()
        pidfile = runtime / f"{entry.service_id}.pid"
        pid = _read_pid(pidfile)
        # PID 再利用対策: 生存していても当該サービスでなければ kill せず noop 成功扱い。
        if pid is None or not _pid_is_service(pid, entry):
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
        # サービス単位の直列化ロック(同一サービスへの同時 start で二重 spawn/孤児化を防ぐ)。
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, service_id: str) -> asyncio.Lock:
        lock = self._locks.get(service_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[service_id] = lock
        return lock

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
        # 同一サービスへの操作は直列化する(並行 start の race を回避)。
        async with self._lock_for(entry.service_id):
            result = await driver.run(settings, entry, action)
        if not result.ok:
            raise ServiceControlError(result)
        return result


async def read_service_logs(
    settings: Settings,
    entry: ServiceCatalogEntry,
    lines: int,
) -> ServiceLogsResult:
    """allowlist 済みサービスのログ末尾を返す。docker は compose、dev uv は .run を読む。"""
    if is_dev_mode(settings) and entry.dev_runner == "uv":
        content = _read_log_tail_lines(_runtime_dir() / f"{entry.service_id}.log", lines)
        return ServiceLogsResult(
            service_id=entry.service_id,
            source="uv",
            lines=lines,
            content=content,
        )

    args = _compose_logs_args(settings, entry, lines)
    cwd = str(REPO_ROOT) if is_dev_mode(settings) else None
    timeout = float(settings.rag_service_control_timeout_seconds)
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ServiceLogsError(f"compose コマンドが見つかりません: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        with _suppress_process_cleanup():
            await process.wait()
        raise ServiceLogsError(f"timeout({timeout}s)で打ち切りました。") from exc
    raw = (stdout or stderr or b"").decode("utf-8", "replace").strip()
    if process.returncode != 0:
        raise ServiceLogsError(raw or "ログ取得に失敗しました。")
    return ServiceLogsResult(
        service_id=entry.service_id,
        source="docker",
        lines=lines,
        content=raw,
    )
