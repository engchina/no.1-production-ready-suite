"""Grounding ステージマイクロサービス。

preset(lean/verified_context/context_enrich/compact/full_governed)→ 検索後処理段フラグを解決して
返す。解決ロジックは backend と同一(rag_pipeline_core.grounding)で決定論・外部依存なし。custom
preset と実際の後処理実行(dedupe/verify/expansion/compression)は backend が担う。
"""

from rag_pipeline_core.stage_service import create_grounding_app

app = create_grounding_app(title="pipeline-grounding")
