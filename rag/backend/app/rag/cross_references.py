"""文書の交差参照の抽出と解決(#1280。handoff §7.2)。

「第3章を参照」「3.2 節参照」「別紙1のとおり」「経費精算マニュアルの「権限」を参照」
「see Section 3.2」のような参照の表記と、手がかりの語の無い「第2章の共通の保守枠で保守します」
(番号の付いた章・条の直後の「の」+ 名詞。#1382)を、chunk の本文から決定論で抜き出す
(LLM は使わない)。

- 取込: 同じ文書の見出しの列(chunk の ``section_path``)に照らして参照先の節を決め、
  chunk の metadata(``reference_targets_json``)に残す(``annotate_cross_references``)。
  参照が無い chunk の metadata は変えない。参照が自分の節だけなら空の列を残す(解決済みの印)。
- 印の無い chunk(#1382 より前に取り込んだ chunk)は、回答のときに本文から抜き出し、その文書の
  見出しの列で解決する(``answer_engine``。再処理しなくても参照を辿れる)。
- 他の文書への参照は、文書名と節の表記だけを残す(未解決)。文書がどのナレッジベースに
  属するかは chunk に焼き込まない(KB は純スコープ。所属を変えても chunk に波及しない)ため、
  回答のときに検索範囲(KB)の中の文書から解決する(``answer_engine``)。
- 文書名で別の文書を指す文(「時間帯は定期保守計画で確かめます」「組織規程に従う」。#1400)は、
  文書名 + 参照の述語(で確かめる・を確認する・を見る・に従う・による・に記載 など)の形を、
  節を決めない文書全体への参照(``kind="document"``)として抜き出す。どの文書かは回答のときに
  検索範囲の文書のタイトル(見出しの列の先頭)・ファイル名と別名(年度・版・括弧書きを外した形)で
  照合して決め(``document_name_match``)、参照の先はその文書の質問に関連の高い chunk に限る。
  誤検出を避けるため、文書名は 4 文字以上・一般語(「システム」「台帳」など)でない・章や条の
  表記を含まないものに限り、照合は別名との完全一致(とファイル名が文書名を含むもの)だけにする。
- 回答: 検索で見つけた chunk の参照先の節の chunk を、上限付きで rerank の候補に足す。

参照先は「見出しの列のどこまでか」(``section_path`` の前方一致)で表す。chunk の id は
再索引で変わるので持たない。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

# chunk の metadata に参照先を載せる key(JSON の文字列。ChunkMetadata は scalar だけを持つ)。
REFERENCE_TARGETS_KEY = "reference_targets_json"
# 回答で参照先として足した chunk に付ける印(起点の chunk_id と参照の表記)。
REFERENCE_FROM_KEY = "reference_from_chunk_id"
REFERENCE_LABEL_KEY = "reference_label"
# 1 つの chunk から残す参照の数の上限(目次や索引のような参照だらけの chunk を抑える)。
MAX_REFERENCES_PER_CHUNK = 6
# 見出しの列の区切り(chunking が「 > 」でつないで保存する)。
SECTION_PATH_SEPARATOR = " > "
# 参照の表記の直前を遡る文字数の上限(手がかりの語から前へ)。
_LOOKBACK_CHARS = 80

type ReferenceKind = Literal[
    "chapter", "section", "jsection", "article", "appendix", "title", "document"
]

_KANJI_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_KANJI_NUMBER = "〇零一二三四五六七八九十百"
_NUM = rf"(?:\d+|[{_KANJI_NUMBER}]+)"

# 付録・別紙の種類(見出しと参照で同じ種類として扱う語をまとめる)。
_APPENDIX_FAMILIES = {
    "別紙": "別紙",
    "別添": "別紙",
    "添付資料": "別紙",
    "別表": "別表",
    "付表": "別表",
    "付録": "付録",
    "附録": "付録",
    "appendix": "付録",
    "annex": "付録",
    "様式": "様式",
}
_APPENDIX_WORDS = "別紙|別添|添付資料|別表|付表|付録|附録|様式"

# 本文の中の参照の表記(NFKC で正規化した本文に当てる)。
_LABEL = re.compile(
    rf"(?P<chapter>第?\s*(?P<ch>{_NUM})\s*章(?:\s*第\s*(?P<ch_sec>{_NUM})\s*節)?)"
    rf"|(?P<article>第\s*(?P<art>{_NUM})\s*条(?:\s*第\s*{_NUM}\s*[項号])*)"
    rf"|(?P<jsection>第\s*(?P<jsec>{_NUM})\s*節)"
    rf"|(?P<appendix>(?P<app>{_APPENDIX_WORDS})\s*"
    rf"(?P<app_no>\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?)"
    r"|(?P<numbered>(?<![\d.])(?P<num>\d+(?:\.\d+)+)(?!\d)\.?\s*(?:節|項)?)"
    r"|(?P<quoted>[「『](?P<title>[^「」『』\n]{1,60})[」』])"
)
# 参照を表す手がかりの語(表記の後ろに来る)。
_JA_CUE = re.compile(
    r"(?:を|に|も)?\s*(?:ご)?参照"
    r"|を?\s*参考"
    r"|を\s*(?:ご覧|見て)"
    r"|に\s*(?:記載|定め|規定|示|従|基づ|準じ|準拠)"
    r"|で\s*(?:定め|規定|説明|述べ)"
    r"|のとおり|の通り|による|を準用"
)
# 表記どうし・表記と手がかりの語の間に置いてよい語(列挙・助詞・「の内容」など)。
_CONNECTOR = re.compile(
    r"(?:\s|[、,・/]|及び|および|並びに|ならびに|又は|または|もしくは|中の|内の|における|"
    r"に記載の|項目|内容|記載|説明|手順|規定|条件|部分|箇所|等|など|と|や|の|も)*"
)
_DOC_SUFFIX = (
    r"(?:マニュアル|規程|規則|規定|細則|要領|要綱|手順書|ガイドライン|ガイド|仕様書|説明書|"
    r"設計書|ハンドブック|基準書|基準|取扱説明書)"
)
# 表記の直前の文書名(『〜』か、「〜マニュアル」などの語で終わる名前)。
_DOC_BEFORE = re.compile(
    rf"(?:[『「](?P<quoted>[^『』「」\n]{{2,60}})[』」]"
    rf"|(?P<plain>[^\s、。,「」『』()\[\]:]{{1,40}}?{_DOC_SUFFIX}))"
    r"\s*(?:の|における|内の|中の)?\s*$"
)
# 同じ文書を指す語(「本マニュアルの第3章」)。
_SAME_DOC_BEFORE = re.compile(
    rf"(?:本|当|この|同)\s*(?:{_DOC_SUFFIX}|書|文書|資料)\s*(?:の|における|内の|中の)?\s*$"
)
# 手がかりの語の無い参照(#1382): 番号の付いた章・節・条・別紙の直後の「の」+ 名詞
# (「第2章の共通の保守枠で保守します」「第4章の代理の規定」)。地の文の「第3章では」「全12章の構成」
# (「第」の無い章)・「第2条の2」(枝番)・「第2章の第3節」(続く表記に任せる)は拾わない。
_POSSESSIVE_TAIL = re.compile(r"\s*の\s*(?![\s、。,.!?()\[\]「」『』]|第|\d)")
# 文書名の前に付いた語(「詳細は」など)を外す区切り(助詞)。
_DOC_PREFIX_SPLIT = re.compile(r"[はがをにでもへ]")
_SENTENCE_END = re.compile(r"[。\n!?]")

_EN_REF = re.compile(
    r"\b(?:see(?:\s+also)?|refer(?:\s+back)?\s+to|referring\s+to|described\s+in|defined\s+in|"
    r"specified\s+in|set\s+out\s+in|according\s+to|in\s+accordance\s+with|as\s+per|pursuant\s+to)"
    r"\s+(?:the\s+)?"
    r"(?:(?P<kind>chapter|section|sec\.|§|appendix|annex|article|clause)\s*"
    r"(?P<no>\d+(?:\.\d+)*|[A-Z])\b"
    r"(?:\s+(?:of|in)\s+(?:the\s+)?(?P<doc>(?:[A-Z][\w&-]*\s+){0,6}"
    r"(?:Manual|Guide|Guidelines?|Handbook|Policy|Standard|Specification|Procedures?|"
    r"Regulations?)))?"
    r"|[\"“](?P<title>[^\"”\n]{2,80})[\"”])",
    re.IGNORECASE,
)
_EN_KINDS: dict[str, ReferenceKind] = {
    "chapter": "chapter",
    "section": "section",
    "sec.": "section",
    "§": "section",
    "clause": "section",
    "article": "article",
    "appendix": "appendix",
    "annex": "appendix",
}

# 見出しの先頭の番号(見出し側の鍵)。
_HEAD_CHAPTER = re.compile(rf"^第\s*({_NUM})\s*章")
_HEAD_JSECTION = re.compile(rf"^第\s*({_NUM})\s*節")
_HEAD_ARTICLE = re.compile(rf"^第\s*({_NUM})\s*条")
_HEAD_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)(?:\.(?!\d)|(?=\s|$|[^\d.]))")
_HEAD_APPENDIX = re.compile(
    rf"^({_APPENDIX_WORDS}|appendix|annex)\s*(\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?",
    re.IGNORECASE,
)
_HEAD_EN = re.compile(r"^(chapter|section|sec\.|§|article|clause)\s*(\d+(?:\.\d+)*)", re.IGNORECASE)
# 見出しの番号の部分(題名の鍵を作るときに外す)。
_HEAD_NUMBERING = re.compile(
    rf"^(?:第\s*{_NUM}\s*[章節条](?:\s*第\s*{_NUM}\s*[節項])?"
    r"|\d+(?:\.\d+)*\.?"
    rf"|(?:{_APPENDIX_WORDS})\s*(?:\d+|[A-Za-z](?![A-Za-z])|[{_KANJI_NUMBER}]+)?"
    r"|(?:chapter|section|article|appendix|annex)\s*[\dA-Z.]*"
    r"|[(\[]\s*\d+\s*[)\]])\s*[:.、]?\s*",
    re.IGNORECASE,
)
_TITLE_NOISE = re.compile(r"[\s_\-・:、。,.()\[\]「」『』【】]+")


def normalize_text(value: str) -> str:
    """全角の数字・記号を半角にそろえる(NFKC)。"""
    return unicodedata.normalize("NFKC", value or "")


def title_key(value: str) -> str:
    """題名・文書名の比較用の形(NFKC・小文字・空白と記号を除く)。"""
    return _TITLE_NOISE.sub("", normalize_text(value).casefold())


def _number(value: str | None) -> str:
    """番号を比較用の形にする(漢数字は算用数字、英字は大文字、先頭の 0 は外す)。"""
    text = normalize_text(value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return str(int(text))
    if all(char in _KANJI_DIGITS or char in "十百" for char in text):
        return str(_kanji_to_int(text))
    if "." in text and all(part.isdigit() for part in text.split(".")):
        return ".".join(str(int(part)) for part in text.split("."))
    return text.upper()


def _kanji_to_int(text: str) -> int:
    total = 0
    current = 0
    for char in text:
        if char == "百":
            total += (current or 1) * 100
            current = 0
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        else:
            current = current * 10 + _KANJI_DIGITS[char]
    return total + current


@dataclass(frozen=True)
class ReferenceSpec:
    """本文の中の参照の表記 1 件(まだ参照先を決めていない)。"""

    label: str
    kind: ReferenceKind
    # 比較用の鍵(chapter: "3"、section: "3.2"、appendix: "別紙:1"、title: 題名の比較形)。
    key: str
    # 他の文書への参照の文書名。None は同じ文書。
    document_title: str | None = None


@dataclass(frozen=True)
class ReferenceTarget:
    """参照の表記と、決まった参照先(同じ文書の節。決まらなければ None)。"""

    label: str
    kind: ReferenceKind
    key: str
    document_title: str | None = None
    # 参照先の節の見出しの列(「 > 」でつないだ形)。この列とその下の節が参照先。
    section_path: str | None = None

    @property
    def resolved(self) -> bool:
        return self.section_path is not None

    @property
    def spec(self) -> ReferenceSpec:
        return ReferenceSpec(self.label, self.kind, self.key, self.document_title)

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "kind": self.kind,
            "key": self.key,
            "document_title": self.document_title,
            "section_path": self.section_path,
            "resolved": self.resolved,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReferenceTarget | None:
        kind = value.get("kind")
        label = value.get("label")
        if kind not in _KINDS or not isinstance(label, str) or not label:
            return None
        document_title = value.get("document_title")
        section_path = value.get("section_path")
        return cls(
            label=label,
            kind=kind,
            key=str(value.get("key") or ""),
            document_title=document_title if isinstance(document_title, str) else None,
            section_path=section_path if isinstance(section_path, str) and section_path else None,
        )


_KINDS = {"chapter", "section", "jsection", "article", "appendix", "title", "document"}


# ---- 抽出 ----


def extract_references(text: str) -> list[ReferenceSpec]:
    """本文から参照の表記を抜き出す(重複は除き、最大 ``MAX_REFERENCES_PER_CHUNK`` 件)。

    日本語は「参照」「に定める」「のとおり」などの手がかりの語の直前に並んだ表記だけを拾う
    (「第3章では〜を説明する」のような地の文は拾わない)。英語は「see」「refer to」などの後ろの
    「Section 3.2」「Appendix A」「"題名"」を拾う。
    """
    normalized = normalize_text(text)
    found: list[ReferenceSpec] = []
    found.extend(_extract_japanese(normalized))
    found.extend(_extract_english(normalized))
    found.extend(_extract_document_names(normalized))
    unique: dict[tuple[str, str, str], ReferenceSpec] = {}
    for spec in found:
        unique.setdefault((spec.kind, spec.key, spec.document_title or ""), spec)
    return list(unique.values())[:MAX_REFERENCES_PER_CHUNK]


def _extract_japanese(text: str) -> list[ReferenceSpec]:
    specs: list[ReferenceSpec] = []
    for cue in _JA_CUE.finditer(text):
        specs.extend(_specs_before(text, cue.start()))
    for label in _LABEL.finditer(text):
        # 「第2章の共通の保守枠で」: 手がかりの語の代わりに、直後の「の」+ 名詞を手がかりにする。
        if _possessive_label(label) and _POSSESSIVE_TAIL.match(text, label.end()):
            specs.extend(_specs_before(text, label.end()))
    return specs


def _specs_before(text: str, cue_start: int) -> list[ReferenceSpec]:
    """手がかりの位置(``cue_start``)の直前に並んだ参照の表記(同じ文の中だけ)。"""
    start = max(0, cue_start - _LOOKBACK_CHARS)
    boundary = max(
        (match.end() for match in _SENTENCE_END.finditer(text, start, cue_start)),
        default=start,
    )
    segment = text[boundary:cue_start]
    labels = list(_LABEL.finditer(segment))
    accepted: list[re.Match[str]] = []
    cursor = len(segment)
    for match in reversed(labels):
        if not _CONNECTOR.fullmatch(segment[match.end() : cursor]):
            break
        accepted.insert(0, match)
        cursor = match.start()
    if not accepted:
        return []
    specs: list[ReferenceSpec] = []
    document_title = _document_before(segment[:cursor])
    for index, match in enumerate(accepted):
        spec = _label_spec(match)
        if spec is None:
            continue
        following = accepted[index + 1] if index + 1 < len(accepted) else None
        if spec.kind == "title" and _document_like(match):
            if following is not None and re.fullmatch(
                r"\s*(?:の|における|内の|中の)\s*",
                segment[match.end() : following.start()],
            ):
                # 『経費精算マニュアル』の「権限」: 前の『』は後ろの表記の文書名。
                document_title = match.group("title").strip()
                continue
            specs.append(
                ReferenceSpec(match.group(0), "document", "", match.group("title").strip())
            )
            continue
        specs.append(replace(spec, document_title=document_title or None))
    return specs


def _possessive_label(match: re.Match[str]) -> bool:
    """「の」+ 名詞を手がかりにしてよい表記か(「第」の付いた章・節・条、番号付きの別紙、「3.2節」)。

    「第」の無い「12章」(「全12章の構成」)、番号の無い「別紙」、「節」「項」の付かない「3.2」
    (版の番号と区別できない)と、題名(「」)は、手がかりの語があるときだけ拾う。
    """
    label = match.group(0).strip()
    if match.group("chapter"):
        return label.startswith("第")
    if match.group("article") or match.group("jsection"):
        return True
    if match.group("appendix"):
        return bool(match.group("app_no"))
    if match.group("numbered"):
        return label.endswith(("節", "項"))
    return False


def _document_like(match: re.Match[str]) -> bool:
    """『』でくくった題名か、「マニュアル」などで終わる題名なら文書名として扱う。"""
    title = match.group("title") or ""
    return match.group(0).startswith("『") or bool(re.search(rf"{_DOC_SUFFIX}$", title))


def _document_before(prefix: str) -> str:
    """表記の直前の文書名。同じ文書を指す語(本マニュアル)や文書名が無ければ空。"""
    if _SAME_DOC_BEFORE.search(prefix):
        return ""
    match = _DOC_BEFORE.search(prefix)
    if match is None:
        return ""
    if match.group("quoted"):
        return match.group("quoted").strip()
    plain = _DOC_PREFIX_SPLIT.split(match.group("plain"))[-1].strip()
    # 「マニュアルの第3章」のように名前が無いときは、どの文書か分からないので同じ文書とみなす。
    return "" if re.fullmatch(_DOC_SUFFIX, plain) else plain


def _label_spec(match: re.Match[str]) -> ReferenceSpec | None:
    label = match.group(0).strip()
    if match.group("chapter"):
        chapter = _number(match.group("ch"))
        if match.group("ch_sec"):
            return ReferenceSpec(label, "section", f"{chapter}.{_number(match.group('ch_sec'))}")
        return ReferenceSpec(label, "chapter", chapter)
    if match.group("article"):
        return ReferenceSpec(label, "article", _number(match.group("art")))
    if match.group("jsection"):
        return ReferenceSpec(label, "jsection", _number(match.group("jsec")))
    if match.group("appendix"):
        family = _APPENDIX_FAMILIES[match.group("app")]
        return ReferenceSpec(label, "appendix", f"{family}:{_number(match.group('app_no'))}")
    if match.group("numbered"):
        return ReferenceSpec(label, "section", _number(match.group("num")))
    if match.group("quoted"):
        key = title_key(match.group("title"))
        return ReferenceSpec(label, "title", key) if len(key) >= 2 else None
    return None


def _extract_english(text: str) -> list[ReferenceSpec]:
    specs: list[ReferenceSpec] = []
    for match in _EN_REF.finditer(text):
        label = match.group(0).strip()
        if match.group("title"):
            key = title_key(match.group("title"))
            if len(key) >= 2:
                specs.append(ReferenceSpec(label, "title", key))
            continue
        kind = _EN_KINDS[match.group("kind").casefold()]
        number = _number(match.group("no"))
        key = f"付録:{number}" if kind == "appendix" else number
        document = (match.group("doc") or "").strip() or None
        specs.append(ReferenceSpec(label, kind, key, document))
    return specs


# ---- 文書名の参照(#1400) ----

# 文書名の直後に来る参照の述語(「定期保守計画で確かめます」「組織規程に従う」)。PDF の抽出で
# 文字の間に空白が入ることがある(「確かめま す」)ので、文字の間の空白を許す。
_DOCUMENT_NAME_PREDICATES = (
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
_DOCUMENT_NAME_PREDICATE = re.compile(
    "|".join(
        r"\s*".join(re.escape(char) for char in predicate)
        for predicate in sorted(_DOCUMENT_NAME_PREDICATES, key=len, reverse=True)
    )
)
# 文書名に使う文字(漢字・カタカナ・英数字・「・」)。ひらがな・句読点・括弧で名前が切れる
# (「サンプル社の定期保守計画」は「定期保守計画」)。文字の間の空白 1 つは許す(PDF の抽出)。
_NAME_CHAR = r"[々〆ヶ一-鿿ァ-ヺー・A-Za-z0-9]"
_DOCUMENT_NAME_BEFORE = re.compile(
    rf"(?P<open>『)?(?P<name>(?:{_NAME_CHAR}(?:\s(?={_NAME_CHAR}))?){{2,40}})(?(open)』)\s*$"
)
# 述語が「で〜」のとき、同じ文の前の「〈文書名〉で、」も同じ述語の参照として拾う
# (「担当部署はシステム台帳で、承認者は組織規程で確かめます」)。
_DOCUMENT_NAME_COORDINATED = re.compile(
    rf"(?<!{_NAME_CHAR})(?P<open>『)?(?P<name>(?:{_NAME_CHAR}(?:\s(?={_NAME_CHAR}))?){{2,40}})"
    r"(?(open)』)\s*で\s*[、,]"
)
# 文書名の最短の文字数(``title_key`` の形で数える)。「台帳」「規程」のような短い語は一般語と区別
# できないので拾わない。
MIN_DOCUMENT_NAME_CHARS = 4
# 文書名として扱わない一般語(``title_key`` の形)。照合は検索範囲の文書との完全一致だけだが、
# 一般語と同じ名前の文書があっても、地の文の一般語を参照として扱わない。
_GENERIC_DOCUMENT_NAMES = frozenset(
    _TITLE_NOISE.sub("", unicodedata.normalize("NFKC", word).casefold())
    for word in (
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
)
# 同じ文書・前に出た文書を指す語(「本手順書」「同規程」「上記の計画」)。
_DEMONSTRATIVE_NAME = re.compile(
    rf"^(?:本|当|同|上記|下記|前記|後記|各|別)\s*(?:{_DOC_SUFFIX}|書|文書|資料|規程|計画書?|台帳|表)$"
)


def document_name_references(text: str) -> list[ReferenceSpec]:
    """本文の中の、文書名で別の文書を指す参照(``kind="document"``。#1400)。

    「時間帯は定期保守計画で確かめます」「担当部署はシステム台帳で、承認者は組織規程で確かめます」
    「『経費精算マニュアル』に従う」の文書名を拾う。どの文書かは決めない(回答のときに検索範囲の
    文書のタイトル・ファイル名で照合する)。``extract_references`` も同じ参照を返す。
    """
    return _extract_document_names(normalize_text(text))


def _extract_document_names(text: str) -> list[ReferenceSpec]:
    specs: list[ReferenceSpec] = []
    for predicate in _DOCUMENT_NAME_PREDICATE.finditer(text):
        start = max(0, predicate.start() - _LOOKBACK_CHARS)
        boundary = max(
            (match.end() for match in _SENTENCE_END.finditer(text, start, predicate.start())),
            default=start,
        )
        segment = text[boundary : predicate.start()]
        match = _DOCUMENT_NAME_BEFORE.search(segment)
        if match is None:
            continue
        names = [match]
        if predicate.group(0).startswith("で"):
            names[:0] = list(_DOCUMENT_NAME_COORDINATED.finditer(segment[: match.start()]))
        for found in names:
            spec = _document_name_spec(found)
            if spec is not None:
                specs.append(spec)
    return specs


def _document_name_spec(match: re.Match[str]) -> ReferenceSpec | None:
    raw = match.group("name").strip()
    # 日本語の文字の間の空白(PDF の抽出で入る)は外す。英字の語の間の空白は残す。
    name = re.sub(r"(?<=[^\x00-\x7f])\s+|\s+(?=[^\x00-\x7f])", "", raw)
    key = title_key(name)
    if len(key) < MIN_DOCUMENT_NAME_CHARS or key in _GENERIC_DOCUMENT_NAMES:
        return None
    if re.fullmatch(_DOC_SUFFIX, name) or _DEMONSTRATIVE_NAME.match(name):
        return None
    # 「第2章」「3.2節」「別紙1」などの表記を含むものは、章・節の参照(別の規則)に任せる。
    if any(not label.group("quoted") for label in _LABEL.finditer(name)):
        return None
    label = f"『{name}』" if match.group("open") else name
    return ReferenceSpec(label, "document", "", name)


# 文書のタイトルの別名を作るときに外す、年度・版の表記(「2026年度」「第2版」「v1.2」「改訂版」)。
_EDITION = (
    r"(?:(?:19|20)\d{2}\s*(?:年度?)?|(?:令和|平成)\s*(?:\d+|元)\s*年度?|第?\s*\d+\s*版|[vV]\.?\s*\d+(?:\.\d+)*"
    r"|ver\.?\s*\d+(?:\.\d+)*|(?:改訂|改定|最新|初|新|旧)版|\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|\d{6,8})"
)
_TRAILING_EDITION = re.compile(rf"(?:[\s_\-]*{_EDITION})+\s*$", re.IGNORECASE)
_LEADING_EDITION = re.compile(rf"^\s*(?:{_EDITION}[\s_\-]*)+", re.IGNORECASE)
# 括弧書き(「組織規程(システムの担当と承認者)」の「(…)」)。
_PARENTHETICAL = re.compile(r"[(\[【][^()\[\]【】]*[)\]】]")
# 先頭の会社名(「サンプル社 定期保守計画」の「サンプル社 」)。
_COMPANY_PREFIX = re.compile(r"^\S{1,20}?社\s+")
_FILE_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def title_heading(heading: str) -> bool:
    """見出しの列の先頭の見出しが文書のタイトルとして使えるか(章・節・条などの番号が無い)。"""
    text = normalize_text(heading).strip()
    return bool(text) and _HEAD_NUMBERING.match(text) is None


def document_name_aliases(value: str) -> set[str]:
    """文書のタイトル・ファイル名の、照合に使う形(``title_key``)の集合。

    そのままの形に加えて、括弧書き・年度や版の表記・先頭の会社名・拡張子を外した形を別名にする
    (「定期保守計画 2026年度」→「定期保守計画」、
    「組織規程(システムの担当と承認者)」→「組織規程」)。
    """
    text = normalize_text(value).strip()
    bases = {text, _FILE_EXTENSION.sub("", text)}
    variants: set[str] = set()
    for base in bases:
        for item in {base, _PARENTHETICAL.sub(" ", base)}:
            for stripped in (
                item,
                _TRAILING_EDITION.sub("", _LEADING_EDITION.sub("", item)),
                _COMPANY_PREFIX.sub("", item),
                _TRAILING_EDITION.sub("", _LEADING_EDITION.sub("", _COMPANY_PREFIX.sub("", item))),
            ):
                variants.add(stripped.strip())
    return {key for key in (title_key(item) for item in variants) if len(key) >= 2}


def document_name_match(name: str, file_name: str, headings: Iterable[str] = ()) -> int | None:
    """参照の文書名が文書に当たるか(当たり方の順位。小さいほど確か。当たらなければ None)。

    - 0: 文書のタイトル(番号の無い先頭の見出し)・ファイル名、またはその別名と一致する。
    - 1: ファイル名(拡張子を除く)が文書名を含む(#1280 の他の文書への参照と同じ規則)。

    NFKC・大文字と小文字・空白と区切りの記号の違いは無視する。タイトルの部分一致は使わない
    (「保守計画」で「定期保守計画」を指さない)。
    """
    key = title_key(name)
    if len(key) < 2:
        return None
    aliases = document_name_aliases(file_name)
    for heading in headings:
        if title_heading(heading):
            aliases |= document_name_aliases(heading)
    if key in aliases:
        return 0
    stem = title_key(_FILE_EXTENSION.sub("", normalize_text(file_name)))
    if len(stem) >= 2 and key in stem:
        return 1
    return None


# ---- 解決 ----


def split_section_path(value: object) -> tuple[str, ...]:
    """保存した ``section_path``(「 > 」でつないだ文字列か list)を見出しの列にする。"""
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    if not isinstance(value, str):
        return ()
    return tuple(part.strip() for part in value.split(SECTION_PATH_SEPARATOR) if part.strip())


def heading_keys(heading: str) -> set[tuple[str, str]]:
    """見出し 1 つが参照で呼ばれうる鍵((種類, 鍵) の集合)。"""
    text = normalize_text(heading).strip()
    keys: set[tuple[str, str]] = set()
    if match := _HEAD_CHAPTER.match(text):
        keys.add(("chapter", _number(match.group(1))))
    if match := _HEAD_JSECTION.match(text):
        keys.add(("jsection", _number(match.group(1))))
    if match := _HEAD_ARTICLE.match(text):
        keys.add(("article", _number(match.group(1))))
    if match := _HEAD_NUMBER.match(text):
        number = _number(match.group(1))
        keys.add(("section", number))
        if "." not in number:
            # 「3 経費の承認」は「第3章」とも呼ばれる(章の見出しが無い文書の代わり)。
            keys.add(("chapter_number", number))
    if match := _HEAD_APPENDIX.match(text):
        family = _APPENDIX_FAMILIES[match.group(1).casefold()]
        number = _number(match.group(2))
        keys.add(("appendix", f"{family}:{number}"))
        keys.add(("appendix", f"{family}:"))
    if match := _HEAD_EN.match(text):
        word = match.group(1).casefold()
        kind = _EN_KINDS.get(word, "section")
        keys.add((kind, _number(match.group(2))))
    title = _HEAD_NUMBERING.sub("", text, count=1)
    for value in {title, text}:
        key = title_key(value)
        if len(key) >= 2:
            keys.add(("title", key))
    return keys


@dataclass(frozen=True)
class _Entry:
    path: tuple[str, ...]
    order: int
    keys: frozenset[tuple[str, str]]


class SectionIndex:
    """文書の見出しの列の一覧から、参照の表記に合う節を引く。"""

    def __init__(self, section_paths: Iterable[object]) -> None:
        entries: dict[tuple[str, ...], _Entry] = {}
        for value in section_paths:
            path = split_section_path(value)
            for depth in range(1, len(path) + 1):
                prefix = path[:depth]
                if prefix not in entries:
                    entries[prefix] = _Entry(
                        prefix, len(entries), frozenset(heading_keys(prefix[-1]))
                    )
        self._entries = list(entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def resolve(self, spec: ReferenceSpec, *, citing_path: Sequence[str] = ()) -> str | None:
        """参照先の節の見出しの列(「 > 」でつないだ形)。合う節が無い・曖昧なら None。

        候補が複数あるときは、参照元の節と共通の祖先が長いもの(同じ章の中の「第2節」など)、
        浅いもの、文書の前の方のものの順に選ぶ。
        """
        citing = tuple(citing_path)
        candidates = self._candidates(spec)
        if not candidates:
            return None
        best = min(
            candidates,
            key=lambda entry: (-_common_prefix(entry.path, citing), len(entry.path), entry.order),
        )
        return SECTION_PATH_SEPARATOR.join(best.path)

    def _candidates(self, spec: ReferenceSpec) -> list[_Entry]:
        for wanted in _wanted_keys(spec):
            found = [entry for entry in self._entries if wanted in entry.keys]
            if found:
                return found
        if spec.kind == "section" and "." in spec.key:
            # 「3.2」は「第3章」の下の「第2節」とも書かれる。
            chapter, _, rest = spec.key.partition(".")
            parents = self._candidates(ReferenceSpec(spec.label, "chapter", chapter))
            if parents and "." not in rest:
                return [
                    entry
                    for entry in self._entries
                    if ("jsection", rest) in entry.keys
                    and any(entry.path[: len(parent.path)] == parent.path for parent in parents)
                ]
        if spec.kind == "title" and len(spec.key) >= 2:
            # 題名が見出しの一部にだけ含まれるとき(「権限」→「4.1 権限の設定」)。曖昧なら決めない。
            partial = [
                entry
                for entry in self._entries
                if any(kind == "title" and spec.key in key for kind, key in entry.keys)
            ]
            distinct = {entry.path[-1] for entry in partial}
            if 0 < len(distinct) <= 2:
                return partial
        return []


def _wanted_keys(spec: ReferenceSpec) -> list[tuple[str, str]]:
    if spec.kind == "chapter":
        return [("chapter", spec.key), ("chapter_number", spec.key)]
    if spec.kind in {"section", "jsection", "article", "appendix", "title"}:
        return [(spec.kind, spec.key)]
    return []


def _common_prefix(left: Sequence[str], right: Sequence[str]) -> int:
    count = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        count += 1
    return count


def section_path_within(chunk_path: object, target: str) -> bool:
    """chunk の見出しの列が参照先の節(またはその下の節)か。"""
    path = split_section_path(chunk_path)
    target_path = split_section_path(target)
    return bool(target_path) and path[: len(target_path)] == target_path


def same_document_title(document_title: str, file_name: str) -> bool:
    """参照の文書名が文書名(拡張子を除く)に当たるか(どちらかがもう一方を含む)。"""
    title = title_key(document_title)
    stem = title_key(re.sub(r"\.[A-Za-z0-9]{1,5}$", "", normalize_text(file_name)))
    if len(title) < 2 or len(stem) < 2:
        return False
    return title in stem or stem in title


# ---- 取込 ----


def annotate_cross_references(chunks: Sequence[Any], *, document_title: str = "") -> int:
    """chunk の metadata に参照先(``reference_targets_json``)を足し、足した chunk 数を返す。

    参照先は同じ文書の chunk 群の見出しの列から決める。自分の節・祖先の節への参照は残さない。
    参照の文書名が自分の文書名なら同じ文書への参照として解決する。他の文書への参照は未解決の
    まま残す(回答のときに検索範囲から解決する)。RAPTOR の要約 chunk には付けない。
    """
    index = SectionIndex(chunk.metadata.get("section_path") for chunk in chunks)
    annotated = 0
    for chunk in chunks:
        metadata = chunk.metadata
        if metadata.get("raptor_summary"):
            continue
        specs = extract_references(chunk.text)
        if not specs:
            continue
        targets = resolve_reference_specs(
            specs,
            index,
            citing_path=split_section_path(metadata.get("section_path")),
            document_title=document_title,
        )
        # 参照が自分の節・祖先の節だけでも空の列を残す(取込で解決済みの印。回答のときに
        # 本文から抜き出し直さない。#1382)。
        metadata[REFERENCE_TARGETS_KEY] = json.dumps(
            [target.to_dict() for target in targets], ensure_ascii=False
        )
        if targets:
            annotated += 1
    return annotated


def resolve_reference_specs(
    specs: Sequence[ReferenceSpec],
    index: SectionIndex,
    *,
    citing_path: Sequence[str] = (),
    document_title: str = "",
) -> list[ReferenceTarget]:
    """参照の表記を参照先にする(取込と、取込で解決していない chunk の回答のときに使う)。

    同じ文書の参照は ``index``(その文書の見出しの列)で節を決め、自分の節・祖先の節への参照は
    残さない。参照の文書名が ``document_title``(参照元の文書名)に当たれば同じ文書として扱う。
    他の文書への参照は文書名と節の表記だけを残す(回答のときに検索範囲から解決する)。
    """
    citing = tuple(citing_path)
    targets: list[ReferenceTarget] = []
    for spec in specs:
        if (
            spec.document_title
            and document_title
            and same_document_title(spec.document_title, document_title)
        ):
            if spec.kind == "document":
                continue
            spec = replace(spec, document_title=None)
        if spec.document_title:
            targets.append(ReferenceTarget(spec.label, spec.kind, spec.key, spec.document_title))
            continue
        if spec.kind == "document":
            continue
        path = index.resolve(spec, citing_path=citing)
        if path is not None and citing[: len(split_section_path(path))] == split_section_path(path):
            # 自分の節・祖先の節への参照は、根拠を足さないので残さない。
            continue
        targets.append(ReferenceTarget(spec.label, spec.kind, spec.key, None, path))
    return targets


def reference_targets(metadata: Mapping[str, object]) -> list[ReferenceTarget]:
    """chunk の metadata に保存した参照先を読む(壊れた値は飛ばす)。"""
    raw = metadata.get(REFERENCE_TARGETS_KEY)
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    targets = [ReferenceTarget.from_dict(item) for item in raw if isinstance(item, Mapping)]
    return [target for target in targets if target is not None]


__all__ = [
    "MAX_REFERENCES_PER_CHUNK",
    "MIN_DOCUMENT_NAME_CHARS",
    "REFERENCE_FROM_KEY",
    "REFERENCE_LABEL_KEY",
    "REFERENCE_TARGETS_KEY",
    "ReferenceSpec",
    "ReferenceTarget",
    "SectionIndex",
    "annotate_cross_references",
    "document_name_aliases",
    "document_name_match",
    "document_name_references",
    "extract_references",
    "heading_keys",
    "reference_targets",
    "resolve_reference_specs",
    "same_document_title",
    "section_path_within",
    "split_section_path",
    "title_heading",
    "title_key",
]
