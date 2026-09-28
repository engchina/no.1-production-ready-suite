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
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import (
    Settings,
    enterprise_ai_default_model_id,
    enterprise_ai_vision_model_id,
)
from app.services.catalog import (
    ServiceCatalogEntry,
    is_dev_mode,
)

logger = logging.getLogger(__name__)

ServiceAction = Literal["start", "stop", "restart", "build", "remove"]
ServiceLogsSource = Literal["docker"]

# backend/app/services/control.py → parents[3] = リポジトリ root(services/<…> を解決する基点)。
REPO_ROOT = Path(__file__).resolve().parents[3]

# compose の project 名。docker-compose.yml の top-level ``name:``・本番の init_script.sh の
# wrapper(``--project-name``)・scripts/build-services.sh と同じ値にする(#310)。
# 未指定だと compose ファイルのディレクトリ名から決まり、画面から操作・ログ取得する project と
# 実際にコンテナが動いている project がずれる(ずれると logs は exit 0 で空を返す)。
COMPOSE_PROJECT_NAME = "production-ready-rag"
# 対象のコンテナが無いと成功扱いにならない操作(ログ取得は read_service_logs で別に確かめる)。
_ACTIONS_REQUIRING_CONTAINER: frozenset[ServiceAction] = frozenset({"stop", "restart"})


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


def _compose_project_directory(settings: Settings) -> Path | None:
    """compose の project directory(build context・相対 volume の基点)。

    dev はホストのリポジトリ root(``rag/``)。prod(backend もコンテナ)はマウントした compose
    ファイルの場所が既定の project directory になるため指定しない(None)。
    """
    return REPO_ROOT if is_dev_mode(settings) else None


def _compose_command(settings: Settings) -> list[str]:
    """compose のベースコマンド(設定で差し替え可能。空なら ``docker compose``)。"""
    return shlex.split(settings.rag_service_control_command) or ["docker", "compose"]


def _compose_target_args(settings: Settings, entry: ServiceCatalogEntry) -> list[str]:
    """ベースコマンドとサブコマンドの間に置く引数(project 名・directory・ファイル・profile)。

    project 名は常に固定し(``COMPOSE_PROJECT_NAME``)、どの cwd から実行しても同じ project の
    コンテナを操作する。dev は ``docker-compose.dev.yml`` を重ね、コンテナの 8000 を localhost の
    dev_port へ公開してホスト backend が /health・/parse を叩けるようにする(ファイルは
    project directory からの絶対パスで渡す)。prod(backend もコンテナ)は base compose のみ。
    GPU サービスは compose の profile gate を越えるため ``--profile gpu`` を付ける。
    """
    project_args = ["--project-name", COMPOSE_PROJECT_NAME]
    project_directory = _compose_project_directory(settings)
    file_args: list[str] = []
    if project_directory is not None:
        project_args += ["--project-directory", str(project_directory)]
        file_args = [
            "-f",
            str(project_directory / "docker-compose.yml"),
            "-f",
            str(project_directory / "docker-compose.dev.yml"),
        ]
    return [*project_args, *file_args, *_compose_profile_args(entry)]


def _compose_base_args(settings: Settings, entry: ServiceCatalogEntry) -> list[str]:
    """compose のサブコマンド前までの引数配列(ベースコマンド + 対象の指定)。"""
    return [*_compose_command(settings), *_compose_target_args(settings, entry)]


def _compose_args(
    settings: Settings,
    entry: ServiceCatalogEntry,
    action: ServiceAction,
) -> list[str]:
    """compose コマンドの引数配列を組み立てる(shell 補間なし)。

    service 名は allowlist 済みエントリからのみ採る。
    """
    prefix = _compose_base_args(settings, entry)
    if action == "start":
        # --no-build: 数 GB の build を start では走らせない(明示の build アクションで行う)。
        # 未ビルドなら compose が即エラーを返し、ユーザに事前 build を促す(timeout 回避)。
        return [*prefix, "up", "-d", "--no-build", entry.service_id]
    if action == "stop":
        # GPU サービスは profile gate に隠れるため stop でも --profile gpu を付ける
        # (付けても既存コンテナを止めるだけで無害)。
        return [*prefix, "stop", entry.service_id]
    if action == "build":
        # 明示的なイメージ build。長時間になるため呼び出し側は build 用 timeout を使う。
        return [*prefix, "build", entry.service_id]
    if action == "remove":
        # コンテナ削除。-s で稼働中なら停止してから、-f で確認なしに削除する。
        return [*prefix, "rm", "-f", "-s", entry.service_id]
    # restart も build しない(既存イメージを使う)。
    return [*prefix, "restart", entry.service_id]


