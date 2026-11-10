"""文書の明示的な機能見出しから、同一操作を識別する。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from rag_engine.retrieval.evidence_cues import EvidenceCues, evidence_cues


def operation_labels(record: Any) -> set[str]:
    """番号付き機能見出しを優先し、章名や共通の操作ラベルだけで連結しない。"""
    labels = (getattr(record, "metadata", {}) or {}).get("section_path", [])
    if not isinstance(labels, list):
        return set()
    normalized = {re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s))) for s in labels}
    cues = evidence_cues()
    numbered = _function_number(cues)
    functions = {s for s in normalized if re.search(numbered + ".+", s)}
    if functions:
        return functions
    # 番号を持たない節の種別ラベル（「A 【画面説明】」「B-1.」の英字系列、「【設定】」の【 】囲み、6 字以内）は機能名ではない。
    # 語の列挙でなく形で除く (#849)。〔 〕囲みの画面名や無印の見出しは従来どおりラベルに残す。
    return {
        s
        for s in normalized
        if len(s) >= 5
        and not re.fullmatch(
            rf"(?:[A-Z]\s*[{cues.label_opens}]?[^{cues.label_opens}{cues.label_closes}0-9０-９]{{1,6}}[{cues.label_closes}]?"
            rf"|[{cues.label_opens}][^{cues.label_opens}{cues.label_closes}0-9０-９]{{1,6}}[{cues.label_closes}])",
            s,
        )
        and not re.fullmatch(rf"[0-9]+[^{cues.chars('heading_label_openers')}]*", s)
    }


def _function_number(cues: EvidenceCues) -> str:
    """番号付きの機能見出しの番号（「〜-(1)」）。区切りの線の字は言語資源。"""
    return rf"[{cues.chars('function_number_dashes')}]\([0-9]+\)"
