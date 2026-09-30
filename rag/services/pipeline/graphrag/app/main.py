"""関係情報の構築ステージマイクロサービス。

profile(off/entities)→ 関係情報の構築フラグを解決して返す。解決ロジックは backend と同一
(rag_pipeline_core.graph)で決定論・外部依存なし。実際の構築(Oracle への保存)は backend の
取込が担う。外部グラフ DB は導入しない。
"""

from rag_pipeline_core.stage_service import create_graph_app

app = create_graph_app(title="pipeline-graphrag")
