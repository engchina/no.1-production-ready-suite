"""サービス起動/停止の制御層。

driver 抽象で実環境を切り替えられるようにしつつ、今回は ``DockerComposeDriver``
(dev/prod とも docker compose)のみ実装する。将来 OKE/Container Instances 用 driver を足せる。

セキュリティ要件:
- ``rag_service_control_enabled`` が False の間は呼び出し側が 409 で拒否する(本層は実行しない)。
- service 名は **カタログの allowlist** に限定し、任意コマンド・任意引数は受けない。
- compose のベースコマンドのみ設定で差し替え可能(``rag_service_control_command``)。
- subprocess は timeout 付きで実行し、失敗は exit code/stderr 付きで構造化返却する。
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.services.catalog import ServiceCatalogEntry, is_dev_mode

logger = logging.getLogger(__name__)

ServiceAction = Literal["start", "stop", "restart"]
ServiceLogsSource = Literal["docker"]

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
        # OCR 系 GPU は engine 別 profile で個別に enable する(vLLM はイメージ内包)。
        for prefix, engine_profile in (
            ("parser-unlimited-ocr", "unlimited-ocr"),
            ("parser-dots-ocr", "dots-ocr"),
            ("parser-glm-ocr", "glm-ocr"),
        ):
            if entry.service_id.startswith(prefix):
                profiles.append(engine_profile)
                break
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
    """``docker compose`` CLI を subprocess で叩く driver(dev/prod とも)。"""

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


class ServiceControlClient:
    """カタログ allowlist と feature flag を front に、``DockerComposeDriver`` へ委譲する。

    dev は ``docker-compose.dev.yml`` を重ねてポートを localhost へ公開し、prod は base
    compose のみ。いずれも docker compose で起動/停止する。
    """

    def __init__(self, docker_driver: DockerComposeDriver | None = None) -> None:
        self._docker_driver = docker_driver or DockerComposeDriver()
        # サービス単位の直列化ロック(同一サービスへの同時 start で二重操作を防ぐ)。
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
        # 同一サービスへの操作は直列化する(並行 start の race を回避)。
        async with self._lock_for(entry.service_id):
            result = await self._docker_driver.run(settings, entry, action)
        if not result.ok:
            raise ServiceControlError(result)
        return result


async def read_service_logs(
    settings: Settings,
    entry: ServiceCatalogEntry,
    lines: int,
) -> ServiceLogsResult:
    """allowlist 済みサービスのログ末尾を ``docker compose logs`` から返す。"""
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
