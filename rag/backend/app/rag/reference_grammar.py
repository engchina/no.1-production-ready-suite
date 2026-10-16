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
from typing import Any

_KANJI_NUMBER = "〇零一二三四五六七八九十百"
_NUM = rf"(?:\d+|[{_KANJI_NUMBER}]+)"
# 一覧が空のときの、何にも当たらない正規表現。
_NEVER = r"(?!)"
# 枝番の数の直後に続くと枝番にしない字（「第三条の一部」「第3条の1つ」の「の」+ 名詞）。
_NOT_BRANCH_FOLLOWERS = "部環種つ"

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
)
# 文書のタイトルの別名で外す版の語・元号・先頭の会社名の語尾
# （「改訂版」「令和6年度」「〈名前〉社 」。#2175）。
DEFAULT_EDITION_WORDS = ("改訂版", "改定版", "最新版", "初版", "新版", "旧版")
DEFAULT_ERA_NAMES = ("令和", "平成")
DEFAULT_COMPANY_SUFFIXES = ("社",)
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


def _marker_pattern(marker: str) -> str:
    """「第N号」の N を数にした正規表現（N の無い語はそのまま）。"""
    return rf"\s*{_NUM}\s*".join(_spaced(part) for part in marker.split("N"))


@dataclass(frozen=True)
class ReferenceGrammar:
    """語から作った正規表現（``cross_references`` が使う）。"""

    vocabulary: ReferenceVocabulary
    branch: str
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


def _edition_pattern(v: ReferenceVocabulary) -> str:
    """タイトルの別名で外す年度・版の表記(「2026年度」「令和6年度」「第2版」「v1.2」「改訂版」)。

    数の表記(西暦・日付・版の番号)は言語の仕組みとして持ち、版の語・元号は設定(#2175)。
    """
    return (
        r"(?:(?:19|20)\d{2}\s*(?:年度?)?"
        rf"|{_alternation(v.era_names)}\s*(?:\d+|元)\s*年度?"
        r"|第?\s*\d+\s*版|[vV]\.?\s*\d+(?:\.\d+)*|ver\.?\s*\d+(?:\.\d+)*"
        rf"|{_alternation(v.edition_words)}"
        r"|\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d{6,8})"
    )


