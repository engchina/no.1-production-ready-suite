"""pipeline ステージ用 FastAPI app factory(fastapi 依存はこのモジュールに隔離)。

各ステージサービスは決定論ロジックを注入して 1 つの FastAPI app を得る。``rag_parser_core``
の ``create_parse_app`` / ``create_preprocess_app`` と同じ思想。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from rag_pipeline_core.chunking import Chunk, chunk_extraction_with_strategy
from rag_pipeline_core.evaluation import resolve_evaluation
from rag_pipeline_core.graph import resolve_graph_profile
from rag_pipeline_core.guardrail import resolve_guardrail
from rag_pipeline_core.stage import (
    ChunkingStageRequest,
    ChunkingStageResponse,
    EvaluationStageRequest,
    EvaluationStageResponse,
    GraphStageRequest,
    GraphStageResponse,
    GuardrailStageRequest,
    GuardrailStageResponse,
    StageHealth,
    VectorIndexStageRequest,
    VectorIndexStageResponse,
)
from rag_pipeline_core.vector_index import resolve_vector_index

ChunkingHealthProbe = Callable[[], StageHealth]
HealthProbe = Callable[[], StageHealth]


def _health_routes(app: FastAPI, probe: HealthProbe) -> None:
    @app.get("/health", response_model=StageHealth)
    def health() -> StageHealth:
        return probe()

    @app.get("/api/ready", response_model=StageHealth)
    def ready() -> StageHealth:
        return probe()


def create_vector_index_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-vector-index"
) -> FastAPI:
    """vector_index ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="vector_index", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=VectorIndexStageResponse)
    def run(request: VectorIndexStageRequest) -> VectorIndexStageResponse:
        resolved = resolve_vector_index(request.profile, request.settings_target_accuracy)
        return VectorIndexStageResponse(
            profile=resolved.profile,
            target_accuracy=resolved.target_accuracy,
            neighbors=resolved.neighbors,
            efconstruction=resolved.efconstruction,
            distance=resolved.distance,
            requires_reprovision=resolved.requires_reprovision,
        )

    return app


def create_graph_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-graphrag"
) -> FastAPI:
    """graphrag ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="graphrag", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=GraphStageResponse)
    def run(request: GraphStageRequest) -> GraphStageResponse:
        resolved = resolve_graph_profile(request.profile)
        return GraphStageResponse(
            profile=resolved.profile,
            build_entities=resolved.build_entities,
            build_relationships=resolved.build_relationships,
        )

    return app


def create_guardrail_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-guardrail"
) -> FastAPI:
    """guardrail ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="guardrail", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=GuardrailStageResponse)
    def run(request: GuardrailStageRequest) -> GuardrailStageResponse:
        resolved = resolve_guardrail(request.policy)
        return GuardrailStageResponse(
            policy=resolved.policy,
            grounding_min_overlap=resolved.grounding_min_overlap,
            grounding_min_ratio=resolved.grounding_min_ratio,
            audit_emphasis=resolved.audit_emphasis,
        )

    return app


def create_evaluation_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-evaluation"
) -> FastAPI:
    """evaluation ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="evaluation", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=EvaluationStageResponse)
    def run(request: EvaluationStageRequest) -> EvaluationStageResponse:
        resolved = resolve_evaluation(request.suite)
        return EvaluationStageResponse(
            suite=resolved.suite,
            thresholds=resolved.thresholds,
        )

    return app


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
            min_chars=request.min_chars,
            delimiter=request.delimiter,
        )
        return ChunkingStageResponse.from_chunks(list(chunks))

    return app
