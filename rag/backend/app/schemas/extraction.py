"""VLM/LLM 構造化抽出スキーマ。"""

from pydantic import BaseModel, Field

ScalarValue = str | int | float | bool | None


class StructuredExtraction(BaseModel):
    """OCI Enterprise AI の VLM 出力を検証して保存するための正規化形。"""

    raw_text: str = ""
    document_type: str = "伝票"
    fields: dict[str, ScalarValue] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)

    def to_document_fields(self) -> dict[str, object]:
        """DocumentDetail.extracted_fields に格納する JSON 互換 dict を返す。"""
        return {
            "raw_text": self.raw_text,
            "document_type": self.document_type,
            "fields": self.fields,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }
