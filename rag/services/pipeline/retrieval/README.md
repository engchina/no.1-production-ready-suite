# pipeline ステージ: retrieval

検索戦略(hybrid_rrf/vector/keyword/graph_augmented/business_context_strict/
corrective_multi_query)→ 検索挙動フラグ(mode_override / strategy_bias / query_expansion /
gap_stop / corrective / business_fit)へ解決するステージマイクロサービス。解決ロジックは backend と
**同一(`rag_pipeline_core.retrieval`)** で決定論・外部依存なし。**実 retrieval(Oracle 26ai 経路)は
backend が実行**し、本サービスは戦略の「決定」のみを担う(vector_index と同じ分離)。外部検索
エンジンは導入しない。

| 項目 | 値 |
|---|---|
| stage | `retrieval` |
| 既定 URL | `http://pipeline-retrieval:8000` / dev port 8038 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`RetrievalStageRequest{strategy, settings_query_expansion}` → `RetrievalStageResponse`)。
  mode/strategy は wire 中立の文字列で受け渡し、backend が SearchMode/SearchStrategy へ写す。
- `GET /health` → `StageHealth`。

backend は `RAG_RETRIEVAL_SERVICE_ENABLED` 真かつ URL 設定時に strategy 解決を委譲し、未達/失敗時は
in-process(同一ロジック)へ安全縮退する。

## 新戦略(段階導入予定)

- `reasoning_tree_search`(PageIndex)/ `colpali_visual_retrieval`(GPU VLM 視覚検索)は追加 LLM/GPU を
  伴うため、Oracle/VLM の実行配線を含め後続で opt-in 戦略として導入する(本サービスは戦略決定の入口)。
