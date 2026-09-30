"""Evaluation ステージマイクロサービス。

評価の基準(standard / strict。#591)→ CI gate 用閾値を解決して返す。解決ロジックは backend と
同一(rag_pipeline_core.evaluation)で決定論・外部依存なし。評価の実行(指標の計算と、標準回答が
あるケースの LLM による比較)は backend が担う。
"""

from rag_pipeline_core.stage_service import create_evaluation_app

app = create_evaluation_app(title="pipeline-evaluation")
