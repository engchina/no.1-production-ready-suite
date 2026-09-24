"""Evaluation ステージマイクロサービス。

suite(request_only/retrieval_focused/balanced/strict_ci/ragas_like)→ CI gate 用閾値 + focus
metrics を解決して返す。解決ロジックは backend と同一(rag_pipeline_core.evaluation)で決定論・
外部依存なし。実評価(決定論指標)は backend が担う。外部評価 SaaS / LLM-as-judge は導入しない。
"""

from rag_pipeline_core.stage_service import create_evaluation_app

app = create_evaluation_app(title="pipeline-evaluation")
