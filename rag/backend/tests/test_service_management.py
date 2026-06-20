"""サービス管理(カタログ / 稼働プローブ / 制御 / API)のテスト。"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from typing import Any, Literal

import pytest
from pytest import MonkeyPatch

from app.config import get_settings
from app.main import app
from app.services import control as control_module
from app.services.catalog import (
    SERVICE_CATALOG,
    ServiceCatalogEntry,
    get_catalog_entry,
    is_dev_mode,
    service_health_url,
)
from app.services.control import (
    ControlResult,
    DockerComposeDriver,
    ServiceControlClient,
    ServiceControlError,
    UvProcessDriver,
    _compose_args,
)
from app.services.status import probe_service_statuses
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


# --- カタログ ---------------------------------------------------------------


def test_catalog_ids_unique_and_url_fields_resolve() -> None:
    settings = get_settings()
    ids = [entry.service_id for entry in SERVICE_CATALOG]
    assert len(ids) == len(set(ids)), "service_id は一意であること"
    for entry in SERVICE_CATALOG:
        # URL フィールドが Settings に実在し、文字列で取得できること。
        assert hasattr(settings, entry.url_field)
        assert isinstance(service_health_url(settings, entry), str)


def test_catalog_covers_preprocess_and_parser_with_gpu() -> None:
    categories = {entry.category for entry in SERVICE_CATALOG}
    # preprocess / parser に加え、pipeline ステージのプラグイン(chunking 等)を含む。
    assert {"preprocess", "parser", "chunking"} <= categories
    gpu_ids = {entry.service_id for entry in SERVICE_CATALOG if entry.profile == "gpu"}
    assert gpu_ids == {"parser-mineru", "parser-dots-ocr", "parser-glm-ocr", "parser-asr"}


def test_get_catalog_entry_allowlist() -> None:
    assert get_catalog_entry("parser-docling") is not None
    assert get_catalog_entry("unknown-service") is None
    assert get_catalog_entry("../etc/passwd") is None


# --- 稼働プローブ -----------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], raise_error: bool = False) -> None:
        self._payload = payload
        self._raise = raise_error

    def raise_for_status(self) -> None:
        if self._raise:
            raise RuntimeError("http error")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """status probe 用の httpx.AsyncClient 代替。url→応答 を引く。"""

    routes: dict[str, _FakeResponse] = {}
    raise_on_connect: set[str] = set()

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        base = url.removesuffix("/health")
        if base in self.raise_on_connect:
            raise ConnectionError("connection refused")
        return self.routes.get(base, _FakeResponse({"status": "ok"}))


def _patch_probe_httpx(monkeypatch: MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


def test_probe_normalizes_statuses(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    _patch_probe_httpx(monkeypatch)

    docling = service_health_url(settings, get_catalog_entry("parser-docling"))  # type: ignore[arg-type]
    marker = service_health_url(settings, get_catalog_entry("parser-marker"))  # type: ignore[arg-type]
    _FakeAsyncClient.routes = {
        docling: _FakeResponse({"status": "ok"}),
        marker: _FakeResponse({"status": "degraded"}),
    }
    _FakeAsyncClient.raise_on_connect = {
        service_health_url(settings, get_catalog_entry("parser-unstructured"))  # type: ignore[arg-type]
    }
    try:
        statuses = asyncio.run(probe_service_statuses(settings))
    finally:
        _FakeAsyncClient.routes = {}
        _FakeAsyncClient.raise_on_connect = set()

    assert statuses["parser-docling"] == "running"
    assert statuses["parser-marker"] == "degraded"
    assert statuses["parser-unstructured"] == "stopped"


def test_probe_unconfigured_when_url_blank(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    # unconfigured 判定は prod(url_field を使う)経路の挙動。dev は dev_port 既定で常に解決される。
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_parser_docling_service_url", "")
    _patch_probe_httpx(monkeypatch)
    statuses = asyncio.run(probe_service_statuses(settings))
    assert statuses["parser-docling"] == "unconfigured"


# --- 制御層 -----------------------------------------------------------------


def test_compose_args_gpu_gets_profile_flag(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")  # prod: override 無し
    mineru = get_catalog_entry("parser-mineru")
    assert mineru is not None
    args = _compose_args(settings, mineru, "start")
    assert args == [
        "docker",
        "compose",
        "--profile",
        "gpu",
        "up",
        "-d",
        "--no-build",
        "parser-mineru",
    ]
    # GPU は profile gate に隠れるため stop / restart でも --profile gpu を付ける。
    assert _compose_args(settings, mineru, "stop") == [
        "docker",
        "compose",
        "--profile",
        "gpu",
        "stop",
        "parser-mineru",
    ]
    assert _compose_args(settings, mineru, "restart") == [
        "docker",
        "compose",
        "--profile",
        "gpu",
        "restart",
        "parser-mineru",
    ]


def test_compose_args_cpu_start_and_stop(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")  # prod: override 無し
    docling = get_catalog_entry("parser-docling")
    assert docling is not None
    # start は --no-build(制御リクエスト内で build しない)。
    assert _compose_args(settings, docling, "start") == [
        "docker",
        "compose",
        "up",
        "-d",
        "--no-build",
        "parser-docling",
    ]
    assert _compose_args(settings, docling, "stop") == [
        "docker",
        "compose",
        "stop",
        "parser-docling",
    ]


def test_compose_args_dev_adds_override_files(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    docling = get_catalog_entry("parser-docling")
    assert docling is not None
    # dev は port 公開 override を重ねてホスト backend から到達可能にする。
    assert _compose_args(settings, docling, "start") == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.dev.yml",
        "up",
        "-d",
        "--no-build",
        "parser-docling",
    ]
    mineru = get_catalog_entry("parser-mineru")
    assert mineru is not None
    # GPU は override に加えて --profile gpu。
    assert _compose_args(settings, mineru, "start") == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.dev.yml",
        "--profile",
        "gpu",
        "up",
        "-d",
        "--no-build",
        "parser-mineru",
    ]


def test_friendly_compose_error_maps_missing_image(monkeypatch: MonkeyPatch) -> None:
    from app.services.control import _friendly_compose_error

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    docling = get_catalog_entry("parser-docling")
    assert docling is not None
    raw = (
        "Error response from daemon: No such image: "
        "no1-production-ready-rag-parser-docling:latest"
    )
    friendly = _friendly_compose_error(raw, settings, docling)
    assert "未ビルド" in friendly
    assert "docker compose" in friendly and "build parser-docling" in friendly
    assert "docker-compose.dev.yml" in friendly  # dev は override 付きで案内

    # GPU は --profile gpu を含める。
    mineru = get_catalog_entry("parser-mineru")
    assert mineru is not None
    assert "--profile gpu" in _friendly_compose_error("no such image: x", settings, mineru)

    # 既知でないエラーはそのまま返す。
    assert _friendly_compose_error("boom", settings, docling) == "boom"


class _FakeProcess:
    def __init__(self, returncode: int, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", self._stderr

    def kill(self) -> None:  # pragma: no cover - timeout テストでのみ使用
        pass

    async def wait(self) -> int:  # pragma: no cover
        return self.returncode


def test_control_client_raises_on_nonzero_exit(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")  # docker driver 経路
    entry = get_catalog_entry("parser-docling")
    assert entry is not None

    async def fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return _FakeProcess(returncode=1, stderr=b"boom")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    client_obj = ServiceControlClient(docker_driver=DockerComposeDriver())
    with pytest.raises(ServiceControlError) as exc:
        asyncio.run(client_obj.control(settings, entry, "start"))
    assert exc.value.result.exit_code == 1
    assert exc.value.result.detail == "boom"


def test_control_client_success(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")  # docker driver 経路
    entry = get_catalog_entry("parser-docling")
    assert entry is not None

    async def fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return _FakeProcess(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(ServiceControlClient().control(settings, entry, "stop"))
    assert isinstance(result, ControlResult)
    assert result.ok is True


# --- API --------------------------------------------------------------------


def test_list_services_returns_catalog_prod(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_service_control_enabled", False)

    async def fake_probe(_settings: Any) -> dict[str, str]:
        return {entry.service_id: "stopped" for entry in SERVICE_CATALOG}

    monkeypatch.setattr("app.api.routes.services.probe_service_statuses", fake_probe)
    resp = client.get("/api/services")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # prod + flag OFF は可視化のみ。
    assert data["control_enabled"] is False
    assert data["deployment_mode"] == "prod"
    assert len(data["services"]) == len(SERVICE_CATALOG)
    assert {s["service_id"] for s in data["services"]} == {e.service_id for e in SERVICE_CATALOG}


def test_list_services_dev_auto_enables_control(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "rag_service_control_enabled", False)

    async def fake_probe(_settings: Any) -> dict[str, str]:
        return {entry.service_id: "stopped" for entry in SERVICE_CATALOG}

    monkeypatch.setattr("app.api.routes.services.probe_service_statuses", fake_probe)
    data = client.get("/api/services").json()["data"]
    # dev は flag OFF でも制御を自動有効化。
    assert data["control_enabled"] is True
    assert data["deployment_mode"] == "dev"


def test_control_rejected_when_disabled_in_prod(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_service_control_enabled", False)
    resp = client.post("/api/services/parser-docling/start")
    assert resp.status_code == 409


def test_control_unknown_service_is_404(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_service_control_enabled", True)
    resp = client.post("/api/services/unknown-service/stop")
    assert resp.status_code == 404


def test_control_success_returns_updated_status(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_service_control_enabled", True)

    async def fake_control(
        _self: Any,
        _settings: Any,
        entry: ServiceCatalogEntry,
        action: Literal["start", "stop", "restart"],
    ) -> ControlResult:
        return ControlResult(ok=True, action=action, service_id=entry.service_id, exit_code=0)

    async def fake_probe(_settings: Any, entry: ServiceCatalogEntry) -> str:
        # 操作対象 1 件のみ再プローブする(route は probe_service_status を使う)。
        return "running"

    monkeypatch.setattr(ServiceControlClient, "control", fake_control)
    monkeypatch.setattr("app.api.routes.services.probe_service_status", fake_probe)
    resp = client.post("/api/services/parser-docling/start")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["service_id"] == "parser-docling"
    assert data["action"] == "start"
    assert data["status"] == "running"


def test_control_failure_returns_502(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_service_control_enabled", True)

    async def fake_control(
        _self: Any,
        _settings: Any,
        entry: ServiceCatalogEntry,
        action: Literal["start", "stop", "restart"],
    ) -> ControlResult:
        raise ServiceControlError(
            ControlResult(
                ok=False,
                action=action,
                service_id=entry.service_id,
                exit_code=1,
                detail="compose failed",
            )
        )

    monkeypatch.setattr(ServiceControlClient, "control", fake_control)
    resp = client.post("/api/services/parser-marker/stop")
    assert resp.status_code == 502


# --- dev モード(uv プロセス driver)-----------------------------------------


def test_is_dev_mode_maps_environment() -> None:
    settings = get_settings()
    for value, expected in (
        ("development", True),
        ("dev", True),
        ("", True),
        ("production", False),
        ("PROD", False),
    ):
        object.__setattr__(settings, "environment", value)
        try:
            assert is_dev_mode(settings) is expected
        finally:
            object.__setattr__(settings, "environment", "dev")


def test_service_health_url_dev_uses_dev_port(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, entry.url_field, "http://preprocess-csv-to-json:8000")
    assert service_health_url(settings, entry) == f"http://127.0.0.1:{entry.dev_port}"


def test_resolve_service_base_url_dev_rewrites_docker_default(monkeypatch: MonkeyPatch) -> None:
    from app.services.catalog import resolve_service_base_url

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    # docker 既定(host == compose service 名)→ dev_port へ書き換え(画面プローブと一致)。
    monkeypatch.setattr(settings, "rag_parser_docling_service_url", "http://parser-docling:8000")
    monkeypatch.setattr(
        settings, "rag_preprocess_csv_to_json_service_url", "http://preprocess-csv-to-json:8000"
    )
    assert (
        resolve_service_base_url(settings, "rag_parser_docling_service_url")
        == "http://127.0.0.1:8020"
    )
    assert (
        resolve_service_base_url(settings, "rag_preprocess_csv_to_json_service_url")
        == "http://127.0.0.1:8012"
    )


def test_resolve_service_base_url_dev_respects_overrides(monkeypatch: MonkeyPatch) -> None:
    from app.services.catalog import resolve_service_base_url

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    # 明示上書き(host != service 名)は尊重する。
    monkeypatch.setattr(settings, "rag_parser_docling_service_url", "http://127.0.0.1:9999")
    assert (
        resolve_service_base_url(settings, "rag_parser_docling_service_url")
        == "http://127.0.0.1:9999"
    )
    # 空欄(未設定)はそのまま空文字(unconfigured)。
    monkeypatch.setattr(settings, "rag_parser_docling_service_url", "")
    assert resolve_service_base_url(settings, "rag_parser_docling_service_url") == ""


def test_resolve_service_base_url_prod_uses_setting(monkeypatch: MonkeyPatch) -> None:
    from app.services.catalog import resolve_service_base_url

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_parser_docling_service_url", "http://parser-docling:8000/")
    assert (
        resolve_service_base_url(settings, "rag_parser_docling_service_url")
        == "http://parser-docling:8000"
    )


def test_parser_client_service_url_dev_resolves_localhost(monkeypatch: MonkeyPatch) -> None:
    from app.clients.parser_service import ParserServiceClient

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "rag_parser_docling_service_url", "http://parser-docling:8000")
    monkeypatch.setattr(settings, "rag_parser_mineru_service_url", "http://parser-mineru:8000")
    client = ParserServiceClient(settings)
    assert client.service_url("docling") == "http://127.0.0.1:8020"
    assert client.service_url("mineru") == "http://127.0.0.1:8023"


def test_preprocess_service_url_dev_resolves_localhost(monkeypatch: MonkeyPatch) -> None:
    from app.rag.preprocess_strategy import preprocess_service_url

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(
        settings, "rag_preprocess_csv_to_json_service_url", "http://preprocess-csv-to-json:8000"
    )
    monkeypatch.setattr(
        settings, "rag_preprocess_office_to_pdf_service_url", "http://preprocess-office-to-pdf:8000"
    )
    assert preprocess_service_url(settings, "csv_to_json") == "http://127.0.0.1:8012"
    assert preprocess_service_url(settings, "office_to_pdf") == "http://127.0.0.1:8010"


def test_service_health_url_prod_uses_url_field(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, entry.url_field, "http://preprocess-csv-to-json:8000/")
    assert service_health_url(settings, entry) == "http://preprocess-csv-to-json:8000"


class _RecordingDriver:
    """run() の呼び出し action を記録する driver スタブ。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self, settings: Any, entry: ServiceCatalogEntry, action: Literal["start", "stop", "restart"]
    ) -> ControlResult:
        self.calls.append(action)
        return ControlResult(ok=True, action=action, service_id=entry.service_id, exit_code=0)


