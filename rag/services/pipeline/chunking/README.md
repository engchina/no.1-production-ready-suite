# pipeline ステージ: chunking

構造化抽出(`StructuredExtraction`)を選択 chunking 戦略で分割するステージマイクロサービス。
chunk 分割ロジックは backend と **同一(`rag_pipeline_core.chunking`)** で決定論・外部依存なし。
pipeline 各ステージの remote 実行プラグイン。

| 項目 | 値 |
|---|---|
| stage | `chunking` |
| 主依存 | rag_pipeline_core(pydantic + rag_parser_core のみ) |
| 既定 URL | `http://pipeline-chunking:8000` |
| dev port | 18030 |
| profile 種別 | CPU(dev は uv プロセス) |

## 契約

- `POST /run`(`ChunkingStageRequest` → `ChunkingStageResponse`): 抽出 + 戦略パラメータ
  (strategy / chunk_size / overlap / child_size / sentence_window_size / min_chars / delimiter)→ chunk 配列。
- `GET /health` → `StageHealth`。

## backend 連携

backend は `app.clients.pipeline_stage.PipelineStageClient` で `POST /run` を試す。
サービス未起動・未到達時は backend in-process の同一 `rag_pipeline_core.chunking` 実装へ縮退する。
remote が応答した後の HTTP error / 不正応答は壊れたサービスとして停止する。

## 起動

```bash
# dev(ホストの uv プロセス)
uv run --directory services/pipeline/chunking uvicorn app.main:app --port 18030

# Docker(build context = リポジトリ root)
docker compose up pipeline-chunking
```
