"""文書解析・検索・根拠付き回答を組み合わせる DocRAG ライブラリ。"""

__version__ = "0.2.0"

_EXPORTS = {
    "ChunkingService": ("docrag.chunking.service", "ChunkingService"),
    "ParsingService": ("docrag.parsing.service", "ParsingService"),
    "IndexingService": ("docrag.indexing", "IndexingService"),
    "RetrievalService": ("docrag.retrieval.service", "RetrievalService"),
    "GenerationService": ("docrag.generation.service", "GenerationService"),
    "IngestionPipeline": ("docrag.workflows", "IngestionPipeline"),
    "QueryPipeline": ("docrag.workflows", "QueryPipeline"),
    "create_oracle_application": ("docrag.composition", "create_oracle_application"),
    "AnswerRequest": ("docrag.generation.application", "AnswerRequest"),
}
__all__ = ["__version__", *_EXPORTS]


def __getattr__(name: str):
    """公開サービスを必要時にだけ読み込み、import による外部初期化を避ける。"""
    if name not in _EXPORTS:
        raise AttributeError(name)
    from importlib import import_module
    module, symbol = _EXPORTS[name]
    value = getattr(import_module(module), symbol)
    globals()[name] = value
    return value