def test_control_client_selects_driver_by_mode_and_runner() -> None:
    settings = get_settings()
    preprocess = get_catalog_entry("preprocess-csv-to-json")  # dev_runner=uv
    parser = get_catalog_entry("parser-docling")  # dev_runner=docker
    assert preprocess is not None and parser is not None

    object.__setattr__(settings, "environment", "dev")
    try:
        # dev + uv runner(前処理)→ uv driver
        docker, uv = _RecordingDriver(), _RecordingDriver()
        c = ServiceControlClient(docker_driver=docker, uv_driver=uv)  # type: ignore[arg-type]
        asyncio.run(c.control(settings, preprocess, "start"))
        assert uv.calls == ["start"] and docker.calls == []

        # dev + docker runner(parser)→ docker driver(ホスト巨大 sync を避ける)
        docker, uv = _RecordingDriver(), _RecordingDriver()
        c = ServiceControlClient(docker_driver=docker, uv_driver=uv)  # type: ignore[arg-type]
        asyncio.run(c.control(settings, parser, "start"))
        assert docker.calls == ["start"] and uv.calls == []

        # prod は runner に関わらず docker driver
        object.__setattr__(settings, "environment", "prod")
        docker, uv = _RecordingDriver(), _RecordingDriver()
        c = ServiceControlClient(docker_driver=docker, uv_driver=uv)  # type: ignore[arg-type]
        asyncio.run(c.control(settings, preprocess, "stop"))
        assert docker.calls == ["stop"] and uv.calls == []
    finally:
        object.__setattr__(settings, "environment", "dev")


