"""Retrieval ステージマイクロサービス。

strategy(hybrid_rrf/vector/keyword/graph_augmented/business_context_strict/
corrective_multi_query)→ 検索挙動フラグを解決して返す。解決ロジックは backend と同一
(rag_pipeline_core.retrieval)で決定論・外部依存なし。実 retrieval(Oracle 26ai 経路)は backend
が実行する。外部検索エンジンは導入しない。
"""

from rag_pipeline_core.stage_service import create_retrieval_app

app = create_retrieval_app(title="pipeline-retrieval")
