"""RAG retrieval 用の query expansion。

既定は LLM を呼ばない deterministic な同義語展開。opt-in
(``rag_query_expansion_llm_enabled``)で OCI Enterprise AI によるマルチクエリ生成を使い、
失敗・空応答時は決定論展開へ縮退する。元の user query は rerank / generation 用に保持する。
query 本文は audit / trace へ出さない。
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

WHITESPACE_RE = re.compile(r"\s+")

MAX_LLM_VARIANTS = 8
MAX_VARIANT_CHARS = 500


class _QueryExpander(Protocol):
    """LLM マルチクエリ拡張を提供する client(OciEnterpriseAiClient)。"""

    async def expand_search_query(self, query: str, *, max_variants: int = 3) -> list[str]: ...


class ExpandedQueryVariants(BaseModel):
    """LLM マルチクエリ拡張の出力スキーマ(LLM 出力はスキーマ検証してから使う)。"""

    variants: list[str] = Field(default_factory=list, max_length=MAX_LLM_VARIANTS)

    @field_validator("variants")
    @classmethod
    def clean_variants(cls, values: list[str]) -> list[str]:
        """空白正規化・空要素除去・長さ上限・重複除去を強制する。"""
        cleaned: list[str] = []
        for value in values:
            normalized = _normalize_query(value)[:MAX_VARIANT_CHARS]
            if normalized:
                cleaned.append(normalized)
        return _dedupe(cleaned)


async def expand_retrieval_queries_with_llm(
    query: str,
    *,
    llm: _QueryExpander,
    max_variants: int = 3,
) -> list[str]:
    """OCI Enterprise AI でクエリ変種を生成し、元 query を先頭に融合して返す。

    LLM 失敗・不正応答・変種なしのときは空 list を返し、呼び出し側が決定論の
    同義語展開へ縮退する。
    """
    normalized = _normalize_query(query)
    if not normalized or max_variants <= 1:
        return []
    try:
        raw_variants = await llm.expand_search_query(normalized, max_variants=max_variants)
        validated = ExpandedQueryVariants(variants=list(raw_variants))
    except ValidationError:
        logger.warning("LLM クエリ拡張の応答がスキーマ検証に失敗したため決定論展開へ縮退します。")
        return []
    except Exception:  # noqa: BLE001 - LLM 経路の失敗は決定論展開へ縮退する
        logger.warning("LLM クエリ拡張に失敗したため決定論展開へ縮退します。", exc_info=True)
        return []
    variants = _dedupe([normalized, *validated.variants])[:max_variants]
    if len(variants) <= 1:
        return []
    return variants


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