class _FakePopen:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid


def test_uv_driver_start_writes_pidfile_and_argv(monkeypatch: MonkeyPatch, tmp_path: Any) -> None:
    settings = get_settings()
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    captured: dict[str, Any] = {}

    def fake_popen(argv: list[str], **kwargs: Any) -> _FakePopen:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakePopen(pid=4321)

    monkeypatch.setattr(control_module, "_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # 起動後検証: 待機を 0 にし、spawn したプロセスは生存しているものとみなす。
    monkeypatch.setattr(control_module, "_START_VERIFY_DELAY_SECONDS", 0)
    monkeypatch.setattr(control_module, "_pid_alive", lambda _pid: True)

    result = asyncio.run(UvProcessDriver().run(settings, entry, "start"))
    assert result.ok is True
    argv = captured["argv"]
    assert argv[:3] == ["uv", "run", "--directory"]
    assert argv[-6:] == [
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(entry.dev_port),
    ]
    assert captured["kwargs"]["start_new_session"] is True
    pidfile = tmp_path / f"{entry.service_id}.pid"
    assert pidfile.read_text() == "4321"


def test_uv_driver_start_idempotent_when_alive(monkeypatch: MonkeyPatch, tmp_path: Any) -> None:
    settings = get_settings()
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    # 当該サービスのプロセスが生存しているので、再 start は spawn せず ok を返す。
    (tmp_path / f"{entry.service_id}.pid").write_text(str(os.getpid()))
    monkeypatch.setattr(control_module, "_runtime_dir", lambda: tmp_path)
    # pid 同一性照合は「当該サービス」とみなす(cmdline 照合は別テストで検証)。
    monkeypatch.setattr(control_module, "_pid_is_service", lambda _pid, _entry: True)

    def boom(*_a: Any, **_k: Any) -> None:  # pragma: no cover - 呼ばれてはいけない
        raise AssertionError("既に起動済みなら Popen は呼ばない")

    monkeypatch.setattr(subprocess, "Popen", boom)
    result = asyncio.run(UvProcessDriver().run(settings, entry, "start"))
    assert result.ok is True


def test_uv_driver_stop_signals_and_clears_pidfile(monkeypatch: MonkeyPatch, tmp_path: Any) -> None:
    settings = get_settings()
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    pidfile = tmp_path / f"{entry.service_id}.pid"
    pidfile.write_text("999999")
    monkeypatch.setattr(control_module, "_runtime_dir", lambda: tmp_path)

    # 生存判定: 初回 True(→SIGTERM)、以降 False(ループ即終了・SIGKILL なし)。
    alive = iter([True, False, False])
    monkeypatch.setattr(control_module, "_pid_alive", lambda _pid: next(alive))
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    signals: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda _pgid, sig: signals.append(sig))

    result = asyncio.run(UvProcessDriver().run(settings, entry, "stop"))
    assert result.ok is True
    assert signals == [signal.SIGTERM]
    assert not pidfile.exists()


