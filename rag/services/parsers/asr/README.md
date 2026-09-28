# parser: ASR(音声文字起こし、`asr`)

audio/video を **ローカル faster-whisper(GPU)** で転写し、共有 contract
(`rag_parser_core`)の `StructuredExtraction` を返す parser マイクロサービス。OCI AI Speech
(backend の service backend `app.clients.oci_speech`)の **fallback** として使う。外部 SaaS は
呼ばない(確定スタック非抵触)。

| 項目 | 値 |
|---|---|
| backend 名 | `asr` |
| 実行 | **GPU**(CUDA。開発環境で `rag/scripts/rag-services.sh install --gpu`。本番の stack には含めない) |
| 主依存 | faster-whisper(CTranslate2)+ ffmpeg |
| 既定 URL | `http://127.0.0.1:18026` |
| dev port | 18026 |

## 契約

- `POST /parse`(multipart: `file` / `content_type` / `source_profile`)→ `ParseResponse`
  (転写を paragraph 要素へ remap、各要素に開始/終了秒・`timestamp` metadata)。転写失敗時は
  `extraction=None` + `unsupported_reason=asr_transcription_failed` を返し backend を縮退させる。
- `GET /health` → faster-whisper 可用性で `ok` / `degraded`。

## モデル設定(env)

- `ASR_MODEL_SIZE`(既定 `large-v3`)/ `ASR_DEVICE`(既定 `cuda`)/ `ASR_COMPUTE_TYPE`(既定 `float16`)。

## 起動

```bash
# GPU(CUDA host)
scripts/rag-services.sh install --gpu   # rag/ で実行(systemd の unit)
```

## 取込時の経路(backend)

audio source kind は backend ingestion が **OCI AI Speech → 本サービス → 未対応** の順で解決する
(`OCI_SPEECH_*` 設定があれば OCI 優先、無ければ本サービスへ HTTP 委譲)。
