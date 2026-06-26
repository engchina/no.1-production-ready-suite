"""parser マイクロサービスと backend 間の HTTP 契約スキーマと共有 contract 型。

サービスは `ParserRegistryResult`(dataclass)を `ParseResponse`(Pydantic)へ変換して
JSON で返し、backend はそれを `ParserRegistryResult` へ戻して既存パイプラインへ渡す。
これにより外部 adapter の出力忠実度(StructuredExtraction)をネットワーク越しに維持する。

`ParserRegistryResult` / 各 backend 集合 / `ExternalAdapterRunner` 型は本モジュール(軽量)に
置く。重い `registry`(local 解析実装)を読み込まずに backend が型・定数を参照できるようにし、
backend では解析実体を import/実行しない方針を成立させる。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from rag_parser_core.extraction import StructuredExtraction

if TYPE_CHECKING:
    from rag_parser_core.source import SourceProfile

# ローカル(in-process)parser の版。実行は backend から撤去済みだが、結果タグの既定値・
# 後方互換のため契約値として残す。
LOCAL_PARSER_VERSION = "local_partition_v1"

# 外部 Python package を持つ adapter とその配布 package 名(registry の package 検出に使う)。
EXTERNAL_ADAPTER_PACKAGES = {
    "docling": "docling",
    "marker": "marker",
    "unstructured": "unstructured",
    # PoweRAG 由来。未導入時は package_missing、導入のみで未実装なら adapter_unsupported を
    # 返して安全に fallback する(実 OCR は OCI Enterprise AI VLM へ再マップ)。
    "mineru": "mineru",
    "dots_ocr": "dots_ocr",
    # GLM-OCR(HuggingFace zai-org/GLM-OCR)。専用 pip package は無く、GPU サービス image
    # では transformers で HF からモデルをロードして実 OCR する(_run_glm_ocr のフォールバック)。
    "glm_ocr": "glm_ocr",
    # Unlimited-OCR(HuggingFace baidu/Unlimited-OCR)。専用 pip package は無く、GPU サービス
    # image で transformers からモデルをロードして実 OCR する。
    "unlimited_ocr": "unlimited_ocr",
}

# service 系 backend。外部 package / parser microservice ではなく、backend が OCI
# クラウドサービス(Enterprise AI VLM / Document Understanding)を直接呼ぶ。
# oci_genai_vision = OCI Generative AI(Vision)。enterprise_ai_vlm は後方互換エイリアス。
SERVICE_ADAPTER_BACKENDS = frozenset(
    {"oci_genai_vision", "enterprise_ai_vlm", "oci_document_understanding"}
)

# 外部 adapter 実行の注入点。backend は HTTP runner を、service/test は in-process を渡す。
ExternalAdapterRunner = Callable[
    [str, bytes, "SourceProfile | None", str], "ParserRegistryResult"
]


@dataclass(frozen=True)
class ParserRegistryResult:
    """parser registry の結果。None extraction は Enterprise AI fallback を意味する。"""

    extraction: StructuredExtraction | None
    parser_backend: str
    parser_version: str = LOCAL_PARSER_VERSION
    fallback_used: bool = False
    template: str = "enterprise_ai_fallback"
    warnings: tuple[str, ...] = ()
    unsupported_reason: str | None = None


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
