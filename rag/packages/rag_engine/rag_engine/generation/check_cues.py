"""回答の検査の手がかりの語（言語資源の answer_check_cues。#2181 段 5）。

確定の操作・限定の語や、手順の番号・引用の区切りを見分ける語は言語資源のデータで、
ここは語を正規表現に組み立てるだけを持つ（言語資源の hash ごとに 1 回だけ）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rag_parser_core.language import FIELD_SPECS, LanguagePack, per_pack
from rag_parser_core.language.markers import marker_patterns
from rag_parser_core.language.text import alternation, range_class

_SECTION = "answer_check_cues"


@dataclass(frozen=True)
class CheckCues:
    """回答の検査の語と、語から作った正規表現。"""

    words: dict[str, tuple[str, ...]]
    alt: dict[str, str]
    # 文字の種類（漢字・カタカナ、英数字を足したもの、ひらがなも足したもの）。
    kana_kanji: str
    any_word_chars: str
    completion: re.Pattern[str]
    constraint: re.Pattern[str]
    ignored_quote_chars: frozenset[str]
    trailing_punctuation: str
    step_split: re.Pattern[str]
    # 行の先頭で ``match`` する手順の番号（①・⑴・(1)・（１）・1.・１．）。小数（1.5）には当たらない。
    step_line_start: re.Pattern[str]
    ellipsis_split: re.Pattern[str]
    bare_label: re.Pattern[str]
    sentence_line: re.Pattern[str]
    name_value_pair: re.Pattern[str]
    multi_condition_modes: tuple[re.Pattern[str], re.Pattern[str]]
    quoted_name: re.Pattern[str]
    bare_title_marks: re.Pattern[str]
    # 番号付きの機能見出し（「〜-(1)…」。線の字は evidence_cues）。
    function_label: re.Pattern[str]

    def has(self, key: str, text: str) -> bool:
        """語の一覧のどれかが text にあるか。"""
        return bool(re.search(self.alt[key], text))

    def chars(self, *keys: str) -> str:
        """語の一覧の字を、文字の集合の中に書ける形でつないだもの。"""
        return re.escape("".join(char for key in keys for char in self.words[key]))


def _body(items: tuple[str, ...]) -> str:
    """1 文字か「最初-最後」の範囲の並びを、文字の集合の中身（[ ] の中）にする。"""
    parts = []
    for item in items:
        if len(item) == 1:
            parts.append(re.escape(item))
        elif len(item) == 3 and item[1] == "-" and item[0] <= item[2]:
            parts.append(f"{re.escape(item[0])}-{re.escape(item[2])}")
    return "".join(parts)


def _build(pack: LanguagePack) -> CheckCues:
    words = {spec.key: pack.words(_SECTION, spec.key) for spec in FIELD_SPECS if spec.section == _SECTION}
    alt = {key: alternation(values) for key, values in words.items()}

    def chars(*keys: str) -> str:
        return re.escape("".join(char for key in keys for char in words[key]))

    kanji_items = pack.words("search_terms", "kanji_chars")
    katakana_items = pack.words("search_terms", "katakana_chars")
    hiragana_items = pack.words("search_terms", "hiragana_chars")
    kana_kanji = range_class(kanji_items + katakana_items)
    any_body = _body((*kanji_items, *hiragana_items, *katakana_items, "A-Z", "a-z", "0-9"))
    any_word_chars = f"[{any_body}]"
    kanji = _body(kanji_items)
    markers = marker_patterns(pack)
    quotes = pack.words("symbols", "quote_brackets")
    first_quote = quotes[0] if quotes else "「」"
    quote_opens = re.escape("".join(pair[0] for pair in quotes))
    quote_closes = re.escape("".join(pair[1] for pair in quotes))
    sentence = re.escape("".join(pack.words("question_cues", "sentence_split_chars")))
    colon = re.escape("".join(pack.words("evidence_cues", "label_colon_chars")))
    item_bullets = re.escape("".join(pack.words("evidence_cues", "item_bullet_chars")))
    notes = re.escape("".join(pack.words("evidence_cues", "note_mark_chars")))
    heading_bullets = re.escape("".join(pack.words("evidence_cues", "heading_bullet_chars")))
    circled = range_class(pack.words("evidence_cues", "step_circled_numbers"))
    number_open = markers.number_open
    number_close = markers.number_close
    constraint = rf"{alt['constraint_words']}|{alt['condition_preceders']}{alt['condition_nouns']}{alt['condition_followers']}"
    dashes = re.escape("".join(pack.words("evidence_cues", "function_number_dashes")))
    return CheckCues(
        words=words,
        alt=alt,
        kana_kanji=kana_kanji,
        any_word_chars=any_word_chars,
        completion=re.compile(
            rf"({alt['completion_verbs']})[{chars('closing_quote_chars')}]?{alt['button_words']}?"
            rf"{alternation(pack.words('question_cues', 'object_particles'))}?"
            rf"(?:{alt['press_words']}[^{sentence}/\n]{{0,16}}?|{alt['completion_connectors']})"
            rf"[^{sentence}/\n]{{0,16}}?{alt['completion_words']}"
        ),
        constraint=re.compile(constraint),
        ignored_quote_chars=frozenset("".join(words["ignored_quote_chars"])),
        trailing_punctuation="".join(words["quote_sentence_end_chars"]),
        step_split=re.compile(
            rf"(?={circled})|(?={number_open}[0-9０-９]{{1,2}}{number_close})"
            rf"|(?<=[{sentence}\s])(?=[0-9０-９]{{1,2}}[{chars('step_split_closers')}])"
        ),
        step_line_start=re.compile(
            rf"[ \t\u3000]*(?:{circled}|{markers.circled}|{number_open}[0-9０-９]{{1,2}}{number_close}"
            rf"|[0-9０-９]{{1,2}}[{chars('step_split_closers')}](?![0-9０-９]))"
        ),
        ellipsis_split=re.compile(
            r"\s*(?:"
            + "|".join(f"{re.escape(char)}{{2,}}" for char in words["repeated_ellipsis_chars"])
            + (f"|[{chars('ellipsis_chars')}]+" if words["ellipsis_chars"] else "")
            + r")\s*"
        ),
        bare_label=re.compile(rf"[{kanji}A-Za-z0-9{chars('bare_label_chars')}]{{1,12}}"),
        sentence_line=re.compile(rf"{alt['sentence_line_endings']}[{chars('sentence_line_closers')}]?$"),
        name_value_pair=re.compile(rf"[^\s/={quote_opens}{quote_closes}]+\s*=\s*[^\s/{quote_closes}]+"),
        multi_condition_modes=(
            re.compile(rf"(?m)^([^\n{sentence}{colon}{heading_bullets}{item_bullets}{notes}]{{2,32}})\n(?=[{item_bullets}{notes}])"),
            re.compile(rf"([{any_body}_]{{2,32}}){alt['mode_topic_particles']}"),
        ),
        quoted_name=re.compile(rf"{re.escape(first_quote[0])}([^{re.escape(first_quote[1])}]+){re.escape(first_quote[1])}"),
        function_label=re.compile(rf"[{dashes}]\([0-9]+\).+"),
        bare_title_marks=re.compile(
            rf"[{re.escape(''.join(w for w in words['bare_title_marks'] if len(w) == 1))}]"
            + "".join(f"|{re.escape(w)}" for w in words["bare_title_marks"] if len(w) > 1)
        ),
    )


check_cues = per_pack(_build)
