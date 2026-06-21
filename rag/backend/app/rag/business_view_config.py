"""業務アシスタント(Business View)の設定解決。

業務アシスタントは「利用者(回答する側)視点」のエンティティで、**複数の KB を束ねた
参照集合**と、**1 つの一貫した query アダプター設定**、および **persona(system prompt /
既定言語)** を持つ。KB(加工する側視点)とは関心事が異なるため別レイヤーとして扱う。

設計:

* 参照 KB は多対多。1 つの KB を複数の業務アシスタントから共有できる。逆も可。
* query 上書き(Retrieval / Grounding / Generation / Guardrail / Vector Index / Evaluation)は
  KB の :class:`KnowledgeBaseQueryConfig` を再利用する。複数 KB の query 設定は競合するため、
  検索時はこの**業務アシスタント 1 枚から**解決する(KB 個別の query 上書きは単一 KB 指定時のみ)。
* persona は Generation の system prompt を runtime 上書きする
  (:attr:`Settings.rag_generation_system_prompt_override`)。
* 取込系(Preprocess / Parser / Chunking / Vector Index build)は KB の物理索引方法なので
  業務アシスタントでは触らない。
* 永続化は ``rag_business_views.view_config`` JSON カラムに一括格納する(DDL 最小)。
* 解決順は request 明示 > 業務アシスタント > (単一 KB 指定時のみ)KB > グローバル既定。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

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
    """業務アシスタントの設定一式(参照 KB + query 上書き + persona)。"""

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
        default="single",
        description=(
            "配信モード。1 文書が複数 chunk_set を持つときの検索時配信方法。single(既定)は "
            "is_serving の単一 chunk_set のみ、fused は複数 chunk_set を RRF 融合 + source-span "
            "重複除去、routed は Router で query ごと選択(後続)。"
        ),
    )

    def normalized_knowledge_base_ids(self) -> list[str]:
        """参照 KB ID の前後空白・重複を取り除く。"""
        return _unique_clean_ids(self.knowledge_base_ids)

    def resolved_system_prompt(self) -> str | None:
        """persona(system_prompt + 既定言語ディレクティブ)を 1 本の prompt へ束ねる。"""
        prompt = (self.system_prompt or "").strip()
        language = (self.default_language or "").strip()
        if not prompt and not language:
            return None
        if language:
            directive = f"回答は原則 {language} で行ってください。"
            prompt = f"{prompt}\n{directive}".strip() if prompt else directive
        return prompt or None


def parse_business_view_config(raw: Mapping[str, object] | None) -> BusinessViewConfig:
    """``view_config`` JSON から業務アシスタント設定を寛容に復元する。"""
    if not raw:
        return BusinessViewConfig()
    try:
        return BusinessViewConfig.model_validate(dict(raw))
    except Exception:  # noqa: BLE001 - 壊れた永続値は空設定へ縮退して検索を止めない
        logger.warning("業務アシスタント設定の復元に失敗したため空へ縮退します。", exc_info=True)
        return BusinessViewConfig()


def dump_business_view_config(config: BusinessViewConfig) -> dict[str, object]:
    """業務アシスタント設定を ``view_config`` カラムへ保存する dict へ変換する。"""
    return config.model_dump(mode="json")


def resolve_business_view_settings(
    global_settings: Settings,
    config: BusinessViewConfig,
    kb_query: KnowledgeBaseQueryConfig | None = None,
) -> tuple[Settings, bool]:
    """グローバルへ (任意の KB query 下層 →) 業務アシスタント query → persona を重ねた Settings。

    ``kb_query`` を渡すと **per-field merge** で「KB 既定 < 業務アシスタント」の層になり、
    業務アシスタントが触れない query 項目は KB 既定が残る(検索対象が単一 KB に解決した
    ときに使う。複数 KB のときは一意な KB 既定が無いので呼び出し側が None を渡す)。
    戻り値 2 番目は上書きが実際に効いたかどうか。
    """
    overlays: list[KnowledgeBaseQueryConfig] = []
    if kb_query is not None:
        overlays.append(kb_query)  # 低優先(KB 既定)
    overlays.append(config.query)  # 高優先(業務アシスタント)
    merged, applied = compose_query_settings(global_settings, overlays)
    updates: dict[str, object] = {}
    persona = config.resolved_system_prompt()
    if persona is not None:
        updates["rag_generation_system_prompt_override"] = persona
    if config.serving_mode != merged.rag_serving_mode:
        updates["rag_serving_mode"] = config.serving_mode
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
