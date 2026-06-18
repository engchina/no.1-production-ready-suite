"""parser マイクロサービスと backend 間の HTTP 契約スキーマ。

サービスは `ParserRegistryResult`(dataclass)を `ParseResponse`(Pydantic)へ変換して
JSON で返し、backend はそれを `ParserRegistryResult` へ戻して既存パイプラインへ渡す。
これにより外部 adapter の出力忠実度(StructuredExtraction)をネットワーク越しに維持する。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from rag_parser_core.extraction import StructuredExtraction
from rag_parser_core.registry import ParserRegistryResult


class ParseResponse(BaseModel):
    """`POST /parse` のレスポンス本体(ParserRegistryResult の wire 形式)。"""

    extraction: StructuredExtraction | None = None
    parser_backend: str
    parser_version: str
    fallback_used: bool = False
    template: str = "enterprise_ai_fallback"
    warnings: list[str] = Field(default_factory=list)
    unsupported_reason: str | None = None

    @classmethod
    def from_result(cls, result: ParserRegistryResult) -> ParseResponse:
        return cls(
            extraction=result.extraction,
            parser_backend=result.parser_backend,
            parser_version=result.parser_version,
            fallback_used=result.fallback_used,
            template=result.template,
            warnings=list(result.warnings),
            unsupported_reason=result.unsupported_reason,
        )

    def to_result(self) -> ParserRegistryResult:
        return ParserRegistryResult(
            extraction=self.extraction,
            parser_backend=self.parser_backend,
            parser_version=self.parser_version,
            fallback_used=self.fallback_used,
            template=self.template,
            warnings=tuple(self.warnings),
            unsupported_reason=self.unsupported_reason,
        )


class ParseHealth(BaseModel):
    """`GET /health` のレスポンス。readiness 表示の値ソース。"""

    status: str = "ok"
    backend: str
    package_name: str | None = None
    package_version: str | None = None
