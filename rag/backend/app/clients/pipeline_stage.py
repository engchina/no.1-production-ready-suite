"""pipeline ステージマイクロサービスへの HTTP 委譲クライアント。

各 pipeline ステージを独立サービスとして呼ぶ。``RAG_<STAGE>_SERVICE_ENABLED`` が真のとき
remote 委譲を試し、サービス未起動・未到達なら ``None`` を返して backend 側の
``rag_pipeline_core`` 実装へ縮退する。remote が応答した後の HTTP error / 不正応答は、
壊れたサービスを隠さないため処理停止する。

確定スタックは不変。ロジックは backend と共有パッケージ ``rag_pipeline_core`` で同一。
"""

from __future__ import annotations

import logging

import httpx
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

from app.config import Settings
from app.rag.chunking import Chunk
from app.services.catalog import resolve_service_base_url

logger = logging.getLogger(__name__)

# ステージ名 → サービス URL の Settings フィールド。
_STAGE_URL_FIELDS: dict[str, str] = {
    "chunking": "rag_chunking_service_url",
    "vector_index": "rag_vector_index_service_url",
    "graphrag": "rag_graph_service_url",
    "generation": "rag_generation_service_url",
    "guardrail": "rag_guardrail_service_url",
    "agentic": "rag_agentic_service_url",
    "grounding": "rag_grounding_service_url",
    "evaluation": "rag_evaluation_service_url",
    "retrieval": "rag_retrieval_service_url",
}
_STAGE_ENABLED_FIELDS: dict[str, str] = {
    "chunking": "rag_chunking_service_enabled",
    "vector_index": "rag_vector_index_service_enabled",
    "graphrag": "rag_graph_service_enabled",
    "generation": "rag_generation_service_enabled",
    "guardrail": "rag_guardrail_service_enabled",
    "agentic": "rag_agentic_service_enabled",
    "grounding": "rag_grounding_service_enabled",
    "evaluation": "rag_evaluation_service_enabled",
    "retrieval": "rag_retrieval_service_enabled",
}


