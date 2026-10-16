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

    cases = {
        "二十八": 28,
        "百四": 104,
        "三千二百万": 32_000_000,
        "一〇四": 104,
        "元": 1,
        "２８": 28,
    }
    assert {text: parse_number(text) for text in cases} == cases
    assert parse_number("拾") is None
    assert [format_kanji_number(n) for n in (28, 104, 1200, 35000, 0)] == [
        "二十八",
        "百四",
        "千二百",
        "三万五千",
        "〇",
    ]
    multipliers = ["十=10", "拾=10", "百=100"]
    custom = resolve(builtin_pack(), {"numbers": {"kanji_multipliers": multipliers}})
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


def test_parse_service_binds_language_pack_from_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """解析のサービスは、要求に付いた言語資源の差をその要求の間だけ使う(#2181 段 1b-3)。"""
    from fastapi.testclient import TestClient

    import rag_parser_core.service as service
    from rag_parser_core.result import ParserRegistryResult

    seen: list[str] = []

    def fake_run(*args: object, **kwargs: object) -> ParserRegistryResult:
        seen.append(current_pack().hash)
        return ParserRegistryResult(extraction=None, parser_backend="xml_tree_json")

    monkeypatch.setattr(service, "run_external_adapter", fake_run)
    app = service.create_parse_app(backend="xml_tree_json", import_name="rag_parser_core")
    client = TestClient(app)
    overrides = json.dumps({"numbers": {"article_units": ["条", "款"]}}, ensure_ascii=False)
    files = {"file": ("a.json", b"{}", "application/json")}
    assert client.post("/parse", files=files, data={"language_pack": overrides}).status_code == 200
    assert client.post("/parse", files=files).status_code == 200
    assert seen[0] != builtin_pack().hash
    assert seen[1] == builtin_pack().hash
    bad = json.dumps({"numbers": {"no_such": ["x"]}})
    assert client.post("/parse", files=files, data={"language_pack": bad}).status_code == 422


def test_xml_tree_labels_follow_the_pack() -> None:
    """XML ツリー JSON のラベル（範囲・版の参照・検索の文の見出し）は言語資源から作る(#2181 段 2)。"""
    from rag_parser_core.xml_tree_json import (
        numbered_label,
        revision_ref_labels,
        xml_tree_search_text,
    )

    assert numbered_label("1:3", "条", arabic=True) == "第1条から第3条まで"
    assert revision_ref_labels("平成一六年六月一一日法律第一〇四号") == (
        "平成十六年法律第百四号",
        "平成16年法律第104号",
    )
    metadata = {"xml_tree_document_title": "テスト法", "xml_tree_parent_caption": "（目的）"}
    assert xml_tree_search_text(metadata, "本文の文").splitlines()[0] == "文書: テスト法"
    # 1 つの語の項目は空にできない。
    with pytest.raises(LanguagePackError):
        resolve(builtin_pack(), {"labels": {"range_end": ""}})
    custom = resolve(
        builtin_pack(),
        {
            "labels": {"range_start": "〜", "range_end": "迄"},
            "search_labels": {"document": "法令名", "caption": "条の見出し"},
        },
    )
    with bind_pack(custom):
        assert numbered_label("1:3", "条", arabic=True) == "第1条〜第3条迄"
        lines = xml_tree_search_text(metadata, "本文の文").splitlines()
        assert lines[0] == "法令名: テスト法"
        assert "条の見出し: （目的）" in lines
