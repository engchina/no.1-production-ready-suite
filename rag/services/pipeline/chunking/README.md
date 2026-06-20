# pipeline ステージ: chunking

構造化抽出(`StructuredExtraction`)を選択 chunking 戦略で分割するステージマイクロサービス。
chunk 分割ロジックは backend と **同一(`rag_pipeline_core.chunking`)** で決定論・外部依存なし。
pipeline 各ステージのプラグイン化(全 service 化)の第 1 弾。

| 項目 | 値 |
|---|---|
| stage | `chunking` |
| 主依存 | rag_pipeline_core(pydantic + rag_parser_core のみ) |
| 既定 URL | `http://pipeline-chunking:8000` |
| dev port | 8030 |
| profile 種別 | CPU(dev は uv プロセス) |

## 契約

- `POST /run`(`ChunkingStageRequest` → `ChunkingStageResponse`): 抽出 + 戦略パラメータ
  (strategy / chunk_size / overlap / child_size / sentence_window_size / min_chars)→ chunk 配列。
- `GET /health` → `StageHealth`。

## backend 連携

backend は `app.clients.pipeline_stage.PipelineStageClient` で委譲する。`RAG_CHUNKING_SERVICE_ENABLED`
が真かつ `RAG_CHUNKING_SERVICE_URL` 設定時に `POST /run` を呼び、**未達/timeout/無効/不正応答時は
in-process(同一ロジック)へ安全縮退**する(常時 remote でも 1 サービス停止で取込は止まらない)。

## 起動

```bash
# dev(ホストの uv プロセス)
uv run --directory services/pipeline/chunking uvicorn app.main:app --port 8030

# Docker(build context = リポジトリ root)
docker compose up pipeline-chunking
```
