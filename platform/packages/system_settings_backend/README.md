# production-ready-system-settings-backend（`pr_system_settings`）

3製品（RAG / NL2SQL / Agent）共通のシステム設定 API（#70）。NL2SQL の実装を基準にしている。

- `pr_system_settings.env_file`：`.env` の排他付き部分更新（`locked_env_file` / `replace_env_file` / `write_env_values`）
- `pr_system_settings.upload_storage`：アップロード保存先 API（`build_upload_storage_router`）
- `pr_system_settings.oci`：OCI 認証 API（`build_oci_router`。`~/.oci/config` の読み書き、秘密鍵の配置、接続テスト、Object Storage namespace 取得）。製品側からは `read_oci_config_text` / `parse_oci_config` / `read_runtime_oci_config` / `test_oci_config(verify_with_oci=)` も使える
- `pr_system_settings.auth`：3製品共通の認証基盤（#212）。ユーザー・ロール・ユーザーとロールの割り当て・セッションは `PLATFORM_*` テーブルで 3 製品が共有する
  - `store`（`InMemoryAuthStore` / `OracleAuthStore(connection_factory)`）、`service`（`AuthService`：ログイン・ロック・セッション・CSRF・パスワード変更・構成管理者・ユーザー / ロール操作・最後の管理者の保護・製品をまたぐ権限昇格の防止）、`dependencies`（`authorize_request`）、`router`（`build_auth_router`）、`migrations`（`PLATFORM_AUTH_DDL` / `apply_platform_auth_schema`）
  - 製品は `AuthService` / `OracleAuthStore` を継承し、実効権限の組み立て（`_role_permissions` / `_build_principal`）と、ロールの製品データの読み書き（`_role_details` / `_replace_role_details` / `_before_delete_role`）を実装する。製品ごとの権限コードのテーブル（`NL2SQL_APP_ROLE_PERMISSIONS` / `RAG_ROLE_PERMISSIONS` / `AGENT_ROLE_PERMISSIONS`）は `PRODUCT_ROLE_PERMISSION_TABLES` に登録し、SYSTEM_ADMIN 以外のロール割り当てでは他製品の権限も操作者に収まることを確認する
- `pr_system_settings.users_roles`：ユーザー管理・ロール管理の API 契約（request / response の Pydantic model、`RoleData` の共通部分、パスワードポリシーと一時パスワード生成。#206）。永続化・認証・認可と、ロールに付ける権限（製品ごとに違う）は製品が持ち、製品は `RoleData` を継承して項目を足す
- `pr_system_settings.oci_connectivity` / `oci_auth`：OCI 接続テストの段階判定と、OCI SDK config の非対話ロード（`oci` extra。SDK は遅延 import）

各製品は router を include し、製品ごとの差（Settings の取得、共通 `.env` の場所、書込み権限・操作権限の依存関係）を引数で渡す。
システム設定の保存先は3製品共通の `platform/.env`（`PLATFORM_*`。#211）で、製品は `platform_env_file(BACKEND_DIR)` の値を `env_file` に渡す。
モデル設定の `model-settings.json` と API key（`PLATFORM_OCI_ENTERPRISE_AI_API_KEY`）も共有し、製品固有の節の secret は `section_env_file`（製品の `backend/.env`）へ保存する。
OCI 認証の `action_dependencies` は、config 読込・接続テスト・namespace 取得に付ける依存関係（Agent は `require_admin`）。

```python
from pr_system_settings.upload_storage import build_upload_storage_router

router.include_router(
    build_upload_storage_router(
        get_settings=get_settings,
        env_file=lambda: PLATFORM_ENV_FILE,
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
