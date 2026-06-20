"""KB 単位のアダプター設定(per-knowledge-base adapter overrides)。

業界の RAG 製品(Dify / RAGFlow / FastGPT 等)に倣い、Parser / Chunking /
Retrieval 系アダプターの既定値を **ナレッジベース単位** で上書きできるようにする。
ただし確定スタック(OCI Enterprise AI / OCI Generative AI Cohere / Oracle 26ai)は
不変で、上書きは既存アダプター preset の選択に限定する。

設計:

* 上書きは 2 つの scope に分かれる。
  - ``ingestion``: 取込時にしか効かない Parser / Chunking。文書取込の瞬間に
    owning KB の設定で確定し(取込時スナップショット)、後から KB を変えても
    既存チャンクは作り直さない。
  - ``query``: クエリ時に効く Retrieval / Grounding / Generation / Guardrail /
    Vector Index / Evaluation。次の検索から即反映。
* Embedding/Rerank(Cohere v4/1536)・DB・OCI・Object Storage はグローバル固定で
  KB 別にしない(ベクトル次元混在不可など物理制約)。
* 永続化は既存 ``rag_knowledge_bases.retrieval_config`` JSON カラムを再利用し、
  DDL 変更を避ける。保存形は :func:`dump_adapter_config` 参照。
* 解決順は request 明示 > KB 設定 > グローバル既定。本モジュールは
  「KB 設定 > グローバル既定」の overlay を担う(request 明示は呼び出し側)。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import (
    ChunkingStrategy,
    GenerationProfile,
    GuardrailPolicyName,
    ParserAdapterBackend,
    PostRetrievalPipeline,
    PreprocessProfile,
    RetrievalStrategy,
    Settings,
    VectorIndexProfile,
)
from app.config import (
    EvaluationSuite as EvaluationSuiteName,
)

logger = logging.getLogger(__name__)

ADAPTER_CONFIG_VERSION = 1

AdapterConfigScope = Literal["ingestion", "query"]

# KB 設定フィールド -> Settings フィールドのマッピング(scope ごとの allowlist)。
# ここに載っていない Settings フィールドは KB 単位で上書きできない。
_INGESTION_FIELD_MAP: dict[str, str] = {
    "preprocess_profile": "rag_preprocess_profile",
    "parser_adapter_backend": "rag_parser_adapter_backend",
    "parser_docling_enabled": "rag_parser_docling_enabled",
    "parser_marker_enabled": "rag_parser_marker_enabled",
    "parser_unstructured_enabled": "rag_parser_unstructured_enabled",
    "chunking_strategy": "rag_chunking_strategy",
    "chunk_size": "rag_chunk_size",
    "chunk_overlap": "rag_chunk_overlap",
    "chunk_child_size": "rag_chunk_child_size",
    "chunk_sentence_window_size": "rag_chunk_sentence_window_size",
    "chunk_min_chars": "rag_chunk_min_chars",
}
_QUERY_FIELD_MAP: dict[str, str] = {
    "retrieval_strategy": "rag_retrieval_strategy",
    "post_retrieval_pipeline": "rag_post_retrieval_pipeline",
    "generation_profile": "rag_generation_profile",
    "guardrail_policy": "rag_guardrail_policy",
    "vector_index_profile": "rag_vector_index_profile",
    "evaluation_suite": "rag_evaluation_suite",
}

# 外部 parser adapter backend -> その有効化 feature flag(Settings フィールド名)。
# KB が backend を明示選択したのに flag が無効だと取込が Enterprise AI へ fallback
# してしまうため、backend 選択を flag 有効化の意思表示として扱う。
_EXTERNAL_PARSER_BACKEND_FLAGS: dict[str, str] = {
    "docling": "rag_parser_docling_enabled",
    "marker": "rag_parser_marker_enabled",
    "unstructured": "rag_parser_unstructured_enabled",
}


class KbAdapterConfigError(ValueError):
    """KB アダプター設定がグローバル設定と整合しないときに送出する。"""


class KnowledgeBaseIngestionConfig(BaseModel):
    """取込時(Parser / Chunking)の KB 上書き。None はグローバル継承。"""

    model_config = ConfigDict(extra="ignore")

    preprocess_profile: PreprocessProfile | None = None
    parser_adapter_backend: ParserAdapterBackend | None = None
    parser_docling_enabled: bool | None = None
    parser_marker_enabled: bool | None = None
    parser_unstructured_enabled: bool | None = None
    chunking_strategy: ChunkingStrategy | None = None
    chunk_size: int | None = Field(default=None, ge=200, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    chunk_child_size: int | None = Field(default=None, ge=80, le=4000)
    chunk_sentence_window_size: int | None = Field(default=None, ge=1, le=20)
    chunk_min_chars: int | None = Field(default=None, ge=0, le=2000)


class KnowledgeBaseQueryConfig(BaseModel):
    """クエリ時(Retrieval 以降)の KB 上書き。None はグローバル継承。"""

    model_config = ConfigDict(extra="ignore")

    retrieval_strategy: RetrievalStrategy | None = None
    post_retrieval_pipeline: PostRetrievalPipeline | None = None
    generation_profile: GenerationProfile | None = None
    guardrail_policy: GuardrailPolicyName | None = None
    vector_index_profile: VectorIndexProfile | None = None
    evaluation_suite: EvaluationSuiteName | None = None


class KnowledgeBaseAdapterConfig(BaseModel):
    """KB 単位のアダプター上書き設定一式。"""

    model_config = ConfigDict(extra="ignore")

    version: int = ADAPTER_CONFIG_VERSION
    ingestion: KnowledgeBaseIngestionConfig = Field(default_factory=KnowledgeBaseIngestionConfig)
    query: KnowledgeBaseQueryConfig = Field(default_factory=KnowledgeBaseQueryConfig)

    def is_empty(self) -> bool:
        """上書きが 1 件も無ければ True。"""
        return not self._scope_overrides("ingestion") and not self._scope_overrides("query")

    def _scope_overrides(self, scope: AdapterConfigScope) -> dict[str, object]:
        """scope の非 None 上書きを {KB フィールド名: 値} で返す。"""
        section = self.ingestion if scope == "ingestion" else self.query
        return {key: value for key, value in section.model_dump().items() if value is not None}

    def settings_overrides(self, scope: AdapterConfigScope) -> dict[str, object]:
        """scope の上書きを {Settings フィールド名: 値} へ変換する。"""
        field_map = _INGESTION_FIELD_MAP if scope == "ingestion" else _QUERY_FIELD_MAP
        overrides = {
            field_map[key]: value
            for key, value in self._scope_overrides(scope).items()
            if key in field_map
        }
        if scope == "ingestion":
            _ensure_external_parser_backend_enabled(overrides)
        return overrides


def _ensure_external_parser_backend_enabled(overrides: dict[str, object]) -> None:
    """外部 parser backend を選択したら対応 feature flag も有効化する。

    KB 設定 UI は backend だけを選ばせ、feature flag は別に持たない。グローバルの
    flag 既定は無効なので、backend 選択だけでは取込時に Enterprise AI へ fallback
    してしまう。backend 選択を「その adapter を使いたい」という明示の意思として扱い、
    KB が flag を明示的に False に上書きしていない限り True を注入する。
    パッケージ未導入時は parser registry 側が安全に fallback する。
    """
    backend = overrides.get("rag_parser_adapter_backend")
    if not isinstance(backend, str):
        return
    flag_field = _EXTERNAL_PARSER_BACKEND_FLAGS.get(backend.strip().casefold())
    if flag_field is None:
        return
    overrides.setdefault(flag_field, True)


def parse_adapter_config(raw: Mapping[str, object] | None) -> KnowledgeBaseAdapterConfig:
    """``retrieval_config`` JSON から KB アダプター設定を寛容に復元する。

    旧来の free-form ``retrieval_config`` や未知キーは ``extra=ignore`` で捨て、
    壊れた値があっても空設定へ縮退して取込/検索を止めない。
    """
    if not raw:
        return KnowledgeBaseAdapterConfig()
    try:
        return KnowledgeBaseAdapterConfig.model_validate(dict(raw))
    except Exception:  # noqa: BLE001 - 壊れた永続値は空設定へ縮退する
        logger.warning("KB アダプター設定の復元に失敗したため空設定へ縮退します。", exc_info=True)
        return KnowledgeBaseAdapterConfig()


def dump_adapter_config(config: KnowledgeBaseAdapterConfig) -> dict[str, object]:
    """KB アダプター設定を ``retrieval_config`` カラムへ保存する dict へ変換する。"""
    return config.model_dump(mode="json")


def _validate_chunk_consistency(settings: Settings) -> None:
    """overlay 後の chunk パラメータ整合性を再検査する(Settings の起動時検証と同等)。"""
    if settings.rag_chunk_overlap >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_overlap は chunk_size より小さくしてください。")
    if settings.rag_chunk_child_size >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_child_size は chunk_size より小さくしてください。")
    if settings.rag_chunk_min_chars >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_min_chars は chunk_size より小さくしてください。")


def resolve_effective_settings(
    global_settings: Settings,
    config: KnowledgeBaseAdapterConfig,
    *,
    scope: AdapterConfigScope,
) -> Settings:
    """グローバル設定へ KB の scope 上書きを重ねた有効 Settings を返す。

    上書きが無ければ ``global_settings`` をそのまま返す。``model_copy`` は
    バリデータを再実行しないため、ingestion scope では chunk 整合性を明示的に
    再検査し、矛盾があれば :class:`KbAdapterConfigError` を送出する。
    """
    overrides = config.settings_overrides(scope)
    if not overrides:
        return global_settings
    merged = global_settings.model_copy(update=overrides)
    if scope == "ingestion":
        _validate_chunk_consistency(merged)
    return merged


def apply_adapter_config_or_global(
    global_settings: Settings,
    config: KnowledgeBaseAdapterConfig,
    *,
    scope: AdapterConfigScope,
) -> tuple[Settings, bool]:
    """有効 Settings と「上書きが適用されたか」を返す堅牢版。

    KB 設定がグローバルと矛盾する場合はグローバルへ縮退し、取込/検索を止めない。
    戻り値 2 番目は上書きが実際に効いたかどうか。
    """
    try:
        merged = resolve_effective_settings(global_settings, config, scope=scope)
    except KbAdapterConfigError:
        logger.warning(
            "KB アダプター設定(%s)がグローバル設定と矛盾するためグローバルへ縮退します。",
            scope,
            exc_info=True,
        )
        return global_settings, False
    return merged, merged is not global_settings
