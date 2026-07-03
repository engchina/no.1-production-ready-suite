"""業務ビュー(Business View)の設定解決。

業務ビューは「利用者(回答する側)視点」のエンティティで、**複数の KB を束ねた
参照集合**と、**1 つの一貫した検索・回答設定**、および **persona(system prompt /
既定言語)** を持つ。KB(加工する側視点)とは関心事が異なるため別レイヤーとして扱う。

設計:

* 参照 KB は多対多。1 つの KB を複数の業務ビューから共有できる。逆も可。
* query 上書き(Retrieval / Grounding / Generation / Guardrail / Evaluation)は
  KB の :class:`KnowledgeBaseQueryConfig` を再利用する。複数 KB の query 設定は競合するため、
  検索時はこの**業務ビュー 1 枚から**解決する。KB 個別の query legacy 値は使わない。
* persona / 既定言語は Generation profile を置換せず、公共制約と形式制約の間へ合成する。
* 取込系(Preprocess / Parser / Chunking / Vector Index build)は KB の物理索引方法なので
  業務ビューでは触らない。
* 永続化は ``rag_business_views.view_config`` JSON カラムに一括格納する(DDL 最小)。
* 解決順は request 明示 > 業務ビュー > グローバル既定。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field
from rag_pipeline_core.retrieval import decompose_retrieval_strategy

from app.config import ServingMode, Settings
from app.rag.kb_adapter_config import (
    KnowledgeBaseQueryConfig,
    compose_query_settings,
)

logger = logging.getLogger(__name__)

BUSINESS_VIEW_CONFIG_VERSION = 1
MAX_BUSINESS_VIEW_KNOWLEDGE_BASES = 200
MAX_SYSTEM_PROMPT_CHARS = 4000
MAX_DEFAULT_LANGUAGE_CHARS = 32


class BusinessViewConfig(BaseModel):
    """業務ビューの設定一式(参照 KB + query 上書き + persona)。"""

    model_config = ConfigDict(extra="ignore")

    version: int = BUSINESS_VIEW_CONFIG_VERSION
    knowledge_base_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_BUSINESS_VIEW_KNOWLEDGE_BASES,
        description="束ねる参照 KB の ID 群(多対多)。検索時にこの集合を検索対象へ展開する。",
    )
    query: KnowledgeBaseQueryConfig = Field(default_factory=KnowledgeBaseQueryConfig)
    system_prompt: str | None = Field(default=None, max_length=MAX_SYSTEM_PROMPT_CHARS)
    default_language: str | None = Field(default=None, max_length=MAX_DEFAULT_LANGUAGE_CHARS)
    serving_mode: ServingMode = Field(
        default="fused",
        description="互換読取用。保存・runtime は全レシピ融合(fused)へ正規化する。",
    )

    def normalized_knowledge_base_ids(self) -> list[str]:
        """参照 KB ID の前後空白・重複を取り除く。"""
        return _unique_clean_ids(self.knowledge_base_ids)

    def resolved_system_prompt(self) -> str | None:
        """前後空白を除いた persona を返す。言語は別 layer として扱う。"""
        prompt = (self.system_prompt or "").strip()
        return prompt or None


def parse_business_view_config(raw: Mapping[str, object] | None) -> BusinessViewConfig:
    """``view_config`` JSON から業務ビュー設定を寛容に復元する。"""
    if not raw:
        return BusinessViewConfig()
    try:
        return BusinessViewConfig.model_validate(dict(raw))
    except Exception:  # noqa: BLE001 - 壊れた永続値は空設定へ縮退して検索を止めない
        logger.warning("業務ビュー設定の復元に失敗したため空へ縮退します。", exc_info=True)
        return BusinessViewConfig()


def _normalized_query(query: KnowledgeBaseQueryConfig) -> KnowledgeBaseQueryConfig:
    """legacy 複合 retrieval_strategy をモード + トグルへ正規化する(保存は新形式のみ)。"""
    if query.retrieval_strategy is None:
        return query
    decomposed = decompose_retrieval_strategy(query.retrieval_strategy)
    if decomposed.legacy_strategy is None and decomposed.mode == query.retrieval_strategy:
        return query
    # 強制トグルは runtime の OR 合成で常に勝つため、正規化でも無条件に True を書く。
    updates: dict[str, object] = {"retrieval_strategy": decomposed.mode}
    if decomposed.forced_query_expansion:
        updates["retrieval_query_expansion"] = True
    if decomposed.forced_gap_stop:
        updates["retrieval_gap_stop"] = True
    if decomposed.forced_corrective_retrieval:
        updates["retrieval_corrective"] = True
    if decomposed.forced_business_fit_weighting:
        updates["retrieval_business_fit_weighting"] = True
    return query.model_copy(update=updates)


def dump_business_view_config(config: BusinessViewConfig) -> dict[str, object]:
    """業務ビュー設定を ``view_config`` カラムへ保存する dict へ変換する。"""
    return config.model_copy(
        update={"serving_mode": "fused", "query": _normalized_query(config.query)}
    ).model_dump(mode="json")


def resolve_business_view_settings(
    global_settings: Settings,
    config: BusinessViewConfig,
) -> tuple[Settings, bool]:
    """グローバルへ 業務ビュー query → persona を重ねた Settings。

    KB はナレッジ構築設定だけを持つため、KB に残る query legacy 値はここでは扱わない。
    戻り値 2 番目は上書きが実際に効いたかどうか。
    """
    overlays: list[KnowledgeBaseQueryConfig] = [config.query]
    merged, applied = compose_query_settings(global_settings, overlays)
    updates: dict[str, object] = {}
    persona = config.resolved_system_prompt()
    if persona is not None:
        updates["rag_generation_system_prompt_override"] = persona
    language = (config.default_language or "").strip()
    if language:
        updates["rag_generation_default_language"] = language
    if merged.rag_serving_mode != "fused":
        updates["rag_serving_mode"] = "fused"
    if updates:
        merged = merged.model_copy(update=updates)
        applied = True
    return merged, applied


def _unique_clean_ids(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned
