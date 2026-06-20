"""pipeline ステージマイクロサービスへの HTTP 委譲クライアント。

各 pipeline ステージ(まずは chunking)を独立サービスとして呼ぶ。``RAG_<STAGE>_SERVICE_URL``
が設定され ``RAG_<STAGE>_SERVICE_ENABLED`` が真のとき ``POST /run`` へ委譲する。未設定・無効・
未達・timeout・不正応答時は **warning を付けて in-process(同一ロジック)へ安全縮退**する
(parser/preprocess サービスと同じ安全網。常時 remote でも 1 サービス停止で全体が止まらない)。

確定スタックは不変。ロジックは backend と共有パッケージ ``rag_pipeline_core`` で同一。
"""

from __future__ import annotations

import logging

import httpx
from rag_pipeline_core.stage import (
    ChunkingStageRequest,
    ChunkingStageResponse,
    GraphStageRequest,
    GraphStageResponse,
    VectorIndexStageRequest,
    VectorIndexStageResponse,
)

from app.config import Settings
from app.rag.chunking import Chunk

logger = logging.getLogger(__name__)

# ステージ名 → サービス URL の Settings フィールド。
_STAGE_URL_FIELDS: dict[str, str] = {
    "chunking": "rag_chunking_service_url",
    "vector_index": "rag_vector_index_service_url",
    "graphrag": "rag_graph_service_url",
}
_STAGE_ENABLED_FIELDS: dict[str, str] = {
    "chunking": "rag_chunking_service_enabled",
    "vector_index": "rag_vector_index_service_enabled",
    "graphrag": "rag_graph_service_enabled",
}


class PipelineStageClient:
    """pipeline ステージを HTTP で実行する(未達は呼び出し側の in-process 縮退に委ねる)。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._timeout = float(
            getattr(settings, "rag_pipeline_stage_timeout_seconds", 120.0)
        )

    def _service_url(self, stage: str) -> str | None:
        field = _STAGE_URL_FIELDS.get(stage)
        if field is None:
            return None
        raw = str(getattr(self._settings, field, "") or "").strip().rstrip("/")
        return raw or None

    def is_enabled(self, stage: str) -> bool:
        """ステージが remote 委譲対象か(URL 設定 + enable フラグ)。"""
        field = _STAGE_ENABLED_FIELDS.get(stage)
        enabled = bool(getattr(self._settings, field, False)) if field else False
        return enabled and self._service_url(stage) is not None

    def _post_run(self, stage: str, request_json: str) -> dict[str, object] | None:
        """``POST /run`` を呼び JSON を返す。委譲不可/失敗時は None(呼び出し側で縮退)。"""
        if not self.is_enabled(stage):
            return None
        url = self._service_url(stage)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{url}/run", content=request_json, headers=_JSON_HEADERS
                )
                response.raise_for_status()
                payload: dict[str, object] = response.json()
                return payload
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "pipeline stage service call failed; falling back to in-process",
                extra={"stage": stage, "service_url": url, "error": str(exc)},
            )
            return None

    def run_chunking(self, request: ChunkingStageRequest) -> list[Chunk] | None:
        """chunking ステージを remote 実行する。委譲不可/失敗時は None(呼び出し側で縮退)。"""
        payload = self._post_run("chunking", request.model_dump_json())
        if payload is None:
            return None
        try:
            parsed = ChunkingStageResponse.model_validate(payload)
        except ValueError:
            return None
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

    def run_vector_index(
        self, request: VectorIndexStageRequest
    ) -> VectorIndexStageResponse | None:
        """vector_index ステージを remote 実行する。委譲不可/失敗時は None。"""
        payload = self._post_run("vector_index", request.model_dump_json())
        if payload is None:
            return None
        try:
            return VectorIndexStageResponse.model_validate(payload)
        except ValueError:
            return None

    def run_graph(self, request: GraphStageRequest) -> GraphStageResponse | None:
        """graphrag ステージを remote 実行する。委譲不可/失敗時は None。"""
        payload = self._post_run("graphrag", request.model_dump_json())
        if payload is None:
            return None
        try:
            return GraphStageResponse.model_validate(payload)
        except ValueError:
            return None


_JSON_HEADERS = {"content-type": "application/json"}
