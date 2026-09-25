# production-ready-system-settings-backend（`pr_system_settings`）

3製品（RAG / NL2SQL / Agent）共通のシステム設定 API（#70）。NL2SQL の実装を基準にしている。

- `pr_system_settings.env_file`：`backend/.env` の排他付き部分更新（`locked_env_file` / `replace_env_file` / `write_env_values`）
- `pr_system_settings.upload_storage`：アップロード保存先 API（`build_upload_storage_router`）

各製品は router を include し、製品ごとの差（Settings の取得、`.env` の場所、書込み権限の依存関係）を引数で渡す。

```python
from pr_system_settings.upload_storage import build_upload_storage_router

router.include_router(
    build_upload_storage_router(
        get_settings=get_settings,
        env_file=lambda: BACKEND_ENV_FILE,
        write_dependencies=[Depends(require_admin)],
    ),
    prefix="/settings",
)
```

Settings は pydantic の model で、`upload_storage_backend` / `local_storage_dir` / `object_storage_region` /
`object_storage_namespace` / `object_storage_bucket` / `max_upload_bytes`（任意で `oci_region`）を持つこと。

```bash
cd platform/packages/system_settings_backend
uv sync --locked --dev
uv run black --check . && uv run ruff check . && uv run mypy src
uv run pytest && uv run bandit -r src && uv run pip-audit
```
