# NL2SQL 設定ソース

`backend/.env.example` を完全な設定カタログとする。各環境の `backend/.env` は secret、
デプロイ差分、明示的なセキュリティ境界だけを保持し、カタログの全項目を複製する必要はない。

設定の優先順位は次のとおり。

1. 設定 UI が `MODEL_SETTINGS_FILE` へ保存した非 secret のモデル設定。
2. `backend/.env.example` に記載した環境既定値。

secret は例外とし、`OCI_ENTERPRISE_AI_API_KEY` は `backend/.env` だけから読み込む。
旧 v1 `model-settings.json` の key は環境 key がない場合に限り一時的に読み込める。
次回のモデル設定保存で `.env` へ移し、secret を含まない v2 JSON へ更新する。
API 応答は `has_api_key`、`secret_source`、`legacy_secret_detected` だけを返す。

OCI SDK の profile は `OCI_CONFIG_PROFILE` を正本とする。`OCI_PROFILE` は
`OCI_CONFIG_PROFILE` が空の場合だけ使う非推奨の互換 fallback であり、新規環境では使用しない。

デプロイ前に read-only audit を実行する。

```bash
cd backend
.venv/bin/python -m app.cli.config_audit
```

このコマンドは stable JSON を返し、設定値を出力しない。終了コードが 0 以外の場合は、
カタログ、本機環境、セキュリティ組み合わせ、ファイル権限、モデル設定のいずれかを修正する。

非 local 環境は起動時に `DEBUG=false` を必須とする。認証を有効にする場合は
`APP_AUTH_COOKIE_SECURE=true` も必須とする。
