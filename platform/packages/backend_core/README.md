# production-ready-backend-core (`pr_backend_core`)

No.1 Production Ready 製品群（**RAG / NL2SQL / Agent**）の backend が共有する FastAPI インフラ。
「全サービスが必ず使う」プラットフォーム機能だけを持ち、業務ロジックは持たない。

技術スタック（確定・RAG を正本）: **Python 3.12 + FastAPI + Pydantic v2 + uv**。
依存は軽量に保つため **oci / oracledb は含めない**（各サービスが自分で持つ）。

## 含むもの

| モジュール | 役割 |
|---|---|
| `create_app(...)` | CORS・request-id/メトリクス・例外 envelope・health/ready・/metrics を備えた app factory |
| `config.BaseServiceSettings` | `.env` ベースの共通設定基底（app_version / log_level / environment / cors_origins） |
| `logging.configure_logging` | JSON 構造化ログ（サービス固有のノイズロガー抑制を注入可能） |
| `schemas` | `ApiResponse[T]` / `Page[T]` / `HealthData`（**RAG 実証済み envelope を標準採用**） |
| `api.errors` | HTTPException / 検証 / 未処理例外 → `ApiResponse` 統一ハンドラ |
| `api.pagination` | `paginate(...)` |
| `api.health` | `create_health_router(version_getter, readiness_checks_getter)` |
| `observability` | `MetricsMiddleware`（request-id 付与 + Prometheus）/ `record_http_request` / `metrics_asgi_app` |
| `security.cors` | `configure_cors` |

## 新サービスの最小構成

```python
# app/main.py
from pr_backend_core import create_app
from app.api.router import api_router
from app.settings import get_settings
from app.readiness import readiness_checks

settings = get_settings()
app = create_app(
    service_name="production-ready-nl2sql",
    version=settings.app_version,
    cors_origins=settings.cors_origins,
    api_router=api_router,
    readiness_checks_getter=lambda: readiness_checks(get_settings()),
)
```

## 既成サービス（RAG）の部分採用

RAG のように独自の lifespan / 認証ミドルウェア / DB 依存 readiness を持つサービスは、
`create_app` を使わず各部品を個別採用できる（envelope / 例外ハンドラ / health router /
MetricsMiddleware / configure_logging を import）。

## 開発

```bash
uv sync --dev
uv run pytest          # 純インフラなので Oracle 不要で全テスト実行可
uv run ruff check . && uv run mypy src
```

## 配布 / 依存方法

開発時は各サービスの `pyproject.toml` で uv の path source を使う:

```toml
dependencies = ["production-ready-backend-core"]

[tool.uv.sources]
production-ready-backend-core = { path = "../../no.1-production-ready-platform/packages/backend_core", editable = true }
```

> path source を変更したら **`uv lock` の再生成**が必要。安定後は GitHub Release / index 配布へ切替可能。