def _compose_logs_args(
    settings: Settings,
    entry: ServiceCatalogEntry,
    lines: int,
) -> list[str]:
    """docker compose logs の引数配列を組み立てる(shell 補間なし)。"""
    return [
        *_compose_base_args(settings, entry),
        "logs",
        "--no-color",
        "--tail",
        str(lines),
        entry.service_id,
    ]


def _compose_ps_args(settings: Settings, entry: ServiceCatalogEntry) -> list[str]:
    """対象サービスのコンテナ ID(停止中を含む)を列挙する引数配列。"""
    return [*_compose_base_args(settings, entry), "ps", "--all", "--quiet", entry.service_id]


def _missing_container_message(entry: ServiceCatalogEntry) -> str:
    """compose project に対象のコンテナが無いときの案内(操作・ログ共通)。"""
    return (
        f"{entry.service_id} のコンテナが compose project「{COMPOSE_PROJECT_NAME}」に"
        "見つかりません。サービスを起動してから再実行してください。"
        f"別の project 名で起動したコンテナは、停止してから「{COMPOSE_PROJECT_NAME}」で"
        "作り直してください(rag/docs/deployment.md の既存環境の更新手順(#310))。"
    )


def _compose_env(settings: Settings) -> dict[str, str]:
    """compose subprocess へ渡す環境変数。

    HuggingFace 設定(``RAG_HUGGINGFACE_*``)を docker-compose の ``${HF_TOKEN}`` /
    ``${HF_ENDPOINT}`` substitution へ供給する(DL 認証/ミラー)。parser コンテナ内の
    huggingface_hub が読む標準名のため、compose 側の名前は変えない。

    OCI parser マイクロサービス(parser-oci-genai-vision 等)はモデル設定 JSON を読まず env
    からのみ OCI 設定を読むため、backend が解決済みの実効値(model-settings.json 由来を含む)を
    ``${PLATFORM_OCI_ENTERPRISE_AI_*}`` substitution へ供給する。これで「モデル画面で設定 →
    parser 再起動 → 稼働中」が成立する(parser は起動時に 1 回だけ env を読むため再起動が必要)。
    ``os.environ`` を継承しつつ上書きする。
    """
    env = dict(os.environ)
    env["HF_TOKEN"] = settings.huggingface_token
    env["HF_ENDPOINT"] = settings.huggingface_endpoint
    # OCI parser コンテナの env へ橋渡しする実効 OCI Enterprise AI 設定。未設定は空文字のまま渡し、
    # parser 側は引き続き degraded(=正しい挙動)になる。api_key は env で渡り argv には乗らない。
    env["PLATFORM_OCI_ENTERPRISE_AI_ENDPOINT"] = settings.oci_enterprise_ai_endpoint
    env["PLATFORM_OCI_ENTERPRISE_AI_API_KEY"] = settings.oci_enterprise_ai_api_key
    env["PLATFORM_OCI_ENTERPRISE_AI_PROJECT_OCID"] = settings.oci_enterprise_ai_project_ocid
    env["PLATFORM_OCI_ENTERPRISE_AI_VLM_MODEL"] = enterprise_ai_vision_model_id(settings)
    env["PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_MODEL"] = enterprise_ai_default_model_id(settings)
    env["PLATFORM_OCI_ENTERPRISE_AI_VLM_PATH"] = settings.oci_enterprise_ai_vlm_path
    env["PLATFORM_OCI_ENTERPRISE_AI_LLM_PATH"] = settings.oci_enterprise_ai_llm_path
    env["PLATFORM_OCI_ENTERPRISE_AI_VLM_INPUT_MODE"] = str(
        settings.oci_enterprise_ai_vlm_input_mode
    )
    return env


def _compose_profile_args(entry: ServiceCatalogEntry) -> list[str]:
    """compose profile 引数を service catalog から組み立てる。"""
    profiles: list[str] = []
    if entry.profile == "gpu":
        profiles.append("gpu")
    return [arg for profile in profiles for arg in ("--profile", profile)]


def _build_command_hint(settings: Settings, entry: ServiceCatalogEntry) -> str:
    """未ビルド時にユーザへ案内する build コマンド(実行する compose と同じ project・ファイル)。

    案内は常に ``docker compose`` で示す(設定のベースコマンドは内部実装の差し替え用)。
    """
    target = _compose_target_args(settings, entry)
    return shlex.join(["docker", "compose", *target, "build", entry.service_id])


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


