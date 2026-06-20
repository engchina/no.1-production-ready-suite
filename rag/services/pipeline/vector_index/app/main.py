"""Vector Index ステージマイクロサービス。

profile(balanced/accurate/fast)→ Oracle 26ai AI Vector Search の target accuracy + HNSW
推奨値を解決して返す。解決ロジックは backend と同一(rag_pipeline_core.vector_index)で
決定論・外部依存なし。外部ベクトル DB は導入しない。
"""

from rag_pipeline_core.stage_service import create_vector_index_app

app = create_vector_index_app(title="pipeline-vector-index")
