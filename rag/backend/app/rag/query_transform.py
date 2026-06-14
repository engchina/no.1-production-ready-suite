"""RAG retrieval 用の軽量 query expansion。

LLM を呼ばずに deterministic な同義語展開だけを行い、元の user query は
rerank / generation 用に保持する。query 本文は audit / trace へ出さない。
"""

from __future__ import annotations

import re

WHITESPACE_RE = re.compile(r"\s+")

SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("請求書", "インボイス", "invoice", "bill"),
    ("伝票", "document", "voucher"),
    ("経費", "費用", "expense", "cost"),
    ("申請", "申込", "request", "application"),
    ("承認", "承認者", "approve", "approval"),
    ("保管", "保存", "格納", "storage", "archive"),
    ("原本", "原紙", "original"),
    ("規程", "規則", "ポリシー", "policy"),
    ("手順", "手順書", "マニュアル", "manual", "procedure"),
    ("検索", "探索", "search", "retrieval"),
    ("表", "表形式", "テーブル", "table"),
    ("図", "図版", "画像", "figure", "image"),
    ("支払", "支払い", "payment"),
    ("期限", "期日", "due date", "deadline"),
)


def expand_retrieval_queries(
    query: str,
    *,
    enabled: bool = True,
    max_variants: int = 3,
) -> list[str]:
    """retrieval に使う query variants を返す。

    先頭は必ず正規化済みの元 query。以降は業務語彙の同義語を付与した variant と、
    同義語だけの fallback variant を最大数まで返す。
    """
    normalized = _normalize_query(query)
    if not normalized:
        return []
    if not enabled or max_variants <= 1:
        return [normalized]

    expansions = _matching_expansions(normalized)
    if not expansions:
        return [normalized]

    candidates = [
        normalized,
        f"{normalized} {' '.join(expansions)}",
        " ".join(expansions),
    ]
    return _dedupe(candidates)[:max_variants]


def _matching_expansions(query: str) -> list[str]:
    """query に含まれる語の同義語だけを順序安定で返す。"""
    lowered = query.casefold()
    expansions: list[str] = []
    for group in SYNONYM_GROUPS:
        if not any(term.casefold() in lowered for term in group):
            continue
        for term in group:
            if term.casefold() not in lowered and term not in expansions:
                expansions.append(term)
    return expansions


def _normalize_query(query: str) -> str:
    """自然言語 query の空白を正規化する。"""
    return WHITESPACE_RE.sub(" ", query).strip()


def _dedupe(values: list[str]) -> list[str]:
    """case-insensitive に重複を除く。"""
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique
