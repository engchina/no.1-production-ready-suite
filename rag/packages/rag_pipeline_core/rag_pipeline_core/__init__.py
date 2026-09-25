"""rag_pipeline_core: pipeline ステージのプラグイン共有契約。

各 pipeline ステージ(chunking / vector_index / retrieval / grounding / generation /
guardrail / evaluation / graphrag / agentic)を独立マイクロサービス化するための共有契約・
決定論ロジック・FastAPI app factory を提供する。``rag_parser_core`` と同じく軽量(依存は
pydantic + rag_parser_core のみ)で、重い実行依存は各サービス側へ隔離する。
"""

from rag_pipeline_core.stage import StageHealth

__all__ = ["StageHealth"]
