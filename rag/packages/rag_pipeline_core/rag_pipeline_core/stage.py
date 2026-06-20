"""pipeline ステージの HTTP 契約(wire schema)。

backend とステージマイクロサービスが共有する request/response。全ステージ共通の
``StageHealth`` と、ステージごとの request/response を定義する(まずは chunking)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from rag_parser_core.extraction import StructuredExtraction


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
    chunk_size: int = 800
    overlap: int = 120
    child_size: int = 320
    sentence_window_size: int = 3
    min_chars: int = 0


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
    temporal: bool


class GenerationStageRequest(BaseModel):
    """``POST /run``(generation)の入力。profile のみ(custom/override は backend)。"""

    profile: str = "grounded_concise"


class GenerationStageResponse(BaseModel):
    """``POST /run``(generation)の出力(静的 system prompt + 構造化出力フラグ)。"""

    profile: str
    system_prompt: str | None = None
    structured_output: bool = False


class GuardrailStageRequest(BaseModel):
    """``POST /run``(guardrail)の入力。policy のみ。"""

    policy: str = "standard"


class GuardrailStageResponse(BaseModel):
    """``POST /run``(guardrail)の出力(groundedness 厳格度 + 監査強調)。"""

    policy: str
    grounding_min_overlap: int
    grounding_min_ratio: float
    audit_emphasis: bool


class AgenticStageRequest(BaseModel):
    """``POST /run``(agentic)の入力。profile のみ(max_subqueries は backend)。"""

    profile: str = "off"


class AgenticStageResponse(BaseModel):
    """``POST /run``(agentic)の出力(クエリ計画の挙動フラグ)。"""

    profile: str
    enabled: bool
    rewrite: bool
    decompose: bool
    multi_hop: bool
    smart_routing: bool
