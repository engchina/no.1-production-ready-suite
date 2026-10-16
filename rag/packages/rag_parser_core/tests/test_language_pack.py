"""言語資源(#2181): 組み込みのパック・重ね合わせ・形の検証・束ね。"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from rag_parser_core.language import (
    FIELD_SPECS,
    LanguagePackError,
    bind_pack,
    builtin_pack,
    current_pack,
    overrides_from,
    resolve,
    set_default_pack,
)


def test_builtin_pack_has_every_field_and_nothing_else() -> None:
    raw = json.loads(
        resources.files("rag_parser_core.language").joinpath("packs", "ja.json").read_text("utf-8")
    )
    keys = {
        (section, key)
        for section, fields in raw.items()
        if isinstance(fields, dict)
        for key in fields
    }
    assert keys == {(spec.section, spec.key) for spec in FIELD_SPECS}
    assert builtin_pack().value("symbols", "normalization") == "NFKC"


def test_resolve_replaces_fields_and_keeps_others() -> None:
    base = builtin_pack()
    pack = resolve(base, {"numbers": {"article_units": ["条", " 款 ", "款", ""]}})
    assert pack.words("numbers", "article_units") == ("条", "款")
    assert pack.words("numbers", "chapter_units") == base.words("numbers", "chapter_units")
    assert pack.hash != base.hash
    assert overrides_from(pack, base) == {"numbers": {"article_units": ["条", "款"]}}
    assert resolve(base, {}).hash == base.hash


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("symbols", "bracket_pairs", ["("]),
        ("numbers", "kanji_digits", ["一=いち"]),
        ("english", "en_section_words", ["part=volume"]),
        ("symbols", "normalization", "NFKD"),
        ("symbols", "name_chars", ["あいう"]),
        ("references", "separate_part_patterns", ["改正"]),
        ("references", "appendix_words", ["別添"]),
        ("numbers", "no_such_field", ["x"]),
    ],
    ids=[
        "pair",
        "char-value",
        "enum-map",
        "choice",
        "char-range",
        "number-pattern",
        "word-map",
        "unknown",
    ],
)
def test_resolve_rejects_malformed_values(section: str, key: str, value: object) -> None:
    with pytest.raises(LanguagePackError) as caught:
        resolve(builtin_pack(), {section: {key: value}})
    assert f"{section}.{key}" in caught.value.errors


def test_current_pack_prefers_bound_then_default_then_builtin() -> None:
    custom = resolve(builtin_pack(), {"numbers": {"article_units": ["款"]}})
    other = resolve(builtin_pack(), {"numbers": {"article_units": ["目"]}})
    assert current_pack() is builtin_pack()
    set_default_pack(custom)
    try:
        assert current_pack() is custom
        with bind_pack(other):
            assert current_pack() is other
        assert current_pack() is custom
    finally:
        set_default_pack(None)
    assert current_pack() is builtin_pack()


def test_parse_and_format_numbers_follow_the_pack() -> None:
    """数の読み方と漢数字の書き方は言語資源の字と位取り(#2181 段 1b)。"""
    from rag_parser_core.language.text import format_kanji_number, parse_number

    cases = {"二十八": 28, "百四": 104, "三千二百万": 32_000_000, "一〇四": 104, "元": 1, "２８": 28}
    assert {text: parse_number(text) for text in cases} == cases
    assert parse_number("拾") is None
    assert [format_kanji_number(n) for n in (28, 104, 1200, 35000, 0)] == [
        "二十八",
        "百四",
        "千二百",
        "三万五千",
        "〇",
    ]
    custom = resolve(builtin_pack(), {"numbers": {"kanji_multipliers": ["十=10", "拾=10", "百=100"]}})
    with bind_pack(custom):
        assert parse_number("三拾") == 30
        assert format_kanji_number(30) == "三十"


def test_headings_citations_and_number_phrases_follow_the_pack() -> None:
    """番号の見出し・質問の引用・本文の数の書き方は言語資源から作る(#2181 段 1b)。"""
    from rag_parser_core.extraction import heading_level_from_prefix, numbered_heading
    from rag_parser_core.xml_tree_json import arabic_number_phrases, query_citation_keys

    assert numbered_heading().match("第2部 概要") is not None
    assert heading_level_from_prefix("第2部") == 1
    assert numbered_heading().match("第2巻 概要") is None
    assert arabic_number_phrases("六十五歳以上の者") == ["65歳以上"]
    assert query_citation_keys("附則第三条の規定") == ["附則第3条"]
    custom = resolve(
        builtin_pack(),
        {
            "numbers": {"part_units": ["編", "部", "巻"], "counters": ["歳", "ヶ月"]},
            "references": {"part_prefixes": ["附則", "補則"]},
        },
    )
    with bind_pack(custom):
        assert numbered_heading().match("第2巻 概要") is not None
        assert heading_level_from_prefix("第2巻") == 1
        assert query_citation_keys("補則第三条の規定") == ["補則第3条"]
        assert arabic_number_phrases("三ヶ月以内") == ["3ヶ月以内"]
