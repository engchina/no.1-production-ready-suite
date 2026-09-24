"""Agentic ステージマイクロサービス。

profile(off/smart_routing/query_rewrite/decompose/multi_hop)→ クエリ計画の挙動フラグを解決して
返す。解決ロジックは backend と同一(rag_pipeline_core.agentic)で決定論・外部依存なし。実 LLM
クエリ計画(OCI Enterprise AI)は backend が担う。外部 LLM provider は導入しない。
"""

from rag_pipeline_core.stage_service import create_agentic_app

app = create_agentic_app(title="pipeline-agentic")
