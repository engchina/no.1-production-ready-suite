"""Guardrail ステージマイクロサービス。

policy(standard/strict/lenient/regulated)→ groundedness 厳格度 + 監査強調を解決して返す。
解決ロジックは backend と同一(rag_pipeline_core.guardrail)で決定論・外部依存なし。block /
PII マスク等の設定由来レバーと OCI Guardrails backend は backend が担う。外部安全 SaaS なし。
"""

from rag_pipeline_core.stage_service import create_guardrail_app

app = create_guardrail_app(title="pipeline-guardrail")
