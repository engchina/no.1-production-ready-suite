"""Generation ステージマイクロサービス。

profile → OCI Enterprise AI へ渡す system prompt 変種 + 構造化出力フラグを解決して返す。
解決ロジックは backend と同一(rag_pipeline_core.generation)で決定論・外部依存なし。実 LLM
生成(OCI Enterprise AI)と custom/persona override は backend が担う。外部 provider なし。
"""

from rag_pipeline_core.stage_service import create_generation_app

app = create_generation_app(title="pipeline-generation")
