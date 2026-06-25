# pipeline ステージ: evaluation

評価スイート(request_only/retrieval_focused/balanced/strict_ci/ragas_like)→ CI gate 用閾値 +
focus metrics へ解決するステージマイクロサービス。解決ロジックは backend と **同一
(`rag_pipeline_core.evaluation`)** で決定論・外部依存なし。実評価(決定論指標)は backend が担う。
外部評価 SaaS / LLM-as-judge は導入しない。

| 項目 | 値 |
|---|---|
| stage | `evaluation` |
| 既定 URL | `http://pipeline-evaluation:8000` / dev port 8037 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`EvaluationStageRequest{suite}` → `EvaluationStageResponse{thresholds, focus_metrics}`)。
- `GET /health` → `StageHealth`。

backend は `RAG_EVALUATION_SERVICE_ENABLED` 真かつ URL 設定時に suite→閾値解決を委譲し、
サービス未起動・未到達時は in-process(同一ロジック)へ安全縮退する。閾値 dict は backend で
`EvaluationThresholds` へ写す。remote が応答した後の HTTP error / 不正応答は壊れたサービスとして
停止する。
