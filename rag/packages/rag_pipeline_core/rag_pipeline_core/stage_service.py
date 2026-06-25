"""pipeline ステージ用 FastAPI app factory(fastapi 依存はこのモジュールに隔離)。

各ステージサービスは決定論ロジックを注入して 1 つの FastAPI app を得る。``rag_parser_core``
の ``create_parse_app`` / ``create_preprocess_app`` と同じ思想。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from rag_pipeline_core.agentic import resolve_agentic
from rag_pipeline_core.chunking import Chunk, chunk_extraction_with_strategy
from rag_pipeline_core.evaluation import resolve_evaluation
from rag_pipeline_core.generation import resolve_generation
from rag_pipeline_core.graph import resolve_graph_profile
from rag_pipeline_core.grounding import resolve_grounding
from rag_pipeline_core.guardrail import resolve_guardrail
from rag_pipeline_core.retrieval import resolve_retrieval
from rag_pipeline_core.stage import (
    AgenticStageRequest,
    AgenticStageResponse,
    ChunkingStageRequest,
    ChunkingStageResponse,
    EvaluationStageRequest,
    EvaluationStageResponse,
    GenerationStageRequest,
    GenerationStageResponse,
    GraphStageRequest,
    GraphStageResponse,
    GroundingStageRequest,
    GroundingStageResponse,
    GuardrailStageRequest,
    GuardrailStageResponse,
    RetrievalStageRequest,
    RetrievalStageResponse,
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
        resolved = resolve_graph_profile(request.profile, legacy_enabled=request.legacy_enabled)
        return GraphStageResponse(
            profile=resolved.profile,
            build_entities=resolved.build_entities,
            build_relationships=resolved.build_relationships,
            build_claims=resolved.build_claims,
            build_community_summary=resolved.build_community_summary,
            temporal=resolved.temporal,
        )

    return app


def create_generation_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-generation"
) -> FastAPI:
    """generation ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="generation", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=GenerationStageResponse)
    def run(request: GenerationStageRequest) -> GenerationStageResponse:
        resolved = resolve_generation(request.profile)
        return GenerationStageResponse(
            profile=resolved.profile,
            system_prompt=resolved.system_prompt,
            structured_output=resolved.structured_output,
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


def create_agentic_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-agentic"
) -> FastAPI:
    """agentic ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="agentic", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=AgenticStageResponse)
    def run(request: AgenticStageRequest) -> AgenticStageResponse:
        resolved = resolve_agentic(request.profile)
        return AgenticStageResponse(
            profile=resolved.profile,
            enabled=resolved.enabled,
            rewrite=resolved.rewrite,
            decompose=resolved.decompose,
            multi_hop=resolved.multi_hop,
            smart_routing=resolved.smart_routing,
            hyde=resolved.hyde,
        )

    return app


def create_grounding_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-grounding"
) -> FastAPI:
    """grounding ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="grounding", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=GroundingStageResponse)
    def run(request: GroundingStageRequest) -> GroundingStageResponse:
        resolved = resolve_grounding(request.pipeline)
        return GroundingStageResponse(
            pipeline=resolved.pipeline,
            dependency_promotion=resolved.dependency_promotion,
            diversity=resolved.diversity,
            expansion_mode=resolved.expansion_mode,
            compression=resolved.compression,
            corrective=resolved.corrective,
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
            focus_metrics=list(resolved.focus_metrics),
        )

    return app


def create_retrieval_app(
    *, health_probe: HealthProbe | None = None, title: str = "pipeline-retrieval"
) -> FastAPI:
    """retrieval ステージサービスの FastAPI app(``POST /run`` + ``GET /health``)。"""
    app = FastAPI(title=title)
    probe = health_probe or (
        lambda: StageHealth(status="ok", stage="retrieval", package_name="rag_pipeline_core")
    )
    _health_routes(app, probe)

    @app.post("/run", response_model=RetrievalStageResponse)
    def run(request: RetrievalStageRequest) -> RetrievalStageResponse:
        resolved = resolve_retrieval(request.strategy, request.settings_query_expansion)
        return RetrievalStageResponse(
            strategy=resolved.strategy,
            mode_override=resolved.mode_override,
            strategy_bias=resolved.strategy_bias,
            query_expansion=resolved.query_expansion,
            gap_stop=resolved.gap_stop,
            corrective_retrieval=resolved.corrective_retrieval,
            business_fit_weighting=resolved.business_fit_weighting,
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
            child_size=request.child_size,
            sentence_window_size=request.sentence_window_size,
            min_chars=request.min_chars,
            delimiter=request.delimiter,
        )
        return ChunkingStageResponse.from_chunks(list(chunks))

    return app
