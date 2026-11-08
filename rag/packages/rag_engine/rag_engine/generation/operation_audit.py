"""回答文と引用本文に対する決定的な判定。LLM を呼ばず、入力を変更しない。"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

from rag_parser_core.language import LanguagePack, per_pack
from rag_parser_core.language.text import alternation

from rag_engine.generation.check_cues import check_cues
from rag_engine.parsing.structural_text import is_table_rule as is_table_rule
from rag_engine.retrieval.task_contract import generic_target_words

_QUOTE_ONLY_LINE = re.compile(r"\s*(?:・|[0-9]+[.)]\s*)?「.*」\s*")
# 中の「。」で文を分けない括弧（開きと閉じ。#1455）。
_BRACKET_PAIRS = {"「": "」", "『": "』", "（": "）", "(": ")", "【": "】"}
_CLOSING_BRACKETS = frozenset(_BRACKET_PAIRS.values())
# 前の文に付ける断片（太字・強調の印と閉じ括弧・句点だけ。#1455）。
_MARKS_ONLY = re.compile(r"[\s*_`」』）)】。]*")


def sentence_ranges(line: str, *, ends: str = "。") -> list[tuple[int, int]]:
    """1 行を文に分けた範囲（開始・終了の位置。前後の空白を含む）。

    「。」で文を分けるが、開いた括弧（「」『』（）()【】）の中の「。」では分けない（括弧の深さを数える。
    #1455）。閉じない括弧は行の終わりまで続くとみなし、行の終わりで切る。開いていない閉じ括弧は数えない。
    文の区切りの「。」の直後の閉じ括弧は前の文に含め、太字の印（`**`）・閉じ括弧だけの断片は前の文に付ける。
    Agent の ``answer_passages.sentence_ranges`` と同じ規則（両方のテストが
    platform/contracts/answer-passages/sentence-splits.json の同じ事例で確かめる）。``ends`` は文の終わりの
    文字（既定は回答の段落の「。」。標準回答の単位は「！」「？」でも分ける。#3575）。
    """
    ranges: list[tuple[int, int]] = []
    expected: list[str] = []
    start = index = 0
    while index < len(line):
        char = line[index]
        if char in _BRACKET_PAIRS:
            expected.append(_BRACKET_PAIRS[char])
        elif char in _CLOSING_BRACKETS and char in expected:
            # 対応する開きの括弧まで閉じる（その間の閉じていない括弧も閉じたとみなす）。
            while expected.pop() != char:
                pass
        elif char in ends and not expected:
            end = index + 1
            # 続く閉じ括弧と文の終わりの文字（「！？」「。。」）も前の文に含める。
            while end < len(line) and (line[end] in _CLOSING_BRACKETS or line[end] in ends):
                end += 1
            ranges.append((start, end))
            start = index = end
            continue
        index += 1
    if start < len(line):
        ranges.append((start, len(line)))
    merged: list[tuple[int, int]] = []
    for begin, end in ranges:
        if merged and _MARKS_ONLY.fullmatch(line[begin:end]):
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    return merged


_PASSAGE_CHARS = 600


def answer_passage_ranges(text: str) -> list[tuple[str, int, int]]:
    """``answer_passages`` の段落の ID と、``text`` の中の位置（開始・終了。``text[start:end]`` が段落の原文）。

    MCP の ``rag_validate_answer`` は段落の判定にこの位置を付けて返し、呼び出し側は段落の分け方を写さずに
    位置で段落を特定する（#3240）。
    """
    return text_unit_ranges(text, prefix="A", whole_line=_QUOTE_ONLY_LINE)


# 1 行を 1 つの単位にする表の行（Markdown の表は「|」で始まる。セルの中の句点で行を切らない）。
_STANDARD_TABLE_ROW = re.compile(r"\s*\|.*")


def standard_answer_unit_ranges(text: str) -> list[tuple[str, int, int]]:
    """標準回答の単位（品質評価の比較範囲の固定でモデルが ID で指す）の ID（``S1``…）と位置（#3575）。

    回答の段落（``answer_passage_ranges``）と同じ分け方（行 → 括弧の外の文の終わり。空行は除く）に、
    「！」「？」でも文を分け、表の行と「…」だけの行は 1 行を 1 つの単位にする。箇条書きの項目は行なので
    前後の項目と混ざらない。
    """
    return text_unit_ranges(text, prefix="S", ends="。！？", whole_line=_STANDARD_WHOLE_LINE)


_STANDARD_WHOLE_LINE = re.compile(f"(?:{_QUOTE_ONLY_LINE.pattern})|(?:{_STANDARD_TABLE_ROW.pattern})")


def text_unit_ranges(text: str, *, prefix: str, ends: str = "。", whole_line: re.Pattern[str]) -> list[tuple[str, int, int]]:
    """本文を行・文に分けた単位の ID（``prefix`` と 1 始まりの番号）と位置（``text[start:end]`` が単位の原文）。

    行ごとに、``whole_line`` に合う行は 1 つの単位、それ以外は ``sentence_ranges`` で文に分ける。前後の空白を
    除き、空の行・断片は単位にしない。長い単位は ``_PASSAGE_CHARS`` 字ごとの連続した範囲に分ける。
    """
    result: list[tuple[str, int, int]] = []
    offset = 0
    for line in text.split("\n"):
        ranges = [(0, len(line))] if whole_line.fullmatch(line) else sentence_ranges(line, ends=ends)
        for begin, end in ranges:
            piece = line[begin:end]
            value = piece.strip()
            if not value:
                continue
            lead = begin + len(piece) - len(piece.lstrip())
            for start in range(0, len(value), _PASSAGE_CHARS):
                stop = min(start + _PASSAGE_CHARS, len(value))
                result.append((f"{prefix}{len(result) + 1}", offset + lead + start, offset + lead + stop))
        offset += len(line) + 1
    return result


def answer_passages(text: str) -> list[dict[str, str]]:
    """改写せず文・改行で区切る。長文も600文字の連続範囲として保持する。

    句点直後の閉じ括弧は同じ文に含める。括弧の中の「。」では切らない（``sentence_ranges``。#1455）。
    括弧の途中で切ると、検証で前の半分を外したときに閉じ括弧の残る断片が本文に残る。行全体が1つの
    「…」の引用（原文のみ提示の行。表の引用は複数の文を含む）は、引用の途中で切らずに1段落として扱う。
    段落の位置は ``answer_passage_ranges``。
    """
    return [{"id": passage_id, "text": text[start:end]} for passage_id, start, end in answer_passage_ranges(text)]


# ---- 段落の形の判定（#4668）。語の意味は判定せず、記号・番号・ファイル名・リンクなどの形だけを見る。見出し・前置き・
# 文書名・利用者への質問・作業の経過のような意味の判定はここに置かない（組み立てた回答は ``RenderedAnswer.non_claim_lines``、
# 外部の回答は監査モデルの構造化出力 not_a_claim が決める）。

# Markdown の強調の印と、箇条書き・番号の印（形だけを外して本体を見る）。
_EMPHASIS = re.compile(r"\*{1,2}|__|`")
_LIST_MARK = re.compile(r"^(?:[-*+・•]\s*|[0-9０-９]{1,3}[.)．）]\s*)")
# 根拠の ID の括弧（システムが渡す ID の書き方。【証拠 ID: 03ac…:1】・〔証拠 ID: 07e8…:5〕・【証拠1】・【evidence_id: …】）。
_EVIDENCE_REF = re.compile(
    r"[【〔［\[]\s*(?:証拠|根拠|出典|evidence|source)(?:\s*_?(?:ID|id))?\s*[:：]?[\s0-9A-Za-z:_\-,，、…①-⑳]{0,200}[】〕］\]]", re.I
)
_LINK = re.compile(r"\[[^\]\n]{1,200}\]\([^)\s]{1,500}\)|https?://\S+")
_FILE_NAME = re.compile(r"[^\s「」『』（）()【】\[\]、,，|]+\.(?:pdf|docx?|xlsx?|pptx?|md|txt|html?|csv)\b", re.I)
_PAGE_NUMBER = re.compile(r"(?:ページ|頁|p\.|page)\s*[0-9０-９]+", re.I)
_REFERENCE_SEPARATORS = re.compile(r"[|\s→>＞\-–—‑―、,，:：/／()（）\[\]【】〔〕「」『』]+")
# Markdown の見出しの行と、行全体が 1 つの強調・【…】の行（出典の組の区切りにだけ使う。主張かどうかは決めない）。
_MARKUP_LINE = re.compile(r"\s*(?:#{1,6}\s+.+|【[^】\n]+】|\*\*[^*\n]+\*\*[:：]?)\s*")
_MD_TABLE_ROW = re.compile(r"\|.*\|")


def is_markup_line(text: str) -> bool:
    """Markdown の見出し・強調だけの行・【…】の行の形か（形だけ。出典の組の区切りに使い、主張かどうかは決めない）。"""
    return bool(_MARKUP_LINE.fullmatch(text))


def is_source_reference_form(text: str) -> bool:
    """根拠の ID・リンク・ファイル名・頁の番号と区切りの記号だけの行か（語の意味は見ない）。

    文書名や章節の名前を語で判定しない（それらは ``non_claim_lines`` か監査モデルが決める）。
    """
    value = _EMPHASIS.sub("", _LIST_MARK.sub("", text.strip(), count=1)).strip()
    if not value:
        return False
    rest, ids = _EVIDENCE_REF.subn(" ", value)
    rest, links = _LINK.subn(" ", rest)
    rest, files = _FILE_NAME.subn(" ", rest)
    rest, pages = _PAGE_NUMBER.subn(" ", rest)
    return bool(ids or links or files or pages) and not _REFERENCE_SEPARATORS.sub("", rest)


def table_header_lines(text: str) -> set[str]:
    """Markdown の表の見出しの行（表の先頭の行で、直後が区切りの行）。形だけで決め、表の本文の行は主張のまま (#1317)。"""
    lines = [line.strip() for line in text.split("\n")]
    return {
        line
        for previous, line, following in zip(["", *lines], lines, lines[1:], strict=False)
        if _MD_TABLE_ROW.fullmatch(line)
        and not _MD_TABLE_ROW.fullmatch(previous)
        and is_table_rule(following)
        and any(_EMPHASIS.sub("", cell).strip() for cell in line.strip("|").split("|"))
    }


def is_non_claim_form(text: str, *, table_headers: Collection[str] = ()) -> bool:
    """形だけで主張ではないと決まる段落か（表の区切りと見出しの行・根拠の ID などの参照だけの行。#1317 / #4668）。"""
    return is_table_rule(text) or text.strip() in table_headers or is_source_reference_form(text)


@dataclass(frozen=True)
class _OperationCues:
    """言語資源（operation_audit_cues。#2181 段 5）から組み立てた、操作の指示の判定。"""

    instruction: re.Pattern[str]


def _build_operation_cues(pack: LanguagePack) -> _OperationCues:
    def alt(key: str) -> str:
        return alternation(pack.words("operation_audit_cues", key))

    english = pack.words("operation_audit_cues", "english_instruction_words")
    deletion = alternation(pack.words("answer_check_cues", "deletion_words"))
    return _OperationCues(
        instruction=re.compile(
            rf"{alt('instruction_stems')}{alt('operation_instruction_endings')}|{alt('instruction_phrases')}"
            rf"|{alt('conditional_operation_verbs')}{alt('conditional_operation_endings')}"
            rf"|{alt('action_verbs')}{alt('action_endings')}"
            rf"|{alt('control_words')}.{{0,30}}{alt('control_actions')}"
            rf"|{deletion}{alt('deletion_endings')}" + (rf"|\b{alternation(english)}\b" if english else ""),
            re.I,
        ),
    )


_operation_cues = per_pack(_build_operation_cues)


def is_operation_instruction(text: str) -> bool:
    """具体的な操作指示を確認留保として無引用で通さないための補助判定（語は言語資源）。"""
    return bool(_operation_cues().instruction.search(text))


# ボタン名とみなす最短の字数。1 字（「各ボタン」「同ボタン」）は指示語・接頭語で、ボタンの名前ではないことが多い。
_MIN_BUTTON_NAME_CHARS = 2
# 名前に付いたファンクションキー（「F5実行ボタン」）。キーの書き方（「(F5)実行」）は原文と違い得るので、名前から除いて比べる。
_BUTTON_FUNCTION_KEY = re.compile(r"(?<![A-Za-z0-9])F(?:1[0-2]|[1-9])(?![0-9])")


def button_names_absent(text: str, cited_texts: Collection[str], question: str = "") -> list[str]:
    """説明が案内したボタン名（「実行ボタン」の「実行」）のうち、引いた根拠のどの本文にも名前が無いもの（#3822）。

    説明が引いた原文より細かい操作（「入力して実行ボタンを押す」）を、画面を開く手順だけの原文で裏付けた形を見つける。
    名前の文字列そのものが、引いたすべての根拠に無いことだけを見る。書き方の違い（「実行のボタン」「"実行"ボタン」）
    では判定せず、名前が原文にあれば、その操作を原文が支えるか（意味の支持）は判定しない。質問にある名前、総称語、
    1 字の名前は対象にしない。呼ぶ側は説明・根拠・質問を同じ文字の形（``check_view``）にそろえて渡す。
    """

    def compact(value: str) -> str:
        return _BUTTON_FUNCTION_KEY.sub("", re.sub(r"\s+", "", value))

    body, asked, generic = compact("\n".join(cited_texts)), compact(question), set(generic_target_words())
    names = (compact(name) for name in check_cues().button.findall(re.sub(r"\s+", "", text)))
    return [
        name
        for name in dict.fromkeys(names)
        if len(name) >= _MIN_BUTTON_NAME_CHARS and name not in generic and name not in body and name not in asked
    ]


# 総称語だけの削除対象。「データを削除」は資料の具体名（「履歴情報」「行」）と一致しないので対象にしない (#696)。
# 操作対象の検査（#700）と同じ語彙（task_contract.generic_target_words。言語資源）を使う。
