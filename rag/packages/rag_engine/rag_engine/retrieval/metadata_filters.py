"""明示的な検索範囲。自然言語の推定を行わない。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalMetadataFilter:
    """呼び出し元が明示した文書・ページの検索条件。"""

    source_file_terms: tuple[str, ...] = ()
    metadata_terms: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()

    @property
    def active(self) -> bool:
        """検索や prompt へ反映する有効条件かどうかを返します。"""
        return bool(self.source_file_terms or self.metadata_terms or self.page_numbers)

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "source_file_terms": list(self.source_file_terms),
            "metadata_terms": list(self.metadata_terms),
            "page_numbers": list(self.page_numbers),
            "active": self.active,
        }


