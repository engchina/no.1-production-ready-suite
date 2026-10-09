"""rag_poc の根拠付き回答フローを backend の検索・rerank・LLM で実行する。

rag_poc の ``answer_question_result``(質問ルーティング / CRAG / 親子文脈 / 生成 + 監査ラウンド)を
そのまま使い、``AnswerDependencies`` の I/O だけを差し替える。

- 検索: backend の Oracle hybrid 検索(
検索・回答プロファイルの KB フィルタとドメインキーワード付き)。

  複数の検索文は原質問主軸の重み付き RRF で融合し、同じ親の兄弟 chunk と
  親本文(``parent_text``)で親子を復元する。
  質問の理解(``inquiry_conditions``)が名指しした文書名・ページは検索条件に足し、
  profile / business_match のチャネルを RRF に加える(#546)。文書の分類は chunk の
  metadata(``document.classification``)に載せ、業務の候補の絞り込みに使う(#545)。
  文書の 1 ページ目の本文(chunk set ごとに 1 つ)も ``document.first_page_context`` に載せ、
  回答の「文書の背景」に使う(#557)。
  質問が名指しした業務(``business_domains``)は、検索範囲の文書の大分類の語の一覧から
  照合して rag_engine へ注入する(domain profile の ``business_patterns`` は使わない。#553)。
- 画面目録の連携(``RAG_SCREEN_LINKING_ENABLED``。#554): 目録は検索範囲の全文書の
  ``section_path`` から DB で作り(範囲と索引の状態ごとに cache)、選ばれた画面の chunk も DB から
  読んで rag_engine へ注入する(検索結果に出なかった画面も候補に加える)。
- rerank: backend の Cohere rerank。
- LLM: openai SDK(OCI_ENTERPRISE_AI_*)。rag_engine 内部の Responses API 経路を使う。
回答フローは同期関数のため worker thread で動かし、非同期 I/O はイベントループへ戻して実行する。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar

from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import OracleClient
from app.config import (
    Settings,
    enterprise_ai_connection_for_model,
    enterprise_ai_default_model_id,
    enterprise_ai_model_catalog,
    enterprise_ai_vision_model_id,
)
from app.rag.answer_metrics import score_answer_evaluation
from app.rag.answer_prompts import prompt_overrides
from app.rag.answer_provenance import answer_prompt_version
from app.rag.chunking_small_to_big import engine_search_text
from app.rag.cross_references import (
    REFERENCE_FROM_KEY,
    REFERENCE_LABEL_KEY,
    REFERENCE_TARGETS_KEY,
    ReferenceTarget,
    SectionIndex,
    extract_references,
    reference_targets,
    resolve_reference_specs,
    same_document_title,
    section_path_within,
    split_section_path,
    title_key,
)
from app.rag.document_crop import DocumentSourceNotFoundError, crop_png, load_parsed_source
from app.rag.element_locator import chunk_element_ids
from app.rag.entity_expansion import (
    ENTITY_EXPANSION_KEY,
    ENTITY_EXPANSION_ROLE,
    ENTITY_SEED_ANCHORS,
    EntityExpansionStore,
    expansion_metadata,
    plan_entity_expansion,
)
from app.rag.field_filter_reader import merge_field_conditions
from app.rag.stored_answer import stored_evaluation_input
from app.schemas.classification import category_label, normalize_category_value
from app.schemas.search import (
    ExtractionFieldCondition,
    RetrievedChunk,
    SearchMode,
    SearchRequest,
)

T = TypeVar("T")

logger = logging.getLogger(__name__)

ANSWER_ENGINE = "grounded"
ENGINE_SOURCE_RUN_ID = "0" * 16
# 回答フロー 1 件の I/O 待ちの上限(秒)。LLM/検索の個別 timeout は各 client が持つ。
_IO_TIMEOUT_SECONDS = 600.0
# 質問が挙げた文書名のうち、ナレッジベースにあるか確かめる数の上限(1 語ごとに SQL 1 回)。
_MAX_QUESTION_FILE_TERMS = 3
# 質問の業務名として照合する大分類の名前の最短の文字数(1 文字の名前は質問の別の語に偶然含まれる)。
_MIN_BUSINESS_NAME_CHARS = 2
# 画面目録の cache((検索条件, 索引の状態) → 目録)。索引の状態は文書の追加・再索引で変わる(#554)。
_SCREEN_CATALOG_CACHE: dict[tuple[str, str], dict[str, list[str]]] = {}
_SCREEN_CATALOG_CACHE_SIZE = 32
# section_path の見出しの区切り(chunking_small_to_big が「 > 」でつないで保存する)。
_SECTION_PATH_SEPARATOR = " > "
# 回答フローの工程の通知(進捗)1 件を待つ上限(秒)。通知は補助なので、超えたら待たずに続ける。
_STEP_NOTIFY_TIMEOUT_SECONDS = 5.0
# 交差参照(#1280): 参照先を辿る起点にする検索候補の数(RRF の上位から)。
_REFERENCE_SOURCE_ANCHORS = 5
# 交差参照: 1 つの参照先の節から足す chunk の数の上限(節の先頭から。親子階層では親本文も入る)。
_REFERENCE_CHUNKS_PER_TARGET = 2
# 交差参照: 参照先の chunk の RRF の点(起点の点に掛ける。rerank が無効なときの並びに使う)。
_REFERENCE_SCORE_DECAY = 0.5
# 交差参照: 他の文書への参照で、文書名が合う文書を探す数の上限。
_REFERENCE_DOCUMENT_CANDIDATES = 5
# 交差参照: 参照先を探す範囲に残す検索条件(ナレッジベースと旧版の扱いだけ)。文書名・ページ・
# 分類などの絞り込みは参照を辿るときには使わない(業務の絞り込みで正当な参照を止めない。§7.2)。
_REFERENCE_SCOPE_FILTER_KEYS = ("knowledge_base_id", "include_superseded")
# 診断に出す交差参照の件数の上限。
_REFERENCE_DIAGNOSTICS_LIMIT = 10
# 実体の 1 段の拡張(#1362)で足した chunk を置く候補の位置(先頭からこの件数の後ろ)。rag_engine は
# この chunk を rerank で並べ替えず(位置を保つ)、文書の選択で後回しにしない。
_ENTITY_INSERT_AFTER = 3
# 実体の拡張の chunk の RRF の点(起点の点に掛ける。rerank が無効なときの並びに使う)。
_ENTITY_SCORE_DECAY = 0.5
# 実体の拡張の範囲に残す検索条件(交差参照と同じ。ナレッジベースと旧版の扱いだけ)。
_ENTITY_SCOPE_FILTER_KEYS = _REFERENCE_SCOPE_FILTER_KEYS

# 回答フローの工程の通知: (工程名, "started" / "success" / "error", 経過秒)。
type StepCallback = Callable[[str, str, float], Awaitable[None]]
# 回答フローの各工程を進捗(SSE の stage)へ出すときの名前の接頭辞。後ろに工程名
# (「質問の理解」「文書検索（1回目）」など、利用者向けの日本語)を付け、
# 画面と時間切れの文言はその工程名を出す(#593)。
ANSWER_STEP_STAGE_PREFIX = "answer_step:"


def answer_step_stage(name: str) -> str:
    """回答フローの工程名を、進捗の工程の名前にする。"""
    return f"{ANSWER_STEP_STAGE_PREFIX}{name}"


@dataclass(frozen=True)
class AnswerOutcome:
    """回答の本文・引用・診断(非機密)。"""

    answer: str
    citations: list[RetrievedChunk]
    diagnostics: dict[str, Any]
    context_text: str
    # 標準回答による LLM 評価(evaluate_answer_payload)の入力。回答記録に保存する。
    evaluation_input: dict[str, Any] | None = None


# evaluate_answer_payload が読む回答 payload のキー(answer_result_payload の部分集合)。
EVALUATION_INPUT_KEYS = (
    "question",
    "answer_text",
    "reasoning_summary",
    "insufficient_reason",
    "used_images",
    "external_data_required",
    "external_data_items",
    "evidence_items",
)


@dataclass
class _SearchState:
    """検索で見つけた backend chunk を chunk_id で保持し、引用へ戻すために使う。

    work_dir は rag_engine の output_dir。根拠画像を ``<work_dir>/<run>/crops/`` へ切り出す。
    """

    chunks: dict[str, RetrievedChunk] = field(default_factory=dict)
    work_dir: Path | None = None
    sources: dict[str, bytes | None] = field(default_factory=dict)
    # 質問の文書名・ページを足した検索条件(#546)。CRAG の各回で同じなので、最初の検索で
    # 1 回だけ決める。
    filters: dict[str, str] | None = None
    # 文書の分類(rag_documents.classification)を document_id ごとに保持する(#545)。
    classifications: dict[str, dict[str, object]] = field(default_factory=dict)
    loaded_classification_ids: set[str] = field(default_factory=set)
    # 画面目録(#554)。CRAG の各回で同じなので、1 回の回答で 1 回だけ作る。
    screen_catalog: dict[str, list[str]] | None = None
    # 質問から読み取った抽出項目の条件のうち検索に足したもの(#652)と、それを外した検索条件。
    # 読み取った条件で 0 件なら、外した条件で 1 回だけ検索し直す(auto_field_relaxed)。
    auto_field_conditions: list[ExtractionFieldCondition] = field(default_factory=list)
    filters_without_auto: dict[str, str] | None = None
    auto_field_relaxed: bool = False
    # 文書の 1 ページ目の本文(rag_chunk_sets.first_page_context)を chunk_set_id ごとに持つ(#557)。
    first_page_contexts: dict[str, dict[str, object]] = field(default_factory=dict)
    loaded_first_page_chunk_set_ids: set[str] = field(default_factory=set)
    # 交差参照(#1280)の参照先の chunk(参照先ごと。CRAG の各回で同じ参照先を読み直さない)と、
    # 足した chunk の記録(診断に出す。chunk_id → 起点・表記・参照先)。
    reference_chunks: dict[tuple[str, ...], list[RetrievedChunk]] = field(default_factory=dict)
    reference_expansions: dict[str, dict[str, object]] = field(default_factory=dict)
    # 実体の 1 段の拡張(#1362)で足した chunk の記録(診断に出す。chunk_id → 実体・段・起点)と、
    # 同じ起点での計画(CRAG の各回で同じ起点なら SQL を読み直さない)。
    entity_expansions: dict[str, dict[str, object]] = field(default_factory=dict)
    entity_plans: dict[tuple[str, ...], list[Any]] = field(default_factory=dict)
    # 取込で参照先を解決していない chunk の参照先(#1382。chunk_id ごと)と、その解決に使う
    # 文書の見出しの列((document_id, chunk_set_id) ごと。1 回の回答で 1 回だけ読む)。
    query_reference_targets: dict[str, list[ReferenceTarget]] = field(default_factory=dict)
    reference_section_indexes: dict[tuple[str, str], SectionIndex] = field(default_factory=dict)


def answer_images_enabled(settings: Settings) -> bool:
    """回答で根拠の原画像を Vision モデルへ添付するか(設定が有効で、既定の Vision モデルがある)。"""
    return settings.rag_answer_vision_enabled and bool(enterprise_ai_vision_model_id(settings))


def build_engine_settings(
    settings: Settings,
    *,
    output_dir: Path,
    runtime_knowledge_path: Path | None = None,
    answer_model_id: str | None = None,
) -> Any:
    """backend Settings から rag_engine の Settings を作る(env や .env は読まない)。

    ``answer_model_id`` を渡すと、回答のモデル(とその接続)をそのモデルにする。チャットの
    モデル比較で、列ごとのモデルで答えるために使う(#593)。省略時は既定のモデル。Vision の
    モデルは比較の対象ではないので、常に既定の Vision モデルを使う。

    業務 profile の JSON はここでは決まらない。`rag_engine.profiles` が process の環境変数
    `RAG_ENGINE_DOMAIN_PROFILE_FILE`(未指定なら作業ディレクトリの `domain_profile.json`)を
    直接読む(#569)。
    """
    from rag_engine.config import get_settings as engine_get_settings

    # 回答のモデルと Vision のモデルは、それぞれのモデルの接続で呼ぶ(#533)。
    answer_model = answer_model_id or enterprise_ai_default_model_id(settings)
    vision_model = enterprise_ai_vision_model_id(settings)
    answer = enterprise_ai_connection_for_model(settings, answer_model)
    vision = enterprise_ai_connection_for_model(settings, vision_model)
    environ = {
        "OCI_ENTERPRISE_AI_ENDPOINT": answer.endpoint,
        "OCI_ENTERPRISE_AI_API_KEY": answer.api_key,
        "OCI_ENTERPRISE_AI_PROJECT_OCID": answer.project_ocid,
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": answer_model,
        "OCI_ENTERPRISE_AI_VLM_MODEL": vision_model,
        "OCI_ENTERPRISE_AI_VLM_ENDPOINT": vision.endpoint,
        "OCI_ENTERPRISE_AI_VLM_API_KEY": vision.api_key,
        "OCI_ENTERPRISE_AI_VLM_PROJECT_OCID": vision.project_ocid,
        # rag_engine は rerank_model と oci_compartment_id が両方あるときだけ rerank を実行する
        # (answer_records._rerank_configured)。rerank 自体は backend の Cohere client を注入して
        # 呼ぶが、この 2 値を渡さないと RAG_RERANK_ENABLED=true でも
        # 常に「未実行」になる(#275)。
        "OCI_COMPARTMENT_ID": settings.oci_compartment_id,
        "RERANK_MODEL": settings.oci_genai_rerank_model,
        "RAG_ENGINE_PROFILE": settings.rag_answer_profile,
        "RAG_ENGINE_OUTPUT_DIR": str(output_dir),
        "LLM_REQUEST_TIMEOUT_SECONDS": str(int(settings.oci_enterprise_ai_timeout_seconds)),
        "LLM_RETRIES": str(int(settings.oci_enterprise_ai_max_retries)),
        # 原画像を添付する回だけ既定の Vision モデルで答える(ほかは既定のテキストモデル。#649)。
        "RAG_ENGINE_ANSWER_IMAGES": "1" if answer_images_enabled(settings) else "0",
        # 画面目録で操作画面を探す(#554)。rag_engine は明示した environ だけを読む。
        "RAG_ENGINE_SCREEN_LINKING": "1" if settings.rag_screen_linking_enabled else "0",
        # 根拠の無い要求だけの再検索と予算(#1279)。
        "RAG_ENGINE_REQUEST_COVERAGE_RETRIEVAL": (
            "1" if settings.rag_request_coverage_retrieval_enabled else "0"
        ),
        "RAG_ENGINE_REQUEST_COVERAGE_MAX_QUERIES": str(settings.rag_request_coverage_max_queries),
        "RAG_ENGINE_REQUEST_COVERAGE_MAX_CHUNKS": str(settings.rag_request_coverage_max_chunks),
        "RAG_ENGINE_REQUEST_COVERAGE_DEADLINE_SECONDS": str(
            settings.rag_request_coverage_deadline_seconds
        ),
    }
    if runtime_knowledge_path is not None:
        environ["RUNTIME_KNOWLEDGE_PATH"] = str(runtime_knowledge_path)
    engine_settings = engine_get_settings(environ=environ, dotenv_path=None)
    return replace(engine_settings, domain_keywords_override=tuple(settings.rag_domain_keywords))


@dataclass(frozen=True)
class AnswerScope:
    """利用者が確認の質問で選んだ条件と対象範囲(#717)。"""

    # 回答のプロンプトの前置きに入れる説明(質問・選んだ選択肢・前提・その他・対象範囲)。
    context: str
    # 補助の検索文に足す語。
    search_terms: tuple[str, ...] = ()
    # 回答の末尾に出す対象の範囲(例: 「出張旅費規程」の「第6条」(p.2))。
    label: str = ""


class AnswerEngine:
    """rag_poc 回答フローを backend の I/O で駆動する。"""

    def __init__(
        self,
        settings: Settings,
        *,
        oracle: OracleClient,
        genai: OciGenAiClient,
        runtime_knowledge_payload: Mapping[str, object] | None = None,
        answer_model_id: str | None = None,
        auto_field_conditions: Sequence[ExtractionFieldCondition] = (),
        approved_faq: tuple[str, str] | None = None,
        scope: AnswerScope | None = None,
        entity_store: EntityExpansionStore | None = None,
    ) -> None:
        self._settings = settings
        self._oracle = oracle
        # 実体の表を読む SQL(#1362)。None は Oracle(``EntityStore``)。テストは同じ規則の表を渡す。
        self._entity_store = entity_store
        self._genai = genai
        self._runtime_knowledge_payload = runtime_knowledge_payload
        # 回答のモデル(チャットのモデル比較の列のモデル。#593)。None は既定のモデル。
        self._answer_model_id = answer_model_id or None
        # 質問から読み取った抽出項目の条件(#652)。手の条件の項目には足さない。
        self._auto_field_conditions = list(auto_field_conditions)
        # 利用者が選んだ類似の承認済み FAQ(質問・承認済みの回答)。選んだときは検索せず、質問と FAQ
        # だけから回答する(#684 / #702)。
        self._approved_faq = approved_faq
        # 利用者が確認の質問で選んだ条件と対象範囲(#717)。範囲での絞り込みは request.filters の
        # page_ranges が行い、ここでは回答の前提・検索に足す語・回答の末尾の表示に使う。
        self._scope = scope

    async def run(
        self, request: SearchRequest, *, step_callback: StepCallback | None = None
    ) -> AnswerOutcome:
        """回答する。``step_callback`` は回答フローの各工程の開始・終了を受け取る(進捗。#593)。"""
        if self._approved_faq is not None:
            return await self._answer_from_approved_faq(request)
        loop = asyncio.get_running_loop()
        state = _SearchState()
        try:
            overrides = await self._oracle.answer_prompt_overrides()
        except Exception:  # noqa: BLE001 - 編集したプロンプトは補助。既定値で回答を続ける。
            logger.warning("answer prompt overrides load failed", exc_info=True)
            overrides = {}
        business_names = await self._business_names(request)
        with tempfile.TemporaryDirectory(prefix="rag-engine-answer-") as work:
            work_dir = Path(work)
            state.work_dir = work_dir
            runtime_path = None
            if self._runtime_knowledge_payload:
                runtime_path = work_dir / "runtime_knowledge.json"
                runtime_path.write_text(
                    json.dumps(self._runtime_knowledge_payload, ensure_ascii=False),
                    encoding="utf-8",
                )
            engine_settings = build_engine_settings(
                self._settings,
                output_dir=work_dir,
                runtime_knowledge_path=runtime_path,
                answer_model_id=self._answer_model_id,
            )
            result = await asyncio.to_thread(
                self._answer_sync,
                request,
                engine_settings,
                loop,
                state,
                overrides,
                business_names,
                step_callback,
            )
        outcome = _outcome_from_result(result, state)
        if self._scope is not None:
            outcome.diagnostics["scope"] = {
                "label": self._scope.label,
                "context": self._scope.context,
                "page_ranges": request.filters.get("page_ranges", ""),
            }
            if self._scope.label and outcome.answer.strip():
                outcome = replace(
                    outcome, answer=f"{outcome.answer}\n\n（対象: {self._scope.label}）"
                )
        outcome.diagnostics["models"] = self._models_used(
            vision=getattr(result, "image_prompt_mode", "") == "vision_attachments"
        )
        # 回答を作ったプロンプトの版（編集した内容とコードのプロンプト。#1276）。
        outcome.diagnostics.setdefault("provenance", {})["prompt_version"] = answer_prompt_version(
            overrides
        )
        if state.entity_expansions:
            outcome.diagnostics["entity_expansion"] = {
                "added_count": len(state.entity_expansions),
                "max_chunks": self._settings.rag_entity_expansion_max_chunks,
                "chunks": list(state.entity_expansions.values())[:_REFERENCE_DIAGNOSTICS_LIMIT],
            }
        if state.reference_expansions:
            outcome.diagnostics["reference_expansion"] = {
                "added_count": len(state.reference_expansions),
                "max_chunks": self._settings.rag_reference_expansion_max_chunks,
                "chunks": list(state.reference_expansions.values())[:_REFERENCE_DIAGNOSTICS_LIMIT],
            }
        if state.auto_field_conditions:
            outcome.diagnostics["auto_field_filter"] = {
                "conditions": [condition.model_dump() for condition in state.auto_field_conditions],
                "relaxed": state.auto_field_relaxed,
            }
        return outcome

    async def _answer_from_approved_faq(self, request: SearchRequest) -> AnswerOutcome:
        """利用者が選んだ類似問の承認済み FAQ だけから回答する(検索しない。#702)。"""
        from rag_engine.generation.faq_answer import answer_from_approved_faq

        assert self._approved_faq is not None
        faq_question, faq_answer = self._approved_faq
        with tempfile.TemporaryDirectory(prefix="rag-engine-faq-") as work:
            engine_settings = build_engine_settings(
                self._settings,
                output_dir=Path(work),
                runtime_knowledge_path=None,
                answer_model_id=self._answer_model_id,
            )
            answer = await asyncio.to_thread(
                answer_from_approved_faq, request.query, faq_question, faq_answer, engine_settings
            )
        return AnswerOutcome(
            answer=f"{answer}\n\n（出典: 承認済み FAQ「{faq_question}」）",
            citations=[],
            diagnostics={
                "answer_source": "approved_faq",
                # 承認済みの回答をそのまま使うので、対応は「答えた」（#1235）。
                "outcome": "answered",
                "envelope": {"schema_version": 1, "outcome": "answered"},
                "approved_faq_question": faq_question,
                # 回答した時点の承認済みの回答。FAQ が変わっても根拠の原文を出せる(#737)。
                "approved_faq_answer": faq_answer,
                "models": self._models_used(),
            },
            # 回答の安全チェックが照合する根拠は、選んだ FAQ だけ。
            context_text=f"類似問: {faq_question}\n承認済みの回答: {faq_answer}",
        )

    def _models_used(self, *, vision: bool = False) -> dict[str, Any]:
        """回答フローで使ったモデル(画面に出す。#649)。

        LLM(既定のテキストモデル。比較の列ではその列のモデル)は質問の理解・拡張と回答の生成に使う。
        根拠の原画像を添付して答えたときは、その回答を既定の Vision モデルで作る(``vision``)。
        rerank は無効なら空。
        """
        settings = self._settings
        labels = {
            model.model_id: model.display_name or model.model_id
            for model in enterprise_ai_model_catalog(settings)
        }

        def model(model_id: str) -> dict[str, str]:
            return {"model_id": model_id, "label": labels.get(model_id, model_id)}

        return {
            "llm": model(self._answer_model_id or enterprise_ai_default_model_id(settings)),
            "vision": model(enterprise_ai_vision_model_id(settings)) if vision else None,
            "embedding": settings.oci_genai_embedding_model,
            "rerank": settings.oci_genai_rerank_model if settings.rag_rerank_enabled else "",
        }

    async def retrieve(self, request: SearchRequest) -> list[RetrievedChunk]:
        """回答を作らずに検索だけを行い、候補の chunk を順位順に返す(#593)。

        回答の検索(``_search``)を原質問 1 本で呼ぶ。質問の理解・質問の拡張・rerank・CRAG・回答の
        生成は行わず、LLM を呼ばない(embedding と Oracle の検索だけ)。KB の検索テストとレシピの
        検索比較で、引用の候補を確かめるために使う。件数は ``request.top_k``。
        """
        state = _SearchState()
        result = await self._search(
            request,
            state,
            retrieval_queries=[request.query],
            candidate_limit=max(1, int(request.top_k)),
            # 検索だけの結果は上位 top_k 件の候補なので、交差参照の chunk は足さない(#1280)。
            expand_references=False,
        )
        return [
            state.chunks[child.chunk_uid]
            for child in result.child_chunks
            if child.chunk_uid in state.chunks
        ]

    async def _business_names(self, request: SearchRequest) -> list[str]:
        """検索範囲(``request.filters``)の文書の大分類の語の一覧を、1 回の回答で 1 回読む(#553)。

        読めないときは空(業務の絞り込みをせずに回答を続ける)。
        """
        try:
            return await self._oracle.retrieval_large_categories(dict(request.filters))
        except Exception:  # noqa: BLE001 - 業務の絞り込みは補助。絞らずに回答を続ける。
            logger.warning("business names load failed", exc_info=True)
            return []

    def _answer_sync(
        self,
        request: SearchRequest,
        engine_settings: Any,
        loop: asyncio.AbstractEventLoop,
        state: _SearchState,
        overrides: Mapping[str, str] | None = None,
        business_names: Sequence[str] = (),
        step_callback: StepCallback | None = None,
    ) -> Any:
        from rag_engine.adapters.oci import parse_multimodal_response, parse_text_response
        from rag_engine.dependencies import AnswerDependencies, bind_dependencies
        from rag_engine.generation.answering import answer_question_result
        from rag_engine.generation.execution_record import bind_step_listener
        from rag_engine.knowledge.classification import classification_filter_from_values
        from rag_engine.retrieval.scope import RETRIEVAL_SCOPE_KNOWLEDGE_BASE

        def run_async(factory: Callable[[], Awaitable[T]]) -> T:
            return asyncio.run_coroutine_threadsafe(_call(factory), loop).result(
                _IO_TIMEOUT_SECONDS
            )

        dependencies = AnswerDependencies(
            search=lambda **kwargs: run_async(lambda: self._search(request, state, **kwargs)),
            check_ready=lambda **kwargs: None,
            parse_text=parse_text_response,
            parse_images=parse_multimodal_response,
            rerank=lambda query, documents, settings, top_n=None: run_async(
                lambda: self._rerank(query, list(documents), top_n)
            ),
            business_domains=lambda question: question_business_domains(question, business_names),
            # 画面目録の連携(#554)。rag_engine は設定が有効なときだけ呼ぶ。
            screen_catalog=lambda **kwargs: run_async(lambda: self._screen_catalog(request, state)),
            screen_chunks=lambda *, links, existing, **kwargs: run_async(
                lambda: self._screen_chunks(request, state, links, existing)
            ),
        )

        def notify_step(name: str, outcome: str, elapsed: float) -> None:
            # 工程の順序を保つため、イベントループで通知し終わるのを待つ(短い上限付き)。
            if step_callback is not None:
                asyncio.run_coroutine_threadsafe(
                    _call(lambda: step_callback(name, outcome, elapsed)), loop
                ).result(_STEP_NOTIFY_TIMEOUT_SECONDS)

        with (
            bind_dependencies(dependencies),
            prompt_overrides(overrides or {}),
            bind_step_listener(notify_step if step_callback is not None else None),
        ):
            return answer_question_result(
                request.query,
                ENGINE_SOURCE_RUN_ID,
                ["docling"],
                engine_settings,
                chunk_top_k=max(1, int(request.top_k)),
                chunk_neighbor_count=self._settings.rag_neighbor_child_count,
                query_strategy=self._settings.rag_query_strategy,
                answer_flow=self._settings.rag_answer_flow,
                rerank_enabled=self._settings.rag_rerank_enabled,
                retrieval_scope=RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
                # RAG 検索の画面は回答を作らない(CRAG も使わない。#649)。
                generate_answer=request.generate_answer,
                # 絞り込み自体は _search の hybrid_search(request.filters)が行う。
                # ここでは回答のプロンプトと実行記録に条件を載せるために渡す。
                classification_filter=classification_filter_from_values(
                    large_category=request.filters.get("large_category", ""),
                    middle_category=request.filters.get("middle_category", ""),
                    small_category=request.filters.get("small_category", ""),
                    as_of=request.filters.get("as_of", ""),
                ),
                scope=(
                    (self._scope.context, self._scope.search_terms)
                    if self._scope is not None
                    else None
                ),
            )

    async def _search(
        self,
        request: SearchRequest,
        state: _SearchState,
        *,
        retrieval_queries: Sequence[str],
        candidate_limit: int = 24,
        vector_only_queries: Sequence[str] = (),
        inquiry_conditions: Any = None,
        settings: Any = None,
        expand_references: bool = True,
        **_: object,
    ) -> Any:
        from rag_engine.models.storage import HybridSearchResult

        queries = [query for query in dict.fromkeys(q.strip() for q in retrieval_queries) if query]
        if not queries:
            return HybridSearchResult(child_chunks=[], all_chunks=[])
        if state.filters is None:
            filters = await self._question_filters(dict(request.filters), inquiry_conditions)
            state.filters, state.auto_field_conditions = merge_field_conditions(
                filters, self._auto_field_conditions
            )
            if state.auto_field_conditions:
                state.filters_without_auto = filters
        vector_only = {query.strip() for query in vector_only_queries}
        embeddings = await self._genai.embed(queries, input_type="SEARCH_QUERY")
        k = float(self._settings.rag_rrf_k)
        derived_weight = 1.0 / max(1, len(queries) - 1)
        while True:
            fused: dict[str, float] = {}
            rankings: list[list[str]] = []
            for index, (query, embedding) in enumerate(zip(queries, embeddings, strict=False)):
                mode = SearchMode.VECTOR if query in vector_only else SearchMode.HYBRID
                hits = await self._oracle.hybrid_search(
                    query, embedding, candidate_limit, mode=mode, filters=dict(state.filters)
                )
                rankings.append([hit.chunk_id for hit in hits])
                # 原質問を主軸にし、派生検索文は合計で原質問 1 本分の票に抑える(rag_poc #975)。
                weight = 1.0 if index == 0 else derived_weight
                for rank, hit in enumerate(hits, start=1):
                    state.chunks.setdefault(hit.chunk_id, hit)
                    fused[hit.chunk_id] = fused.get(hit.chunk_id, 0.0) + weight / (k + rank)
            # 質問から読み取った条件(#652)で 0 件なら、その条件を外して 1 回だけ検索し直す
            # (以降の検索(CRAG の補正検索など)も外したまま)。
            if fused or state.filters_without_auto is None or state.auto_field_relaxed:
                break
            state.filters = state.filters_without_auto
            state.auto_field_relaxed = True
        await self._load_classifications([state.chunks[chunk_id] for chunk_id in fused], state)
        if inquiry_conditions is not None:
            pool = [
                _stored_child(
                    state.chunks[chunk_id],
                    rrf_score=0.0,
                    classification=state.classifications.get(state.chunks[chunk_id].document_id),
                )
                for chunk_id in fused
            ]
            channels = _inquiry_channel_rankings(
                pool,
                inquiry_conditions,
                rankings,
                limit=candidate_limit,
                profile_enabled=bool(getattr(settings, "profile_channel_enabled", True)),
            )
            for _channel, channel_weight, ranking in channels:
                for rank, (chunk_id, _score) in enumerate(ranking, start=1):
                    fused[chunk_id] = fused.get(chunk_id, 0.0) + channel_weight / (k + rank)
        ranked = sorted(fused, key=lambda chunk_id: -fused[chunk_id])[:candidate_limit]
        anchors = [state.chunks[chunk_id] for chunk_id in ranked]
        siblings = await self._oracle.context_group_siblings(
            anchors, max_chunks_per_group=max(1, self._settings.rag_context_group_max_chunks)
        )
        for sibling in siblings:
            state.chunks.setdefault(sibling.chunk_id, sibling)
        # 上位の候補が本文で参照する節の chunk(#1280)。起点の候補の直後に置き、rerank に任せる。
        references = (
            await self._reference_expansion(
                request,
                state,
                anchors,
                existing={chunk.chunk_id for chunk in [*anchors, *siblings]},
            )
            if expand_references
            else {}
        )
        referenced = [chunk for chunks in references.values() for chunk in chunks]
        # 質問と上位の候補の実体から、実体の表との join で関連する chunk を 1 段だけ足す(#1362)。
        entity_chunks = (
            await self._entity_expansion(request, state, anchors) if expand_references else []
        )
        entity_ids = {chunk.chunk_id for chunk in entity_chunks}
        if referenced or entity_chunks:
            await self._load_classifications([*referenced, *entity_chunks], state)
        if answer_images_enabled(self._settings):
            for chunk in [*anchors, *siblings, *referenced, *entity_chunks]:
                await self._materialize_image_evidence(chunk, state)
            anchors = [state.chunks[chunk.chunk_id] for chunk in anchors]
            siblings = [state.chunks.get(chunk.chunk_id, chunk) for chunk in siblings]
            references = {
                anchor_id: [state.chunks.get(chunk.chunk_id, chunk) for chunk in chunks]
                for anchor_id, chunks in references.items()
            }
            entity_chunks = [state.chunks.get(chunk.chunk_id, chunk) for chunk in entity_chunks]
        await self._load_first_page_contexts(
            [*anchors, *siblings, *referenced, *entity_chunks], state
        )
        classifications = state.classifications
        children = []
        for chunk in anchors:
            if chunk.chunk_id in entity_ids:
                # 実体の拡張で足す chunk は、拡張の位置に置く(下)。
                continue
            children.append(
                _stored_child(
                    chunk,
                    rrf_score=fused.get(chunk.chunk_id, 0.0),
                    classification=classifications.get(chunk.document_id),
                    first_page_context=_first_page_context(chunk, state),
                )
            )
            children.extend(
                _stored_child(
                    reference,
                    rrf_score=fused.get(chunk.chunk_id, 0.0) * _REFERENCE_SCORE_DECAY,
                    classification=classifications.get(reference.document_id),
                    first_page_context=_first_page_context(reference, state),
                )
                for reference in references.get(chunk.chunk_id, ())
                if reference.chunk_id not in entity_ids
            )
        if entity_chunks:
            insert_at = min(len(children), _ENTITY_INSERT_AFTER)
            base_score = fused.get(anchors[0].chunk_id, 0.0) if anchors else 0.0
            children[insert_at:insert_at] = [
                _stored_child(
                    chunk,
                    rrf_score=base_score * _ENTITY_SCORE_DECAY,
                    classification=classifications.get(chunk.document_id),
                    first_page_context=_first_page_context(chunk, state),
                )
                for chunk in entity_chunks
            ]
        all_children = {chunk.chunk_uid: chunk for chunk in children}
        for sibling in siblings:
            all_children.setdefault(
                sibling.chunk_id,
                _stored_child(
                    sibling,
                    rrf_score=0.0,
                    classification=classifications.get(sibling.document_id),
                    first_page_context=_first_page_context(sibling, state),
                ),
            )
        parents = _stored_parents(list(all_children.values()), state)
        return HybridSearchResult(
            child_chunks=children, all_chunks=[*all_children.values(), *parents]
        )

    async def _entity_expansion(
        self,
        request: SearchRequest,
        state: _SearchState,
        anchors: Sequence[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """質問と上位の候補の実体から、関連する chunk を 1 段だけ足す(#1362)。

        実体は文書レシピで実体の抽出を選んだ文書にだけある。足す chunk は検索範囲のナレッジベース(と
        利用者の権限・旧版の扱い)の中だけで、合計 ``rag_entity_expansion_max_chunks`` 件まで。
        上位の候補(起点)は足さない(すでに候補の先頭にある)。読めないとき(実体の表が無い古い
        schema など)は足さずに回答を続ける。LLM は呼ばない。
        """
        if not self._settings.rag_entity_expansion_enabled:
            # 検索・回答プロファイル(と評価の rag_overrides)で選んだときだけ(#1388)。
            return []
        scope = {
            key: value
            for key, value in request.filters.items()
            if key in _ENTITY_SCOPE_FILTER_KEYS and value.strip()
        }
        seeds = list(anchors[:ENTITY_SEED_ANCHORS])
        key = (
            request.query,
            json.dumps(scope, ensure_ascii=False, sort_keys=True),
            *(chunk.chunk_id for chunk in seeds),
        )
        plan = state.entity_plans.get(key)
        if plan is None:
            try:
                plan = await plan_entity_expansion(
                    self._entity_store_or_default(),
                    scope,
                    question=request.query,
                    seed_chunks=seeds,
                    exclude_chunk_ids={chunk.chunk_id for chunk in seeds},
                    max_chunks=self._settings.rag_entity_expansion_max_chunks,
                )
            except Exception:  # noqa: BLE001 - 実体の拡張は補助。足さずに回答を続ける。
                logger.warning("entity expansion failed", exc_info=True)
                plan = []
            state.entity_plans[key] = plan
        added: list[RetrievedChunk] = []
        for item in plan:
            marked = expansion_metadata(
                state.chunks.get(item.chunk.chunk_id, item.chunk), item.info
            )
            state.chunks[item.chunk.chunk_id] = marked
            state.entity_expansions.setdefault(
                item.chunk.chunk_id,
                {
                    "chunk_id": item.chunk.chunk_id,
                    "document_id": item.chunk.document_id,
                    **item.info,
                },
            )
            added.append(marked)
        return added

    def _entity_store_or_default(self) -> EntityExpansionStore:
        if self._entity_store is None:
            from app.clients.entity_store import EntityStore

            self._entity_store = EntityStore(self._oracle)
        return self._entity_store

    async def _reference_expansion(
        self,
        request: SearchRequest,
        state: _SearchState,
        anchors: Sequence[RetrievedChunk],
        *,
        existing: set[str],
    ) -> dict[str, list[RetrievedChunk]]:
        """上位の候補が本文で参照する節の chunk を、上限まで集める(起点の chunk_id ごと。#1280)。

        参照先は取込時に chunk の metadata(``reference_targets_json``)へ解決してある。参照の無い
        候補だけなら DB を読まない。足す chunk は 1 回の検索で合計
        ``rag_reference_expansion_max_chunks`` 件まで、参照先 1 つにつき節の先頭から
        ``_REFERENCE_CHUNKS_PER_TARGET`` 件まで。順位は rag_engine の rerank が決める(LLM は
        呼ばない)。参照先の範囲は検索範囲のナレッジベース(と利用者の権限)で、文書名・ページ・
        分類の絞り込みは使わない。読めない参照先は足さない(回答は続ける)。
        """
        if not self._settings.rag_reference_expansion_enabled:
            return {}
        budget = self._settings.rag_reference_expansion_max_chunks
        scope = {
            key: value
            for key, value in request.filters.items()
            if key in _REFERENCE_SCOPE_FILTER_KEYS and value.strip()
        }
        seen = set(existing)
        added: dict[str, list[RetrievedChunk]] = {}
        count = 0
        for anchor in anchors[:_REFERENCE_SOURCE_ANCHORS]:
            try:
                targets = await self._anchor_reference_targets(scope, state, anchor)
            except Exception:  # noqa: BLE001 - 参照先は補助。この候補の参照を辿らずに続ける。
                logger.warning("reference target resolution failed", exc_info=True)
                continue
            for target in targets:
                if count >= budget:
                    return added
                try:
                    chunks = await self._reference_target_chunks(scope, state, anchor, target)
                except Exception:  # noqa: BLE001 - 参照先は補助。この参照先を足さずに続ける。
                    logger.warning("reference target load failed", exc_info=True)
                    continue
                fresh = [chunk for chunk in chunks if chunk.chunk_id not in seen]
                for chunk in fresh[: min(_REFERENCE_CHUNKS_PER_TARGET, budget - count)]:
                    seen.add(chunk.chunk_id)
                    marked = state.chunks.setdefault(
                        chunk.chunk_id,
                        chunk.model_copy(
                            update={
                                "metadata": {
                                    **chunk.metadata,
                                    REFERENCE_FROM_KEY: anchor.chunk_id,
                                    REFERENCE_LABEL_KEY: target.label,
                                }
                            }
                        ),
                    )
                    added.setdefault(anchor.chunk_id, []).append(marked)
                    state.reference_expansions.setdefault(
                        chunk.chunk_id,
                        {
                            "chunk_id": chunk.chunk_id,
                            "document_id": chunk.document_id,
                            "from_chunk_id": anchor.chunk_id,
                            "label": target.label,
                            "document_title": target.document_title,
                            "section_path": str(chunk.metadata.get("section_path") or ""),
                            "resolved_at": (
                                "query"
                                if anchor.chunk_id in state.query_reference_targets
                                else "ingest"
                            ),
                        },
                    )
                    count += 1
        return added

    async def _anchor_reference_targets(
        self, scope: dict[str, str], state: _SearchState, anchor: RetrievedChunk
    ) -> list[ReferenceTarget]:
        """候補の chunk の参照先。取込で解決した参照先が無ければ本文から抜き出して解決する(#1382)。

        取込は参照の表記のある chunk に ``reference_targets_json`` を残す(参照が自分の節だけでも
        空の列)。印の無い chunk(抽出を広げる前に取り込んだ chunk)は、本文に参照の表記が
        あるときだけ、その文書の同じ版(chunk_set)の見出しの列を検索と同じ範囲(KB・権限・
        有効な chunk_set)で 1 回読み、取込と同じ規則で決める。RAPTOR の要約 chunk は辿らない。
        """
        metadata = anchor.metadata
        if REFERENCE_TARGETS_KEY in metadata:
            return reference_targets(metadata)
        if anchor.chunk_id in state.query_reference_targets:
            return state.query_reference_targets[anchor.chunk_id]
        specs = [] if metadata.get("raptor_summary") else extract_references(anchor.text)
        targets: list[ReferenceTarget] = []
        if specs:
            file_name = anchor.file_name or ""
            chunk_set_id = str(metadata.get("chunk_set_id") or "")
            key = (anchor.document_id, chunk_set_id)
            same_document = any(
                not spec.document_title or same_document_title(spec.document_title, file_name)
                for spec in specs
            )
            if same_document and key not in state.reference_section_indexes:
                filters = {**scope, "document_id": anchor.document_id}
                if chunk_set_id:
                    filters["chunk_set_id"] = chunk_set_id
                sections = await self._oracle.retrieval_screen_sections(filters)
                state.reference_section_indexes[key] = SectionIndex(
                    path for _name, path, _count in sections
                )
            targets = resolve_reference_specs(
                specs,
                # 他の文書への参照だけなら、この文書の見出しは読まない(参照先は検索範囲から探す)。
                state.reference_section_indexes[key] if same_document else SectionIndex(()),
                citing_path=split_section_path(metadata.get("section_path")),
                document_title=file_name,
            )
        state.query_reference_targets[anchor.chunk_id] = targets
        return targets

    async def _reference_target_chunks(
        self,
        scope: dict[str, str],
        state: _SearchState,
        anchor: RetrievedChunk,
        target: ReferenceTarget,
    ) -> list[RetrievedChunk]:
        """参照先の節の chunk(読み順)。同じ文書は参照元と同じ版(chunk_set)から読む。"""
        limit = _REFERENCE_CHUNKS_PER_TARGET + _REFERENCE_SOURCE_ANCHORS
        if target.document_title is None:
            if target.section_path is None:
                return []
            chunk_set_id = str(anchor.metadata.get("chunk_set_id") or "")
            key = ("same", anchor.document_id, chunk_set_id, target.section_path)
            if key not in state.reference_chunks:
                rows = await self._oracle.retrieval_reference_chunks(
                    scope,
                    document_id=anchor.document_id,
                    section_path=target.section_path,
                    chunk_set_id=chunk_set_id or None,
                    limit=limit,
                )
                state.reference_chunks[key] = [
                    row
                    for row in rows
                    if section_path_within(row.metadata.get("section_path"), target.section_path)
                ]
            return state.reference_chunks[key]
        if target.kind == "document":
            # 文書全体への参照は、どの節を足すか決まらないので辿らない。
            return []
        key = ("document", title_key(target.document_title), target.kind, target.key)
        if key not in state.reference_chunks:
            state.reference_chunks[key] = await self._other_document_reference_chunks(
                scope, anchor, target, limit=limit
            )
        return state.reference_chunks[key]

    async def _other_document_reference_chunks(
        self,
        scope: dict[str, str],
        anchor: RetrievedChunk,
        target: ReferenceTarget,
        *,
        limit: int,
    ) -> list[RetrievedChunk]:
        """他の文書への参照を検索範囲の文書から解決し、参照先の節の chunk を返す(#1280)。

        文書名が合う文書のうち、名前が文書名とそろうものを優先し、無ければ名前の短いものを選ぶ。
        節は、その文書の見出しの列から取込時と同じ規則で決める。版は 1 つの chunk_set にそろえる。
        """
        title = target.document_title or ""
        documents = await self._oracle.retrieval_reference_documents(
            scope, title=title_key(title), limit=_REFERENCE_DOCUMENT_CANDIDATES
        )
        candidates = [
            (document_id, file_name)
            for document_id, file_name in documents
            if document_id != anchor.document_id and same_document_title(title, file_name)
        ]
        if not candidates:
            return []
        exact = [item for item in candidates if title_key(title) == _file_stem(item[1])]
        document_id = (exact or candidates)[0][0]
        sections = await self._oracle.retrieval_screen_sections(
            {**scope, "document_id": document_id}
        )
        section_path = SectionIndex(path for _name, path, _count in sections).resolve(target.spec)
        if section_path is None:
            return []
        rows = await self._oracle.retrieval_reference_chunks(
            scope, document_id=document_id, section_path=section_path, limit=limit
        )
        rows = [
            row
            for row in rows
            if section_path_within(row.metadata.get("section_path"), section_path)
        ]
        if not rows:
            return []
        first_set = rows[0].metadata.get("chunk_set_id")
        return [row for row in rows if row.metadata.get("chunk_set_id") == first_set]

    async def _question_filters(
        self, filters: dict[str, str], inquiry_conditions: Any
    ) -> dict[str, str]:
        """質問が名指しした文書名・ページを検索条件(``hybrid_search`` の filters)へ足す(#546)。

        - 画面で文書(``file_name`` / ``document_id``)を指定したときは、そちらを優先して足さない。
          ページも同じく、画面で指定したときは足さない。
        - ナレッジベースに無い文書名は足さない(0 件にしない。rag_poc の
          ``_resolved_metadata_filter``)。
          ページは文書名と組でだけ使う条件なので、文書名を足さないときは一緒に外す。
        - filters の ``file_name`` は 1 語なので、質問が複数の文書名を挙げたときは、
          最初に見つかったものを使う。
        """
        metadata_filter = getattr(inquiry_conditions, "metadata_filter", None)
        terms = [
            term.strip()
            for term in getattr(metadata_filter, "source_file_terms", ())
            if isinstance(term, str) and term.strip()
        ]
        # 確認で章節の範囲(page_ranges)を選んだときも、そちらを優先して足さない(#717)。
        if not terms or any(
            filters.get(key, "").strip() for key in ("file_name", "document_id", "page_ranges")
        ):
            return filters
        for term in terms[:_MAX_QUESTION_FILE_TERMS]:
            candidate = {**filters, "file_name": term}
            if await self._oracle.has_retrieval_chunks(candidate):
                break
        else:
            return filters
        pages = [
            page
            for page in getattr(metadata_filter, "page_numbers", ())
            if isinstance(page, int) and page > 0
        ]
        if pages and not any(
            filters.get(key, "").strip() for key in ("page_number_min", "page_number_max")
        ):
            candidate["page_number_min"] = str(min(pages))
            candidate["page_number_max"] = str(max(pages))
        return candidate

    async def _screen_catalog(
        self, request: SearchRequest, state: _SearchState
    ) -> dict[str, list[str]]:
        """検索範囲の全文書の見出しから画面目録を作る(範囲と索引の状態ごとに cache。#554)。

        1 回の回答では最初の 1 回だけ作る。読めないときは空(rag_engine は LLM を呼ばず
        画面を足さない)。
        """
        if state.screen_catalog is None:
            state.screen_catalog = await self._load_screen_catalog(request)
        return state.screen_catalog

    async def _load_screen_catalog(self, request: SearchRequest) -> dict[str, list[str]]:
        from rag_engine.retrieval.screen_catalog import catalog_from_section_paths

        filters = dict(request.filters)
        try:
            index_state = await self._oracle.retrieval_scope_state(filters)
            key = (json.dumps(filters, ensure_ascii=False, sort_keys=True), index_state)
            cached = _SCREEN_CATALOG_CACHE.get(key)
            if cached is not None:
                return cached
            sections = await self._oracle.retrieval_screen_sections(filters)
        except Exception:  # noqa: BLE001 - 画面目録は補助。画面を足さずに回答を続ける。
            logger.warning("screen catalog load failed", exc_info=True)
            return {}
        catalog = catalog_from_section_paths(
            (file_name, _section_headings(section_path), count)
            for file_name, section_path, count in sections
        )
        if len(_SCREEN_CATALOG_CACHE) >= _SCREEN_CATALOG_CACHE_SIZE:
            _SCREEN_CATALOG_CACHE.clear()
        _SCREEN_CATALOG_CACHE[key] = catalog
        return catalog

    async def _screen_chunks(
        self,
        request: SearchRequest,
        state: _SearchState,
        links: Sequence[tuple[str, str]],
        existing: set[str],
    ) -> list[Any]:
        """選ばれた画面の child(画面ごとに上限まで)と、その親を検索範囲から読む(#554)。

        検索結果に無かった chunk も引用に戻せるよう ``state.chunks`` に入れる。分類・1 ページ目の
        本文(#557)・根拠画像は検索の候補と同じく付ける。読めない画面は足さない(回答は続ける)。
        """
        from rag_engine.retrieval.screen_catalog import MAX_CHILDREN_PER_SCREEN

        filters = dict(request.filters)
        added: dict[str, RetrievedChunk] = {}
        for file_name, heading in links:
            try:
                rows = await self._oracle.retrieval_screen_chunks(
                    filters,
                    file_name=file_name,
                    heading=heading,
                    limit=MAX_CHILDREN_PER_SCREEN * 4 + len(existing),
                )
            except Exception:  # noqa: BLE001 - 画面の根拠は補助。この画面を足さずに続ける。
                logger.warning("screen chunks load failed", exc_info=True)
                continue
            matched = [
                chunk
                for chunk in rows
                if chunk.chunk_id not in existing
                and chunk.chunk_id not in added
                and heading in _chunk_headings(chunk)
            ]
            for chunk in matched[:MAX_CHILDREN_PER_SCREEN]:
                added[chunk.chunk_id] = state.chunks.setdefault(chunk.chunk_id, chunk)
        if not added:
            return []
        chunks = list(added.values())
        # 分類と 1 ページ目の本文は、読んでいない文書・chunk set の分だけをまとめて 1 回で読む。
        await self._load_classifications(chunks, state)
        await self._load_first_page_contexts(chunks, state)
        if answer_images_enabled(self._settings):
            for chunk in chunks:
                await self._materialize_image_evidence(chunk, state)
            chunks = [state.chunks[chunk.chunk_id] for chunk in chunks]
        children = [
            _stored_child(
                chunk,
                rrf_score=0.0,
                classification=state.classifications.get(chunk.document_id),
                first_page_context=_first_page_context(chunk, state),
            )
            for chunk in chunks
        ]
        return [*children, *_stored_parents(children, state)]

    async def _load_classifications(
        self, chunks: Sequence[RetrievedChunk], state: _SearchState
    ) -> None:
        """まだ読んでいない文書の分類を、ヒットした document_id でまとめて 1 回で読む(#545)。"""
        missing = sorted({chunk.document_id for chunk in chunks} - state.loaded_classification_ids)
        if not missing:
            return
        state.classifications.update(await self._oracle.document_classifications(missing))
        state.loaded_classification_ids.update(missing)

    async def _load_first_page_contexts(
        self, chunks: Sequence[RetrievedChunk], state: _SearchState
    ) -> None:
        """まだ読んでいない chunk set の 1 ページ目の本文を、まとめて 1 回で読む(#557)。"""
        chunk_set_ids = {str(chunk.metadata.get("chunk_set_id") or "") for chunk in chunks}
        missing = sorted(chunk_set_ids - {""} - state.loaded_first_page_chunk_set_ids)
        if not missing:
            return
        state.first_page_contexts.update(await self._oracle.chunk_set_first_page_contexts(missing))
        state.loaded_first_page_chunk_set_ids.update(missing)

    async def _materialize_image_evidence(self, chunk: RetrievedChunk, state: _SearchState) -> None:
        """根拠 chunk の image_evidence を作業ディレクトリへ切り出し、crop_path を差し替える。

        rag_poc の answer_images は ``output_dir / source_run_id / crop_path`` を読むため、
        文書ごとの run ディレクトリへ PNG を書き、metadata(engine_metadata_json)を書き換える。
        切り出せない画像は添付しない(回答は続ける)。
        """
        metadata = _engine_metadata(chunk)
        images = metadata.get("image_evidence")
        width = chunk.metadata.get("page_width")
        height = chunk.metadata.get("page_height")
        if not isinstance(images, list) or not images or state.work_dir is None:
            return
        if not isinstance(width, int | float) or not isinstance(height, int | float):
            return
        run_id = _document_run_id(chunk.document_id)
        crop_dir = state.work_dir / run_id / "crops"
        for image in images:
            if not isinstance(image, dict):
                continue
            bbox = image.get("bbox")
            page = _int(image.get("page"))
            image_id = str(image.get("image_id") or image.get("record_id") or "").strip()
            if not image_id or page < 1 or not isinstance(bbox, list) or len(bbox) != 4:
                continue
            name = f"{_safe_name(image_id)}.png"
            target = crop_dir / name
            if not target.is_file():
                source = await self._parsed_source(chunk.document_id, state)
                if source is None:
                    return
                try:
                    png = await asyncio.to_thread(
                        crop_png,
                        source,
                        page,
                        (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
                        (float(width), float(height)),
                    )
                except ValueError:
                    continue
                crop_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(png)
            image["source_run_id"] = run_id
            image["crop_path"] = f"crops/{name}"
        state.chunks[chunk.chunk_id] = chunk.model_copy(
            update={
                "metadata": {
                    **chunk.metadata,
                    "engine_metadata_json": json.dumps(metadata, ensure_ascii=False, default=str),
                }
            }
        )

    async def _parsed_source(self, document_id: str, state: _SearchState) -> bytes | None:
        if document_id not in state.sources:
            try:
                state.sources[document_id] = await load_parsed_source(self._oracle, document_id)
            except (DocumentSourceNotFoundError, ValueError):
                state.sources[document_id] = None
        return state.sources[document_id]

    async def _rerank(self, query: str, documents: list[str], top_n: int | None) -> list[Any]:
        from rag_engine.models.llm import RerankTextRank

        if not documents:
            return []
        ranked = await self._genai.rerank(query, documents, top_n or len(documents))
        return [RerankTextRank(index=index, relevance_score=score) for index, score in ranked]


async def _call[R](factory: Callable[[], Awaitable[R]]) -> R:
    return await factory()


def question_business_domains(question: str, large_categories: Sequence[str]) -> list[str]:
    """質問が名指しした大分類を、検索範囲の大分類の語の一覧(保存どおりの値)から返す(#553)。

    - 比べるときは #547 の正規化(NFKC・空白・``category_label`` で番号の接頭辞を外す)と
      大文字・小文字の違いを無視する。返す値は保存どおり(rag_engine の ``_same_business_records`` は
      候補の大分類と番号の接頭辞を外して完全一致で比べる)。
    - 長い名前から照合し、照合した箇所は短い名前の照合に使わない
      (「業務A」の中の「業務」を拾わない)。
    - 名指しが無ければ空(絞り込まない)。
    """
    text = (normalize_category_value(question) or "").casefold()
    values_by_label: dict[str, list[str]] = {}
    for value in large_categories:
        label = category_label(value).casefold()
        if len(label) >= _MIN_BUSINESS_NAME_CHARS:
            values_by_label.setdefault(label, []).append(value)
    matched: list[str] = []
    for label in sorted(values_by_label, key=lambda item: (-len(item), item)):
        if label in text:
            text = text.replace(label, "\0")
            matched.extend(values_by_label[label])
    return list(dict.fromkeys(matched))


def _json_list(value: object) -> list[Any]:
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except ValueError:
            return []
        return decoded if isinstance(decoded, list) else []
    return list(value) if isinstance(value, list) else []


def _engine_metadata(chunk: RetrievedChunk) -> dict[str, Any]:
    raw = chunk.metadata.get("engine_metadata_json")
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    # 親子階層以外の分割方式の chunk も最小の v4 metadata で扱う。
    section = str(chunk.metadata.get("section_path") or "")
    return {
        "schema_version": 4,
        "active": True,
        "atomic": False,
        "content_hash": str(chunk.metadata.get("text_sha256") or ""),
        "classification": {},
        "source_categories": ["Text"],
        "section_path": [part for part in section.split(" > ") if part],
        "section_path_sources": [],
    }


def _document_run_id(document_id: str) -> str:
    return hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:16]


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120]


def _file_stem(file_name: str) -> str:
    """文書名から拡張子を除いた比較形(交差参照の文書名の照合。#1280)。"""
    return title_key(re.sub(r"\.[A-Za-z0-9]{1,5}$", "", file_name))


def _section_headings(section_path: str) -> list[str]:
    return [part.strip() for part in section_path.split(_SECTION_PATH_SEPARATOR) if part.strip()]


def _chunk_headings(chunk: RetrievedChunk) -> list[str]:
    """chunk の見出しの列。

    rag_engine の metadata の section_path。無ければ保存した文字列を分ける。
    """
    path = _engine_metadata(chunk).get("section_path")
    if isinstance(path, list):
        return [str(part).strip() for part in path]
    return _section_headings(str(chunk.metadata.get("section_path") or ""))


def _finite_float(value: object) -> float | None:
    """有限の数値なら float、それ以外(None・bool・NaN・文字列など)は None。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return default


def _group_id(chunk: RetrievedChunk) -> str:
    return str(chunk.metadata.get("chunk_group_id") or chunk.chunk_id)


def _inquiry_channel_rankings(
    pool: Sequence[Any],
    inquiry_conditions: Any,
    rankings: Sequence[Sequence[str]],
    *,
    limit: int,
    profile_enabled: bool,
) -> list[tuple[str, float, Sequence[tuple[str, float | None]]]]:
    """質問の理解に合う候補を、RRF に足すチャネル(profile / business_match)の順位で返す(#546)。

    rag_poc の検索(``adapters/oracle/store.py``)と同じ rag_engine の関数で計算する。
    対象は今回の検索で
    見つかった候補だけで、ナレッジベースの全 chunk は読まない。文書名・ページは検索条件
    (``_question_filters``)で適用済みか、ナレッジベースに無いため外したので、ここでは使わない。
    """
    from rag_engine.adapters.oracle.store import (
        _business_match_ranking,
        _chunks_matching_metadata_filter,
    )
    from rag_engine.retrieval.inquiry_conditions import profile_channel_rankings

    metadata_filter = getattr(inquiry_conditions, "metadata_filter", None)
    if metadata_filter is not None:
        metadata_filter = replace(metadata_filter, source_file_terms=(), page_numbers=())
    matched = _chunks_matching_metadata_filter(pool, metadata_filter)
    channels: list[tuple[str, float, Sequence[tuple[str, float | None]]]] = []
    if profile_enabled:
        channels.extend(profile_channel_rankings(matched, inquiry_conditions, limit=limit))
    channels.extend(
        _business_match_ranking(
            matched,
            metadata_filter,
            [[(chunk_id, None) for chunk_id in ranking] for ranking in rankings],
            [],
            limit=limit,
        )
    )
    return channels


def _first_page_context(chunk: RetrievedChunk, state: _SearchState) -> Mapping[str, object] | None:
    return state.first_page_contexts.get(str(chunk.metadata.get("chunk_set_id") or ""))


def _stored_child(
    chunk: RetrievedChunk,
    *,
    rrf_score: float,
    classification: Mapping[str, object] | None = None,
    first_page_context: Mapping[str, object] | None = None,
) -> Any:
    """backend の chunk を rag_engine の子 chunk にする。

    ``classification`` は文書の分類(rag_documents.classification)。rag_engine は
    ``metadata["document"]["classification"]`` で業務の候補を絞る
    (``_same_business_records``。#545)。
    ``first_page_context`` は chunk set の文書の 1 ページ目の本文。rag_engine は
    ``metadata["document"]["first_page_context"]`` を回答の「文書の背景」にする(#557)。
    chunk 自身が持つ値(#557 より前に保存した chunk)があれば、そちらを使う。
    分類と 1 ページ目の本文は chunk の保存内容(埋め込み・検索文)には入れず、回答のときにだけ付ける。
    """
    from rag_engine.models.storage import StoredChunk

    metadata = _engine_metadata(chunk)
    metadata["rrf_score"] = rrf_score
    metadata["document_id"] = chunk.document_id
    if classification:
        document = metadata.get("document")
        metadata["document"] = {
            **(document if isinstance(document, dict) else {}),
            "classification": dict(classification),
        }
    if first_page_context:
        document = metadata.get("document")
        document = document if isinstance(document, dict) else {}
        if "first_page_context" not in document:
            metadata["document"] = {**document, "first_page_context": dict(first_page_context)}
    if location := _sheet_location(chunk.metadata):
        # 表計算の根拠の場所(#1221)。回答の本文の出典を「シート・セル範囲」にする(#1224)。
        metadata["sheet_location"] = location
    if element_ids := chunk_element_ids(chunk.metadata):
        # 解析の要素の ID。複数レシピ融合で元の文書の範囲が同じ根拠を 1 つにまとめる(#1331)。
        metadata["element_ids"] = element_ids
    if isinstance(expansion := chunk.metadata.get(ENTITY_EXPANSION_KEY), dict):
        # 実体の 1 段の拡張で足した chunk(#1362)。rag_engine は rerank で位置を変えず、文書の選択で
        # 後回しにせず、文脈の親の枠を確保する。
        metadata["entity_expansion"] = dict(expansion)
    page_start = _int(chunk.metadata.get("page_start") or chunk.metadata.get("page_number"), 1)
    return StoredChunk(
        chunk_uid=chunk.chunk_id,
        chunk_id=chunk.chunk_id,
        chunk_level="child",
        chunk_seq=_int(chunk.metadata.get("engine_chunk_seq") or chunk.metadata.get("chunk_index")),
        parent_chunk_uid=f"{chunk.document_id}:{_group_id(chunk)}",
        parent_chunk_id=f"{chunk.document_id}:{_group_id(chunk)}",
        child_chunk_ids=(),
        text=chunk.text,
        retrieval_text=engine_search_text(chunk.metadata) or chunk.text,
        source_run_id=ENGINE_SOURCE_RUN_ID,
        source_file_name=_source_file_name(chunk),
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=page_start,
        page_end=_int(chunk.metadata.get("page_end"), page_start),
        source_seq_ranges=tuple(_json_list(chunk.metadata.get("source_seq_ranges_json"))),
        source_record_refs=tuple(_json_list(chunk.metadata.get("source_record_refs_json"))),
        metadata=metadata,
    )


# 新しい版に置き換えた文書(旧版)の出典の文書名に付ける印(#1248)。
SUPERSEDED_FILE_NAME_SUFFIX = "（旧版）"


def _source_file_name(chunk: RetrievedChunk) -> str:
    """回答の出典に出す文書名。旧版の文書は「(旧版)」を付け、今の版と区別できるようにする。"""
    file_name = chunk.file_name or ""
    if file_name and chunk.metadata.get("document_superseded") is True:
        return f"{file_name}{SUPERSEDED_FILE_NAME_SUFFIX}"
    return file_name


_CELL_RANGE = re.compile(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$")


def _sheet_location(metadata: Mapping[str, object]) -> dict[str, object] | None:
    """Excel の行の記録の chunk の場所(#1221 の sheet_name・row_start・row_end・cell_range)。"""
    sheet = metadata.get("sheet_name")
    if not isinstance(sheet, str) or not sheet.strip():
        return None
    location: dict[str, object] = {"sheet_name": sheet.strip()}
    for key in ("row_start", "row_end"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            location[key] = value
    cell_range = metadata.get("cell_range")
    if isinstance(cell_range, str) and _CELL_RANGE.match(cell_range.strip()):
        location["cell_range"] = cell_range.strip()
    return location


def _column_number(letters: str) -> int:
    number = 0
    for char in letters:
        number = number * 26 + (ord(char) - 64)
    return number


def _merged_sheet_location(locations: list[object]) -> dict[str, object] | None:
    """親の chunk の場所(子の場所が同じシートなら、行と列の範囲をまとめる。#1224)。"""
    items = [item for item in locations if isinstance(item, dict)]
    sheets = {item.get("sheet_name") for item in items}
    if not items or len(sheets) != 1:
        return None
    ranges = [
        match.groups()
        for item in items
        if isinstance(item.get("cell_range"), str)
        and (match := _CELL_RANGE.match(str(item["cell_range"])))
    ]
    location: dict[str, object] = {"sheet_name": next(iter(sheets))}
    if ranges:
        first_column = min((r[0] for r in ranges), key=_column_number)
        last_column = max((r[2] for r in ranges), key=_column_number)
        row_start = min(int(r[1]) for r in ranges)
        row_end = max(int(r[3]) for r in ranges)
        location.update(
            row_start=row_start,
            row_end=row_end,
            cell_range=f"{first_column}{row_start}:{last_column}{row_end}",
        )
    return location


def _parent_sheet_location(metadata: Mapping[str, object]) -> dict[str, object] | None:
    """行の記録の子が持つ、親(表の一部)の場所(#1349)。無ければ None。"""
    sheet = metadata.get("sheet_name")
    cell_range = metadata.get("parent_cell_range")
    row_start = metadata.get("parent_row_start")
    row_end = metadata.get("parent_row_end")
    if (
        not isinstance(sheet, str)
        or not sheet.strip()
        or not isinstance(cell_range, str)
        or not _CELL_RANGE.match(cell_range.strip())
        or not isinstance(row_start, int)
        or not isinstance(row_end, int)
    ):
        return None
    return {
        "sheet_name": sheet.strip(),
        "row_start": row_start,
        "row_end": row_end,
        "cell_range": cell_range.strip(),
    }


def _stored_parents(children: list[Any], state: _SearchState) -> list[Any]:
    """子の metadata に保持した親本文から親 chunk を復元する。"""
    from rag_engine.models.storage import StoredChunk

    by_parent: dict[str, list[Any]] = {}
    for child in children:
        by_parent.setdefault(child.parent_chunk_uid, []).append(child)
    parents = []
    for parent_uid, members in by_parent.items():
        members.sort(key=lambda child: child.chunk_seq)
        source = state.chunks.get(members[0].chunk_uid)
        parent_text = str((source.metadata if source else {}).get("parent_text") or "")
        text = parent_text or "\n\n".join(child.text for child in members)
        metadata = dict(members[0].metadata)
        metadata.pop("rrf_score", None)
        if element_ids := list(
            dict.fromkeys(
                element_id for child in members for element_id in chunk_element_ids(child.metadata)
            )
        ):
            # 親の範囲は今回見つかった子の要素の和(#1331)。
            metadata["element_ids"] = element_ids
        if location := (
            _parent_sheet_location(source.metadata if source else {}) if parent_text else None
        ) or _merged_sheet_location([child.metadata.get("sheet_location") for child in members]):
            # 親の本文が表の一部(行の記録の親子。#1349)なら、その本文の範囲を親の場所にする。
            metadata["sheet_location"] = location
        parents.append(
            StoredChunk(
                chunk_uid=parent_uid,
                chunk_id=parent_uid,
                chunk_level="parent",
                chunk_seq=members[0].chunk_seq,
                parent_chunk_uid="",
                parent_chunk_id="",
                child_chunk_ids=tuple(child.chunk_id for child in members),
                text=text,
                retrieval_text=text,
                source_run_id=ENGINE_SOURCE_RUN_ID,
                source_file_name=members[0].source_file_name,
                source_engine_id="docling",
                source_engine_label="Docling",
                page_start=min(child.page_start for child in members),
                page_end=max(child.page_end for child in members),
                source_seq_ranges=tuple(
                    item for child in members for item in child.source_seq_ranges
                ),
                source_record_refs=tuple(
                    item for child in members for item in child.source_record_refs
                ),
                metadata=metadata,
            )
        )
    return parents


def _outcome_from_result(result: Any, state: _SearchState) -> AnswerOutcome:
    """rag_poc の AnswerQuestionResult を backend の回答・引用・診断へ写す。"""
    tree: list[dict[str, Any]] = []
    # (並びの順位, chunk_id, 根拠の子, 検索で当たった子の関連度の順位（無ければ 0）)。
    ordered: list[tuple[int, str, dict[str, Any], int]] = []
    # rerank の結果(chunk_id → (順位, 関連度))。rag_engine は rerank した候補の record に
    # ``metadata["rerank"]`` を載せ、根拠の child に ``rerank_rank`` / ``rerank_score`` として出す。
    # 引用へ写さないと、rerank を実行しても画面は「Rerank 未実行」になる(#662)。
    reranked: dict[str, tuple[int | None, float]] = {}
    for position, parent in enumerate(result.evidence_items or ()):
        children = []
        for child in parent.get("children") or []:
            chunk_id = str(child.get("chunk_id") or child.get("id") or "")
            used = bool(child.get("is_model_used"))
            rerank_score = _finite_float(child.get("rerank_score"))
            if rerank_score is not None and chunk_id not in reranked:
                position_rank = _int(child.get("rerank_rank"))
                reranked[chunk_id] = (position_rank if position_rank > 0 else None, rerank_score)
            children.append(
                {
                    "chunk_id": chunk_id,
                    "role": str(child.get("retrieval_role") or ""),
                    "reason": str(child.get("context_reason") or ""),
                    "is_model_used": used,
                    "page": child.get("page_start"),
                }
            )
            rank = _int(child.get("model_usage_rank"), 10_000) if used else 10_000 + position
            # 当たった子（retrieved_anchor）の関連度の順位は rerank の後の順（#1348）。
            ordered.append((rank, chunk_id, children[-1], _int(child.get("retrieval_rank"))))
        tree.append(
            {
                "parent_id": str(parent.get("chunk_id") or parent.get("id") or ""),
                "source": str(parent.get("source") or ""),
                "page": parent.get("page_start"),
                "reason": str(parent.get("context_reason") or ""),
                "children": children,
            }
        )
    citations: list[RetrievedChunk] = []
    seen: set[str] = set()
    for _, chunk_id, evidence, retrieval_rank in sorted(ordered, key=lambda item: item[0]):
        chunk = state.chunks.get(chunk_id)
        if chunk is None or chunk_id in seen:
            continue
        seen.add(chunk_id)
        metadata: dict[str, Any] = {
            **chunk.metadata,
            "evidence_role": evidence["role"],
            "evidence_model_used": evidence["is_model_used"],
        }
        if ENTITY_EXPANSION_KEY in chunk.metadata and evidence["role"] in {"retrieved_anchor", ""}:
            # 実体の 1 段の拡張で足した chunk は、検索で当たった chunk と分ける(#1362)。
            metadata["evidence_role"] = ENTITY_EXPANSION_ROLE
        if retrieval_rank > 0:
            # MCP の根拠は当たった子を関連度の順に先にする（画面の並びは変えない。#1348）。
            metadata["evidence_retrieval_rank"] = retrieval_rank
        update: dict[str, Any] = {"metadata": metadata}
        if chunk_id in reranked:
            rerank_rank, rerank_score = reranked[chunk_id]
            update["rerank_score"] = rerank_score
            if rerank_rank is not None:
                metadata["rerank_rank"] = rerank_rank
        citations.append(chunk.model_copy(update=update))
    citations = _with_citation_lines(
        citations,
        result.evidence_items or (),
        (result.generation_trace or {}).get("citation_lines"),
    )
    steps = [
        {
            "name": str(step.get("name") or ""),
            "status": str(step.get("status") or ""),
            "elapsed_seconds": step.get("elapsed_seconds"),
            "llm_calls": step.get("llm_calls"),
        }
        for step in result.execution_steps or ()
        if isinstance(step, Mapping)
    ]
    diagnostics = {
        "answer_flow": result.answer_flow,
        "strategy": result.effective_strategy,
        "confidence": result.confidence,
        "needs_human_review": result.needs_human_review,
        "insufficient_reason": result.insufficient_reason,
        # 回答の構造（AnswerEnvelope。#1235）。outcome は回答の対応（answered / conditional / …）。
        "outcome": (result.envelope or {}).get("outcome"),
        "envelope": dict(result.envelope or {}),
        "reasoning_summary": result.reasoning_summary,
        # rag_poc の回答 viewer が出していた外部データの確認と問い合わせ型(#651)。
        "external_data_required": result.external_data_required,
        "external_data_items": list(result.external_data_items or ()),
        "question_type": list(result.question_type or ()),
        "generated_queries": list(result.generated_queries),
        "text_search_tokens": list(result.text_search_tokens),
        "crag_attempt_count": len(result.crag_attempts or ()),
        # 要求ごとの根拠の覆域と、根拠の無い要求の再検索(#1279)。
        "request_coverage": dict((result.generation_trace or {}).get("request_coverage") or {}),
        "execution_steps": steps,
        "evidence_tree": tree,
    }
    context_text = "\n\n".join(chunk.text for chunk in citations)
    answer = result.answer_text or result.answer
    return AnswerOutcome(
        answer=answer,
        citations=citations,
        diagnostics=diagnostics,
        context_text=context_text,
        evaluation_input=_evaluation_input(result),
    )


# 本文の出典行（「根拠：」）の順番（1 始まり）を、出典行が指す根拠の chunk の metadata に
# 入れる key（#1330）。画面は n 番目の出典行を、この list に n を持つ根拠に結ぶ
# （ファイル名と頁で推測しない）。
ANSWER_CITATION_LINES_KEY = "answer_citation_lines"


def _with_citation_lines(
    citations: list[RetrievedChunk], evidence_items: Any, refs: Any
) -> list[RetrievedChunk]:
    """出典行ごとの根拠（rag_engine の ``citation_lines``）を、引用の chunk に結ぶ（#1330）。

    出典行の根拠は親の範囲（``source_id``）で、引用は子の chunk。引用の位置が 1 つの子に
    決まっていれば（``scope_source_ids``）その子、決まっていなければ親の子のうち回答に使った・
    頁の合うものを選ぶ。
    """
    if not isinstance(refs, list) or not refs or not citations:
        return citations
    citation_ids = [chunk.chunk_id for chunk in citations]
    pages = {chunk.chunk_id: _chunk_page_range(chunk) for chunk in citations}
    children_by_parent: dict[str, list[tuple[str, bool]]] = {}
    for parent in evidence_items:
        if not isinstance(parent, Mapping):
            continue
        children = [
            (str(child.get("chunk_id") or child.get("id") or ""), bool(child.get("is_model_used")))
            for child in parent.get("children") or []
            if isinstance(child, Mapping)
        ]
        for key in ("chunk_uid", "id", "chunk_id"):
            if value := str(parent.get(key) or ""):
                children_by_parent.setdefault(value, children)
    lines: dict[str, list[int]] = {}
    for ordinal, ref in enumerate(refs, 1):
        if not isinstance(ref, Mapping):
            continue
        scoped = [str(item) for item in ref.get("scope_source_ids") or () if str(item) in pages]
        if scoped:
            candidates = scoped
        else:
            children = [
                (chunk_id, used)
                for chunk_id, used in children_by_parent.get(str(ref.get("source_id") or ""), [])
                if chunk_id in pages
            ]
            page = _int(ref.get("page"))
            candidates = [
                chunk_id
                for chunk_id, _used in sorted(
                    children,
                    key=lambda item: (
                        not item[1],
                        not (page and pages[item[0]][0] <= page <= pages[item[0]][1]),
                        citation_ids.index(item[0]),
                    ),
                )
            ]
        if candidates:
            lines.setdefault(candidates[0], []).append(ordinal)
    if not lines:
        return citations
    return [
        chunk.model_copy(
            update={
                "metadata": {**chunk.metadata, ANSWER_CITATION_LINES_KEY: lines[chunk.chunk_id]}
            }
        )
        if chunk.chunk_id in lines
        else chunk
        for chunk in citations
    ]


def _chunk_page_range(chunk: RetrievedChunk) -> tuple[int, int]:
    start = _int(chunk.metadata.get("page_start") or chunk.metadata.get("page_number"), 1)
    return start, max(start, _int(chunk.metadata.get("page_end"), start))


def _evaluation_input(result: Any) -> dict[str, Any] | None:
    """rag_poc の回答 payload から、標準回答での評価に使う部分だけを取り出す。"""
    from rag_engine.generation.answer_payload import answer_result_payload

    try:
        payload = answer_result_payload(result, answer_id="", run_id=ENGINE_SOURCE_RUN_ID)
    except Exception:  # noqa: BLE001 - 評価の入力は補助。回答の返却を止めない。
        logger.warning("evaluation input build failed", exc_info=True)
        return None
    # 根拠は評価が読む項目だけにする（回答の記録にもこの形で保存する。#1371）。
    return stored_evaluation_input({key: payload.get(key) for key in EVALUATION_INPUT_KEYS})


def evaluate_answer_record(
    evaluation_input: Mapping[str, Any],
    standard_answer: str,
    settings: Settings,
    *,
    citations: Sequence[RetrievedChunk],
) -> dict[str, Any]:
    """保存した回答を標準回答で評価する。

    LLM(rag_engine の evaluate_answer_payload)が標準回答の項目の照合と主張の監査を行い、
    評価の基準の指標と閾値で合否を付ける(#680)。``citations`` は回答の引用(決定的な指標に使う)。
    同期関数で、LLM を複数回呼ぶ。呼び出し側は worker thread で動かす。
    """
    from rag_engine.evaluation.answer_eval import evaluate_answer_payload

    with tempfile.TemporaryDirectory(prefix="rag-engine-eval-") as work:
        engine_settings = build_engine_settings(settings, output_dir=Path(work))
        evaluation = evaluate_answer_payload(
            {**evaluation_input, "standard_answer": standard_answer}, engine_settings
        )
    return score_answer_evaluation(
        evaluation,
        answer=str(evaluation_input.get("answer_text") or ""),
        citations=citations,
        insufficient_reason=str(evaluation_input.get("insufficient_reason") or ""),
        settings=settings,
    )
