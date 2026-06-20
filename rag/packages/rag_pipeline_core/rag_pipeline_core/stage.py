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
