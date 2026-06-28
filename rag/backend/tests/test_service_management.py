"""サービス管理(カタログ / 稼働プローブ / 制御 / API)のテスト。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import pytest
from pytest import MonkeyPatch

from app.config import get_settings
from app.main import app
from app.services.catalog import (
    SERVICE_CATALOG,
    ServiceCatalogEntry,
    get_catalog_entry,
    is_dev_mode,
    service_health_url,
    service_model_cache_host_path,
)
from app.services.control import (
    ControlResult,
    DockerComposeDriver,
    ServiceControlClient,
    ServiceControlError,
    ServiceLogsResult,
    _compose_args,
    _compose_env,
    _compose_logs_args,
    read_service_logs,
)
from app.services.status import probe_service_status, probe_service_statuses
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
    assert gpu_ids == {
        "parser-unlimited-ocr",
        "parser-mineru",
        "parser-dots-ocr",
        "parser-glm-ocr",
        "parser-asr",
    }
    # OCR 系 parser は推論サーバー(SGLang/vLLM)をイメージへ内包する単一サービス。
    assert get_catalog_entry("parser-unlimited-ocr") is not None
    assert get_catalog_entry("parser-dots-ocr") is not None
    assert get_catalog_entry("parser-glm-ocr") is not None


def test_catalog_execution_policies_mark_fallback_boundaries() -> None:
    by_id = {entry.service_id: entry for entry in SERVICE_CATALOG}
    assert by_id["pipeline-chunking"].execution_policy == "in_process_when_disabled"
    assert by_id["pipeline-retrieval"].execution_policy == "in_process_when_disabled"
    assert by_id["pipeline-generation"].execution_policy == "in_process_when_disabled"
    assert by_id["parser-docling"].execution_policy == "selected_adapter"
    assert by_id["preprocess-office-to-pdf"].execution_policy == "selected_adapter"


# サービス化が未成熟な純 CPU 段。UI/API のデプロイ操作を出さず backend 内処理で動作する。
_DEMOTED_STAGE_IDS = {
    "pipeline-chunking",
    "pipeline-vector-index",
    "pipeline-graphrag",
    "pipeline-grounding",
    "pipeline-guardrail",
    "pipeline-evaluation",
    "pipeline-agentic",
}


def test_catalog_deployable_marks_future_service_stages() -> None:
    by_id = {entry.service_id: entry for entry in SERVICE_CATALOG}
    # 格下げ 7 段は deployable=False かつ backend 内処理(in_process_when_disabled)。
    for sid in _DEMOTED_STAGE_IDS:
        assert by_id[sid].deployable is False, sid
        assert by_id[sid].execution_policy == "in_process_when_disabled", sid
    # サービス維持: retrieval/generation と parser/preprocess 代表。
    assert by_id["pipeline-retrieval"].deployable is True
    assert by_id["pipeline-generation"].deployable is True
    assert by_id["parser-docling"].deployable is True
    assert by_id["preprocess-office-to-pdf"].deployable is True


def test_model_cache_path_set_only_for_model_downloading_parsers() -> None:
    """モデル DL を行う 7 parser だけ model_cache_path を持ち、root/appuser でパスが分かれる。"""
    by_id = {entry.service_id: entry for entry in SERVICE_CATALOG}
    root_cache = {"parser-unlimited-ocr", "parser-dots-ocr", "parser-glm-ocr"}
    appuser_cache = {"parser-mineru", "parser-marker", "parser-docling", "parser-asr"}
    for service_id in root_cache:
        assert by_id[service_id].model_cache_path == "/root/.cache"
    for service_id in appuser_cache:
        assert by_id[service_id].model_cache_path == "/home/appuser/.cache"
    # それ以外(OCI proxy / pipeline / preprocess / unstructured)はモデル DL なし。
    with_cache = {e.service_id for e in SERVICE_CATALOG if e.model_cache_path is not None}
    assert with_cache == root_cache | appuser_cache


def test_service_model_cache_host_path_is_download_dir_per_service() -> None:
    settings = get_settings()
    settings = settings.model_copy(update={"huggingface_download_dir": "/u01/models/huggingface/"})
    glm = get_catalog_entry("parser-glm-ocr")
    assert glm is not None
    # 末尾スラッシュは正規化し、<download_dir>/<service_id> を返す。
    assert service_model_cache_host_path(settings, glm) == "/u01/models/huggingface/parser-glm-ocr"
    # model_cache_path 未設定(モデル DL なし)のサービスは None。
    chunking = get_catalog_entry("pipeline-chunking")
    assert chunking is not None
    assert service_model_cache_host_path(settings, chunking) is None


def test_compose_env_injects_huggingface_settings() -> None:
    settings = get_settings()
    settings = settings.model_copy(
        update={
            "huggingface_download_dir": "/u01/models/huggingface",
            "huggingface_token": "hf_secret",
            "huggingface_endpoint": "https://hf-mirror.com",
        }
    )
    env = _compose_env(settings)
    assert env["HF_DOWNLOAD_DIR"] == "/u01/models/huggingface"
    assert env["HF_TOKEN"] == "hf_secret"
    assert env["HF_ENDPOINT"] == "https://hf-mirror.com"
    # os.environ を継承していること(PATH などが残る)。
    assert "PATH" in env


def test_compose_env_injects_oci_enterprise_ai_settings() -> None:
    """OCI parser コンテナ向けに実効 OCI Enterprise AI 設定を env 注入する。"""
    settings = get_settings().model_copy(
        update={
            "oci_enterprise_ai_endpoint": "https://inference.example/openai/v1",
            "oci_enterprise_ai_api_key": "sk-secret",
            "oci_enterprise_ai_project_ocid": "ocid1.generativeaiproject.oc1..x",
            # catalog が空でも resolver は legacy VLM/LLM へフォールバックする。
            "oci_enterprise_ai_models": [],
            "oci_enterprise_ai_vlm_model": "xai.grok-4.3",
            "oci_enterprise_ai_llm_model": "xai.grok-4.3",
            "oci_enterprise_ai_vlm_input_mode": "files_api",
        }
    )
    env = _compose_env(settings)
    assert env["OCI_ENTERPRISE_AI_ENDPOINT"] == "https://inference.example/openai/v1"
    assert env["OCI_ENTERPRISE_AI_API_KEY"] == "sk-secret"
    assert env["OCI_ENTERPRISE_AI_PROJECT_OCID"] == "ocid1.generativeaiproject.oc1..x"
    assert env["OCI_ENTERPRISE_AI_VLM_MODEL"] == "xai.grok-4.3"
    assert env["OCI_ENTERPRISE_AI_DEFAULT_MODEL"] == "xai.grok-4.3"
    assert env["OCI_ENTERPRISE_AI_VLM_INPUT_MODE"] == "files_api"


def test_compose_env_oci_vlm_model_empty_when_unconfigured() -> None:
    """Vision モデル未設定なら空文字で渡し、parser は degraded を維持する。"""
    settings = get_settings().model_copy(
        update={
            "oci_enterprise_ai_models": [],
            "oci_enterprise_ai_vlm_model": "",
            "oci_enterprise_ai_llm_model": "",
        }
    )
    env = _compose_env(settings)
    assert env["OCI_ENTERPRISE_AI_VLM_MODEL"] == ""


def test_compose_passes_oci_enterprise_ai_env_to_vision_parser() -> None:
    """compose は parser-oci-genai-vision へ ${OCI_ENTERPRISE_AI_*} を渡す。"""
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert "OCI_ENTERPRISE_AI_VLM_MODEL: ${OCI_ENTERPRISE_AI_VLM_MODEL:-}" in text
    assert "OCI_ENTERPRISE_AI_ENDPOINT: ${OCI_ENTERPRISE_AI_ENDPOINT:-}" in text


def test_list_services_exposes_model_cache_mount(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "huggingface_download_dir", "/u01/models/huggingface")

    async def fake_probe(_settings: Any) -> dict[str, str]:
        return {entry.service_id: "stopped" for entry in SERVICE_CATALOG}

    monkeypatch.setattr("app.api.routes.services.probe_service_statuses", fake_probe)
    data = client.get("/api/services").json()["data"]
    glm = next(s for s in data["services"] if s["service_id"] == "parser-glm-ocr")
    assert glm["model_cache"] == {
        "container_path": "/root/.cache",
        "host_path": "/u01/models/huggingface/parser-glm-ocr",
        "editable": False,
    }
    # モデル DL なしのサービスは model_cache=None。
    chunking = next(s for s in data["services"] if s["service_id"] == "pipeline-chunking")
    assert chunking["model_cache"] is None


def test_compose_uses_shared_oci_config_volume() -> None:
    """production compose は共有 oci-config volume を OCI service へ mount する。"""
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    assert text.count("oci-config:/home/appuser/.oci") == 4
    assert "~/.oci:/home/appuser/.oci:ro" not in text
    assert "\n  oci-config:" in text


def test_get_catalog_entry_allowlist() -> None:
    assert get_catalog_entry("parser-docling") is not None
    assert get_catalog_entry("unknown-service") is None
    assert get_catalog_entry("../etc/passwd") is None


def test_catalog_dev_ports_avoid_app_backend_range() -> None:
    """dev parser/preprocess ports は sibling app の backend port と衝突しない高番台に寄せる。"""
    ports = [entry.dev_port for entry in SERVICE_CATALOG]
    assert len(ports) == len(set(ports)), "dev_port は一意であること"
    assert all(port >= 18000 for port in ports)


# --- 稼働プローブ -----------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], raise_error: bool = False) -> None:
        self._payload = payload
        self._raise = raise_error
        self.status_code = 500 if raise_error else 200

    def raise_for_status(self) -> None:
        if self._raise:
            raise RuntimeError("http error")

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """status probe 用の httpx.AsyncClient 代替。url→応答 を引く。"""

    routes: dict[str, _FakeResponse] = {}
    raise_on_connect: set[str] = set()
    calls: list[str] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        base = url.removesuffix("/health")
        if base in self.raise_on_connect:
            raise ConnectionError("connection refused")
        return self.routes.get(base, _FakeResponse({"status": "ok"}))

    async def request(self, method: str, url: str, **_kwargs: Any) -> _FakeResponse:
        assert method == "GET"
        return await self.get(url)


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


def test_probe_stopped_service_is_not_retried(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    _patch_probe_httpx(monkeypatch)
    entry = get_catalog_entry("parser-docling")
    assert entry is not None
    url = service_health_url(settings, entry)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.raise_on_connect = {url}
    try:
        status = asyncio.run(probe_service_status(settings, entry))
        calls = list(_FakeAsyncClient.calls)
    finally:
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.raise_on_connect = set()

    assert status == "stopped"
    assert calls == [f"{url}/health"]


def test_probe_unconfigured_when_url_blank(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    # unconfigured 判定は prod(url_field を使う)経路の挙動。dev は dev_port 既定で常に解決される。
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_parser_docling_service_url", "")
    _patch_probe_httpx(monkeypatch)
    statuses = asyncio.run(probe_service_statuses(settings))
    assert statuses["parser-docling"] == "unconfigured"


def test_probe_non_deployable_returns_in_process_without_http(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    _patch_probe_httpx(monkeypatch)
    entry = get_catalog_entry("pipeline-chunking")
    assert entry is not None and entry.deployable is False
    _FakeAsyncClient.calls = []
    try:
        status = asyncio.run(probe_service_status(settings, entry))
        calls = list(_FakeAsyncClient.calls)
    finally:
        _FakeAsyncClient.calls = []
    # backend 内処理の段は /health を叩かず固定で in_process を返す。
    assert status == "in_process"
    assert calls == []


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

    unlimited = get_catalog_entry("parser-unlimited-ocr")
    assert unlimited is not None
    assert _compose_args(settings, unlimited, "start") == [
        "docker",
        "compose",
        "--profile",
        "gpu",
        "--profile",
        "unlimited-ocr",
        "up",
        "-d",
        "--no-build",
        "parser-unlimited-ocr",
    ]

    # OCR 系 parser は推論サーバー内包の単一サービスなので gpu-vllm profile は付かない。
    dots = get_catalog_entry("parser-dots-ocr")
    assert dots is not None
    assert _compose_args(settings, dots, "start") == [
        "docker",
        "compose",
        "--profile",
        "gpu",
        "--profile",
        "dots-ocr",
        "up",
        "-d",
        "--no-build",
        "parser-dots-ocr",
    ]

    glm = get_catalog_entry("parser-glm-ocr")
    assert glm is not None
    assert _compose_args(settings, glm, "start") == [
        "docker",
        "compose",
        "--profile",
        "gpu",
        "--profile",
        "glm-ocr",
        "up",
        "-d",
        "--no-build",
        "parser-glm-ocr",
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


def test_compose_args_build_and_remove(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    docling = get_catalog_entry("parser-docling")
    assert docling is not None
    # build はイメージ生成。明示アクションで実行する。
    assert _compose_args(settings, docling, "build") == [
        "docker",
        "compose",
        "build",
        "parser-docling",
    ]
    # remove はコンテナ削除(稼働中なら停止してから force 削除)。
    assert _compose_args(settings, docling, "remove") == [
        "docker",
        "compose",
        "rm",
        "-f",
        "-s",
        "parser-docling",
    ]
    # GPU サービスの build/remove も profile gate を越える。
    glm = get_catalog_entry("parser-glm-ocr")
    assert glm is not None
    assert _compose_args(settings, glm, "build") == [
        "docker",
        "compose",
        "--profile",
        "gpu",
        "--profile",
        "glm-ocr",
        "build",
        "parser-glm-ocr",
    ]


def test_build_uses_longer_build_timeout(monkeypatch: MonkeyPatch) -> None:
    """build は専用の長い timeout を、その他は通常 timeout を使う。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_service_control_timeout_seconds", 60.0)
    monkeypatch.setattr(settings, "rag_service_build_timeout_seconds", 1800.0)
    docling = get_catalog_entry("parser-docling")
    assert docling is not None

    async def fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return _FakeProcess(returncode=0)

    used: list[float] = []
    real_wait_for = asyncio.wait_for

    async def spy_wait_for(awaitable: Any, timeout: float) -> Any:
        used.append(timeout)
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", spy_wait_for)
    driver = DockerComposeDriver()
    asyncio.run(driver.run(settings, docling, "build"))
    asyncio.run(driver.run(settings, docling, "stop"))
    assert used == [1800.0, 60.0]


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


