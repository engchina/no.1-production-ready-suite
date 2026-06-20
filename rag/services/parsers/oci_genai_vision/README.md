# parser-oci-genai-vision

OCI Generative AI (Vision) を呼ぶ parser マイクロサービス(OCI クラウド・薄いプロキシ)。

- 共有 core `rag_parser_core.oci_enterprise_ai` を **env 由来 config** で駆動する。
- `POST /parse`(file + content_type + prompt)→ OCI Generative AI の Chat/Responses 推論
  (+ Files API: `…/openai/v1/files` で `purpose=vision`/`user_data` upload→file_id 参照→delete、
  3 つの `vlm_input_mode` auto/files_api/inline_image)を Vision モデルで呼び、
  `StructuredExtraction`(ParseResponse)で返す。
- `GET /health` は OCI 設定の充足(endpoint / api_key / vision model)で ok/degraded。
- **OCI 認証はメインプロジェクト設定を継承**: docker-compose で `backend/.env` と `~/.oci`
  マウント、`OCI_CONFIG_FILE` を受け取る(個別設定なし)。`--profile oci` で opt-in。
- OCI Generative AI は **OpenAI 互換の API キー認証(httpx)** のため oci 署名 SDK は不要。
- 未設定/失敗時は extraction=None を返し、backend 側で既存 in-process VLM(PDF 分割込み)/
  ローカルフローへ安全に縮退する。PDF 分割・checkpoint は backend(DB 結合)側に残る。

## 環境変数(backend と同じキーを継承)

`OCI_ENTERPRISE_AI_ENDPOINT` / `OCI_ENTERPRISE_AI_API_KEY` / `OCI_ENTERPRISE_AI_PROJECT_OCID` /
`OCI_COMPARTMENT_ID`、`OCI_ENTERPRISE_AI_VLM_MODEL`(vision model) /
`OCI_ENTERPRISE_AI_DEFAULT_MODEL`、`OCI_ENTERPRISE_AI_VLM_PATH` / `_VLM_RESPONSE_PATH` /
`_VLM_PAYLOAD_TEMPLATE` / `_VLM_INPUT_MODE` / `_VLM_MAX_OUTPUT_TOKENS`、
`OCI_ENTERPRISE_AI_TIMEOUT_SECONDS` / `_MAX_RETRIES`。

## ローカル実行

```bash
uv run --directory services/parsers/oci_genai_vision \
  uvicorn app.main:app --port 8027
```
