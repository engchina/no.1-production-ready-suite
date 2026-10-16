"""回答の「当てはめ」: 支持された規則の item を、質問に書かれた事実に当てはめた結果の検査（#2155）。

原文の引用で支える item（``grounded.GroundedItem``）は、規則を質問の事実に当てはめた結果（計算の結果・
該当するか）を書けない。当てはめは次の検査をすべて通ったものだけを公開する:

- 前提（premises）は質問の原文の連続した一部（決定的に照合する）。
- 使う規則は、検査と監査を通った item（原文の引用つき）を index で指す。指した item が降格したら公開しない。
- 本文の算式（数と ＋−×÷ と ＝）は計算し直して、合わないものは公開しない。
- 監査（``GroundedAudit.application_reviews``）が正しいと判定したものだけを公開する。

特定の業務・文書の知識は持たない（規則は根拠の原文、事実は質問から取る）。
"""

from __future__ import annotations

import ast
import math
import operator
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from rag_engine.models.llm import GroundedApplication, GroundedAudit

# 回答の本文の節の見出し。
APPLICATIONS_SECTION_TITLE = "質問の条件への当てはめ"
PREMISES_LABEL = "前提："
REASONING_LABEL = "当てはめ："


@dataclass
class CheckedApplication:
    """検査後の当てはめ。``entries`` は指した規則の item（``grounded.CheckedItem``）。"""

    application: GroundedApplication
    entries: list[Any] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.reasons.append(reason)

    @property
    def published(self) -> bool:
        """公開できるか（検査・監査を通り、指した規則の item がすべて支持されたまま）。"""
        return not self.reasons and bool(self.entries) and all(_supported(entry) for entry in self.entries)

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.application.model_dump(),
            "published": self.published,
            "reasons": list(self.reasons),
        }


def _supported(entry: Any) -> bool:
    return bool(entry.span) and not entry.quote_only and entry.item.kind != "gap" and entry.item.applies != "unverified"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def check_applications(
    question: str, applications: Sequence[GroundedApplication], items_by_index: Mapping[int, Any]
) -> list[CheckedApplication]:
    """前提の照合・規則の参照・算式の計算を決定的に確かめる（監査の前）。"""
    asked = _compact(question)
    checked: list[CheckedApplication] = []
    for application in applications:
        entry = CheckedApplication(application)
        premises = [premise for premise in application.premises if premise.strip()]
        if not premises:
            entry.reject("前提が無い")
        for premise in premises:
            if _compact(premise) not in asked:
                entry.reject(f"前提『{premise}』は質問に無い")
        indexes = list(dict.fromkeys(application.item_indexes))
        if not indexes:
            entry.reject("当てはめる規則の item が無い")
        for index in indexes:
            item = items_by_index.get(index)
            if item is None or not item.span or item.item.kind == "gap":
                entry.reject(f"items[{index}] は引用のある規則ではない")
            else:
                entry.entries.append(item)
        if mismatch := arithmetic_mismatch(f"{application.reasoning}\n{application.result}"):
            entry.reject(f"算式が合わない: {mismatch}")
        checked.append(entry)
    return checked


def apply_application_audit(checked: Sequence[CheckedApplication], audit: GroundedAudit | None) -> None:
    """監査が正しいと判定した当てはめだけを残す。監査が無い・判定が無いものは公開しない。"""
    reviews = {review.index: review for review in (audit.application_reviews if audit else [])}
    for index, entry in enumerate(checked):
        review = reviews.get(index)
        if audit is None:
            entry.reject("監査なし")
        elif review is None:
            entry.reject("監査の判定なし")
        elif not review.valid:
            entry.reject(f"監査: {review.reason}")


def audit_view(checked: Sequence[CheckedApplication], index_of: Callable[[Any], int | None]) -> list[dict[str, Any]]:
    """監査に渡す当てはめ（検査を通ったもの）。規則は監査の items の index で指す。"""
    view: list[dict[str, Any]] = []
    for number, entry in enumerate(checked):
        if entry.reasons:
            continue
        view.append(
            {
                "index": number,
                "request_id": entry.application.request_id,
                "premises": list(entry.application.premises),
                "item_indexes": [i for i in (index_of(item) for item in entry.entries) if i is not None],
                "reasoning": entry.application.reasoning,
                "result": entry.application.result,
            }
        )
    return view


