"""カテゴリ関連スキーマ。"""

from pydantic import BaseModel


class Category(BaseModel):
    """伝票分類。"""

    id: str
    name: str
    enabled: bool = True


DEFAULT_CATEGORIES: tuple[Category, ...] = (
    Category(id="invoice", name="請求書"),
    Category(id="receipt", name="領収書"),
    Category(id="delivery_note", name="納品書"),
    Category(id="purchase_order", name="発注書"),
    Category(id="other", name="その他伝票"),
)


def default_categories() -> list[Category]:
    """既定カテゴリを呼び出し側で安全に扱えるコピーとして返す。"""
    return [category.model_copy() for category in DEFAULT_CATEGORIES]
