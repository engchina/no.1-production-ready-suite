"""Chunking アダプター(chunks 段階の分割戦略)の runtime レジストリ。

`parser_adapter_readiness.py` と同型で、選択された戦略と利用可能な戦略一覧を非機密の
runtime snapshot として返す。実際の分割実装は `chunking.py` の
`chunk_extraction_with_strategy` に委譲する。外部ベクトル DB / 別 LLM provider は導入せず、
全戦略を決定論的に本プロジェクトの `StructuredExtraction` へ再マップする。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import ChunkingStrategy, Settings
from app.rag.chunking import CHUNKING_STRATEGIES

ChunkingStrategyName = ChunkingStrategy
DEFAULT_CHUNKING_STRATEGY: ChunkingStrategyName = "structure_aware"
CHUNKING_STRATEGY_ORDER: tuple[ChunkingStrategyName, ...] = (
    "structure_aware",
    "recursive_character",
    "sentence_window",
    "hierarchical_parent_child",
    "markdown_heading",
    "page_level",
    "fixed_size",
)


@dataclass(frozen=True)
class ChunkingStrategySpec:
    """1 戦略の由来と適用場面(機械可読の非機密 metadata)。"""

    name: ChunkingStrategyName
    origin: str
    recommended_for: tuple[str, ...]
    uses_child_size: bool = False
    uses_sentence_window: bool = False


CHUNKING_STRATEGY_SPECS: dict[ChunkingStrategyName, ChunkingStrategySpec] = {
    "structure_aware": ChunkingStrategySpec(
        name="structure_aware",
        origin="ragflow_docling_marker",
        recommended_for=("pdf", "office", "html", "table"),
    ),
    "recursive_character": ChunkingStrategySpec(
        name="recursive_character",
        origin="langchain_recursive_character",
        recommended_for=("text", "markdown"),
    ),
    "sentence_window": ChunkingStrategySpec(
        name="sentence_window",
        origin="llamaindex_sentence_window",
        recommended_for=("faq", "qa"),
        uses_sentence_window=True,
    ),
    "hierarchical_parent_child": ChunkingStrategySpec(
        name="hierarchical_parent_child",
        origin="llamaindex_auto_merging",
        recommended_for=("long_document", "report"),
        uses_child_size=True,
    ),
    "markdown_heading": ChunkingStrategySpec(
        name="markdown_heading",
        origin="markdown_header_splitter",
        recommended_for=("markdown", "policy"),
    ),
    "page_level": ChunkingStrategySpec(
        name="page_level",
        origin="pageindex_coarse",
        recommended_for=("pdf", "scan"),
    ),
    "fixed_size": ChunkingStrategySpec(
        name="fixed_size",
        origin="ragflow_general_fixed",
        recommended_for=("text", "generic"),
    ),
}


@dataclass(frozen=True)
class ChunkingStrategyParams:
    """chunking 戦略へ渡す多様化パラメータ。"""

    strategy: ChunkingStrategyName
    chunk_size: int
    overlap: int
    child_size: int
    sentence_window_size: int
    min_chars: int


@dataclass(frozen=True)
class ChunkingStrategyStatus:
    """1 戦略の選択状態と適用場面。"""

    name: ChunkingStrategyName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    uses_child_size: bool
    uses_sentence_window: bool


@dataclass(frozen=True)
class ChunkingRuntimeSettings:
    """chunking 戦略の非機密 runtime snapshot。"""

    strategy: ChunkingStrategyName
    chunk_size: int
    overlap: int
    child_size: int
    sentence_window_size: int
    min_chars: int
    strategies: tuple[ChunkingStrategyStatus, ...]


def normalize_chunking_strategy(value: object) -> ChunkingStrategyName:
    """未知の戦略名は既定 structure_aware へ寄せる。"""
    normalized = str(value).casefold()
    if normalized in CHUNKING_STRATEGIES:
        return normalized  # type: ignore[return-value]
    return DEFAULT_CHUNKING_STRATEGY


def resolve_chunking_params(settings: Settings) -> ChunkingStrategyParams:
    """Settings から chunking 戦略パラメータを解決する。"""
    return ChunkingStrategyParams(
        strategy=normalize_chunking_strategy(
            getattr(settings, "rag_chunking_strategy", DEFAULT_CHUNKING_STRATEGY)
        ),
        chunk_size=int(getattr(settings, "rag_chunk_size", 800)),
        overlap=int(getattr(settings, "rag_chunk_overlap", 120)),
        child_size=int(getattr(settings, "rag_chunk_child_size", 320)),
        sentence_window_size=int(getattr(settings, "rag_chunk_sentence_window_size", 3)),
        min_chars=int(getattr(settings, "rag_chunk_min_chars", 0)),
    )


def chunking_runtime_settings(settings: Settings) -> ChunkingRuntimeSettings:
    """Settings から chunking 戦略 readiness snapshot を作る。"""
    params = resolve_chunking_params(settings)
    statuses = tuple(
        ChunkingStrategyStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.strategy,
            uses_child_size=spec.uses_child_size,
            uses_sentence_window=spec.uses_sentence_window,
        )
        for spec in (CHUNKING_STRATEGY_SPECS[name] for name in CHUNKING_STRATEGY_ORDER)
    )
    return ChunkingRuntimeSettings(
        strategy=params.strategy,
        chunk_size=params.chunk_size,
        overlap=params.overlap,
        child_size=params.child_size,
        sentence_window_size=params.sentence_window_size,
        min_chars=params.min_chars,
        strategies=statuses,
    )