def test_uv_driver_stop_noop_without_pidfile(monkeypatch: MonkeyPatch, tmp_path: Any) -> None:
    settings = get_settings()
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    monkeypatch.setattr(control_module, "_runtime_dir", lambda: tmp_path)
    result = asyncio.run(UvProcessDriver().run(settings, entry, "stop"))
    assert result.ok is True


def test_pid_is_service_rejects_reused_pid(monkeypatch: MonkeyPatch) -> None:
    """生存していても cmdline が当該サービスでなければ False(PID 再利用対策)。"""
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    monkeypatch.setattr(control_module, "_pid_alive", lambda _pid: True)

    # 無関係なプロセスの cmdline → False。
    monkeypatch.setattr(control_module, "_proc_cmdline", lambda _pid: "/usr/bin/some-other-daemon")
    assert control_module._pid_is_service(12345, entry) is False

    # 当該サービスの uvicorn(--port が一致)→ True。
    matching = f"uv run --directory x uvicorn app.main:app --host 127.0.0.1 --port {entry.dev_port}"
    monkeypatch.setattr(control_module, "_proc_cmdline", lambda _pid: matching)
    assert control_module._pid_is_service(12345, entry) is True

    # /proc 不可(非 Linux)は生存判定にフォールバック。
    monkeypatch.setattr(control_module, "_proc_cmdline", lambda _pid: None)
    assert control_module._pid_is_service(12345, entry) is True