def render_lines(checked: Sequence[CheckedApplication], citation: Callable[[dict], str], cite: Callable[[dict], None]) -> list[str]:
    """公開する当てはめの本文の行。指した規則の出典行を、本文の出典行と同じ形で続ける。"""
    lines: list[str] = []
    for entry in checked:
        if not entry.published:
            continue
        application = entry.application
        lines.append(f"・{application.result.strip()}")
        premises = "".join(f"「{premise.strip()}」" for premise in application.premises if premise.strip())
        if premises:
            lines.append(PREMISES_LABEL + premises)
        lines.append(REASONING_LABEL + " ".join(application.reasoning.split()))
        shown: list[str] = []
        for item in entry.entries:
            line = citation(item.span)
            if line not in shown:
                shown.append(line)
                lines.append(line)
                cite(item.span)
    return lines


# ---- 算式の計算 ---------------------------------------------------------------

# 数に付く単位。万・億は桁として計算し、それ以外は外す（同じ算式の中の単位はそろっている前提）。
_SCALE = {"万": 10_000, "億": 100_000_000}
_UNIT = r"(?:万円|億円|円|万|億|か月|ヶ月|ケ月|カ月|月|日|年|歳|%|人|件|回)"
_NUMBER = r"\d+(?:,\d{3})*(?:\.\d+)?"
_TERM = rf"\(*\s*{_NUMBER}\s*{_UNIT}?\s*\)*"
_OPERATOR = r"[+\-*/]"
# 「A＋B÷C＝D」: 演算子を 1 つ以上含む左辺と、数 1 つの右辺。
_EQUATION = re.compile(rf"(?P<left>{_TERM}(?:\s*{_OPERATOR}\s*{_TERM})+)\s*=\s*(?P<right>{_NUMBER}\s*{_UNIT}?)")
# 日付の形（「8年3月」「令和8年」）が式に入ると数として計算できないので、その式は確かめない。
_DATE_LIKE = re.compile(r"\d+\s*年\s*\d+\s*月|\d+\s*月\s*\d+\s*日")
_OPERATORS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def _normalize_expression(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text)
    return folded.translate(str.maketrans({"×": "*", "÷": "/", "−": "-", "–": "-"}))


def _number_value(match: re.Match[str]) -> str:
    number = float(match.group(1).replace(",", ""))
    unit = match.group(2) or ""
    scale = next((value for prefix, value in _SCALE.items() if unit.startswith(prefix)), 1)
    return repr(number * scale)


def _evaluate(expression: str) -> float | None:
    python = re.sub(rf"({_NUMBER})\s*({_UNIT})?", _number_value, expression)
    try:
        tree = ast.parse(python, mode="eval")
    except SyntaxError:
        return None

    def value(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return value(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -value(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
            return _OPERATORS[type(node.op)](value(node.left), value(node.right))
        raise ValueError(type(node).__name__)

    try:
        return value(tree)
    except (ValueError, ZeroDivisionError):
        return None


def arithmetic_mismatch(text: str) -> str:
    """本文の算式（「44万円＋180万円÷12＝59万円」）を計算し直し、合わない最初の式を返す。合えば空。

    日付の形を含む式、計算できない式は確かめない（誤って公開を止めない）。端数の丸めは 1% まで許す。
    """
    normalized = _normalize_expression(text)
    for match in _EQUATION.finditer(normalized):
        left, right = match.group("left"), match.group("right")
        if _DATE_LIKE.search(left):
            continue
        expected, actual = _evaluate(left), _evaluate(right)
        if expected is None or actual is None:
            continue
        if not math.isclose(expected, actual, rel_tol=0.01, abs_tol=0.01):
            return match.group(0).strip()
    return ""
