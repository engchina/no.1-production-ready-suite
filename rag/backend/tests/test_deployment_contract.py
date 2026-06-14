"""コンテナ配布物の最低限の本番運用契約を固定するテスト。"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_frontend_image_uses_reproducible_build_install() -> None:
    """frontend build image は lockfile で再現可能に依存解決する。"""
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert "RUN npm ci\n" in dockerfile
    assert "RUN npm install" not in dockerfile


def test_frontend_image_serves_static_assets_with_unprivileged_nginx() -> None:
    """frontend runtime image は非 root Nginx で静的 assets を配信する。"""
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM nginxinc/nginx-unprivileged:1.27-alpine AS runner" in dockerfile
    assert "COPY --from=builder /app/dist /usr/share/nginx/html" in dockerfile
    assert "COPY --from=builder /app/node_modules" not in dockerfile


def test_backend_image_runs_as_non_root_app_user() -> None:
    """backend runtime image は専用の非 root ユーザーで起動する。"""
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "useradd --create-home --shell /usr/sbin/nologin appuser" in dockerfile
    assert "USER appuser" in dockerfile


def test_backend_image_uses_gunicorn_uvicorn_worker() -> None:
    """backend production image は Gunicorn で Uvicorn worker を管理する。"""
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert '"gunicorn>=23,<24"' in pyproject
    assert "exec uv run --no-sync gunicorn app.main:app" in dockerfile
    assert "--worker-class uvicorn.workers.UvicornWorker" in dockerfile
    assert "--workers ${WEB_CONCURRENCY:-2}" in dockerfile
    assert "--timeout ${GUNICORN_TIMEOUT:-60}" in dockerfile
    assert "--graceful-timeout ${GUNICORN_GRACEFUL_TIMEOUT:-30}" in dockerfile
    assert "WEB_CONCURRENCY=${WEB_CONCURRENCY:-2}" in compose
    assert "GUNICORN_TIMEOUT=${GUNICORN_TIMEOUT:-60}" in compose


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