def test_compose_logs_args_dev_adds_tail_and_override(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    docling = get_catalog_entry("parser-docling")
    assert docling is not None
    assert _compose_logs_args(settings, docling, 123) == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.dev.yml",
        "logs",
        "--no-color",
        "--tail",
        "123",
        "parser-docling",
    ]


def test_friendly_compose_error_maps_missing_image(monkeypatch: MonkeyPatch) -> None:
    from app.services.control import _friendly_compose_error

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")
    docling = get_catalog_entry("parser-docling")
    assert docling is not None
    raw = (
        "Error response from daemon: No such image: "
        "no1-production-ready-rag-parser-docling:dev-local"
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
    def __init__(self, returncode: int, stderr: bytes = b"", stdout: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

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


def test_read_service_logs_docker_success(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    entry = get_catalog_entry("parser-docling")
    assert entry is not None
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return _FakeProcess(returncode=0, stdout=b"line1\nline2\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(read_service_logs(settings, entry, 50))

    assert result == ServiceLogsResult(
        service_id="parser-docling",
        source="docker",
        lines=50,
        content="line1\nline2",
    )
    assert captured["args"][-5:] == ["logs", "--no-color", "--tail", "50", "parser-docling"]
    assert captured["kwargs"]["cwd"] is None


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
    dots = next(s for s in data["services"] if s["service_id"] == "parser-dots-ocr")
    assert dots["execution_policy"] == "selected_adapter"
    chunking = next(s for s in data["services"] if s["service_id"] == "pipeline-chunking")
    assert chunking["execution_policy"] == "in_process_when_disabled"
    assert chunking["deployable"] is False
    retrieval = next(s for s in data["services"] if s["service_id"] == "pipeline-retrieval")
    assert retrieval["execution_policy"] == "in_process_when_disabled"
    assert retrieval["deployable"] is True


def test_control_rejects_non_deployable_stage(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "dev")  # dev は制御自動有効

    async def fail_exec(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("non-deployable stage must not invoke compose")

    # 409 ガードは control 実行前。compose が呼ばれたらテスト失敗にする。
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_exec)
    resp = client.post("/api/services/pipeline-chunking/start")
    assert resp.status_code == 409


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


def test_list_service_catalog_does_not_probe_status(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_service_control_enabled", False)

    async def fail_probe(_settings: Any) -> dict[str, str]:
        raise AssertionError("catalog endpoint must not probe service health")

    monkeypatch.setattr("app.api.routes.services.probe_service_statuses", fail_probe)
    resp = client.get("/api/services/catalog")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["control_enabled"] is False
    assert data["deployment_mode"] == "prod"
    assert len(data["services"]) == len(SERVICE_CATALOG)
    assert "status" not in data["services"][0]
    dots = next(s for s in data["services"] if s["service_id"] == "parser-dots-ocr")
    assert dots["execution_policy"] == "selected_adapter"
    chunking = next(s for s in data["services"] if s["service_id"] == "pipeline-chunking")
    assert chunking["execution_policy"] == "in_process_when_disabled"
    retrieval = next(s for s in data["services"] if s["service_id"] == "pipeline-retrieval")
    assert retrieval["execution_policy"] == "in_process_when_disabled"


def test_get_service_status_probes_only_target(monkeypatch: MonkeyPatch) -> None:
    seen: list[str] = []

    async def fake_probe(_settings: Any, entry: ServiceCatalogEntry) -> str:
        seen.append(entry.service_id)
        return "running"

    monkeypatch.setattr("app.api.routes.services.probe_service_status", fake_probe)
    resp = client.get("/api/services/parser-dots-ocr/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["service_id"] == "parser-dots-ocr"
    assert data["status"] == "running"
    assert seen == ["parser-dots-ocr"]


def test_get_service_logs_returns_tail(monkeypatch: MonkeyPatch) -> None:
    async def fake_logs(
        _settings: Any, entry: ServiceCatalogEntry, lines: int
    ) -> ServiceLogsResult:
        return ServiceLogsResult(
            service_id=entry.service_id,
            source="docker",
            lines=lines,
            content="ready",
        )

    monkeypatch.setattr("app.api.routes.services.read_service_logs", fake_logs)
    resp = client.get("/api/services/parser-docling/logs?lines=50")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data == {
        "service_id": "parser-docling",
        "source": "docker",
        "lines": 50,
        "content": "ready",
    }


def test_get_service_logs_unknown_service_is_404() -> None:
    resp = client.get("/api/services/unknown-service/logs")
    assert resp.status_code == 404


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


def _spy_control(monkeypatch: MonkeyPatch) -> list[tuple[str, str]]:
    """ServiceControlClient.control を記録のみのスパイへ差し替え、(service_id, action) 列を返す。"""
    calls: list[tuple[str, str]] = []

    async def fake_control(
        _self: Any,
        _settings: Any,
        entry: ServiceCatalogEntry,
        action: Literal["start", "stop", "restart"],
    ) -> ControlResult:
        calls.append((entry.service_id, action))
        return ControlResult(ok=True, action=action, service_id=entry.service_id, exit_code=0)

    monkeypatch.setattr(ServiceControlClient, "control", fake_control)
    return calls


def test_control_acts_on_single_service(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_service_control_enabled", True)
    calls = _spy_control(monkeypatch)

    async def fake_probe(_settings: Any, entry: ServiceCatalogEntry) -> str:
        return "running"

    monkeypatch.setattr("app.api.routes.services.probe_service_status", fake_probe)
    resp = client.post("/api/services/parser-dots-ocr/start")
    assert resp.status_code == 200
    # 各 OCR parser は推論サーバー内包の単一サービス。連鎖制御は無く本体のみ操作する。
    assert calls == [("parser-dots-ocr", "start")]


def test_control_build_and_remove_endpoints(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_service_control_enabled", True)
    calls = _spy_control(monkeypatch)

    async def fake_probe(_settings: Any, entry: ServiceCatalogEntry) -> str:
        return "stopped"

    monkeypatch.setattr("app.api.routes.services.probe_service_status", fake_probe)
    build = client.post("/api/services/parser-docling/build")
    remove = client.post("/api/services/parser-docling/remove")
    assert build.status_code == 200
    assert remove.status_code == 200
    assert build.json()["data"]["action"] == "build"
    assert remove.json()["data"]["action"] == "remove"
    assert calls == [("parser-docling", "build"), ("parser-docling", "remove")]


def test_build_remove_blocked_when_control_disabled(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "rag_service_control_enabled", False)
    assert client.post("/api/services/parser-docling/build").status_code == 409
    assert client.post("/api/services/parser-docling/remove").status_code == 409


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
        settings, "rag_parser_unlimited_ocr_service_url", "http://parser-unlimited-ocr:8000"
    )
    monkeypatch.setattr(
        settings, "rag_preprocess_csv_to_json_service_url", "http://preprocess-csv-to-json:8000"
    )
    assert (
        resolve_service_base_url(settings, "rag_parser_docling_service_url")
        == "http://127.0.0.1:18020"
    )
    assert (
        resolve_service_base_url(settings, "rag_parser_unlimited_ocr_service_url")
        == "http://127.0.0.1:18029"
    )
    assert (
        resolve_service_base_url(settings, "rag_preprocess_csv_to_json_service_url")
        == "http://127.0.0.1:18012"
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
    monkeypatch.setattr(
        settings,
        "rag_parser_unlimited_ocr_service_url",
        "http://parser-unlimited-ocr:8000",
    )
    monkeypatch.setattr(settings, "rag_parser_mineru_service_url", "http://parser-mineru:8000")
    client = ParserServiceClient(settings)
    assert client.service_url("docling") == "http://127.0.0.1:18020"
    assert client.service_url("unlimited_ocr") == "http://127.0.0.1:18029"
    assert client.service_url("mineru") == "http://127.0.0.1:18023"


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
    assert preprocess_service_url(settings, "csv_to_json") == "http://127.0.0.1:18012"
    assert preprocess_service_url(settings, "office_to_pdf") == "http://127.0.0.1:18010"


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
    parser = get_catalog_entry("parser-docling")
    assert parser is not None

    object.__setattr__(settings, "environment", "dev")
    try:
        # dev は docker compose driver(override でポート公開)
        docker = _RecordingDriver()
        c = ServiceControlClient(docker_driver=docker)  # type: ignore[arg-type]
        asyncio.run(c.control(settings, parser, "start"))
        assert docker.calls == ["start"]

        # prod も docker driver
        object.__setattr__(settings, "environment", "prod")
        docker = _RecordingDriver()
        c = ServiceControlClient(docker_driver=docker)  # type: ignore[arg-type]
        asyncio.run(c.control(settings, parser, "stop"))
        assert docker.calls == ["stop"]
    finally:
        object.__setattr__(settings, "environment", "dev")


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
