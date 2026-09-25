"""GraphRAG ステージマイクロサービス。

profile(off/entities/full)+ legacy フラグ → KG 構築フラグを解決して返す。解決ロジックは
backend と同一(rag_pipeline_core.graph)で決定論・外部依存なし。実 KG 構築(LLM/Oracle)は
backend ingestion が担う。外部グラフ DB は導入しない。
"""

from rag_pipeline_core.stage_service import create_graph_app

app = create_graph_app(title="pipeline-graphrag")
