# parser-oci-document-understanding

OCI Document Understanding を呼ぶ parser マイクロサービス(OCI クラウド・薄いプロキシ)。

- 共有 core `rag_parser_core.oci_document_understanding` を **env 由来 config** で駆動する。
- `POST /parse`(file + content_type)→ OCI Document Understanding の非同期 processor job
  で OCR/表抽出 → `StructuredExtraction`(ParseResponse)で返す。
- `GET /health` は OCI 設定の充足(compartment / namespace / 入力 bucket)で ok/degraded。
- **OCI 認証はメインプロジェクト設定を継承**: docker-compose で共通 `platform/.env`・`backend/.env` と
  `~/.oci` マウント、`PLATFORM_OCI_CONFIG_FILE` を受け取る(個別設定なし)。`--profile oci` で opt-in。
- 未設定/SDK 失敗/job 失敗/timeout 時は extraction=None を返し、backend 側で既存
  in-process / ローカルフローへ安全に縮退する。

## 環境変数(backend と同じキーを継承)

`RAG_OCI_DOCUMENT_UNDERSTANDING_COMPARTMENT_ID` / `PLATFORM_OCI_COMPARTMENT_ID`、
`RAG_OCI_DOCUMENT_UNDERSTANDING_NAMESPACE` / `PLATFORM_OBJECT_STORAGE_NAMESPACE`、
`RAG_OCI_DOCUMENT_UNDERSTANDING_INPUT_BUCKET` / `PLATFORM_OBJECT_STORAGE_BUCKET`、
`RAG_OCI_DOCUMENT_UNDERSTANDING_OUTPUT_BUCKET` / `_INPUT_PREFIX` / `_OUTPUT_PREFIX` /
`_LANGUAGE` / `_FEATURES` / `_POLL_INTERVAL_SECONDS` / `_TIMEOUT_SECONDS`、
`PLATFORM_OCI_CONFIG_FILE` / `PLATFORM_OCI_CONFIG_PROFILE` / `PLATFORM_OCI_REGION` / `PLATFORM_OBJECT_STORAGE_REGION`。

## ローカル実行

```bash
uv run --directory services/parsers/oci_document_understanding \
  uvicorn app.main:app --port 18028
```
