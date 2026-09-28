"""配布物(ネイティブ配備と、段階的に廃止するコンテナ)の最低限の本番運用契約を固定するテスト。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# monorepo（#71）では GitHub workflow は suite root（rag/ の親）の .github に置く。
SUITE_ROOT = REPO_ROOT.parent


def test_frontend_image_uses_reproducible_build_install() -> None:
    """frontend build image は lockfile で再現可能に依存解決する。"""
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    # 共有パッケージ（platform）と frontend の両方を lockfile から入れる（#177）。
    assert "RUN cd platform && npm ci && npm run build\n" in dockerfile
    assert "RUN cd rag/frontend && npm ci\n" in dockerfile
    assert "npm install" not in dockerfile


def test_frontend_image_resolves_platform_packages() -> None:
    """compose は platform を別 context で渡す（frontend の file: 依存のため。#177）。"""
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "COPY --from=platform package.json package-lock.json ./platform/" in dockerfile
    assert "platform: ../platform" in compose


def test_frontend_image_serves_static_assets_with_unprivileged_nginx() -> None:
    """frontend runtime image は非 root Nginx で静的 assets を配信する。"""
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM nginxinc/nginx-unprivileged:1.27-alpine AS runner" in dockerfile
    assert "COPY --from=builder /src/rag/frontend/dist /usr/share/nginx/html" in dockerfile
    assert "node_modules /" not in dockerfile


def test_backend_image_runs_as_non_root_app_user() -> None:
    """backend runtime image は専用の非 root ユーザーで起動する。"""
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "useradd --create-home --shell /usr/sbin/nologin appuser" in dockerfile
    assert "USER appuser" in dockerfile


def test_backend_image_is_pinned_and_excludes_heavy_parser_adapters() -> None:
    """backend runtime image は latest tag を使わず、重い parser 依存を載せない。

    外部 parser は services/parsers/<name> の独立サービスへ切り出したため、runtime image は
    `--extra parser-adapters` を同期しない(共有 contract package のみ取り込む)。
    """
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12.11-slim AS base" in dockerfile
    assert "COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /uvx /bin/" in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    # runtime image は重い parser 依存を持たない(HTTP 委譲)。
    assert "--extra parser-adapters" not in dockerfile
    # 共有 contract package を repo 相対レイアウトで取り込む(path 依存解決のため)。
    assert "COPY packages/rag_parser_core /build/packages/rag_parser_core" in dockerfile
    assert ":latest" not in dockerfile


def test_backend_image_uses_gunicorn_uvicorn_worker() -> None:
    """backend production image は Gunicorn で Uvicorn worker を管理する。"""
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"gunicorn>=26,<27"' in pyproject
    assert "exec uv run --no-sync gunicorn app.main:app" in dockerfile
    # gunicorn 26 の control socket は既定で /run/user/<uid> に作られる。非 root の appuser では
    # 作れない場所なので、使わない control interface は無効にする。
    assert "--no-control-socket" in dockerfile
    assert "--worker-class uvicorn.workers.UvicornWorker" in dockerfile
    assert "--workers ${WEB_CONCURRENCY:-2}" in dockerfile
    assert "--timeout ${GUNICORN_TIMEOUT:-60}" in dockerfile
    assert "--graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30}" in dockerfile
    assert "WEB_CONCURRENCY: ${WEB_CONCURRENCY:-2}" in compose
    assert "GUNICORN_TIMEOUT: ${GUNICORN_TIMEOUT:-60}" in compose


def test_docker_contexts_exclude_local_build_artifacts() -> None:
    """Docker context には local cache、依存物、secret env を含めない。"""
    frontend_ignore = (REPO_ROOT / "frontend" / ".dockerignore").read_text(encoding="utf-8")
    backend_ignore = (REPO_ROOT / "backend" / ".dockerignore").read_text(encoding="utf-8")

    assert "node_modules" in frontend_ignore
    assert "dist" in frontend_ignore
    assert "*.tsbuildinfo" in frontend_ignore
    assert ".env.*" in frontend_ignore
    assert ".venv" in backend_ignore
    assert "tests" in backend_ignore
    assert ".env.*" in backend_ignore


