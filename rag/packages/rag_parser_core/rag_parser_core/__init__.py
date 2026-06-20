"""rag_parser_core — backend と parser マイクロサービスが共有する parser 契約。

- schema: `extraction`(StructuredExtraction 系)/ `source`(SourceProfile 系)
- `routing`: source kind 別 adapter 優先順
- `registry`: ローカル parser + 外部 adapter remap(`parse_with_registry`)
- `result`: HTTP 契約(`ParseResponse` / `ParseHealth`)

外部 parser 依存(docling/marker/...)は registry が遅延 import する任意依存であり、
本 package 自体の依存は pydantic + charset-normalizer のみに保つ。
"""

from rag_parser_core.preprocess import (
    DEFAULT_PREPROCESS_PROFILE,
    PREPROCESS_PROFILES,
    ConvertHealth,
    ConvertOutcome,
    ConvertResponse,
    SourceDerivation,
    normalize_preprocess_profile,
    supported_profiles_from,
)
from rag_parser_core.registry import (
    ParserRegistryResult,
    parse_with_registry,
)
from rag_parser_core.result import ParseHealth, ParseResponse

__all__ = [
    "DEFAULT_PREPROCESS_PROFILE",
    "PREPROCESS_PROFILES",
    "ConvertHealth",
    "ConvertOutcome",
    "ConvertResponse",
    "ParseHealth",
    "ParseResponse",
    "ParserRegistryResult",
    "SourceDerivation",
    "normalize_preprocess_profile",
    "parse_with_registry",
    "supported_profiles_from",
]