@lru_cache(maxsize=16)
def build_grammar(vocabulary: ReferenceVocabulary) -> ReferenceGrammar:
    """語から正規表現を作る（同じ語の組は使い回す）。"""
    from app.rag.cross_references import EN_APPENDIX_FAMILIES, NAME_CHAR

    v = vocabulary
    families = v.appendix_families()
    appendix_words = _alternation(tuple(families))
    edition = _edition_pattern(v)
    connectors = _alternation(v.branch_connectors)
    branch = rf"(?:\s*{connectors}\s*{_NUM}(?![\d{_KANJI_NUMBER}{_NOT_BRANCH_FOLLOWERS}]))"
    relative = _alternation((*v.previous_words, *v.next_words, *v.same_words))
    item = (
        "(?:" + ("|".join(_marker_pattern(pattern) for pattern in v.item_patterns) or _NEVER) + ")"
    )
    part = _alternation(v.part_prefixes, spaced=True)
    label = re.compile(
        rf"(?P<chapter>第?\s*(?P<ch>{_NUM})\s*章(?:\s*第\s*(?P<ch_sec>{_NUM})\s*節)?)"
        rf"|(?P<article>(?:{part}\s*)?(?:第\s*(?P<art>{_NUM})\s*条(?P<branch>{branch}*)|(?P<rel>{relative})\s*条)"
        rf"(?:\s*{item})*)"
        rf"|(?P<jsection>第\s*(?P<jsec>{_NUM})\s*節)"
        rf"|(?P<appendix>(?P<app>{appendix_words})\s*"
        rf"(?P<app_no>\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?)"
        r"|(?P<numbered>(?<![\d.])(?P<num>\d+(?:\.\d+)+)(?!\d)\.?\s*(?:節|項)?)"
        r"|(?P<quoted>[「『](?P<title>[^「」『』\n]{1,60})[」』])"
    )
    head_numbering = re.compile(
        rf"^(?:第\s*{_NUM}\s*[章節条]{branch}*(?:\s*第\s*{_NUM}\s*[節項])?"
        r"|\d+(?:\.\d+)*\.?"
        rf"|{appendix_words}\s*(?:\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?"
        r"|(?:chapter|section|article|appendix|annex)\s*[\dA-Z.]*"
        r"|[(\[]\s*\d+\s*[)\]])\s*[:.、]?\s*",
        re.IGNORECASE,
    )
    suffix = _alternation(v.document_suffixes)
    numbered = _alternation(v.numbered_document_suffixes)
    self_words = _alternation(v.self_words)
    return ReferenceGrammar(
        vocabulary=v,
        branch=branch,
        label=label,
        head_article=re.compile(rf"^第\s*({_NUM})\s*条({branch}*)"),
        head_numbering=head_numbering,
        document_suffix=suffix,
        # 表記の直前の文書名（『〜』か、「〜マニュアル」などの語尾で終わる名前）。
        document_before=re.compile(
            rf"(?:[『「](?P<quoted>[^『』「」\n]{{2,60}})[』」]"
            rf"|(?P<plain>[^\s、。,「」『』()\[\]:]{{1,40}}?{suffix}))"
            r"\s*(?:の|における|内の|中の)?\s*$"
        ),
        # 同じ文書を指す語（「本マニュアルの第3章」「同規程の第2条」）。
        same_document_before=re.compile(
            rf"{_alternation((*v.self_words, *v.same_words))}\s*(?:{suffix}|書|文書|資料)"
            r"\s*(?:の|における|内の|中の)?\s*$"
        ),
        # 番号の表記の直前の文書名
        # （「〈他の法令〉附則第4条の3」「〈他の法令〉(昭和N年法律第N号)第5条」）。
        numbered_document_before=re.compile(
            rf"(?P<name>{NAME_CHAR}{{0,40}}?{numbered})\s*(?:\([^()]*\))?\s*(?:の)?\s*$"
        ),
        same_numbered_document_before=re.compile(rf"{self_words}\s*{numbered}\s*(?:の)?\s*$"),
        excluded_name_ending=re.compile(rf"{_alternation(v.excluded_name_endings)}$"),
        # 文書の種類の語尾（「テスト法」と「テスト法施行令」を別の文書にする）。
        document_kind_tail=re.compile(
            rf"{_alternation((*v.numbered_document_suffixes, *v.document_suffixes))}$"
        ),
        demonstrative_name=re.compile(
            rf"^{_alternation(v.demonstrative_words)}\s*(?:{suffix}|書|文書|資料|規程|計画書?|台帳|表)$"
        ),
        part_prefix=re.compile(rf"^\s*{part}"),
        part_heading=re.compile(rf"^\s*{part}"),
        separate_part_pattern=re.compile(
            "|".join(_marker_pattern(marker) for marker in v.separate_part_patterns) or _NEVER
        ),
        # 参照の手がかり（字の間の空白を許す）と、表記の間に置いてよい語の並び（#2171）。
        cue=re.compile(_alternation(v.cue_words, spaced=True)),
        connector=re.compile(rf"(?:\s|{_alternation(v.connector_words)})*"),
        item=re.compile(item),
        # 別紙の語の種類(英語の appendix・annex は英語の参照の書き方と同じくコードに持つ)。
        appendix_family={**{k.casefold(): f for k, f in EN_APPENDIX_FAMILIES.items()}, **families},
        head_appendix=re.compile(
            rf"^({_alternation((*families, *EN_APPENDIX_FAMILIES))})\s*"
            rf"(\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?",
            re.IGNORECASE,
        ),
        document_name_predicate=re.compile(_alternation(v.document_name_predicates, spaced=True)),
        trailing_edition=re.compile(rf"(?:[\s_\-]*{edition})+\s*$", re.IGNORECASE),
        leading_edition=re.compile(rf"^\s*(?:{edition}[\s_\-]*)+", re.IGNORECASE),
        company_prefix=re.compile(rf"^\S{{1,20}}?{_alternation(v.company_suffixes)}\s+"),
    )


def current_grammar() -> ReferenceGrammar:
    """今の設定の語から作った正規表現。"""
    from app.config import get_settings

    return build_grammar(ReferenceVocabulary.from_settings(get_settings()))


DEFAULT_VOCABULARY = ReferenceVocabulary()
