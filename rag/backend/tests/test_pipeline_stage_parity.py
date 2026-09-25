"""pipeline stage の local core と HTTP service factory の同一性テスト。"""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError
from rag_parser_core.extraction import StructuredExtraction
from rag_pipeline_core.agentic import resolve_agentic
from rag_pipeline_core.chunking import chunk_extraction_with_strategy
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
    VectorIndexStageRequest,
    VectorIndexStageResponse,
)
from rag_pipeline_core.stage_service import (
    create_agentic_app,
    create_chunking_app,
    create_evaluation_app,
    create_generation_app,
    create_graph_app,
    create_grounding_app,
    create_guardrail_app,
    create_retrieval_app,
    create_vector_index_app,
)
from rag_pipeline_core.vector_index import resolve_vector_index


def _run(app: FastAPI, request: BaseModel, response_model: type[BaseModel]) -> BaseModel:
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/run" and "POST" in route.methods
    )
    return response_model.model_validate(route.endpoint(request))


def test_chunking_service_matches_local_core() -> None:
    request = ChunkingStageRequest(
        extraction=StructuredExtraction(raw_text="第1章 概要\n本文です。"),
        strategy="fixed_size",
        chunk_size=200,
        overlap=0,
    )
    remote = _run(create_chunking_app(), request, ChunkingStageResponse)
    local_chunks = chunk_extraction_with_strategy(
        request.extraction,
        strategy=request.strategy,
        chunk_size=request.chunk_size,
        overlap=request.overlap,
        child_size=request.child_size,
        min_chars=request.min_chars,
        delimiter=request.delimiter,
    )
    local = ChunkingStageResponse.from_chunks(cast("list[object]", local_chunks))
    assert remote == local


def test_chunking_stage_request_enforces_product_limits() -> None:
    extraction = StructuredExtraction(raw_text="本文です。")
    accepted = ChunkingStageRequest(
        extraction=extraction,
        chunk_size=32_000,
        overlap=8_000,
    )
    assert accepted.chunk_size == 32_000
    assert accepted.overlap == 8_000

    with pytest.raises(ValidationError):
        ChunkingStageRequest(extraction=extraction, chunk_size=32_001)
    with pytest.raises(ValidationError):
        ChunkingStageRequest(extraction=extraction, chunk_size=200, overlap=200)


def test_vector_index_service_matches_local_core() -> None:
    request = VectorIndexStageRequest(profile="accurate", settings_target_accuracy=95)
    remote = _run(create_vector_index_app(), request, VectorIndexStageResponse)
    local_resolved = resolve_vector_index(request.profile, request.settings_target_accuracy)
    local = VectorIndexStageResponse(**local_resolved.__dict__)
    assert remote == local


def test_graph_service_matches_local_core() -> None:
    request = GraphStageRequest(profile="full", legacy_enabled=False)
    remote = _run(create_graph_app(), request, GraphStageResponse)
    local_resolved = resolve_graph_profile(request.profile, legacy_enabled=request.legacy_enabled)
    local = GraphStageResponse(
        profile=local_resolved.profile,
        build_entities=local_resolved.build_entities,
        build_relationships=local_resolved.build_relationships,
        build_claims=local_resolved.build_claims,
        build_community_summary=local_resolved.build_community_summary,
        temporal=local_resolved.temporal,
    )
    assert remote == local


def test_generation_service_matches_local_core() -> None:
    request = GenerationStageRequest(profile="inline_cited")
    remote = _run(create_generation_app(), request, GenerationStageResponse)
    local = GenerationStageResponse(**resolve_generation(request.profile).__dict__)
    assert remote == local


def test_guardrail_service_matches_local_core() -> None:
    request = GuardrailStageRequest(policy="regulated")
    remote = _run(create_guardrail_app(), request, GuardrailStageResponse)
    local = GuardrailStageResponse(**resolve_guardrail(request.policy).__dict__)
    assert remote == local


def test_agentic_service_matches_local_core() -> None:
    request = AgenticStageRequest(profile="multi_hop")
    remote = _run(create_agentic_app(), request, AgenticStageResponse)
    local = AgenticStageResponse(**resolve_agentic(request.profile).__dict__)
    assert remote == local


def test_grounding_service_matches_local_core() -> None:
    request = GroundingStageRequest(pipeline="verified_context")
    remote = _run(create_grounding_app(), request, GroundingStageResponse)
    local = GroundingStageResponse(**resolve_grounding(request.pipeline).__dict__)
    assert remote == local


def test_evaluation_service_matches_local_core() -> None:
    request = EvaluationStageRequest(suite="strict_ci")
    remote = _run(create_evaluation_app(), request, EvaluationStageResponse)
    local = EvaluationStageResponse(**resolve_evaluation(request.suite).__dict__)
    assert remote == local


def test_retrieval_service_matches_local_core() -> None:
    request = RetrievalStageRequest(strategy="corrective_multi_query")
    remote = _run(create_retrieval_app(), request, RetrievalStageResponse)
    local = RetrievalStageResponse(
        **resolve_retrieval(request.strategy, request.settings_query_expansion).__dict__
    )
    assert remote == local
