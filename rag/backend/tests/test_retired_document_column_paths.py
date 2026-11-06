"""文書の行の旧列（extraction・processing_config）を読み書きする入口を持たない（#4185）。

抽出と処理設定の正本はレシピの層にあり、文書の行の列で代用する入口を残さない。
"""

import pytest

from app.clients.oracle import OracleClient


@pytest.mark.parametrize(
    "name",
    [
        "list_document_extractions",
        "_list_document_extractions_with_oracle",
        "get_document_processing_config",
        "update_document_processing_config",
        "save_extraction",
        "_save_extraction_with_oracle",
    ],
)
def test_oracle_client_has_no_document_column_entry(name: str) -> None:
    assert not hasattr(OracleClient, name)
