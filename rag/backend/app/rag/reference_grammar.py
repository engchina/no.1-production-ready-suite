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
DEFAULT_BRANCH_CONNECTORS = ("の",)
DEFAULT_PREVIOUS_WORDS = ("前",)
DEFAULT_NEXT_WORDS = ("次",)
DEFAULT_SAME_WORDS = ("同",)
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
    # 枝番のつなぎの語（「第7条の2」の「の」）。
    branch_connectors: tuple[str, ...] = DEFAULT_BRANCH_CONNECTORS
    # 相対の参照の語（「前条」「次条」「同条」）。同は、同じ本文の中で直前に出た条・文書を指す。
    previous_words: tuple[str, ...] = DEFAULT_PREVIOUS_WORDS
    next_words: tuple[str, ...] = DEFAULT_NEXT_WORDS
    same_words: tuple[str, ...] = DEFAULT_SAME_WORDS
    # 本体の文書から派生した文書の名前の語尾（「〈本体〉施行令」の「施行令」。#2169）。参照元の
    # 文書の名前がこの語で終わるとき、語尾を除いた名前を本体の名前にする。
    derived_document_suffixes: tuple[str, ...] = DEFAULT_DERIVED_DOCUMENT_SUFFIXES
    # 略称と参照先の文書名の型（「法=*」。* は本体の名前。#2169）。派生の文書の中の「法第N条」を
    # 本体の文書の参照にする。
    abbreviation_targets: tuple[str, ...] = DEFAULT_ABBREVIATION_TARGETS

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
    """「第N号」の N を数にした正規表現。"""
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


@lru_cache(maxsize=16)
def build_grammar(vocabulary: ReferenceVocabulary) -> ReferenceGrammar:
    """語から正規表現を作る（同じ語の組は使い回す）。"""
    from app.rag.cross_references import APPENDIX_WORDS, NAME_CHAR

    v = vocabulary
    connectors = _alternation(v.branch_connectors)
    branch = rf"(?:\s*{connectors}\s*{_NUM}(?![\d{_KANJI_NUMBER}{_NOT_BRANCH_FOLLOWERS}]))"
    relative = _alternation((*v.previous_words, *v.next_words, *v.same_words))
    part = _alternation(v.part_prefixes, spaced=True)
    label = re.compile(
        rf"(?P<chapter>第?\s*(?P<ch>{_NUM})\s*章(?:\s*第\s*(?P<ch_sec>{_NUM})\s*節)?)"
        rf"|(?P<article>(?:{part}\s*)?(?:第\s*(?P<art>{_NUM})\s*条(?P<branch>{branch}*)|(?P<rel>{relative})\s*条)"
        rf"(?:\s*第\s*{_NUM}\s*[項号])*(?:\s*各\s*号)?)"
        rf"|(?P<jsection>第\s*(?P<jsec>{_NUM})\s*節)"
        rf"|(?P<appendix>(?P<app>{APPENDIX_WORDS})\s*"
        rf"(?P<app_no>\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?)"
        r"|(?P<numbered>(?<![\d.])(?P<num>\d+(?:\.\d+)+)(?!\d)\.?\s*(?:節|項)?)"
        r"|(?P<quoted>[「『](?P<title>[^「」『』\n]{1,60})[」』])"
    )
    head_numbering = re.compile(
        rf"^(?:第\s*{_NUM}\s*[章節条]{branch}*(?:\s*第\s*{_NUM}\s*[節項])?"
        r"|\d+(?:\.\d+)*\.?"
        rf"|(?:{APPENDIX_WORDS})\s*(?:\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?"
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
    )


def current_grammar() -> ReferenceGrammar:
    """今の設定の語から作った正規表現。"""
    from app.config import get_settings

    return build_grammar(ReferenceVocabulary.from_settings(get_settings()))


DEFAULT_VOCABULARY = ReferenceVocabulary()
