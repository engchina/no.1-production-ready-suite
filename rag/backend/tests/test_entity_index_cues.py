"""実体・別名索引の本文の抽出の形の規則（#5167）を、合成の本文で確かめる。

- 「名前（以下、略称）」の読点を別名に入れない。
- 英字の名前の前に付けるカナ・漢字の語を、語の途中から始めない。
- 大文字で始まる英字の語は、日本語の本文に混じるときだけ名前にする（英語の本文の文頭の語を実体に
  しない。英語の本文でも大文字が 2 つ以上の語は名前にする）。
"""

from __future__ import annotations

import pytest

from app.rag.chunking import Chunk
from app.rag.entity_index import EntityIndex, build_entity_index


def _chunk(text: str, index: int = 0, **metadata: str) -> Chunk:
    return Chunk(
        text=text,
        index=index,
        start_offset=0,
        end_offset=len(text),
        metadata={"content_kind": "text", **metadata},
    )


def _index(*texts: str) -> EntityIndex:
    return build_entity_index(
        document_id="doc-1",
        chunks=[_chunk(text, index) for index, text in enumerate(texts)],
        chunk_set_id="cs-1",
    )


def _names(index: EntityIndex) -> set[str]:
    return {entity.display_name for entity in index.entities}


def _aliases(index: EntityIndex) -> dict[str, set[str]]:
    return {
        entity.display_name: {alias.alias_text for alias in entity.aliases}
        for entity in index.entities
    }


@pytest.mark.parametrize(
    "text",
    [
        "この手引きは、温度ロガー TL-300（以下、本装置）の使い方を説明します。",
        "この手引きは、温度ロガー TL-300（以下、「本装置」）の使い方を説明します。",
        "この手引きは、温度ロガー TL-300（以下 、本装置）の使い方を説明します。",
    ],
    ids=["clause-mark", "clause-mark-quoted", "space-before-mark"],
)
def test_paren_alias_skips_the_clause_mark_after_the_alias_word(text: str) -> None:
    index = _index(text)

    aliases = _aliases(index)
    assert "本装置" in aliases["温度ロガー TL-300"]
    every_alias = {alias.alias_key for entity in index.entities for alias in entity.aliases}
    assert not [key for key in every_alias if key.startswith("、")]


def test_paren_alias_keeps_the_existing_forms() -> None:
    index = _index(
        "記録装置 Alpha（略称: ALP）を使います。",
        "集計室（以下「集計担当」）が確認します。",
        "情報基盤室（略称: 基盤）は、通称「基盤チーム」と呼びます。",
    )

    aliases = _aliases(index)
    assert "ALP" in aliases["記録装置 Alpha"]
    assert "集計担当" in aliases["集計室"]
    assert {"基盤", "基盤チーム"} <= aliases["情報基盤室"]


def test_latin_name_prefix_does_not_start_inside_a_long_word() -> None:
    # 「データ変換ユーティリティ」は 12 文字（前に付ける語の上限 8 文字より長い）。
    index = _index("本書は、データ変換ユーティリティ Kawa Convert（略称: KWC）の説明です。")

    names = _names(index)
    assert "データ変換ユーティリティ Kawa Convert" in names
    assert "Kawa Convert" in names
    # 語の途中から始めた名前（先頭の文字が欠けた名前）を作らない。
    assert {name for name in names if name.endswith("Kawa Convert")} == {
        "データ変換ユーティリティ Kawa Convert",
        "Kawa Convert",
    }


def test_latin_name_keeps_a_short_kana_kanji_prefix() -> None:
    names = _names(_index("申請は経費 Portal から行い、結果は集計 Board に出ます。"))

    assert {"経費 Portal", "集計 Board"} <= names


def test_capitalized_words_in_english_prose_are_not_names() -> None:
    index = _index(
        "This guide describes the REST API. Every request returns JSON.\n"
        "If MaxRetryCount is zero, the client stops. Only one Retry Policy applies."
    )

    names = _names(index)
    assert not {"This", "Every", "If", "Only", "If MaxRetryCount", "Retry Policy"} & names
    assert {"REST API", "JSON", "MaxRetryCount"} <= names


def test_latin_names_inside_japanese_prose_are_kept() -> None:
    index = _index(
        "毎月の集計は HRM で行い、結果を Document Portal に載せます。\n"
        "設定値は 3（MaxRetryCount）です。"
    )

    names = _names(index)
    assert {"Document Portal", "MaxRetryCount"} <= names
    assert any(name.endswith("HRM") for name in names)


def test_a_latin_heading_line_follows_the_surrounding_lines() -> None:
    japanese = _names(_index("Document Portal\n社内の文書を探す画面です。"))
    english = _names(_index("Document Portal\nThe portal lists internal documents."))

    assert "Document Portal" in japanese
    assert "Document Portal" not in english


def test_record_rows_and_id_terms_are_unchanged() -> None:
    index = build_entity_index(
        document_id="doc-1",
        chunks=[
            _chunk(
                "システムID: SYS-901 / 正式名: 記録管理 Hub / 略称: RMH / 担当部署: 情報基盤室",
                0,
                content_kind="record",
            ),
            _chunk("Every report refers to SYS-901.", 1),
        ],
        chunk_set_id="cs-1",
    )

    aliases = _aliases(index)
    assert {"SYS-901", "記録管理 Hub", "RMH"} <= aliases["記録管理 Hub"]
    assert "情報基盤室" in aliases
    assert "Every" not in aliases
