# pipeline ステージ: graphrag

GraphRAG プロファイル(off/entities/full)+ legacy フラグ → KG 構築フラグへ解決するステージ
マイクロサービス。解決ロジックは backend と **同一(`rag_pipeline_core.graph`)** で決定論・外部
依存なし。実 KG 構築(LLM=OCI Enterprise AI / Oracle 26ai)は backend ingestion が担う。
外部グラフ DB は導入しない。

| 項目 | 値 |
|---|---|
| stage | `graphrag` |
| 既定 URL | `http://pipeline-graphrag:8000` / dev port 8032 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`GraphStageRequest{profile, legacy_enabled}` → `GraphStageResponse`)。
- Temporal GraphRAG(full 時の timestamp 付与)は backend 設定 `RAG_GRAPH_TEMPORAL_ENABLED` で
  制御し、build 側で適用する。
- `GET /health` → `StageHealth`。

backend は `RAG_GRAPH_SERVICE_ENABLED` 真かつ URL 設定時に委譲し、サービス未起動・未到達時は
in-process(同一ロジック)へ安全縮退する。remote が応答した後の HTTP error / 不正応答は
壊れたサービスとして停止する。
