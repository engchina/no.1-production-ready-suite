# pipeline ステージ: vector_index

Vector Index プロファイル(balanced/accurate/fast)を Oracle 26ai AI Vector Search の
target accuracy + HNSW 推奨ビルド値へ解決するステージマイクロサービス。解決ロジックは backend と
**同一(`rag_pipeline_core.vector_index`)** で決定論・外部依存なし。外部ベクトル DB は導入しない。

| 項目 | 値 |
|---|---|
| stage | `vector_index` |
| 既定 URL | `http://pipeline-vector-index:8000` / dev port 8031 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`VectorIndexStageRequest{profile, settings_target_accuracy}` → `VectorIndexStageResponse`)。
- `GET /health` → `StageHealth`。

backend は `RAG_VECTOR_INDEX_SERVICE_ENABLED` 真かつ URL 設定時に委譲し、サービス未起動・
未到達時は in-process(同一ロジック)へ安全縮退する。remote が応答した後の HTTP error /
不正応答は壊れたサービスとして停止する。
