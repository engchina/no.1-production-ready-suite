"""回答の本文と根拠の本文の決定的な照合（最終の検証に渡す根拠の並べ替え。#1451）。

最終の検証は根拠の参照を接続ごとに上限（30 件）で切るため、多段の質問では前の段の根拠（古い
呼び出しの根拠）が上限の外に落ち、回答が使った根拠で確かめられずに正しい結論が外れる。回答が
`chunk_id` を書けば先にできる（#1364）が、文書名・章・行で出典を書くモデルでは働かない。
ここでは回答の本文に現れる根拠の特徴で、回答が使った根拠を見分ける（モデルは呼ばない）:

- ID・コード（`SYS-157` のような英字と数字の組。NFKC で全角も半角にそろえる）
- 回答の引用（「…」『…』）が根拠の本文にあるか
- 章節の見出し・シート名が回答にあるか（「第 1 章」だけの短い見出しは数えない）
- 根拠の本文と回答が同じ長い文字の並び（表の行の値の書き写しなど）を持つか
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 英字で始まり数字を含む ID・コード（`SYS-157`・`ISO27001`・`A-12-3`）。
_CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*")
_MIN_CODE_CHARS = 3
# 回答の引用の括弧。
_QUOTE_PATTERN = re.compile(r"「([^「」]+)」|『([^『』]+)』|“([^“”]+)”")
_MIN_QUOTE_CHARS = 3
# 見出し・シート名として数える最小の文字数と、番号だけの見出し（「第 22 章」「3.1」）。
_MIN_LABEL_CHARS = 4
_NUMBER_ONLY_LABEL = re.compile(r"^(第?[0-9.]+[章節条項部編]?|[0-9.]+)$")
# 回答と根拠が同じ並びを持つとみなす長さ（空白を除いた文字数）。
SHARED_RUN_CHARS = 16
# 回答に固有の特徴とみなす、その特徴を持つ根拠の数の上限（多くの根拠が持つ特徴は弱い）。
MAX_DISTINCT_REFS = 5


def normalize(text: str) -> str:
    """NFKC にして空白を除く（全角の英数字・空白の違いで照合を外さない）。"""
    return "".join(unicodedata.normalize("NFKC", text).split())


def _codes(normalized: str) -> set[str]:
    return {
        code
        for code in _CODE_PATTERN.findall(normalized)
        if len(code) >= _MIN_CODE_CHARS and any(char.isdigit() for char in code)
    }


@dataclass(frozen=True)
class AnswerFeatures:
    """回答の本文から取り出した、根拠と照らす特徴。"""

    text: str
    codes: frozenset[str]
    quotes: tuple[str, ...]
    runs: frozenset[str]

    @property
    def empty(self) -> bool:
        return not self.text


def answer_features(answer: str) -> AnswerFeatures:
    text = normalize(answer)
    quotes: list[str] = []
    for match in _QUOTE_PATTERN.finditer(answer):
        quote = normalize(next(group for group in match.groups() if group))
        if len(quote) >= _MIN_QUOTE_CHARS and quote not in quotes:
            quotes.append(quote)
    runs = frozenset(
        text[index : index + SHARED_RUN_CHARS] for index in range(len(text) - SHARED_RUN_CHARS + 1)
    )
    return AnswerFeatures(text=text, codes=frozenset(_codes(text)), quotes=tuple(quotes), runs=runs)


@dataclass
class EvidenceText:
    """根拠 1 件の照合に使う本文と見出し（同じ根拠を複数のツールが返したら足し合わせる）。"""

    texts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def add(self, text: object, labels: list[object]) -> None:
        if isinstance(text, str) and text.strip():
            self.texts.append(text)
        for label in labels:
            if isinstance(label, str) and label.strip() and label not in self.labels:
                self.labels.append(label)


def matched_features(features: AnswerFeatures, evidence: EvidenceText) -> frozenset[str]:
    """根拠の特徴のうち回答の本文に現れたもの（空なら回答と当たらない）。

    鍵は特徴の種類と値（`code:SYS-157`・`quote:…`・`label:…`・`run:<同じ並び>`）。同じ鍵を持つ
    根拠の数で重みを決める（`order_weights`）。
    """
    if features.empty:
        return frozenset()
    text = normalize("\n".join(evidence.texts))
    found = {f"code:{code}" for code in _codes(text) & features.codes}
    found.update(f"quote:{quote}" for quote in features.quotes if quote in text)
    for label in evidence.labels:
        normalized = normalize(label)
        if (
            len(normalized) >= _MIN_LABEL_CHARS
            and not _NUMBER_ONLY_LABEL.match(normalized)
            and normalized in features.text
        ):
            found.add(f"label:{normalized}")
    if features.runs:
        found.update(
            f"run:{run}"
            for index in range(len(text) - SHARED_RUN_CHARS + 1)
            if (run := text[index : index + SHARED_RUN_CHARS]) in features.runs
        )
    return frozenset(found)


def order_weights(matched: list[frozenset[str]]) -> list[tuple[bool, float]]:
    """根拠ごとの（回答に固有の特徴で当たったか, 当たりの重み）。

    多くの根拠が共有する特徴（台帳のすべての行が持つシート名・表頭など）は回答が使った根拠を
    見分けないので、特徴の重みは 1 / それを持つ根拠の数にし、持つ根拠が
    `MAX_DISTINCT_REFS` 件までの特徴で当たったものだけを「固有の特徴で当たった」とする。
    """
    counts: dict[str, int] = {}
    for keys in matched:
        for key in keys:
            counts[key] = counts.get(key, 0) + 1
    return [
        (
            any(counts[key] <= MAX_DISTINCT_REFS for key in keys),
            sum(1 / counts[key] for key in keys),
        )
        for keys in matched
    ]
