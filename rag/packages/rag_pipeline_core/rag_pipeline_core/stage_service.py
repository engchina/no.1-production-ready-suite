"""pipeline ステージ用 FastAPI app factory(fastapi 依存はこのモジュールに隔離)。

各ステージサービスは決定論ロジックを注入して 1 つの FastAPI app を得る。``rag_parser_core``
の ``create_parse_app`` / ``create_preprocess_app`` と同じ思想。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from rag_pipeline_core.chunking import Chunk, chunk_extraction_with_strategy
from rag_pipeline_core.stage import (
    ChunkingStageRequest,
    ChunkingStageResponse,
    StageHealth,
)

ChunkingHealthProbe = Callable[[], StageHealth]


def create_chunking_app(
    *,
    health_probe: ChunkingHealthProbe | None = None,
    title: str = "pipeline-chunking",
) -> FastAPI:
    """chunking ステージサービスの FastAPI app を生成する(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)

    @app.get("/health", response_model=StageHealth)
    def health() -> StageHealth:
        if health_probe is not None:
            return health_probe()
        return StageHealth(status="ok", stage="chunking", package_name="rag_pipeline_core")

    @app.get("/api/ready", response_model=StageHealth)
    def ready() -> StageHealth:
        return health()

    @app.post("/run", response_model=ChunkingStageResponse)
    def run(request: ChunkingStageRequest) -> ChunkingStageResponse:
        chunks: list[Chunk] = chunk_extraction_with_strategy(
            request.extraction,
            strategy=request.strategy,
            chunk_size=request.chunk_size,
            overlap=request.overlap,
            child_size=request.child_size,
            sentence_window_size=request.sentence_window_size,
            min_chars=request.min_chars,
        )
        return ChunkingStageResponse.from_chunks(list(chunks))

    return app
