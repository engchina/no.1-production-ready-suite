"""pipeline stage の local core と HTTP service factory の同一性テスト。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel
from rag_pipeline_core.evaluation import resolve_evaluation
from rag_pipeline_core.graph import resolve_graph_profile
from rag_pipeline_core.guardrail import resolve_guardrail
from rag_pipeline_core.stage import (
    EvaluationStageRequest,
    EvaluationStageResponse,
    GraphStageRequest,
    GraphStageResponse,
    GuardrailStageRequest,
    GuardrailStageResponse,
    VectorIndexStageRequest,
    VectorIndexStageResponse,
)
from rag_pipeline_core.stage_service import (
    create_evaluation_app,
    create_graph_app,
    create_guardrail_app,
    create_vector_index_app,
)
from rag_pipeline_core.vector_index import resolve_vector_index


def _run(app: FastAPI, request: BaseModel, response_model: type[BaseModel]) -> BaseModel:
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/run"
        and "POST" in (route.methods or set())
    )
    return response_model.model_validate(route.endpoint(request))


def test_vector_index_service_matches_local_core() -> None:
    request = VectorIndexStageRequest(profile="accurate", settings_target_accuracy=95)
    remote = _run(create_vector_index_app(), request, VectorIndexStageResponse)
    local_resolved = resolve_vector_index(request.profile, request.settings_target_accuracy)
    local = VectorIndexStageResponse(**local_resolved.__dict__)
    assert remote == local


def test_graph_service_matches_local_core() -> None:
    request = GraphStageRequest(profile="entities")
    remote = _run(create_graph_app(), request, GraphStageResponse)
    local = GraphStageResponse(**resolve_graph_profile(request.profile).__dict__)
    assert remote == local


def test_guardrail_service_matches_local_core() -> None:
    request = GuardrailStageRequest(policy="regulated")
    remote = _run(create_guardrail_app(), request, GuardrailStageResponse)
    local = GuardrailStageResponse(**resolve_guardrail(request.policy).__dict__)
    assert remote == local


def test_evaluation_service_matches_local_core() -> None:
    request = EvaluationStageRequest(suite="strict")
    remote = _run(create_evaluation_app(), request, EvaluationStageResponse)
    local = EvaluationStageResponse(**resolve_evaluation(request.suite).__dict__)
    assert remote == local
