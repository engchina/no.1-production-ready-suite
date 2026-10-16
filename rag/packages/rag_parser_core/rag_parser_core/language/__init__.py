"""言語資源（書き方パック。#2181）。

文書と質問を読むときの言語の書き方（数・単位・記号・助詞・参照の書き方など）を、コードではなく
データで持つ。設計は ``rag/docs/language-resources.md``。

- 組み込みのパック（``packs/<locale>.json``）が既定値。今までのコードの値をそのまま持つ。
- 上書きは「節 → 項目 → 値」の形で、項目ごとに組み込みの値を置き換える（``resolve``）。
- 実行のときは ``current_pack()`` で読む。backend が全体の上書きを ``set_default_pack`` で置き、
  取込の job・回答の要求の間だけ ``bind_pack`` で KB・プロファイルの上書きを束ねる。
- 項目の種類（``FieldKind``）は語の一覧か決まった型だけ。正規表現は受け付けない。

このモジュールは仕組み（スキーマ・検証・重ね合わせ）だけを持ち、言語の書き方の値は持たない。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Any

DEFAULT_LOCALE = "ja"
# 語の一覧の上限（1 項目の語の数・1 つの語の文字数）。
MAX_ITEMS = 300
MAX_ITEM_CHARS = 40
# 「語=値」の区切り。
MAP_SEPARATOR = "="


class FieldKind(StrEnum):
    """項目の値の種類（画面の欄と検証が使う）。"""

    WORDS = "words"  # 語の一覧
    PAIRS = "pairs"  # 開きと閉じの 2 文字の組
    NUMBER_PATTERNS = "number_patterns"  # N を番号の位置にした型（N が要る）
    PATTERNS = "patterns"  # 型（N・YYYY などの記号は項目ごとの意味）
    CHAR_VALUES = "char_values"  # 「字=数」（字は 1 文字）
    WORD_MAP = "word_map"  # 「語=値」
    ENUM_MAP = "enum_map"  # 「語=決まった値」
    CHAR_RANGES = "char_ranges"  # 1 文字か「最初-最後」
    CHOICE = "choice"  # 決まった値から 1 つ


@dataclass(frozen=True)
class FieldSpec:
    """言語資源の項目の定義（値は持たない）。"""

    section: str
    key: str
    kind: FieldKind
    # ENUM_MAP の値・CHOICE の選択肢（処理が対応する値。言語の書き方ではない）。
    choices: tuple[str, ...] = ()
    # 取込のときに使う書き方か（変えたら文書の再分割が要る）。
    ingest: bool = True


def _fields(section: str, kind: FieldKind, *keys: str, **options: Any) -> tuple[FieldSpec, ...]:
    return tuple(FieldSpec(section, key, kind, **options) for key in keys)


_W, _P, _NP, _PT = FieldKind.WORDS, FieldKind.PAIRS, FieldKind.NUMBER_PATTERNS, FieldKind.PATTERNS
_CV, _WM, _EM, _CR = (
    FieldKind.CHAR_VALUES,
    FieldKind.WORD_MAP,
    FieldKind.ENUM_MAP,
    FieldKind.CHAR_RANGES,
)

# 節と項目の定義（画面の並び）。値は組み込みのパックと上書きにある。
FIELD_SPECS: tuple[FieldSpec, ...] = (
    # 番号の書き方。
    *_fields(
        "numbers",
        _W,
        "number_prefixes",
        "chapter_units",
        "section_units",
        "article_units",
        "paragraph_units",
        "item_units",
    ),
    *_fields("numbers", _PT, "item_patterns"),
    *_fields("numbers", _CV, "kanji_digits", "kanji_multipliers"),
    *_fields("numbers", _W, "branch_connectors", "branch_stop_chars", "era_names"),
    # 記号と文字。
    FieldSpec("symbols", "normalization", FieldKind.CHOICE, choices=("NFKC", "NFC", "none")),
    *_fields("symbols", _W, "title_ignored_chars", "sentence_end_chars", "clause_separator_chars"),
    *_fields("symbols", _P, "bracket_pairs", "quote_brackets", "document_title_brackets"),
    *_fields("symbols", _CR, "name_chars"),
    # 参照の書き方。
    *_fields(
        "references",
        _W,
        "cue_words",
        "connector_words",
        "document_suffixes",
        "numbered_document_suffixes",
        "excluded_name_endings",
        "generic_names",
        "demonstrative_words",
        "self_words",
        "document_nouns",
        "name_prefix_particles",
        "possessive_particles",
        "name_label_connectors",
        "part_prefixes",
    ),
    *_fields("references", _NP, "separate_part_patterns"),
    *_fields("references", _W, "previous_words", "next_words", "same_words"),
    *_fields("references", _WM, "appendix_words"),
    *_fields("references", _W, "document_name_predicates", "coordination_particles"),
    *_fields("references", _W, "derived_document_suffixes"),
    *_fields("references", _WM, "abbreviation_targets"),
    # 文書名の照合で外す表記。
    *_fields("titles", _PT, "edition_patterns"),
    *_fields("titles", _W, "edition_words", "company_suffixes"),
    # 英語の参照の書き方。
    *_fields("english", _W, "en_cue_words"),
    FieldSpec(
        "english",
        "en_section_words",
        FieldKind.ENUM_MAP,
        choices=("chapter", "section", "article", "appendix"),
    ),
    *_fields("english", _W, "en_document_suffixes"),
    *_fields("english", _P, "en_quote_brackets"),
    *_fields("english", _W, "en_document_connectors", "en_articles"),
)
SECTIONS: tuple[str, ...] = tuple(dict.fromkeys(spec.section for spec in FIELD_SPECS))
_SPECS = {(spec.section, spec.key): spec for spec in FIELD_SPECS}


class LanguagePackError(ValueError):
    """言語資源の形の誤り。``errors`` は「節.項目」→ 理由。"""

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{key}: {message}" for key, message in errors.items()))
        self.errors = dict(errors)


@dataclass(frozen=True)
class LanguagePack:
    """検証済みの言語資源（節 → 項目 → 値。値は語の tuple か文字列）。"""

    locale: str
    sections: Mapping[str, Mapping[str, Any]]
    hash: str

    def value(self, section: str, key: str) -> Any:
        return self.sections[section][key]

    def words(self, section: str, key: str) -> tuple[str, ...]:
        value = self.sections[section][key]
        return value if isinstance(value, tuple) else ()

    def mapping(self, section: str, key: str) -> dict[str, str]:
        """「語=値」の項目を、語 → 値の dict にする。"""
        result: dict[str, str] = {}
        for item in self.words(section, key):
            word, _sep, value = item.partition(MAP_SEPARATOR)
            result[word.strip()] = value.strip()
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "locale": self.locale,
            **{
                section: {
                    key: list(value) if isinstance(value, tuple) else value
                    for key, value in fields.items()
                }
                for section, fields in self.sections.items()
            },
        }


def _clean_words(value: Any) -> tuple[str, ...]:
    items = value.split("\n") if isinstance(value, str) else list(value or [])
    return tuple(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def _check(spec: FieldSpec, value: Any) -> tuple[Any, str | None]:
    """値をそろえ、形の誤りがあれば理由を返す。"""
    if spec.kind is FieldKind.CHOICE:
        text = str(value or "").strip()
        return text, None if text in spec.choices else f"{'・'.join(spec.choices)} のどれかです。"
    words = _clean_words(value)
    if len(words) > MAX_ITEMS:
        return words, f"{MAX_ITEMS} 件以内で書いてください。"
    if any(len(word) > MAX_ITEM_CHARS for word in words):
        return words, f"1 つは {MAX_ITEM_CHARS} 文字以内で書いてください。"
    for word in words:
        left, sep, right = word.partition(MAP_SEPARATOR)
        left, right = left.strip(), right.strip()
        if spec.kind is FieldKind.PAIRS and len(word) != 2:
            return words, "開きと閉じの 2 文字で書いてください(例: 「」)。"
        if spec.kind is FieldKind.NUMBER_PATTERNS and "N" not in word:
            return words, "番号の位置を N で書いてください(例: 第N号)。"
        if spec.kind is FieldKind.CHAR_VALUES and not (sep and len(left) == 1 and right.isdigit()):
            return words, "「字=数」の形で、字は 1 文字で書いてください(例: 一=1)。"
        if spec.kind is FieldKind.WORD_MAP and not (sep and left and right):
            return words, "「語=値」の形で書いてください(例: 別添=別紙)。"
        if spec.kind is FieldKind.ENUM_MAP and not (sep and left and right in spec.choices):
            return words, f"「語=値」の形で、値は {'・'.join(spec.choices)} のどれかです。"
        if spec.kind is FieldKind.CHAR_RANGES and not (
            len(word) == 1 or (len(word) == 3 and word[1] == "-" and word[0] <= word[2])
        ):
            return words, "1 文字か「最初-最後」の範囲で書いてください(例: ー、一-鿿)。"
    return words, None


def build_pack(data: Mapping[str, Any]) -> LanguagePack:
    """節 → 項目 → 値の dict を検証して言語資源にする（項目の欠け・余り・形の誤りは例外）。"""
    errors: dict[str, str] = {}
    sections: dict[str, dict[str, Any]] = {section: {} for section in SECTIONS}
    for spec in FIELD_SPECS:
        raw = (data.get(spec.section) or {}).get(spec.key)
        if raw is None:
            errors[f"{spec.section}.{spec.key}"] = "値がありません。"
            continue
        value, error = _check(spec, raw)
        if error:
            errors[f"{spec.section}.{spec.key}"] = error
        sections[spec.section][spec.key] = value
    for section, fields in data.items():
        if section in ("locale", "version"):
            continue
        for key in fields or {}:
            if (section, key) not in _SPECS:
                errors[f"{section}.{key}"] = "知らない項目です。"
    if errors:
        raise LanguagePackError(errors)
    locale = str(data.get("locale") or DEFAULT_LOCALE)
    canonical = json.dumps(
        {
            s: {k: list(v) if isinstance(v, tuple) else v for k, v in f.items()}
            for s, f in sections.items()
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(f"{locale}\n{canonical}".encode()).hexdigest()[:16]
    return LanguagePack(locale=locale, sections=sections, hash=digest)


@lru_cache(maxsize=4)
def builtin_pack(locale: str = DEFAULT_LOCALE) -> LanguagePack:
    """組み込みのパック（パッケージに同梱した既定値）。"""
    text = resources.files(__package__).joinpath("packs", f"{locale}.json").read_text("utf-8")
    return build_pack(json.loads(text))


def resolve(base: LanguagePack, *overrides: Mapping[str, Mapping[str, Any]] | None) -> LanguagePack:
    """組み込み（か下の層）の言語資源に、上書きを順に重ねる（項目ごとに置き換える）。"""
    data: dict[str, Any] = {
        "locale": base.locale,
        **{section: dict(fields) for section, fields in base.sections.items()},
    }
    unknown: dict[str, str] = {}
    for override in overrides:
        for section, fields in (override or {}).items():
            for key, value in (fields or {}).items():
                if (section, key) not in _SPECS:
                    unknown[f"{section}.{key}"] = "知らない項目です。"
                    continue
                data[section][key] = value
    if unknown:
        raise LanguagePackError(unknown)
    return build_pack(data)


def overrides_from(pack: LanguagePack, base: LanguagePack) -> dict[str, dict[str, Any]]:
    """``base`` と違う項目だけの上書き（保存の形）。"""
    result: dict[str, dict[str, Any]] = {}
    for spec in FIELD_SPECS:
        value = pack.value(spec.section, spec.key)
        if value != base.value(spec.section, spec.key):
            result.setdefault(spec.section, {})[spec.key] = (
                list(value) if isinstance(value, tuple) else value
            )
    return result


_default: LanguagePack | None = None
_bound: ContextVar[LanguagePack | None] = ContextVar("rag_language_pack", default=None)


def set_default_pack(pack: LanguagePack | None) -> None:
    """束ねていないときに使う言語資源（backend の全体の上書き）。None は組み込みのパック。"""
    global _default
    _default = pack


def current_pack() -> LanguagePack:
    """今の言語資源（束ねたもの → 全体の既定 → 組み込みの順）。"""
    return _bound.get() or _default or builtin_pack()


@contextmanager
def bind_pack(pack: LanguagePack | None) -> Iterator[None]:
    """取込の job・回答の要求・解析の要求の間だけ言語資源を束ねる（None は束ねない）。"""
    token = _bound.set(pack)
    try:
        yield
    finally:
        _bound.reset(token)


__all__ = [
    "DEFAULT_LOCALE",
    "FIELD_SPECS",
    "FieldKind",
    "FieldSpec",
    "LanguagePack",
    "LanguagePackError",
    "SECTIONS",
    "bind_pack",
    "build_pack",
    "builtin_pack",
    "current_pack",
    "overrides_from",
    "resolve",
    "set_default_pack",
]
