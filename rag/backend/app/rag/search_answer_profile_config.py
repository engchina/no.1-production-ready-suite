"""検索・回答プロファイル(Search Answer Profile)の設定解決。

検索・回答プロファイルは「利用者(回答する側)視点」のエンティティで、**複数の KB を束ねた
参照集合**と、**1 つの一貫した回答の設定**を持つ。KB(加工する側視点)とは関心事が
異なるため別レイヤーとして扱う。

設計:

* 参照 KB は多対多。1 つの KB を複数の検索・回答プロファイルから共有できる。逆も可。
* query 上書き(回答の検索と生成・安全チェック)は KB の :class:`KnowledgeBaseQueryConfig` を
  再利用する。複数 KB の query 設定は競合するため、検索時はこの**検索・回答プロファイル 1 枚から**
  解決する。KB 個別の query legacy 値は使わない。
* persona(system prompt / 既定言語)は旧 standard の回答エンジンだけが使っていたため #595 で
  削除した。保存済みの値は ``extra=ignore`` で読み込み時に捨て、次回保存で消える。
* 取込系(Preprocess / Parser / Chunking / Vector Index build)は KB の物理索引方法なので
  検索・回答プロファイルでは触らない。
* 永続化は ``rag_search_answer_profiles.profile_config`` JSON カラムに一括格納する(DDL 最小)。
* 解決順は 検索・回答プロファイル > グローバル既定。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from rag_parser_core.language import FIELD_SPECS, LanguagePackError, builtin_pack, resolve

from app.config import Settings
from app.rag.kb_adapter_config import (
    KnowledgeBaseQueryConfig,
    compose_query_settings,
)

logger = logging.getLogger(__name__)

SEARCH_ANSWER_PROFILE_CONFIG_VERSION = 1
MAX_SEARCH_ANSWER_PROFILE_KNOWLEDGE_BASES = 200


class SearchAnswerProfileConfig(BaseModel):
    """検索・回答プロファイルの設定一式(参照 KB + query 上書き)。"""

    model_config = ConfigDict(extra="ignore")

    version: int = SEARCH_ANSWER_PROFILE_CONFIG_VERSION
    knowledge_base_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_SEARCH_ANSWER_PROFILE_KNOWLEDGE_BASES,
        description="束ねる参照 KB の ID 群(多対多)。検索時にこの集合を検索対象へ展開する。",
    )
    query: KnowledgeBaseQueryConfig = Field(default_factory=KnowledgeBaseQueryConfig)
    # 回答前の確認（#1839）の方針。confirm = 曖昧な質問を確認してから回答する（ルールごとの確認・
    # 業務ガイドの条件の確認を出す）/ assume = 確認せずに前提を示して回答する
    # （チャットに確認を出さない）。
    # 1 つの質問で聞き返すのは 1 回だけ（#717）なので、確認の上限（往復）は持たない。
    clarification_policy: Literal["confirm", "assume"] = "confirm"
    # 言語資源の上書き（#2181。全体の言語資源に重ねる「節 → 項目 → 値」）。検索・回答で使う項目
    # （ingest=False）だけを持てる。取込の項目は文書の分割と食い違うので持たない。
    language_pack: dict[str, dict[str, object]] = Field(default_factory=dict)

    @field_validator("language_pack")
    @classmethod
    def _validate_language_pack(
        cls, value: dict[str, dict[str, object]]
    ) -> dict[str, dict[str, object]]:
        specs = {(spec.section, spec.key): spec for spec in FIELD_SPECS}
        cleaned: dict[str, dict[str, object]] = {}
        for section, fields in (value or {}).items():
            for key, item in (fields or {}).items():
                spec = specs.get((section, key))
                if spec is None:
                    raise ValueError(f"{section}.{key}: 知らない項目です。")
                if spec.ingest:
                    raise ValueError(
                        f"{section}.{key}: 取込の項目はプロファイルで上書きできません。"
                    )
                cleaned.setdefault(section, {})[key] = item
        if cleaned:
            try:
                resolve(builtin_pack(), cleaned)
            except LanguagePackError as exc:
                raise ValueError(str(exc)) from exc
        return cleaned

    def normalized_knowledge_base_ids(self) -> list[str]:
        """参照 KB ID の前後空白・重複を取り除く。"""
        return _unique_clean_ids(self.knowledge_base_ids)


def parse_search_answer_profile_config(
    raw: Mapping[str, object] | None,
) -> SearchAnswerProfileConfig:
    """``profile_config`` JSON から検索・回答プロファイル設定を寛容に復元する。"""
    if not raw:
        return SearchAnswerProfileConfig()
    try:
        return SearchAnswerProfileConfig.model_validate(dict(raw))
    except Exception:  # noqa: BLE001 - 壊れた永続値は空設定へ縮退して検索を止めない
        logger.warning(
            "検索・回答プロファイル設定の復元に失敗したため空へ縮退します。", exc_info=True
        )
        return SearchAnswerProfileConfig()


def dump_search_answer_profile_config(config: SearchAnswerProfileConfig) -> dict[str, object]:
    """検索・回答プロファイル設定を ``profile_config`` カラムへ保存する dict へ変換する。"""
    return config.model_dump(mode="json")


def resolve_search_answer_profile_settings(
    global_settings: Settings,
    config: SearchAnswerProfileConfig,
) -> tuple[Settings, bool]:
    """グローバルへ検索・回答プロファイルの query 上書きを重ねた Settings。

    KB はナレッジ構築設定だけを持つため、KB に残る query legacy 値はここでは扱わない。
    戻り値 2 番目は上書きが実際に効いたかどうか。
    """
    overlays: list[KnowledgeBaseQueryConfig] = [config.query]
    settings, applied = compose_query_settings(global_settings, overlays)
    if config.language_pack:
        settings = settings.model_copy(update={"rag_language_pack_overrides": config.language_pack})
        applied = True
    return settings, applied


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
