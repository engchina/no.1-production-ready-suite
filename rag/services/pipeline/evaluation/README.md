# pipeline ステージ: evaluation

評価スイート(request_only/retrieval_focused/balanced/strict_ci/ragas_like)→ CI gate 用閾値へ
解決するステージマイクロサービス。解決ロジックは backend と **同一
(`rag_pipeline_core.evaluation`)** で決定論・外部依存なし。実評価(決定論指標)は backend が担う。
外部評価 SaaS / LLM-as-judge は導入しない。

| 項目 | 値 |
|---|---|
| stage | `evaluation` |
| 既定 URL | `http://pipeline-evaluation:8000` / dev port 8037 |
| profile 種別 | CPU(dev は uv プロセス) |

- `POST /run`(`EvaluationStageRequest{suite}` → `EvaluationStageResponse{thresholds}`)。
- `GET /health` → `StageHealth`。

suite→閾値解決は決定論の name→閾値 lookup のため、backend は表示も実 gate も in-process
(同一 `rag_pipeline_core.evaluation`)で解決する。本サービスは同一ロジックを公開する独立
マイクロサービスとして提供するが、現状 backend の閾値解決経路では使用しない。閾値 dict は
backend で `EvaluationThresholds` へ写す。
