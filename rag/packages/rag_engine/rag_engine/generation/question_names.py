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


def absent_question_names(text: str, question: str, sources: Sequence[str]) -> list[str]:
    """``text`` に書いた質問の問いの文の名前のうち、根拠（``sources``）にも質問が述べた事実にも無い名前（出てきた順）。"""
    statements = question_statements(question)
    asked = [sentence for sentence in _sentences(question) if sentence not in statements]
    written, known = _form(text), _form("\n".join((*statements, *sources)))
    normalized = [unicodedata.normalize("NFKC", sentence) for sentence in asked]
    names = dict.fromkeys(
        match.group(0) for sentence in normalized for match in _NAME.finditer(sentence) if not _EXAMPLE.match(sentence, match.end())
    )
    return [name for name in names if _form(name) in written and _form(name) not in known]