class PipelineStageClient:
    """pipeline ステージを HTTP で実行する。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = float(getattr(settings, "rag_pipeline_stage_timeout_seconds", 120.0))

    def _service_url(self, stage: str) -> str | None:
        field = _STAGE_URL_FIELDS.get(stage)
        if field is None:
            return None
        raw = resolve_service_base_url(self._settings, field).strip().rstrip("/")
        return raw or None

    def is_enabled(self, stage: str) -> bool:
        """ステージが remote 委譲対象か(URL 設定 + enable フラグ)。"""
        field = _STAGE_ENABLED_FIELDS.get(stage)
        enabled = bool(getattr(self._settings, field, False)) if field else False
        return enabled and self._service_url(stage) is not None

    def _post_run(self, stage: str, request_json: str) -> dict[str, object] | None:
        """``POST /run`` を呼び JSON を返す。未設定・未到達なら None、壊れた応答は例外。"""
        if not self.is_enabled(stage):
            return None
        url = self._service_url(stage)
        if url is None:
            return None
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(
                    "POST",
                    f"{url}/run",
                    content=request_json,
                    headers=_JSON_HEADERS,
                )
                response.raise_for_status()
                payload: dict[str, object] = response.json()
                return payload
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "pipeline stage service returned error",
                extra={"stage": stage, "service_url": url, "error": str(exc)},
            )
            raise PipelineStageServiceError(stage, "remote_error", service_url=url) from exc
        except httpx.InvalidURL as exc:
            logger.warning(
                "pipeline stage service URL is invalid",
                extra={"stage": stage, "service_url": url, "error": str(exc)},
            )
            raise PipelineStageServiceError(stage, "invalid_url", service_url=url) from exc
        except httpx.RequestError as exc:
            logger.info(
                "pipeline stage service unavailable; falling back to in-process",
                extra={"stage": stage, "service_url": url, "error": str(exc)},
            )
            return None
        except ValueError as exc:
            logger.warning(
                "pipeline stage service returned invalid JSON",
                extra={"stage": stage, "service_url": url, "error": str(exc)},
            )
            raise PipelineStageServiceError(stage, "invalid_response", service_url=url) from exc

    def run_chunking(self, request: ChunkingStageRequest) -> list[Chunk] | None:
        """chunking ステージを remote 実行する。未到達なら None。"""
        payload = self._post_run("chunking", request.model_dump_json())
        if payload is None:
            return None
        try:
            parsed = ChunkingStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("chunking", "invalid_response") from exc
        return [
            Chunk(
                text=item.text,
                index=item.index,
                start_offset=item.start_offset,
                end_offset=item.end_offset,
                metadata=dict(item.metadata),
            )
            for item in parsed.chunks
        ]

    def run_vector_index(self, request: VectorIndexStageRequest) -> VectorIndexStageResponse | None:
        """vector_index ステージを remote 実行する。委譲不可なら None、失敗時は例外。"""
        payload = self._post_run("vector_index", request.model_dump_json())
        if payload is None:
            return None
        try:
            return VectorIndexStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("vector_index", "invalid_response") from exc

    def run_graph(self, request: GraphStageRequest) -> GraphStageResponse | None:
        """graphrag ステージを remote 実行する。委譲不可なら None、失敗時は例外。"""
        payload = self._post_run("graphrag", request.model_dump_json())
        if payload is None:
            return None
        try:
            return GraphStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("graphrag", "invalid_response") from exc

    def run_generation(self, request: GenerationStageRequest) -> GenerationStageResponse | None:
        """generation ステージ(静的 prompt 解決)を remote 実行する。

        委譲不可なら None、失敗時は例外。
        """
        payload = self._post_run("generation", request.model_dump_json())
        if payload is None:
            return None
        try:
            return GenerationStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("generation", "invalid_response") from exc

    def run_guardrail(self, request: GuardrailStageRequest) -> GuardrailStageResponse | None:
        """guardrail ステージ(静的 policy 解決)を remote 実行する。

        委譲不可なら None、失敗時は例外。
        """
        payload = self._post_run("guardrail", request.model_dump_json())
        if payload is None:
            return None
        try:
            return GuardrailStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("guardrail", "invalid_response") from exc

    def run_agentic(self, request: AgenticStageRequest) -> AgenticStageResponse | None:
        """agentic ステージ(静的 profile 解決)を remote 実行する。

        委譲不可なら None、失敗時は例外。
        """
        payload = self._post_run("agentic", request.model_dump_json())
        if payload is None:
            return None
        try:
            return AgenticStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("agentic", "invalid_response") from exc

    def run_grounding(self, request: GroundingStageRequest) -> GroundingStageResponse | None:
        """grounding ステージ(preset 解決)を remote 実行する。委譲不可なら None、失敗時は例外。"""
        payload = self._post_run("grounding", request.model_dump_json())
        if payload is None:
            return None
        try:
            return GroundingStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("grounding", "invalid_response") from exc

    def run_evaluation(self, request: EvaluationStageRequest) -> EvaluationStageResponse | None:
        """evaluation ステージ(suite→閾値解決)を remote 実行する。

        委譲不可なら None、失敗時は例外。
        """
        payload = self._post_run("evaluation", request.model_dump_json())
        if payload is None:
            return None
        try:
            return EvaluationStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("evaluation", "invalid_response") from exc

    def run_retrieval(self, request: RetrievalStageRequest) -> RetrievalStageResponse | None:
        """retrieval ステージ(strategy 解決)を remote 実行する。委譲不可なら None、失敗時は例外。"""
        payload = self._post_run("retrieval", request.model_dump_json())
        if payload is None:
            return None
        try:
            return RetrievalStageResponse.model_validate(payload)
        except ValueError as exc:
            raise PipelineStageServiceError("retrieval", "invalid_response") from exc


_JSON_HEADERS = {"content-type": "application/json"}


class PipelineStageServiceError(RuntimeError):
    """有効化された pipeline stage サービスを実行できないため処理を止めるエラー。"""

    safe_for_user = True

    def __init__(
        self,
        stage: str,
        reason: str,
        *,
        service_url: str | None = None,
    ) -> None:
        self.stage = stage
        self.reason = reason
        self.service_url = service_url
        self.error_code = f"{stage}_service_unavailable"
        super().__init__(_stage_error_message(stage, reason, service_url=service_url))


def _stage_error_message(stage: str, reason: str, *, service_url: str | None = None) -> str:
    label = stage.replace("_", " ")
    service_id = f"pipeline-{stage.replace('_', '-')}"
    suffix = f" 接続先: {service_url}" if service_url else ""
    if reason == "invalid_url":
        return (
            f"処理ステージ（{label}）サービスの接続先 URL が不正です。"
            f"{service_id} の設定を確認してください。"
            f"{suffix}"
        )
    if reason == "invalid_response":
        return (
            f"処理ステージ（{label}）サービスから不正な応答を受信しました。"
            f"壊れた応答は fallback せずに停止しました。サービス管理画面で {service_id} "
            "のログを確認してください。"
            f"{suffix}"
        )
    return (
        f"処理ステージ（{label}）サービスがエラーを返しました。"
        f"応答済みサービスの失敗は fallback せずに停止しました。サービス管理画面で {service_id} "
        "の状態とログを確認してください。"
        f"{suffix}"
    )
