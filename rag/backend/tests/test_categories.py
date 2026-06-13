"""カテゴリ API のテスト。"""

from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


def test_list_categories_returns_japanese_document_categories() -> None:
    """参照実装の主要カテゴリを ApiResponse 形式で返す。"""
    response = client.get("/api/categories")

    assert response.status_code == 200
    body = response.json()
    assert body["error_messages"] == []
    categories = body["data"]
    assert [category["id"] for category in categories] == [
        "invoice",
        "receipt",
        "delivery_note",
        "purchase_order",
        "other",
    ]
    assert categories[0]["name"] == "請求書"
    assert all(category["enabled"] is True for category in categories)