@dataclass(frozen=True)
class _ComposeOutput:
    """compose subprocess の終了コードと出力(decode 済み)。"""

    returncode: int
    stdout: str
    stderr: str


class _ComposeExecError(Exception):
    """compose を実行できなかった(コマンド無し / timeout)。メッセージは利用者向け。"""


async def _exec_compose(settings: Settings, args: list[str], timeout: float) -> _ComposeOutput:
    """compose を shell なし・timeout 付きで実行する(操作・ログ・コンテナ確認で共通)。"""
    # dev はホストのリポジトリ root から compose ファイル群を解決する。
    # prod(コンテナ)は cwd を変えず、マウント済み compose を既定の cwd から解決する。
    cwd = str(REPO_ROOT) if is_dev_mode(settings) else None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=_compose_env(settings),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise _ComposeExecError(f"compose コマンドが見つかりません: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        with _suppress_process_cleanup():
            await process.wait()
        raise _ComposeExecError(f"timeout({timeout}s)で打ち切りました。") from exc
    return _ComposeOutput(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout=(stdout or b"").decode("utf-8", "replace").strip(),
        stderr=(stderr or b"").decode("utf-8", "replace").strip(),
    )


async def _service_container_exists(settings: Settings, entry: ServiceCatalogEntry) -> bool:
    """compose project に対象サービスのコンテナ(停止中を含む)があるかを返す。

    ``compose logs`` / ``stop`` / ``restart`` は、project にコンテナが無くても exit 0 で何もせず
    終わるため、空のログや成功として返さないよう事前に確かめる(#310)。
    確認そのものに失敗したら ``_ComposeExecError`` を送出する。
    """
    output = await _exec_compose(
        settings,
        _compose_ps_args(settings, entry),
        float(settings.rag_service_control_timeout_seconds),
    )
    if output.returncode != 0:
        raise _ComposeExecError(
            output.stderr or output.stdout or "コンテナの状態を確認できませんでした。"
        )
    return bool(output.stdout)


class DockerComposeDriver:
    """``docker compose`` CLI を subprocess で叩く driver(dev/prod とも)。"""

    async def run(
        self,
        settings: Settings,
        entry: ServiceCatalogEntry,
        action: ServiceAction,
    ) -> ControlResult:
        args = _compose_args(settings, entry, action)
        # build はイメージ生成で長時間になるため別枠の長い timeout を使う。
        timeout = float(
            settings.rag_service_build_timeout_seconds
            if action == "build"
            else settings.rag_service_control_timeout_seconds
        )
        logger.info(
            "service_control_exec",
            extra={"service_id": entry.service_id, "action": action, "argv": args},
        )
        try:
            if action in _ACTIONS_REQUIRING_CONTAINER and not await _service_container_exists(
                settings, entry
            ):
                return ControlResult(
                    ok=False,
                    action=action,
                    service_id=entry.service_id,
                    detail=_missing_container_message(entry),
                )
            output = await _exec_compose(settings, args, timeout)
        except _ComposeExecError as exc:
            return ControlResult(
                ok=False,
                action=action,
                service_id=entry.service_id,
                detail=str(exc),
            )
        if output.returncode == 0:
            return ControlResult(ok=True, action=action, service_id=entry.service_id, exit_code=0)
        raw = output.stderr or output.stdout
        detail = _friendly_compose_error(raw, settings, entry)
        return ControlResult(
            ok=False,
            action=action,
            service_id=entry.service_id,
            exit_code=output.returncode,
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
    """allowlist 済みサービスのログ末尾を ``docker compose logs`` から返す。

    compose project に対象のコンテナが無いときは ``ServiceLogsError`` にする(空のログとして
    返さない)。コンテナがあってログが 0 行なら、空の本文をそのまま返す。
    """
    timeout = float(settings.rag_service_control_timeout_seconds)
    try:
        if not await _service_container_exists(settings, entry):
            raise ServiceLogsError(_missing_container_message(entry))
        output = await _exec_compose(settings, _compose_logs_args(settings, entry, lines), timeout)
    except _ComposeExecError as exc:
        raise ServiceLogsError(str(exc)) from exc
    if output.returncode != 0:
        raise ServiceLogsError(output.stderr or output.stdout or "ログ取得に失敗しました。")
    return ServiceLogsResult(
        service_id=entry.service_id,
        source="docker",
        lines=lines,
        content=output.stdout,
    )
