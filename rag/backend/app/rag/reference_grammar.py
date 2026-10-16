"""交差参照の解析の語（参照の書き方）を設定から読み、
正規表現にする（#2157）。

解析の仕組み（``cross_references``）は通用で、書き方の語は
「取込・回答設定 > 文書分割」の設定にある。
既定値は規程・手引き・法令の一般の書き方（`附則第N条`・`第N条の二`・`前条`・`〜法第N条` など）で、
業務の文書の書き方に合わせて管理者が変えられる。語の一覧が空なら、その書き方は解析しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from functools import lru_cache
from operator import attrgetter
from typing import Any

# 一覧が空のときの、何にも当たらない正規表現。
_NEVER = r"(?!)"
# 番号の書き方（#2179）。番号の前置き・章・節・条・項・号の単位・漢数字の字と値・位取りの字と倍率・
# 枝番にしない後ろの字（「第三条の一部」「第3条の1つ」の「の」+ 名詞）。
DEFAULT_NUMBER_PREFIXES = ("第",)
DEFAULT_CHAPTER_UNITS = ("章",)
DEFAULT_SECTION_UNITS = ("節",)
DEFAULT_ARTICLE_UNITS = ("条",)
DEFAULT_PARAGRAPH_UNITS = ("項",)
DEFAULT_ITEM_UNITS = ("号",)
DEFAULT_KANJI_DIGITS = (
    "〇=0",
    "零=0",
    "一=1",
    "二=2",
    "三=3",
    "四=4",
    "五=5",
    "六=6",
    "七=7",
    "八=8",
    "九=9",
)
DEFAULT_KANJI_MULTIPLIERS = ("十=10", "百=100")
DEFAULT_BRANCH_STOP_CHARS = ("部", "環", "種", "つ")
# 文書名に使う文字（1 文字か「最初-最後」の範囲。#2179）。ひらがな・句読点・括弧で名前が切れる。
DEFAULT_NAME_CHARS = ("々", "〆", "ヶ", "一-鿿", "ァ-ヺ", "ー", "・", "A-Z", "a-z", "0-9")
# 文書名だけの参照で並列を見分ける助詞（「〈文書名〉で、〈文書名〉で確かめます」の「で」。#2179）。
DEFAULT_COORDINATION_PARTICLES = ("で",)

# 既定の語（設定の既定値と同じ。`app.config.Settings` の rag_reference_* が使う）。
DEFAULT_DOCUMENT_SUFFIXES = (
    "マニュアル",
    "規程",
    "規則",
    "規定",
    "細則",
    "要領",
    "要綱",
    "手順書",
    "ガイドライン",
    "ガイド",
    "仕様書",
    "説明書",
    "設計書",
    "ハンドブック",
    "基準書",
    "基準",
    "取扱説明書",
)
DEFAULT_NUMBERED_DOCUMENT_SUFFIXES = ("法律", "法", "政令", "省令", "令")
DEFAULT_EXCLUDED_NAME_ENDINGS = (
    "方法",
    "手法",
    "用法",
    "文法",
    "寸法",
    "作法",
    "技法",
    "療法",
    "製法",
    "工法",
    "説法",
    "話法",
    "語法",
    "算法",
    "筆法",
    "論法",
    "加法",
    "減法",
    "乗法",
    "除法",
    "命法",
    "指法",
    "司法",
)
DEFAULT_GENERIC_NAMES = (
    "システム",
    "ドキュメント",
    "ポータル",
    "社内ポータル",
    "ホームページ",
    "ウェブサイト",
    "Webサイト",
    "管理画面",
    "設定画面",
    "画面",
    "一覧",
    "資料",
    "文書",
    "書類",
    "手順",
    "手順書",
    "計画",
    "計画書",
    "台帳",
    "申請書",
    "様式",
    "目的",
    "概要",
    "適用範囲",
    "対象範囲",
    "お知らせ",
    "メール",
    "チャット",
    "ログ",
    "エラーメッセージ",
    "メッセージ",
    "ヘルプ",
    "FAQ",
    "利用者",
    "担当者",
    "承認者",
    "担当部署",
    "管理者",
    "責任者",
    "上長",
    "本文",
    "原本",
    "添付",
    "添付ファイル",
    "ファイル",
    "データ",
    "データベース",
    "テーブル",
    "マスタ",
    "マスター",
)
DEFAULT_DEMONSTRATIVE_WORDS = ("本", "当", "同", "上記", "下記", "前記", "後記", "各", "別")
DEFAULT_SELF_WORDS = ("本", "当", "この")
DEFAULT_PART_PREFIXES = ("附則",)
DEFAULT_SEPARATE_PART_PATTERNS = ("第N号",)
# 条の表記に続く項・号の書き方（N は数。「第七条第一項」の「第一項」。#2173）。
DEFAULT_ITEM_PATTERNS = ("第N項", "第N号", "各号")
DEFAULT_BRANCH_CONNECTORS = ("の",)
DEFAULT_PREVIOUS_WORDS = ("前",)
DEFAULT_NEXT_WORDS = ("次",)
DEFAULT_SAME_WORDS = ("同",)
# 参照を表す手がかりの語（表記の後ろに来る。「第3章を参照」「第5条の規定による」。#2171）。
DEFAULT_CUE_WORDS = (
    "参照",
    "を参照",
    "に参照",
    "も参照",
    "ご参照",
    "をご参照",
    "にご参照",
    "もご参照",
    "参考",
    "を参考",
    "をご覧",
    "を見て",
    "に記載",
    "に定め",
    "に規定",
    "に示",
    "に従",
    "に基づ",
    "に準じ",
    "に準拠",
    "で定め",
    "で規定",
    "で説明",
    "で述べ",
    "のとおり",
    "の通り",
    "による",
    "を準用",
)
# 表記どうし・表記と手がかりの語の間に置いてよい語（列挙・助詞・「の内容」など。空白は常に置ける。
# #2171）。
DEFAULT_CONNECTOR_WORDS = (
    "、",
    ",",
    "・",
    "/",
    "及び",
    "および",
    "並びに",
    "ならびに",
    "又は",
    "または",
    "もしくは",
    "中の",
    "内の",
    "における",
    "に記載の",
    "項目",
    "内容",
    "記載",
    "説明",
    "手順",
    "規定",
    "条件",
    "部分",
    "箇所",
    "等",
    "など",
    "と",
    "や",
    "の",
    "も",
)
# 文書名だけの参照の述語（「『〈文書名〉』に従う」「〈文書名〉で確かめます」。#1400・#2175）。
DEFAULT_DOCUMENT_NAME_PREDICATES = (
    "で確かめ",
    "で確認",
    "で調べ",
    "で定め",
    "で規定",
    "を確かめ",
    "を確認",
    "を調べ",
    "を参照",
    "参照",
    "を参考",
    "を見て",
    "を見る",
    "を見ます",
    "を見れば",
    "をご覧",
    "に従",
    "に基づ",
    "に記載",
    "に定め",
    "に規定",
    "による",
    "により",
    "に準じ",
    "に準拠",
    "のとおり",
    "の通り",
)
# 別紙の語と種類（「語=種類」。同じ種類の語は同じ別紙の参照にする。#2175）。
DEFAULT_APPENDIX_WORDS = (
    "別紙=別紙",
    "別添=別紙",
    "添付資料=別紙",
    "別表=別表",
    "付表=別表",
    "付録=付録",
    "附録=付録",
    "様式=様式",
    "appendix=付録",
    "annex=付録",
)
# 文書のタイトルの別名で外す版の語・元号・先頭の会社名の語尾
# （「改訂版」「令和6年度」「〈名前〉社 」。#2175）。
DEFAULT_EDITION_WORDS = ("改訂版", "改定版", "最新版", "初版", "新版", "旧版")
DEFAULT_ERA_NAMES = ("令和", "平成")
DEFAULT_COMPANY_SUFFIXES = ("社",)
# タイトルの別名で外す年度・版・日付の型（#2177）。YYYY は西暦 4 桁、MM は月、DD は日、N は数
# （「1.2」のような点の区切りを含む）、G は元号（``era_names``）。英字の大小は区別しない。
DEFAULT_EDITION_PATTERNS = (
    "YYYY年度",
    "YYYY年",
    "YYYY",
    "GN年度",
    "GN年",
    "G元年度",
    "G元年",
    "第N版",
    "N版",
    "vN",
    "v.N",
    "verN",
    "ver.N",
    "YYYY-MM-DD",
    "YYYY/MM/DD",
    "YYYY.MM.DD",
    "YYYY-MM",
    "YYYY/MM",
    "YYYY.MM",
    "YYYYMMDD",
    "YYYYMM",
)
# 記号（#2177）。照合で無視する記号・文の区切り（改行は常に区切り）・読点など・括弧書きの括弧・
# 題名の括弧と、必ず文書名とみなす題名の括弧（括弧は開きと閉じの 2 文字）。
DEFAULT_TITLE_IGNORED_CHARS = (
    "_",
    "-",
    "・",
    ":",
    "、",
    "。",
    ",",
    ".",
    "(",
    ")",
    "[",
    "]",
    "「",
    "」",
    "『",
    "』",
    "【",
    "】",
)
DEFAULT_SENTENCE_END_CHARS = ("。", "!", "?")
DEFAULT_CLAUSE_SEPARATOR_CHARS = ("、", ",", ":")
DEFAULT_BRACKET_PAIRS = ("()", "[]", "【】")
DEFAULT_QUOTE_BRACKETS = ("「」", "『』")
DEFAULT_DOCUMENT_TITLE_BRACKETS = ("『』",)
# 助詞（#2177）。文書名の前で切る助詞・番号の付いた表記の後の「の」・文書名と表記の間の語。
DEFAULT_NAME_PREFIX_PARTICLES = ("は", "が", "を", "に", "で", "も", "へ")
DEFAULT_POSSESSIVE_PARTICLES = ("の",)
DEFAULT_NAME_LABEL_CONNECTORS = ("の", "における", "内の", "中の")
# 指示の語の後に来る文書の語（「本書」「同資料」「上記の台帳」。#2177）。
DEFAULT_DOCUMENT_NOUNS = ("書", "文書", "資料", "規程", "計画書", "計画", "台帳", "表")
# 英語の参照の書き方（#2177）。手がかりの語・章や節の語と種類（「語=種類」。種類は chapter・
# section・article・appendix）・文書名の語尾・引用符・文書名の前の語（of / in）・冠詞。
DEFAULT_EN_CUE_WORDS = (
    "see",
    "see also",
    "refer to",
    "refer back to",
    "referring to",
    "described in",
    "defined in",
    "specified in",
    "set out in",
    "according to",
    "in accordance with",
    "as per",
    "pursuant to",
)
DEFAULT_EN_SECTION_WORDS = (
    "chapter=chapter",
    "section=section",
    "sec.=section",
    "§=section",
    "clause=section",
    "article=article",
    "appendix=appendix",
    "annex=appendix",
)
EN_SECTION_KINDS = ("chapter", "section", "article", "appendix")
DEFAULT_EN_DOCUMENT_SUFFIXES = (
    "Manual",
    "Guide",
    "Guidelines",
    "Guideline",
    "Handbook",
    "Policy",
    "Standard",
    "Specification",
    "Procedures",
    "Procedure",
    "Regulations",
    "Regulation",
)
DEFAULT_EN_QUOTE_BRACKETS = ('""', "“”")
DEFAULT_EN_DOCUMENT_CONNECTORS = ("of", "in")
DEFAULT_EN_ARTICLES = ("the",)
DEFAULT_DERIVED_DOCUMENT_SUFFIXES = ("施行令", "施行規則")
DEFAULT_ABBREVIATION_TARGETS = ("法=*", "令=*施行令")
# 略称と参照先の文書名の型の区切りと、型の中の本体の名前の位置（#2169）。
ABBREVIATION_SEPARATOR = "="
BASE_NAME_PLACEHOLDER = "*"

# 相対の参照の鍵（設定の語を、この鍵にそろえて解決する）。
PREVIOUS_KEY, NEXT_KEY, SAME_KEY = "前", "次", "同"


@dataclass(frozen=True)
class ReferenceVocabulary:
    """参照の書き方の語。各項目は ``Settings`` の ``rag_reference_<項目>`` と対応する。"""

    # 他の文書の名前とみなす語尾（「経費精算マニュアル」「出張旅費規程」）。
    document_suffixes: tuple[str, ...] = DEFAULT_DOCUMENT_SUFFIXES
    # 番号の表記の直前にあるときだけ文書名とみなす語尾（「〈他の法令〉第5条」の「法」）。
    # 短い語は一般語と区別
    # できないので、番号の表記が続くときに限る。
    numbered_document_suffixes: tuple[str, ...] = DEFAULT_NUMBERED_DOCUMENT_SUFFIXES
    # 上の語尾で終わっても文書名でない語（「処理方法第3条」の「方法」）。
    excluded_name_endings: tuple[str, ...] = DEFAULT_EXCLUDED_NAME_ENDINGS
    # 文書名として扱わない一般語（「システム」「台帳」）。
    generic_names: tuple[str, ...] = DEFAULT_GENERIC_NAMES
    # 文書名の前に付くと、名前ではなく指示の語になる語（「本手順書」「同規程」「上記の計画書」）。
    demonstrative_words: tuple[str, ...] = DEFAULT_DEMONSTRATIVE_WORDS
    # 同じ文書を指す語（「本マニュアルの第3章」「この法律第2条」）。
    self_words: tuple[str, ...] = DEFAULT_SELF_WORDS
    # 部の前置きの語（「附則第3条」）。前置きのある参照はその部の中で、
    # 無い参照は前置きの部の外で解決する。
    part_prefixes: tuple[str, ...] = DEFAULT_PART_PREFIXES
    # 部の見出しにあると、その部の番号が別の体系になる印
    # （N は数。「附則(令和二年法律第八号)」の「第N号」）。
    separate_part_patterns: tuple[str, ...] = DEFAULT_SEPARATE_PART_PATTERNS
    # 条の表記に続く項・号の書き方（N は数。「第N項」「各号」。#2173）。条の表記の範囲・列の
    # つなぎ（「第一項及び第二項」）・文書名の候補から外す表記に使う。
    item_patterns: tuple[str, ...] = DEFAULT_ITEM_PATTERNS
    # 枝番のつなぎの語（「第7条の2」の「の」）。
    branch_connectors: tuple[str, ...] = DEFAULT_BRANCH_CONNECTORS
    # 相対の参照の語（「前条」「次条」「同条」）。同は、同じ本文の中で直前に出た条・文書を指す。
    previous_words: tuple[str, ...] = DEFAULT_PREVIOUS_WORDS
    next_words: tuple[str, ...] = DEFAULT_NEXT_WORDS
    same_words: tuple[str, ...] = DEFAULT_SAME_WORDS
    # 参照を表す手がかりの語（「を参照」「による」。#2171）。
    # 手がかりの直前に並んだ表記を参照にする。
    cue_words: tuple[str, ...] = DEFAULT_CUE_WORDS
    # 表記どうし・表記と手がかりの語の間に置いてよい語（「及び」「又は」「、」。#2171）。
    connector_words: tuple[str, ...] = DEFAULT_CONNECTOR_WORDS
    # 文書名だけの参照の述語（「〈文書名〉で確かめます」。#2175）。
    document_name_predicates: tuple[str, ...] = DEFAULT_DOCUMENT_NAME_PREDICATES
    # 別紙の語と種類（「別添=別紙」。#2175）。
    appendix_words: tuple[str, ...] = DEFAULT_APPENDIX_WORDS
    # 文書のタイトルの別名で外す版の語・元号・先頭の会社名の語尾（#2175）。
    edition_words: tuple[str, ...] = DEFAULT_EDITION_WORDS
    era_names: tuple[str, ...] = DEFAULT_ERA_NAMES
    company_suffixes: tuple[str, ...] = DEFAULT_COMPANY_SUFFIXES
    # タイトルの別名で外す年度・版・日付の型（YYYY・MM・DD・N・G。#2177）。
    edition_patterns: tuple[str, ...] = DEFAULT_EDITION_PATTERNS
    # 記号（#2177）。
    title_ignored_chars: tuple[str, ...] = DEFAULT_TITLE_IGNORED_CHARS
    sentence_end_chars: tuple[str, ...] = DEFAULT_SENTENCE_END_CHARS
    clause_separator_chars: tuple[str, ...] = DEFAULT_CLAUSE_SEPARATOR_CHARS
    bracket_pairs: tuple[str, ...] = DEFAULT_BRACKET_PAIRS
    quote_brackets: tuple[str, ...] = DEFAULT_QUOTE_BRACKETS
    document_title_brackets: tuple[str, ...] = DEFAULT_DOCUMENT_TITLE_BRACKETS
    # 助詞と、指示の語の後に来る文書の語（#2177）。
    name_prefix_particles: tuple[str, ...] = DEFAULT_NAME_PREFIX_PARTICLES
    possessive_particles: tuple[str, ...] = DEFAULT_POSSESSIVE_PARTICLES
    name_label_connectors: tuple[str, ...] = DEFAULT_NAME_LABEL_CONNECTORS
    document_nouns: tuple[str, ...] = DEFAULT_DOCUMENT_NOUNS
    # 英語の参照の書き方（#2177）。
    en_cue_words: tuple[str, ...] = DEFAULT_EN_CUE_WORDS
    en_section_words: tuple[str, ...] = DEFAULT_EN_SECTION_WORDS
    en_document_suffixes: tuple[str, ...] = DEFAULT_EN_DOCUMENT_SUFFIXES
    en_quote_brackets: tuple[str, ...] = DEFAULT_EN_QUOTE_BRACKETS
    en_document_connectors: tuple[str, ...] = DEFAULT_EN_DOCUMENT_CONNECTORS
    en_articles: tuple[str, ...] = DEFAULT_EN_ARTICLES
    # 番号の書き方・文書名の文字・並列の助詞（#2179）。
    number_prefixes: tuple[str, ...] = DEFAULT_NUMBER_PREFIXES
    chapter_units: tuple[str, ...] = DEFAULT_CHAPTER_UNITS
    section_units: tuple[str, ...] = DEFAULT_SECTION_UNITS
    article_units: tuple[str, ...] = DEFAULT_ARTICLE_UNITS
    paragraph_units: tuple[str, ...] = DEFAULT_PARAGRAPH_UNITS
    item_units: tuple[str, ...] = DEFAULT_ITEM_UNITS
    kanji_digits: tuple[str, ...] = DEFAULT_KANJI_DIGITS
    kanji_multipliers: tuple[str, ...] = DEFAULT_KANJI_MULTIPLIERS
    branch_stop_chars: tuple[str, ...] = DEFAULT_BRANCH_STOP_CHARS
    name_chars: tuple[str, ...] = DEFAULT_NAME_CHARS
    coordination_particles: tuple[str, ...] = DEFAULT_COORDINATION_PARTICLES
    # 本体の文書から派生した文書の名前の語尾（「〈本体〉施行令」の「施行令」。#2169）。参照元の
    # 文書の名前がこの語で終わるとき、語尾を除いた名前を本体の名前にする。
    derived_document_suffixes: tuple[str, ...] = DEFAULT_DERIVED_DOCUMENT_SUFFIXES
    # 略称と参照先の文書名の型（「法=*」。* は本体の名前。#2169）。派生の文書の中の「法第N条」を
    # 本体の文書の参照にする。
    abbreviation_targets: tuple[str, ...] = DEFAULT_ABBREVIATION_TARGETS

    def appendix_families(self) -> dict[str, str]:
        """別紙の語 → 種類（「語=種類」の形でない行は語そのものを種類にする）。"""
        families: dict[str, str] = {}
        for item in self.appendix_words:
            word, _sep, family = item.partition(ABBREVIATION_SEPARATOR)
            if word.strip():
                families[word.strip()] = family.strip() or word.strip()
        return families

    def kanji_values(self, items: tuple[str, ...]) -> dict[str, int]:
        """「字=数」の行から、1 文字の字 → 数（形の違う行は飛ばす）。"""
        values: dict[str, int] = {}
        for item in items:
            char, _sep, value = item.partition(ABBREVIATION_SEPARATOR)
            if len(char.strip()) == 1 and value.strip().isdigit():
                values[char.strip()] = int(value.strip())
        return values

    def en_section_kinds(self) -> dict[str, str]:
        """英語の章・節の語（小文字）→ 種類。種類の分からない行は飛ばす。"""
        kinds: dict[str, str] = {}
        for item in self.en_section_words:
            word, _sep, kind = item.partition(ABBREVIATION_SEPARATOR)
            if word.strip() and kind.strip() in EN_SECTION_KINDS:
                kinds[word.strip().casefold()] = kind.strip()
        return kinds

    def abbreviation_title(self, name: str, citing_title: str) -> str | None:
        """参照の文書名が略称なら、参照元の文書の名前から決めた参照先の文書名。

        決まらなければ None（略称でない・参照元の名前が派生の語尾で終わらない）。
        """
        pairs = (item.partition(ABBREVIATION_SEPARATOR) for item in self.abbreviation_targets)
        targets = {
            abbr.strip(): template.strip()
            for abbr, sep, template in pairs
            if sep and abbr.strip() and template.strip()
        }
        template = targets.get(name.strip())
        if template is None:
            return None
        title = citing_title.strip()
        for suffix in sorted(self.derived_document_suffixes, key=len, reverse=True):
            if title.endswith(suffix) and len(title) > len(suffix):
                return template.replace(BASE_NAME_PLACEHOLDER, title[: -len(suffix)])
        return None

    @classmethod
    def from_settings(cls, settings: Any) -> ReferenceVocabulary:
        """``Settings`` の ``rag_reference_*`` から作る。無い項目は既定値。"""
        values = {}
        for item in fields(cls):
            raw = getattr(settings, f"rag_reference_{item.name}", None)
            if raw is not None:
                values[item.name] = _words(raw)
        return cls(**values)


def _words(value: Any) -> tuple[str, ...]:
    """空白を除いた重複の無い語の並び。"""
    items = value.split("\n") if isinstance(value, str) else list(value or [])
    return tuple(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))


def _spaced(word: str) -> str:
    """字の間に空白を許す（PDF の抽出の「附 則」）。"""
    return r"\s*".join(re.escape(char) for char in word)


def _alternation(words: tuple[str, ...], *, spaced: bool = False) -> str:
    """長い語を先にした選択。空なら何にも当たらない。"""
    if not words:
        return _NEVER
    ordered = sorted(words, key=len, reverse=True)
    return "(?:" + "|".join(_spaced(word) if spaced else re.escape(word) for word in ordered) + ")"


def _marker_pattern(marker: str, number: str) -> str:
    """「第N号」の N を数（``number``）にした正規表現（N の無い語はそのまま）。"""
    return rf"\s*{number}\s*".join(_spaced(part) for part in marker.split("N"))


def _char_class(items: tuple[str, ...]) -> str:
    """1 文字か「最初-最後」の範囲の並びを、文字の集合にする。無ければ何にも当たらない。"""
    parts = []
    for item in items:
        if len(item) == 1:
            parts.append(re.escape(item))
        elif len(item) == 3 and item[1] == "-" and item[0] <= item[2]:
            parts.append(f"{re.escape(item[0])}-{re.escape(item[2])}")
    return f"[{''.join(parts)}]" if parts else _NEVER


@dataclass(frozen=True)
class ReferenceGrammar:
    """語から作った正規表現（``cross_references`` が使う）。"""

    vocabulary: ReferenceVocabulary
    branch: str
    # 番号（算用数字か漢数字）の正規表現・漢数字の字と値・位取りの字と倍率・文書名の文字（#2179）。
    number: str
    kanji_digits: dict[str, int]
    kanji_multipliers: dict[str, int]
    name_char: str
    number_prefix: re.Pattern[str]
    head_chapter: re.Pattern[str]
    head_jsection: re.Pattern[str]
    paragraph_label: re.Pattern[str]
    item_label: re.Pattern[str]
    line_number: re.Pattern[str]
    label: re.Pattern[str]
    head_article: re.Pattern[str]
    head_numbering: re.Pattern[str]
    document_suffix: str
    document_before: re.Pattern[str]
    same_document_before: re.Pattern[str]
    numbered_document_before: re.Pattern[str]
    same_numbered_document_before: re.Pattern[str]
    excluded_name_ending: re.Pattern[str]
    document_kind_tail: re.Pattern[str]
    demonstrative_name: re.Pattern[str]
    part_prefix: re.Pattern[str]
    part_heading: re.Pattern[str]
    separate_part_pattern: re.Pattern[str]
    cue: re.Pattern[str]
    connector: re.Pattern[str]
    item: re.Pattern[str]
    appendix_family: dict[str, str]
    head_appendix: re.Pattern[str]
    document_name_predicate: re.Pattern[str]
    trailing_edition: re.Pattern[str]
    leading_edition: re.Pattern[str]
    company_prefix: re.Pattern[str]
    # 記号・助詞から作った正規表現（#2177）。
    title_noise: re.Pattern[str]
    sentence_end: re.Pattern[str]
    name_prefix_split: re.Pattern[str]
    possessive_tail: re.Pattern[str]
    parenthetical: re.Pattern[str]
    name_label_tail: re.Pattern[str]
    document_name_before: re.Pattern[str]
    document_name_coordinated: re.Pattern[str]
    # 必ず文書名とみなす題名の括弧（開き → 閉じ）。
    document_title_brackets: dict[str, str]
    # 英語の参照の書き方（#2177）。
    en_ref: re.Pattern[str]
    en_head: re.Pattern[str]
    en_kinds: dict[str, str]

    def relative_key(self, word: str) -> str:
        """相対の参照の語を鍵（前・次・同）にする。"""
        for key, words in (
            (PREVIOUS_KEY, self.vocabulary.previous_words),
            (NEXT_KEY, self.vocabulary.next_words),
            (SAME_KEY, self.vocabulary.same_words),
        ):
            if word in words:
                return key
        return ""

    def starts_with_same_word(self, name: str) -> bool:
        return any(name.startswith(word) for word in self.vocabulary.same_words)


# 年度・版・日付の型の記号（#2177）。
_EDITION_PLACEHOLDERS = {
    "YYYY": r"(?:19|20)\d{2}",
    "MM": r"\d{1,2}",
    "DD": r"\d{1,2}",
    "N": r"\d+(?:\.\d+)*",
}
EDITION_PLACEHOLDER_NAMES = (*_EDITION_PLACEHOLDERS, "G")


def _edition_template(template: str, era: str) -> str:
    """年度・版・日付の型を正規表現にする（記号と字の間に空白を許す）。"""
    tokens: list[str] = []
    rest = template
    while rest:
        name = next((n for n in EDITION_PLACEHOLDER_NAMES if rest.startswith(n)), None)
        if name is not None:
            tokens.append(era if name == "G" else _EDITION_PLACEHOLDERS[name])
            rest = rest[len(name) :]
        else:
            tokens.append(re.escape(rest[0]))
            rest = rest[1:]
    return r"\s*".join(tokens)


def _edition_pattern(v: ReferenceVocabulary) -> str:
    """タイトルの別名で外す年度・版の表記(「2026年度」「令和6年度」「第2版」「v1.2」「改訂版」)。

    型（``edition_patterns``）・版の語・元号はすべて設定（#2175・#2177）。
    """
    era = _alternation(v.era_names)
    templates = sorted(v.edition_patterns, key=len, reverse=True)
    parts = [_edition_template(template, era) for template in templates if template.strip()]
    return "(?:" + "|".join([*parts, _alternation(v.edition_words)]) + ")"


def _pair_class(pairs: tuple[str, ...], position: int) -> str:
    """括弧の組（2 文字）の開き（0）か閉じ（1）の字の集合。無ければ何にも当たらない。"""
    chars = "".join(pair[position] for pair in pairs if len(pair) == 2)
    return f"[{re.escape(chars)}]" if chars else _NEVER


def _pair_chars(pairs: tuple[str, ...]) -> str:
    return re.escape("".join(pair for pair in pairs if len(pair) == 2))


def _en_words(words: tuple[str, ...]) -> str:
    """英語の語の選択（語の間の空白は 1 つ以上の空白）。"""
    if not words:
        return _NEVER
    ordered = sorted(words, key=len, reverse=True)
    return (
        "(?:" + "|".join(r"\s+".join(re.escape(w) for w in word.split()) for word in ordered) + ")"
    )


@lru_cache(maxsize=16)
def build_grammar(vocabulary: ReferenceVocabulary) -> ReferenceGrammar:
    """語から正規表現を作る（同じ語の組は使い回す）。"""
    v = vocabulary
    kanji_digits = v.kanji_values(v.kanji_digits)
    kanji_multipliers = v.kanji_values(v.kanji_multipliers)
    kanji = re.escape("".join([*kanji_digits, *kanji_multipliers]))
    num = rf"(?:\d+|[{kanji}]+)" if kanji else r"\d+"
    kanji_class = f"[{kanji}]+" if kanji else _NEVER
    NAME_CHAR = _char_class(v.name_chars)  # noqa: N806 - 文書名の文字の集合(以前の定数の名前のまま)
    prefix = _alternation(v.number_prefixes)
    chapter_unit = _alternation(v.chapter_units)
    section_unit = _alternation(v.section_units)
    article_unit = _alternation(v.article_units)
    paragraph_unit = _alternation(v.paragraph_units)
    item_unit = _alternation(v.item_units)
    branch_stop = re.escape("".join(v.branch_stop_chars))
    coordination = _alternation(v.coordination_particles)
    families = v.appendix_families()
    # 英字の別紙の語（appendix など）は英語の参照の書き方で拾い、日本語の表記には使わない。
    appendix_words = _alternation(tuple(w for w in families if not re.fullmatch(r"[A-Za-z]+", w)))
    en_kinds = v.en_section_kinds()
    en_section = _en_words(tuple(en_kinds))
    en_head_words = _en_words(tuple(w for w, kind in en_kinds.items() if kind != "appendix"))
    quote_open, quote_close = _pair_class(v.quote_brackets, 0), _pair_class(v.quote_brackets, 1)
    quote_chars = _pair_chars(v.quote_brackets)
    title_open = _pair_class(v.document_title_brackets, 0)
    title_close = _pair_class(v.document_title_brackets, 1)
    brackets = tuple(pair for pair in v.bracket_pairs if len(pair) == 2)
    bracket_chars = _pair_chars(brackets)
    # 括弧書き（「(〜)」）。括弧の組ごとに、中に括弧の無い 1 段だけ。
    parenthetical = (
        "|".join(
            rf"{re.escape(pair[0])}[^{bracket_chars}]*{re.escape(pair[1])}" for pair in brackets
        )
        or _NEVER
    )
    separators = _alternation(v.clause_separator_chars)
    sentence_ends = _alternation(v.sentence_end_chars)
    # 文書名に入らない字（空白・読点など・文の区切り・括弧・題名の括弧）。
    name_stop = rf"(?:\s|{separators}|{sentence_ends}|[{bracket_chars}{quote_chars}])"
    name_label = rf"\s*(?:{_alternation(v.name_label_connectors)})?\s*$"
    nouns = _alternation(v.document_nouns)
    edition = _edition_pattern(v)
    connectors = _alternation(v.branch_connectors)
    branch = rf"(?:\s*{connectors}\s*{num}(?![\d{kanji}{branch_stop}]))"
    relative = _alternation((*v.previous_words, *v.next_words, *v.same_words))
    item = (
        "(?:"
        + ("|".join(_marker_pattern(pattern, num) for pattern in v.item_patterns) or _NEVER)
        + ")"
    )
    part = _alternation(v.part_prefixes, spaced=True)
    label = re.compile(
        rf"(?P<chapter>{prefix}?\s*(?P<ch>{num})\s*{chapter_unit}"
        rf"(?:\s*{prefix}\s*(?P<ch_sec>{num})\s*{section_unit})?)"
        rf"|(?P<article>(?:{part}\s*)?(?:{prefix}\s*(?P<art>{num})\s*{article_unit}(?P<branch>{branch}*)"
        rf"|(?P<rel>{relative})\s*{article_unit})"
        rf"(?:\s*{item})*)"
        rf"|(?P<jsection>{prefix}\s*(?P<jsec>{num})\s*{section_unit})"
        rf"|(?P<appendix>(?P<app>{appendix_words})\s*"
        rf"(?P<app_no>\d+|[A-Za-z](?![A-Za-z])|{kanji_class})?)"
        r"|(?P<numbered>(?<![\d.])(?P<num>\d+(?:\.\d+)+)(?!\d)\.?\s*"
        rf"(?:{section_unit}|{paragraph_unit})?)"
        rf"|(?P<quoted>{quote_open}(?P<title>[^{quote_chars}\n]{{1,60}}){quote_close})"
    )
    head_numbering = re.compile(
        rf"^(?:{prefix}\s*{num}\s*(?:{chapter_unit}|{section_unit}|{article_unit}){branch}*"
        rf"(?:\s*{prefix}\s*{num}\s*(?:{section_unit}|{paragraph_unit}))?"
        r"|\d+(?:\.\d+)*\.?"
        rf"|{appendix_words}\s*(?:\d+|[A-Za-z](?![A-Za-z])|{kanji_class})?"
        rf"|{en_section}\s*[\dA-Z.]*"
        + "".join(rf"|{re.escape(pair[0])}\s*\d+\s*{re.escape(pair[1])}" for pair in brackets)
        + rf")\s*(?:{separators}|\.)?\s*",
        re.IGNORECASE,
    )
    suffix = _alternation(v.document_suffixes)
    numbered = _alternation(v.numbered_document_suffixes)
    self_words = _alternation(v.self_words)
    return ReferenceGrammar(
        vocabulary=v,
        branch=branch,
        number=num,
        kanji_digits=kanji_digits,
        kanji_multipliers=kanji_multipliers,
        name_char=NAME_CHAR,
        number_prefix=re.compile(rf"^{prefix}"),
        head_chapter=re.compile(rf"^{prefix}\s*({num})\s*{chapter_unit}"),
        head_jsection=re.compile(rf"^{prefix}\s*({num})\s*{section_unit}"),
        paragraph_label=re.compile(rf"{prefix}\s*({num})\s*{paragraph_unit}"),
        item_label=re.compile(rf"{prefix}\s*({num})\s*{item_unit}"),
        line_number=re.compile(rf"\s*({num})\s"),
        label=label,
        head_article=re.compile(rf"^{prefix}\s*({num})\s*{article_unit}({branch}*)"),
        head_numbering=head_numbering,
        document_suffix=suffix,
        # 表記の直前の文書名（『〜』か、「〜マニュアル」などの語尾で終わる名前）。
        document_before=re.compile(
            rf"(?:{quote_open}(?P<quoted>[^{quote_chars}\n]{{2,60}}){quote_close}"
            rf"|(?P<plain>(?:(?!{name_stop}).){{1,40}}?{suffix}))" + name_label
        ),
        # 同じ文書を指す語（「本マニュアルの第3章」「同規程の第2条」）。
        same_document_before=re.compile(
            rf"{_alternation((*v.self_words, *v.same_words))}\s*(?:{suffix}|{nouns})" + name_label
        ),
        # 番号の表記の直前の文書名
        # （「〈他の法令〉附則第4条の3」「〈他の法令〉(昭和N年法律第N号)第5条」）。
        numbered_document_before=re.compile(
            rf"(?P<name>{NAME_CHAR}{{0,40}}?{numbered})\s*(?:{parenthetical})?" + name_label
        ),
        same_numbered_document_before=re.compile(rf"{self_words}\s*{numbered}" + name_label),
        excluded_name_ending=re.compile(rf"{_alternation(v.excluded_name_endings)}$"),
        # 文書の種類の語尾（「テスト法」と「テスト法施行令」を別の文書にする）。
        document_kind_tail=re.compile(
            rf"{_alternation((*v.numbered_document_suffixes, *v.document_suffixes))}$"
        ),
        demonstrative_name=re.compile(
            rf"^{_alternation(v.demonstrative_words)}\s*(?:{suffix}|{nouns})$"
        ),
        part_prefix=re.compile(rf"^\s*{part}"),
        part_heading=re.compile(rf"^\s*{part}"),
        separate_part_pattern=re.compile(
            "|".join(_marker_pattern(marker, num) for marker in v.separate_part_patterns) or _NEVER
        ),
        # 参照の手がかり（字の間の空白を許す）と、表記の間に置いてよい語の並び（#2171）。
        cue=re.compile(_alternation(v.cue_words, spaced=True)),
        connector=re.compile(rf"(?:\s|{_alternation(v.connector_words)})*"),
        item=re.compile(item),
        # 別紙の語の種類（英字の語は小文字でも引く）。
        appendix_family={**{k.casefold(): f for k, f in families.items()}, **families},
        head_appendix=re.compile(
            rf"^({_alternation(tuple(families))})\s*"
            rf"(\d+|[A-Za-z](?![A-Za-z])|{kanji_class})?",
            re.IGNORECASE,
        ),
        document_name_predicate=re.compile(_alternation(v.document_name_predicates, spaced=True)),
        trailing_edition=re.compile(rf"(?:[\s_\-]*{edition})+\s*$", re.IGNORECASE),
        leading_edition=re.compile(rf"^\s*(?:{edition}[\s_\-]*)+", re.IGNORECASE),
        company_prefix=re.compile(rf"^\S{{1,20}}?{_alternation(v.company_suffixes)}\s+"),
        title_noise=re.compile(rf"(?:\s|{_alternation(v.title_ignored_chars)})+"),
        sentence_end=re.compile(rf"\n|{sentence_ends}"),
        name_prefix_split=re.compile(_alternation(v.name_prefix_particles)),
        # 「第2章の共通の保守枠」の「の」+ 名詞
        # （文の区切り・読点・括弧・「第」・数字が続けば違う）。
        possessive_tail=re.compile(
            rf"\s*{_alternation(v.possessive_particles)}\s*"
            rf"(?![\s{bracket_chars}{quote_chars}]|{separators}|{sentence_ends}|\.|{prefix}|\d)"
        ),
        parenthetical=re.compile(parenthetical),
        name_label_tail=re.compile(rf"^{name_label}"),
        document_name_before=re.compile(
            rf"(?P<open>{title_open})?(?P<name>(?:{NAME_CHAR}(?:\s(?={NAME_CHAR}))?){{2,40}})"
            rf"(?(open){title_close})\s*$"
        ),
        document_name_coordinated=re.compile(
            rf"(?<!{NAME_CHAR})(?P<open>{title_open})?"
            rf"(?P<name>(?:{NAME_CHAR}(?:\s(?={NAME_CHAR}))?){{2,40}})"
            rf"(?(open){title_close})\s*{coordination}\s*(?:{separators})"
        ),
        document_title_brackets={
            pair[0]: pair[1] for pair in v.document_title_brackets if len(pair) == 2
        },
        en_ref=re.compile(
            rf"\b{_en_words(v.en_cue_words)}\s+(?:{_en_words(v.en_articles)}\s+)?"
            rf"(?:(?P<kind>{en_section})\s*(?P<no>\d+(?:\.\d+)*|[A-Z])\b"
            rf"(?:\s+{_en_words(v.en_document_connectors)}\s+(?:{_en_words(v.en_articles)}\s+)?"
            rf"(?P<doc>(?:[A-Z][\w&-]*\s+){{0,6}}{_en_words(v.en_document_suffixes)}))?"
            rf"|{_pair_class(v.en_quote_brackets, 0)}"
            rf"(?P<title>[^{_pair_chars(v.en_quote_brackets)}\n]{{2,80}})"
            rf"{_pair_class(v.en_quote_brackets, 1)})",
            re.IGNORECASE,
        ),
        en_head=re.compile(rf"^({en_head_words})\s*(\d+(?:\.\d+)*)", re.IGNORECASE),
        en_kinds=en_kinds,
    )


_SETTING_NAMES = tuple(f"rag_reference_{item.name}" for item in fields(ReferenceVocabulary))
# 設定の値をまとめて読む(C の実装。毎回の比べる手間を小さくする)。
_setting_values = attrgetter(*_SETTING_NAMES)
# 直前に作った正規表現と、そのときの設定の値(同じ物かを ``is`` で比べる。値の物を持つので id は
# 使い回されない)。設定の画面の保存は値を新しい list に置き換える。
_current: tuple[tuple[object, ...], ReferenceGrammar] | None = None


def current_grammar() -> ReferenceGrammar:
    """今の設定の語から作った正規表現。

    解析の関数は 1 回の取込で何万回も呼ぶので、設定の値が変わったときだけ作り直す
    (語の組から作るのは ``build_grammar`` の cache)。
    """
    global _current
    from app.config import loaded_settings

    settings = loaded_settings()
    values = (settings, *_setting_values(settings))
    cached = _current
    # tuple の比較は同じ物なら中身を比べない(設定の画面の保存は値を新しい list に置き換える)。
    if cached is not None and cached[0] == values:
        return cached[1]
    grammar = build_grammar(ReferenceVocabulary.from_settings(settings))
    _current = (values, grammar)
    return grammar


DEFAULT_VOCABULARY = ReferenceVocabulary()