def test_frontend_build_does_not_fetch_remote_fonts() -> None:
    """frontend build は Google Fonts などの外部 font fetch に依存しない。"""
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "frontend" / "src").rglob("*")
        if path.is_file() and path.suffix in {".css", ".ts", ".tsx"}
    )

    assert "fonts.googleapis.com" not in source_text
    assert "fonts.gstatic.com" not in source_text


def test_nightly_rag_workflow_runs_search_load_gate() -> None:
    """nightly RAG gate は評価 trend と検索 p95 trend を同じ artifact に残す。"""
    workflow = (SUITE_ROOT / ".github" / "workflows" / "rag-evaluation-nightly.yml").read_text(
        encoding="utf-8"
    )

    assert "search_load_path:" in workflow
    assert "app.rag.evaluation_cli" in workflow
    assert "app.rag.search_load_cli" in workflow
    assert "--trend-output ../artifacts/evaluation-trend.json" in workflow
    assert "--trend-output ../artifacts/search-load-trend.json" in workflow
    assert "export RAG_SEARCH_LOAD_TENANT_ID" in workflow
    assert "if: always()" in workflow


def test_nightly_rag_workflow_runs_parser_adapter_contract_gate() -> None:
    """nightly RAG gate は外部 parser adapter の schema remap smoke を任意に厳格化できる。"""
    workflow = (SUITE_ROOT / ".github" / "workflows" / "rag-evaluation-nightly.yml").read_text(
        encoding="utf-8"
    )

    assert "install_parser_adapters:" in workflow
    # 外部 parser はサービス化したため combined extra は廃止。in-process smoke は共存可能な
    # docling + unstructured のみ導入する。
    assert "uv sync --locked --dev --extra docling --extra unstructured" in workflow
    assert "strict_adapter_contract_required=false" in workflow
    assert "adapter_contract_strict_enabled=false" in workflow
    assert "adapter_contract_strict_enabled=true" in workflow
    assert "run_parser_adapter_contract:" in workflow
    assert "parser_adapter_contract_strict:" in workflow
    assert "parser_adapter_contract_source_kinds:" in workflow
    assert "require_real_world_file_processing_manifest:" in workflow
    assert "app.rag.parser_adapter_contract_cli" in workflow
    assert "--output ../artifacts/parser-adapter-compatibility.json" in workflow
    assert "--require-real-world-policy" in workflow
    assert "parser_adapter_contract_args+=(--strict)" in workflow
    assert "staging_args+=(--parser-adapter-contract-strict)" in workflow
    assert "parser adapter contract gate failed" in workflow
    assert workflow.index("app.rag.parser_adapter_contract_cli") < workflow.index(
        "app.rag.file_processing_golden_cli"
    )


# --- ネイティブ配備(uv の venv + systemd。#286) --------------------------------


def _systemd_script() -> str:
    return (REPO_ROOT / "scripts" / "rag-systemd.sh").read_text(encoding="utf-8")


def _script_microservices() -> dict[str, tuple[str, int, int]]:
    """rag/scripts/rag-systemd.sh の RAG_MICROSERVICES(id|dir|port|timeout)を読む。"""
    import re

    block = re.search(r"(?ms)^RAG_MICROSERVICES=\(\n(.*?)^\)$", _systemd_script())
    assert block is not None, "RAG_MICROSERVICES が見つからない"
    services: dict[str, tuple[str, int, int]] = {}
    for line in block.group(1).splitlines():
        service_id, directory, port, timeout = line.strip().strip('"').split("|")
        services[service_id] = (directory, int(port), int(timeout))
    return services


