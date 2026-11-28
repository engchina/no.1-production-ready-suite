"""適用が未確認の説明の本文の組み立て（#5991）。

モデルが ``applies="unverified"`` を付けた説明は、言い換えではなく原文の引用として「参考情報」の節に出す。
削除済みの属性を読んで組み立てが止まらないことを確かめる。
"""

from __future__ import annotations

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedCitation, GroundedItem

QUOTE = "設定 A の値を入力します。"
SPAN = {"text": QUOTE, "evidence_id": "E1", "source": "合成資料.pdf", "page": 1, "function": ["設定画面"]}


def _unverified_entry() -> grounded.CheckedItem:
    item = GroundedItem(
        kind="operation",
        text="合成の説明",
        citations=[GroundedCitation(evidence_id="E1", quote=QUOTE)],
        applies="unverified",
    )
    return grounded.CheckedItem(item, span=dict(SPAN))


def test_unverified_published_item_is_shown_as_quote():
    assert grounded._shown_as_quote(_unverified_entry())


def test_render_answer_quotes_the_unverified_item_instead_of_the_paraphrase():
    rendered = grounded.render_answer("要約", [_unverified_entry()])

    assert QUOTE in rendered.text
    assert "合成の説明" not in rendered.text
