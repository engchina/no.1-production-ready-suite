"""説明・要約に書いた質問の対象の名前を、引いた根拠に照らす（#4327）。

質問が問うた製品・サービスなどの名前を説明に書き、回答が引いたどの原文にもその名前が無いとき、説明は一般の規則を対象へ
当てはめた飛躍になり得る。名前の有無は決定的に照らせるのでコードで確かめる。本文は書き換えず、見つけた名前を返すだけ
（呼び出し側が既存の印と是正に乗せる）。

利用者が質問で事実として述べた文の名前は、確かめる対象にしない（当てはめの前提は利用者が述べている）。
一般の語や言い換えを誤って疑わないよう、英字で始まる 3 字以上の語だけを名前とみなす。カタカナ・漢字の名前は確かめない。
「X など」のように分類の例として挙げた名前も確かめない。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

_NAME = re.compile(r"[A-Za-z][0-9A-Za-z._+-]{2,}")
# 「X など」の X は分類の例で、確かめる対象ではない（質問が問うているのは分類）。
_EXAMPLE = re.compile(r"\s*(?:など|等|といった|のような)")
_SENTENCE_SPLIT = re.compile(r"(?<=[。．？?！!])|\n")
_ASKING_END = re.compile(r"(?:か|ください|下さい|[?？])[。．.!！]?$")


def _sentences(question: str) -> list[str]:
    return [sentence for sentence in (item.strip() for item in _SENTENCE_SPLIT.split(question)) if sentence]


def question_statements(question: str) -> list[str]:
    """質問の文のうち、問い・依頼でない文（利用者が事実として述べた文）。"""
    return [sentence for sentence in _sentences(question) if not _ASKING_END.search(sentence)]


def _form(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _unconfirmed_names(question: str, sources: Sequence[str]) -> list[tuple[str, str]]:
    """質問の問いの文の名前のうち、根拠にも質問が述べた事実にも無いもの（名前と、その名前のある文。出てきた順）。"""
    statements = question_statements(question)
    known = _form("\n".join((*statements, *sources)))
    found: dict[str, tuple[str, str]] = {}
    for sentence in _sentences(question):
        if sentence in statements:
            continue
        normalized = unicodedata.normalize("NFKC", sentence)
        for match in _NAME.finditer(normalized):
            if not _EXAMPLE.match(normalized, match.end()) and _form(match.group(0)) not in known:
                found.setdefault(match.group(0), (match.group(0), normalized))
    return list(found.values())


def absent_question_names(text: str, question: str, sources: Sequence[str]) -> list[str]:
    """``text`` に書いた質問の対象の名前のうち、根拠にも質問が述べた事実にも無い名前（出てきた順）。"""
    written = _form(text)
    return [name for name, _ in _unconfirmed_names(question, sources) if _form(name) in written]


def unconfirmed_question_phrases(question: str, sources: Sequence[str], gaps: Sequence[str] = ()) -> list[str]:
    """回答で確認できないと伝える質問の対象の名前。``gaps`` に書いた名前は除く。空白だけで続く名前は 1 つにまとめる。"""
    written = _form("\n".join(gaps))
    names = [item for item in _unconfirmed_names(question, sources) if _form(item[0]) not in written]
    phrases: list[str] = []
    previous = ("", "")
    for name, sentence in names:
        if phrases and sentence == previous[1] and re.search(re.escape(previous[0]) + r"\s+" + re.escape(name), sentence):
            phrases[-1] += " " + name
        else:
            phrases.append(name)
        previous = (name, sentence)
    return phrases