def test_systemd_units_match_service_catalog() -> None:
    """unit を作るスクリプトのサービス・ディレクトリ・ポートは、backend のカタログと同じ。"""
    from app.services.catalog import SERVICE_CATALOG, SYSTEMD_UNIT_PREFIX

    services = _script_microservices()
    deployable = {entry.service_id: entry for entry in SERVICE_CATALOG if entry.deployable}
    assert set(services) == set(deployable)
    for service_id, (directory, port, _timeout) in services.items():
        entry = deployable[service_id]
        assert directory == entry.working_dir, service_id
        assert port == entry.port, service_id
        assert (REPO_ROOT / directory / "pyproject.toml").is_file(), service_id
        assert (REPO_ROOT / directory / "uv.lock").is_file(), service_id
    assert f'RAG_UNIT_PREFIX="{SYSTEMD_UNIT_PREFIX}"' in _systemd_script()


def test_systemd_sudoers_matches_backend_argv() -> None:
    """sudoers は backend が sudo -n で実行する argv と引数まで同じ(違うと操作が拒否される)。"""
    from app.services.systemd import (
        JOURNAL_FETCH_LINES,
        JOURNALCTL,
        SYSTEMCTL,
        journalctl_argv,
        systemctl_action_argv,
    )

    script = _systemd_script()
    assert f'RAG_SYSTEMCTL="{SYSTEMCTL}"' in script
    assert f'RAG_JOURNALCTL="{JOURNALCTL}"' in script
    assert f"RAG_JOURNAL_FETCH_LINES={JOURNAL_FETCH_LINES}" in script
    unit = "production-ready-rag-parser-docling.service"
    templates = {
        '"${RAG_SYSTEMCTL} enable --now ${unit}"': systemctl_action_argv("start", unit),
        '"${RAG_SYSTEMCTL} disable --now ${unit}"': systemctl_action_argv("stop", unit),
        '"${RAG_SYSTEMCTL} restart ${unit}"': systemctl_action_argv("restart", unit),
        '"${RAG_JOURNALCTL} -u ${unit} -n ${RAG_JOURNAL_FETCH_LINES} --no-pager -o short-iso"': (
            journalctl_argv(unit)
        ),
    }
    for template, argv in templates.items():
        assert template in script
        # sudo -n の後ろが sudoers の 1 行と同じ。
        assert argv[:2] == ["/usr/bin/sudo", "-n"]
        rendered = (
            template.strip('"')
            .replace("${RAG_SYSTEMCTL}", SYSTEMCTL)
            .replace("${RAG_JOURNALCTL}", JOURNALCTL)
            .replace("${RAG_JOURNAL_FETCH_LINES}", str(JOURNAL_FETCH_LINES))
            .replace("${unit}", unit)
        )
        assert rendered == " ".join(argv[2:])


def test_native_deployment_defaults_match_backend_settings() -> None:
    """init_script.sh が前提にする既定値(upload 上限・実行用 env・保存先)は Settings と同じ。"""
    from app.config import BACKEND_ROOT, DEFAULT_LOCAL_STORAGE_DIR, Settings

    init_script = (REPO_ROOT / "init_script.sh").read_text(encoding="utf-8")
    fields = Settings.model_fields
    assert f"RAG_DEFAULT_MAX_UPLOAD_BYTES={fields['max_upload_bytes'].default}" in init_script
    assert fields["rag_service_runtime_env_file"].default == str(
        BACKEND_ROOT / "service-runtime.env"
    )
    assert 'SERVICE_RUNTIME_ENV_FILE="${BACKEND_DIR}/service-runtime.env"' in init_script
    assert f'DATA_DIR="${{DATA_DIR:-{DEFAULT_LOCAL_STORAGE_DIR}}}"' in init_script
    # 配備は Docker を入れず、サービスごとの venv を Python 3.12 で作る。
    assert "docker-ce" not in init_script
    assert "docker compose" not in init_script
    assert 'RAG_PYTHON_VERSION="3.12"' in _systemd_script()
    assert "sync --locked --no-dev --python" in _systemd_script()
