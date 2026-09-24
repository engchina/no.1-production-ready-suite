"""文書の明示的な機能見出しから、同一操作と候補別検索を識別する。"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence


def operation_labels(record: Any) -> set[str]:
    """番号付き機能見出しを優先し、章名や共通の操作ラベルだけで連結しない。"""
    labels = (getattr(record, "metadata", {}) or {}).get("section_path", [])
    if not isinstance(labels, list):
        return set()
    normalized = {re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))) for s in labels}
    functions = {s for s in normalized if re.search(r"[-－—―ー]\([0-9]+\).+", s)}
    if functions:
        return functions
    # 番号を持たない節の種別ラベル（「A 【画面説明】」「B-1.」の英字系列、「【設定】」の【 】囲み、6 字以内）は機能名ではない。
    # 語の列挙でなく形で除く (#849)。〔 〕囲みの画面名や無印の見出しは従来どおりラベルに残す。
    return {s for s in normalized if len(s) >= 5
            and not re.fullmatch(r"(?:[A-Z]\s*【?[^【】0-9０-９]{1,6}】?|【[^【】0-9０-９]{1,6}】)", s)
            and not re.fullmatch(r"[0-9]+[^【〔「(]*", s)}


def operation_section_pattern() -> "re.Pattern[str]":
    """操作手順の節（機能見出し直下の「操作説明」など）を表す見出し語の正規表現。

    特定マニュアルの節記法（「B 【操作説明】」）に固定せず、一般語の既定と業務 profile の
    `operation_section_pattern` で判定する (#849)。
    """
    from docrag.resources.runtime import current_profile
    pattern = current_profile().operation_section_pattern or r"操作説明|操作手順|操作方法"
    return re.compile(pattern)


def mentions_operation_section(text: str) -> bool:
    """本文か見出しに操作手順の節ラベルが含まれるか（入口ボーナスの条件）。"""
    return bool(operation_section_pattern().search(unicodedata.normalize("NFKC", str(text or ""))))


def alternative_operation_queries(question: str, records: Sequence[Any], *, limit: int = 2) -> tuple[str, ...]:
    """競合する番号付き機能を別々に検索する。原質問を保持し、適用先とは断定しない。"""
    candidates = sorted({label for record in records for label in operation_labels(record)
                         if re.search(r"[-－—―ー]\([0-9]+\).+", label)})
    if len(candidates) < 2:
        return ()
    # 同一章内の候補だけを比較し、無関係な業務の画面を検索に追加しない。
    groups: dict[str, list[str]] = {}
    for label in candidates:
        prefix = re.split(r"[-－—―ー]\([0-9]+\)", label, maxsplit=1)[0]
        groups.setdefault(prefix, []).append(label)
    competing = [labels for labels in groups.values() if len(labels) > 1]
    if not competing:
        return ()
    labels = min(competing, key=lambda values: (-sum(question.count(re.split(r"\)", s, maxsplit=1)[-1]) for s in values), values))
    return tuple(f"{question} {label} 操作方法 適用条件" for label in labels[:limit])


def operation_target_score(question: str, record: Any) -> float:
    """質問の編集対象と原文の項目見出しの一致を測る。検索語や例示表の反復は数えない。"""
    focus = question.split('の', 1)[-1]
    terms = set(re.findall(r'[一-龯々ァ-ヶー]{2,}', focus)) - {
        '変更', '修正', '訂正', '編集', '使用', '確認', '入力', '表示', '設定', '方法', '手順', '内容', '場合'}
    text = str(getattr(record, 'text', '') or '')
    headings = [line.strip() for line in text.splitlines() if line.strip()
                and (line.strip().startswith(('●', '■', '◆'))
                     or re.fullmatch(r'[一-龯々ァ-ヶー・]{2,24}', line.strip()))]
    hits = {term for term in terms if any(term in heading for heading in headings)}
    explicit = {term for term in hits if any(heading.startswith(('●', '■', '◆')) and term in heading for heading in headings)}
    # OCR の項目名だけより、項目別の説明本文が続く手引きの見出しを優先する。
    return float(sum(min(len(term), 8) for term in hits) * 20 + 80 * len(explicit))
