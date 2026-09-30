"""pipeline ステージの HTTP 契約(wire schema)。

backend とステージマイクロサービスが共有する request/response。全ステージ共通の
``StageHealth`` と、ステージごとの request/response を定義する(まずは chunking)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from rag_parser_core.extraction import StructuredExtraction

from .chunking import (
    CHUNK_OVERLAP_MAX_CHARS,
    CHUNK_SIZE_MAX_CHARS,
    CHUNK_SIZE_MIN_CHARS,
)


class StageHealth(BaseModel):
    """``GET /health`` のレスポンス(readiness 表示の値ソース)。"""

    status: str = "ok"
    stage: str = "pipeline"
    package_name: str | None = None
    package_version: str | None = None


class ChunkModel(BaseModel):
    """分割後チャンクの wire 形式(backend `app.rag.chunking.Chunk` と 1:1)。"""

    text: str
    index: int
    start_offset: int
    end_offset: int
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ChunkingStageRequest(BaseModel):
    """``POST /run``(chunking)の入力。構造化抽出 + 戦略パラメータ。"""

    extraction: StructuredExtraction
    strategy: str = "structure_aware"
    chunk_size: int = Field(
        default=800,
        ge=CHUNK_SIZE_MIN_CHARS,
        le=CHUNK_SIZE_MAX_CHARS,
    )
    overlap: int = Field(default=120, ge=0, le=CHUNK_OVERLAP_MAX_CHARS)
    min_chars: int = Field(default=120, ge=0, le=2000)
    delimiter: str = Field(default="\\n\\n", min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_chunk_bounds(self) -> ChunkingStageRequest:
        """backend と同じ相互制約を stage の入口でも 422 にする。"""
        if self.strategy == "fixed_delimiter":
            return self
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap は chunk_size より小さくしてください。")
        if (
            self.strategy
            in {
                "structure_aware",
                "recursive_character",
                "markdown_heading",
                "page_level",
            }
            and self.min_chars >= self.chunk_size
        ):
            raise ValueError("min_chars は chunk_size より小さくしてください。")
        return self


class ChunkingStageResponse(BaseModel):
    """``POST /run``(chunking)の出力。"""

    chunks: list[ChunkModel] = Field(default_factory=list)

    @classmethod
    def from_chunks(cls, chunks: list[object]) -> ChunkingStageResponse:
        """backend `Chunk` dataclass のリストを wire 形式へ変換する。"""
        items = [
            ChunkModel(
                text=chunk.text,  # type: ignore[attr-defined]
                index=chunk.index,  # type: ignore[attr-defined]
                start_offset=chunk.start_offset,  # type: ignore[attr-defined]
                end_offset=chunk.end_offset,  # type: ignore[attr-defined]
                metadata=dict(chunk.metadata),  # type: ignore[attr-defined]
            )
            for chunk in chunks
        ]
        return cls(chunks=items)


class VectorIndexStageRequest(BaseModel):
    """``POST /run``(vector_index)の入力。profile + balanced 用 target accuracy。"""

    profile: str = "balanced"
    settings_target_accuracy: int = 95


class VectorIndexStageResponse(BaseModel):
    """``POST /run``(vector_index)の出力(解決済みパラメータ)。"""

    profile: str
    target_accuracy: int
    neighbors: int
    efconstruction: int
    distance: str
    requires_reprovision: bool


class GraphStageRequest(BaseModel):
    """``POST /run``(graphrag)の入力。profile + legacy enabled。"""

    profile: str = "off"
    legacy_enabled: bool = False


class GraphStageResponse(BaseModel):
    """``POST /run``(graphrag)の出力(KG 構築フラグ)。"""

    profile: str
    build_entities: bool
    build_relationships: bool
    build_claims: bool
    build_community_summary: bool


class GuardrailStageRequest(BaseModel):
    """``POST /run``(guardrail)の入力。policy のみ。"""

    policy: str = "standard"


class GuardrailStageResponse(BaseModel):
    """``POST /run``(guardrail)の出力(groundedness 厳格度 + 監査強調)。"""

    policy: str
    grounding_min_overlap: int
    grounding_min_ratio: float
    audit_emphasis: bool


class EvaluationStageRequest(BaseModel):
    """``POST /run``(evaluation)の入力。suite(評価の基準)のみ。"""

    suite: str = "standard"


class EvaluationStageResponse(BaseModel):
    """``POST /run``(evaluation)の出力(CI gate 用閾値)。"""

    suite: str
    thresholds: dict[str, float] = Field(default_factory=dict)
