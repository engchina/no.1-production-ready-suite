# pipeline ステージ: agentic

クエリ計画プロファイル(off/smart_routing/query_rewrite/decompose/multi_hop)→ 挙動フラグへ解決
するステージマイクロサービス。解決ロジックは backend と **同一(`rag_pipeline_core.agentic`)** で
決定論・外部依存なし。実 LLM クエリ計画(OCI Enterprise AI)は backend が担う。外部 LLM provider
は導入しない。

| 項目 | 値 |
|---|---|
| stage | `agentic` |
| 既定 URL | `http://pipeline-agentic:8000` / dev port 8035 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`AgenticStageRequest{profile}` → `AgenticStageResponse`)。
- 新 profile `smart_routing`(v1): query を LLM で理解・書き換えして検索向けに正規化
  (query-type aware routing の入口。現状は query_rewrite と同じ LLM 計画経路)。
- `GET /health` → `StageHealth`。

backend は `RAG_AGENTIC_SERVICE_ENABLED` 真かつ URL 設定時に静的解決を委譲し、未達/失敗時は
in-process(同一ロジック)へ安全縮退する。