def test_uv_driver_start_detects_immediate_exit(monkeypatch: MonkeyPatch, tmp_path: Any) -> None:
    """spawn 直後に即死したら start は失敗を返し、ログ末尾を添える。"""
    settings = get_settings()
    entry = get_catalog_entry("preprocess-csv-to-json")
    assert entry is not None
    monkeypatch.setattr(control_module, "_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(control_module, "_START_VERIFY_DELAY_SECONDS", 0)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _FakePopen(pid=4321))
    # 起動済み判定は False(新規 spawn させる)、検証時の生存判定も False(即死)。
    monkeypatch.setattr(control_module, "_pid_is_service", lambda _pid, _entry: False)
    monkeypatch.setattr(control_module, "_pid_alive", lambda _pid: False)
    # ログ末尾が detail に載ること。
    (tmp_path / f"{entry.service_id}.log").write_text("ERROR: address already in use")

    result = asyncio.run(UvProcessDriver().run(settings, entry, "start"))
    assert result.ok is False
    assert "起動直後にプロセスが終了" in (result.detail or "")
    assert "address already in use" in (result.detail or "")
    # 即死後は pidfile を残さない。
    assert not (tmp_path / f"{entry.service_id}.pid").exists()


def test_control_client_serializes_same_service() -> None:
    """同一サービスへの並行 control はロックで直列化される。"""
    settings = get_settings()
    object.__setattr__(settings, "environment", "prod")  # docker driver 経路
    entry = get_catalog_entry("parser-docling")
    assert entry is not None

    active = 0
    max_active = 0

    class _SlowDriver:
        async def run(
            self, _s: Any, e: ServiceCatalogEntry, action: Literal["start", "stop", "restart"]
        ) -> ControlResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return ControlResult(ok=True, action=action, service_id=e.service_id, exit_code=0)

    c = ServiceControlClient(docker_driver=_SlowDriver())  # type: ignore[arg-type]

    async def _drive() -> None:
        await asyncio.gather(
            c.control(settings, entry, "start"),
            c.control(settings, entry, "start"),
            c.control(settings, entry, "start"),
        )

    try:
        asyncio.run(_drive())
    finally:
        object.__setattr__(settings, "environment", "dev")
    assert max_active == 1, "同一サービスの control は同時に 1 つだけ実行されること"
