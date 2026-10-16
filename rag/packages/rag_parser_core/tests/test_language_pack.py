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
