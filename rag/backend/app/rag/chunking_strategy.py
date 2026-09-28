"""Chunking アダプター(chunks 段階の分割戦略)の runtime レジストリ。

`parser_adapter_readiness.py` と同型で、選択された戦略と利用可能な戦略一覧を非機密の
runtime snapshot として返す。実際の分割実装は `chunking.py` の
`chunk_extraction_with_strategy` に委譲する。外部ベクトル DB / 別 LLM provider は導入せず、
全戦略を決定論的に本プロジェクトの `StructuredExtraction` へ再マップする。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import (
    DOCRAG_CHILD_TARGET_CHARS_DEFAULT,
    DOCRAG_PARENT_MAX_CHILDREN_DEFAULT,
    DOCRAG_PARENT_MAX_PAGES_DEFAULT,
    DOCRAG_PARENT_TARGET_CHARS_DEFAULT,
    DOCRAG_TABLE_CHILD_TARGET_CHARS_DEFAULT,
    LEGACY_CHUNKING_STRATEGY_ALIASES,
    ChunkingStrategy,
    Settings,
)
from app.rag.chunking import CHUNKING_STRATEGIES

ChunkingStrategyName = ChunkingStrategy
DEFAULT_CHUNKING_STRATEGY: ChunkingStrategyName = "structure_aware"
DOCRAG_CHUNKING_STRATEGY_NAME: ChunkingStrategyName = "docrag_small_to_big"
# 画面の並び順。DocRAG 親子階層は、削除した「親子階層」があった位置(3 番目)に置く(#271)。
CHUNKING_STRATEGY_ORDER: tuple[ChunkingStrategyName, ...] = (
    "structure_aware",
    "recursive_character",
    "docrag_small_to_big",
    "markdown_heading",
    "page_level",
    "fixed_size",
    "fixed_delimiter",
)


@dataclass(frozen=True)
class ChunkingStrategySpec:
    """1 戦略の由来と適用場面(機械可読の非機密 metadata)。"""

    name: ChunkingStrategyName
    origin: str
    recommended_for: tuple[str, ...]


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
    # rag_poc(DocRAG)の Small-to-Big 親子分割。docling(DocRAG)の解析結果が必要。
    "docrag_small_to_big": ChunkingStrategySpec(
        name="docrag_small_to_big",
        origin="docrag_small_to_big",
        recommended_for=("pdf", "manual", "table", "screenshot"),
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
    "fixed_delimiter": ChunkingStrategySpec(
        name="fixed_delimiter",
        origin="fixed_delimiter_split",
        recommended_for=("text", "custom_separator"),
    ),
}


@dataclass(frozen=True)
class DocragChunkingParams:
    """DocRAG 親子階層の分割パラメータ(docrag.chunking.constants.ChunkingConfig の 5 項目)。"""

    child_target_chars: int = DOCRAG_CHILD_TARGET_CHARS_DEFAULT
    table_child_target_chars: int = DOCRAG_TABLE_CHILD_TARGET_CHARS_DEFAULT
    parent_target_chars: int = DOCRAG_PARENT_TARGET_CHARS_DEFAULT
    parent_max_pages: int = DOCRAG_PARENT_MAX_PAGES_DEFAULT
    parent_max_children: int = DOCRAG_PARENT_MAX_CHILDREN_DEFAULT


@dataclass(frozen=True)
class ChunkingStrategyParams:
    """chunking 戦略へ渡す多様化パラメータ。"""

    strategy: ChunkingStrategyName
    chunk_size: int
    overlap: int
    min_chars: int
    delimiter: str
    docrag: DocragChunkingParams = DocragChunkingParams()


@dataclass(frozen=True)
class ChunkingStrategyStatus:
    """1 戦略の選択状態と適用場面。"""

    name: ChunkingStrategyName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool


@dataclass(frozen=True)
class ChunkingRuntimeSettings:
    """chunking 戦略の非機密 runtime snapshot。"""

    strategy: ChunkingStrategyName
    chunk_size: int
    overlap: int
    min_chars: int
    delimiter: str
    docrag: DocragChunkingParams
    strategies: tuple[ChunkingStrategyStatus, ...]


def normalize_chunking_strategy(value: object) -> ChunkingStrategyName:
    """未知の戦略名は既定 structure_aware へ寄せる。削除した戦略は後継へ読み替える。"""
    normalized = str(value).strip().casefold()
    normalized = LEGACY_CHUNKING_STRATEGY_ALIASES.get(normalized, normalized)
    if normalized in CHUNKING_STRATEGIES or normalized == DOCRAG_CHUNKING_STRATEGY_NAME:
        return normalized  # type: ignore[return-value]
    return DEFAULT_CHUNKING_STRATEGY


def resolve_docrag_chunking_params(settings: Settings) -> DocragChunkingParams:
    """Settings から DocRAG 親子階層の分割パラメータを解決する。"""
    return DocragChunkingParams(
        child_target_chars=int(
            getattr(settings, "rag_docrag_child_target_chars", DOCRAG_CHILD_TARGET_CHARS_DEFAULT)
        ),
        table_child_target_chars=int(
            getattr(
                settings,
                "rag_docrag_table_child_target_chars",
                DOCRAG_TABLE_CHILD_TARGET_CHARS_DEFAULT,
            )
        ),
        parent_target_chars=int(
            getattr(settings, "rag_docrag_parent_target_chars", DOCRAG_PARENT_TARGET_CHARS_DEFAULT)
        ),
        parent_max_pages=int(
            getattr(settings, "rag_docrag_parent_max_pages", DOCRAG_PARENT_MAX_PAGES_DEFAULT)
        ),
        parent_max_children=int(
            getattr(settings, "rag_docrag_parent_max_children", DOCRAG_PARENT_MAX_CHILDREN_DEFAULT)
        ),
    )


def resolve_chunking_params(settings: Settings) -> ChunkingStrategyParams:
    """Settings から chunking 戦略パラメータを解決する。"""
    return ChunkingStrategyParams(
        strategy=normalize_chunking_strategy(
            getattr(settings, "rag_chunking_strategy", DEFAULT_CHUNKING_STRATEGY)
        ),
        chunk_size=int(getattr(settings, "rag_chunk_size", 800)),
        overlap=int(getattr(settings, "rag_chunk_overlap", 120)),
        min_chars=int(getattr(settings, "rag_chunk_min_chars", 120)),
        delimiter=str(getattr(settings, "rag_chunk_delimiter", "\\n\\n")).strip(),
        docrag=resolve_docrag_chunking_params(settings),
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
        )
        for spec in (CHUNKING_STRATEGY_SPECS[name] for name in CHUNKING_STRATEGY_ORDER)
    )
    return ChunkingRuntimeSettings(
        strategy=params.strategy,
        chunk_size=params.chunk_size,
        overlap=params.overlap,
        min_chars=params.min_chars,
        delimiter=params.delimiter,
        docrag=params.docrag,
        strategies=statuses,
    )
