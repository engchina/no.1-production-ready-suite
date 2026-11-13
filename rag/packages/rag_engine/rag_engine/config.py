"""環境変数からアプリ設定と LLM provider 設定を構築する。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pr_backend_core.output_budget import DEFAULT_ONLINE_MAX_OUTPUT_TOKENS

if TYPE_CHECKING:
    from rag_engine.adapters.oracle.connection import AdbSettings

from rag_engine.retrieval.text_search_tokenizer import (
    DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER,
    DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT,
)

# LLM は本リポジトリのモデル設定(OCI Enterprise AI, OpenAI 互換 Responses API)に一本化する。
# 回答系(生成)と Vision(図説明)で model だけを分ける。
ENTERPRISE_AI_LLM_PROVIDER = "enterprise-ai"
ENTERPRISE_AI_VISION_LLM_PROVIDER = "enterprise-ai-vision"
# 確認を通らなかったときのモデル(モデルのカスケード。#4581)。backend が検索・回答プロファイルの設定の接続を
# RAG_ENGINE_ESCALATION_* で渡したときだけ作る(利用者がモデルを明示した実行には渡さない)。
ESCALATION_LLM_PROVIDER = "enterprise-ai-escalation"
ESCALATION_ENV_PREFIX = "RAG_ENGINE_ESCALATION"


DEFAULT_LLM_PROVIDER = ENTERPRISE_AI_LLM_PROVIDER
DEFAULT_CHICAGO_REGION = "us-chicago-1"
DEFAULT_OSAKA_REGION = "ap-osaka-1"
DEFAULT_OCI_CONFIG_FILE = "~/.oci/config"
DEFAULT_OCI_PROFILE = "DEFAULT"
# 「今日」(検索の基準日の既定)を決める時間帯(#3249)。backend の共通の設定 `PLATFORM_TIMEZONE` の既定と同じ。
DEFAULT_TIMEZONE = "UTC"
OCI_INFERENCE_ENDPOINT_TEMPLATE = "https://inference.generativeai.{region}.oci.oraclecloud.com"
DEFAULT_OCI_VISION_ENDPOINT = OCI_INFERENCE_ENDPOINT_TEMPLATE.format(region=DEFAULT_CHICAGO_REGION)
OPENAI_RESPONSES_LLM_API_MODE = "openai_responses"
DEFAULT_LLM_API_MODE = OPENAI_RESPONSES_LLM_API_MODE
DEFAULT_EMBEDDING_REGION = DEFAULT_OSAKA_REGION
DEFAULT_EMBEDDING_MODEL = "cohere.embed-v4.0"
DEFAULT_EMBEDDING_OUTPUT_DIMENSIONS = 1536
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_IMAGE_EMBEDDING_RRF_WEIGHT = 0.75
DEFAULT_MAX_ANCHORS_PER_PARENT = 2
# rerank の後、起点（上位 top_k）に採る同じ文書の child の上限（#2167）。0 は上限なし（SDK の既定）。
DEFAULT_MAX_ANCHORS_PER_DOCUMENT = 0
# 検索の起点 child 数（UI の既定値）、context に入れる parent 数、生成に渡す原文の総予算（文字）の既定値 (#1054)。
# chunking.constants.DEFAULT_RETRIEVAL_TOP_K / answer_records.MAX_CONTEXT_RECORDS / evidence_selection.EVIDENCE_BUDGET_CHARS
# と同じ値（循環 import を避けて写す。一致は tests/test_retrieval_budget_settings.py で確認する）。
DEFAULT_RETRIEVAL_TOP_K_SETTING = 20
DEFAULT_MAX_CONTEXT_RECORDS = 12
DEFAULT_EVIDENCE_BUDGET_CHARS = 40000
DEFAULT_TEXT_SEARCH_QUERY_VARIANT_LIMIT = 6
DEFAULT_RERANK_MIN_RELEVANCE_SCORE = 0.3
# Reciprocal Rank Fusion の平滑化定数。ADB の hybrid 検索と回答側の順位融合で同じ値を使う。
RRF_K = 60


def oci_inference_endpoint(region: str) -> str:
    """region の OCI Generative AI inference endpoint（root URL）を返します。"""
    return OCI_INFERENCE_ENDPOINT_TEMPLATE.format(region=region)


def llm_provider_label(location: str, model: str) -> str:
    """UI に表示する provider label を、実際に設定された model ID から組み立てます。

    model ID はコードに既定値を持たず環境変数だけが決めるため、装飾せずそのまま表示します。
    未設定のときは location だけを返し、label から model 未設定だと分かるようにします。
    """
    model_id = model.strip()
    return f"{location} / {model_id}" if model_id else location


# 値の前後の空白・引用符を strip しない項目。password は空白や引用符が値の一部になり得る (#837)。
_VERBATIM_ENV_KEYS = frozenset({"RAG_ENGINE_AUTH_PASSWORD"})


def _dotenv_items(text: str) -> list[tuple[str, str]]:
    """.env の本文を (key, value) に分解する。load_dotenv と get_settings で同じ規則を使う。

    通常の項目は前後の空白と引用符を外す。_VERBATIM_ENV_KEYS は外側の一対の引用符（"…" / '…'）だけを外し、
    中身はそのまま（`" pa'ss "` → ` pa'ss `、`pass'` → `pass'`）。
    """
    items: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in _VERBATIM_ENV_KEYS:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
        else:
            value = value.strip().strip('"').strip("'")
        items.append((key, value))
    return items


def load_dotenv(path: str | Path = ".env") -> None:
    """外部依存なしで .env を読み込む。既存の環境変数は上書きしない。"""
    env_path = Path(path)
    if not env_path.exists():
        return
    for key, value in _dotenv_items(env_path.read_text(encoding="utf-8")):
        os.environ.setdefault(key, value)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except ValueError:
        return default


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    """1 以上の整数(値なし・整数でない・1 未満は既定)。"""
    value = _env_int(env, name, default)
    return value if value >= 1 else default


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, default))
    except ValueError:
        return default


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_llm_provider_id(value: str | None, default: str = DEFAULT_LLM_PROVIDER) -> str:
    """LLM provider の別名を内部 provider ID へ正規化します。"""
    normalized = str(value or "").strip().lower().replace("_", "-")
    if normalized in {"", "default"}:
        return default
    aliases = {
        "enterprise-ai": ENTERPRISE_AI_LLM_PROVIDER,
        "vision": ENTERPRISE_AI_VISION_LLM_PROVIDER,
        "enterprise-ai-vision": ENTERPRISE_AI_VISION_LLM_PROVIDER,
    }
    return aliases.get(normalized, normalized)


def normalize_llm_api_mode(value: str | None, default: str = DEFAULT_LLM_API_MODE) -> str:
    """LLM は openai SDK(Responses API)のみを使う。OCI SDK の LLM 経路は持たない。"""
    return OPENAI_RESPONSES_LLM_API_MODE


# 1 回の呼び出しの出力の上限の既定(tokens。#1719。platform の DEFAULT_ENTERPRISE_AI_MAX_OUTPUT_TOKENS と同じ)。
# 回答の上限(`answer_max_tokens`)の既定と、モデルの出力の上限が渡されないときの値。
DEFAULT_MODEL_OUTPUT_TOKENS = 32768


@dataclass(frozen=True)
class LlmProviderSettings:
    """1 つの LLM provider の接続先、認証、model 能力を保持します。"""

    provider_id: str
    label: str
    # 記録用(base URL の組み立てには使わない。#1440)。
    region: str
    # OCI の OpenAI 互換 API が要求する project OCID。他の OpenAI 互換 API では空のままにする。
    project_id: str
    api_key: str
    model: str
    supports_vision: bool = False
    endpoint: str = ""
    # 推論(思考)の量の送る値(#1425。Responses API の `reasoning.effort`)。空は送らない。
    # backend が platform の `reasoning_effort_to_send` で決めて渡す(none は空)。
    reasoning_effort: str = ""
    # モデルの 1 回の呼び出しの出力の上限(tokens。#1719。推論のあるモデルは推論の token も含む)。backend が
    # platform の `enterprise_ai_output_token_budget`(モデルの出力の上限と入力の上限の小さい方)で決めて渡す。
    max_output_tokens: int = DEFAULT_MODEL_OUTPUT_TOKENS

    @property
    def base_url(self) -> str:
        """OpenAI 互換 API の base URL(endpoint を入力どおり使う。#1440)。

        path は補わない(OCI Enterprise AI も利用者が `/openai/v1` まで書く)。backend は
        platform の `enterprise_ai_base_url` で決めた値を `OCI_ENTERPRISE_AI_ENDPOINT` で渡すので、
        ここでは前後の空白と末尾の `/` を除くだけにして、3 製品と同じ URL を呼ぶ。
        """
        return self.endpoint.strip().rstrip("/")


@dataclass(frozen=True)
class Settings:
    """アプリ全体の環境変数由来設定を保持します。"""

    host: str
    port: int
    output_dir: Path
    runtime_knowledge_path: Path | None
    render_dpi: int
    max_default_pages: int
    enabled_engines: list[str]
    docling_device: str
    docling_num_threads: int
    docling_do_ocr: bool
    docling_do_table_structure: bool
    docling_keep_picture_child_text: bool
    oci_config_file: str
    oci_profile: str
    oci_compartment_id: str
    oci_vision_endpoint: str
    llm_request_timeout_seconds: int
    # 構造化出力の呼び出し 1 回の出力の上限(tokens。既定 32768。#1719)。送る値はこれと呼ぶモデルの出力の上限
    # (`LlmProviderSettings.max_output_tokens`)の小さい方(`llm_output_token_limit`)。
    answer_max_tokens: int
    # 通常のオンライン生成だけの要求予算。固定課題と文書解析は answer_max_tokens を使う。
    online_max_output_tokens: int
    default_rerank_enabled: bool
    rerank_model: str
    oci_rerank_endpoint: str
    rerank_request_timeout_seconds: int
    rerank_retries: int
    rerank_retry_initial_wait_seconds: float
    rerank_retry_max_wait_seconds: float
    rerank_min_relevance_score: float
    # 粗→細の検索: rerank 後の候補を文書ごとに集約し、rerank の点数が明らかに低い文書を後回しにする (#1028)。既定 on (#1040)。
    # 命中の件数は判定に使わない（#3504）。
    document_selection_enabled: bool
    document_selection_score_ratio: float
    embedding_region: str
    embedding_endpoint: str
    embedding_model: str
    embedding_output_dimensions: int
    embedding_batch_size: int
    image_embedding_enabled: bool
    image_embedding_rrf_weight: float
    text_search_query_variant_limit: int
    # 文脈構築で同じ親から起点（retrieved_anchor）に採る child の上限（#669、#889）。1 以上。
    max_anchors_per_parent: int
    # 起点に採る同じ文書の child の上限（#2167）。超えた child は落とさず後ろへ回す。0 は上限なし。
    max_anchors_per_document: int
    text_search_tokenizer_sudachi_dict: str
    text_search_tokenizer_sudachi_config: str
    text_search_tokenizer_latin_stemmer: str
    llm_api_mode: str
    llm_providers: dict[str, LlmProviderSettings]
    # 画像を読む取り込みの処理（図の説明・解析の VLM）の既定のモデル。回答には使わない（#3444）。
    default_vision_llm: str
    # 回答のモデル。回答の実行の LLM の呼び出しはすべてこのモデルで行い、根拠の画像を添付するかは
    # このモデルが画像を読めるか（`supports_vision`）で決める（#3444）。
    default_answer_llm: str

    # None は保存済み辞書、空 tuple は評価用の辞書無効化を表す。共有ファイルは変更しない。
    domain_keywords_override: tuple[str, ...] | None = None
    # 検索の比較実験用。重み付けを切ると全検索文が同じ票、original_query_only は原質問だけで検索する。
    retrieval_query_sources: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # 検索文ごとに、全文検索の語の上限で優先する語（質問に足した用語・同義語）。ドメインキーワードと同じ段で扱う。
    retrieval_query_keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)
    hybrid_search_backend: str = "oracle_hybrid_index"
    rrf_method_ratio: tuple[float, float] = (7.0, 3.0)
    rrf_source_ratio: tuple[float, float] = (7.0, 3.0)
    # rerank の後の並びの枠（rerank の順の件数・検索の順の件数。#4664）。
    rerank_slot_ratio: tuple[int, int] = (7, 3)
    runtime_terms_search_enabled: bool = False
    runtime_terms_answer_enabled: bool = False
    original_query_only: bool = False

    workspace_dir: Path | None = None
    prompt_dir: Path | None = None
    faq_path: Path | None = None
    adb_settings: AdbSettings | None = None
    approved_faq_semantic_matching_enabled: bool = True
    approved_faq_semantic_suggestions_enabled: bool = False
    # Gradio UI のログイン。password が空のままでは create_app() が起動を拒否する。
    auth_user: str = "admin"
    auth_password: str = ""
    # 真のときだけ UI を認証なしで公開する。ローカル検証専用の逸脱で、既定は必ず False。
    auth_disabled: bool = False
    # 検索の起点 child 数（UI の既定値。API・SDK は引数で指定）、context に入れる parent 数、生成の原文予算（文字）(#1054)。
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K_SETTING
    # 基本候補と、拡張を含む精排入力の合計は回答起点数から独立する。
    retrieval_candidate_limit: int = 140
    rerank_candidate_limit: int = 200
    rerank_top_n: int = 60
    # 呼出元が解決した、各候補経路の既存の上限。KB/レシピには設定しない。
    retrieval_stage_limits: dict[str, int] = field(default_factory=dict)
    max_context_records: int = DEFAULT_MAX_CONTEXT_RECORDS
    evidence_budget_chars: int = DEFAULT_EVIDENCE_BUDGET_CHARS
    batch_read_workers: int = 1
    request_coverage_max_queries: int = 2
    request_coverage_max_chunks: int = 4
    request_coverage_deadline_seconds: float = 10.0
    # 丸数字・(1) などで並べた小問ごとの検索をする小問の数と、小問ごとに確保する根拠の数 (#2399)。
    request_coverage_max_subquestions: int = 5
    request_coverage_chunks_per_subquestion: int = 2
    # 主の検索の後に足す検索（業務ガイドの手順ごとの検索・小問ごとの検索・根拠の無い要求の再検索）の回数の
    # 合計の枠 (#2395)。種類ごとの上限はこの枠の中で数える（`retrieval.extra_retrieval`）。
    extra_retrieval_max_queries: int = 8
    # 業務ガイドの手順ごとに検索し、手順が参照する資料の頁の根拠を回答の材料に入れる (#2395)。LLM は呼ばない。
    # 必須対象は追加検索の合計枠を先に確保する。回数はその後の補助検索、参照数は最低1件以外の追加候補を制限する。
    guide_step_retrieval_enabled: bool = True
    guide_step_max_queries: int = 4
    guide_step_chunks_per_step: int = 2
    guide_reference_max_chunks: int = 4
    guide_step_deadline_seconds: float = 60.0
    # 当たった chunk の章節から親の章節へ段階的に広げて文脈に入れる (#1436)。章節と chunk の対応を注入した
    # 呼び出し元（`AnswerDependencies.section_outline` / `section_chunks`）でだけ動く。上限は、根拠の入力 token
    # （モデルの入力の上限に対する割合）・上がる段の数。期限は回答全体のものを使う。
    section_expansion_enabled: bool = True
    section_expansion_max_levels: int = 4
    section_expansion_input_token_percent: int = 50
    # 業務ガイドの手順ごとの検索の後に広げる段のために、最初の段で使わずに残す入力の割合（上の入力の上限に対する %。#2816）。
    section_expansion_followup_reserve_percent: int = 20
    # 回答のモデルの入力の上限（tokens。#1437）。backend が共通のモデル設定の登録モデルの値を
    # `OCI_ENTERPRISE_AI_MAX_INPUT_TOKENS` で渡す（platform の enterprise_ai_max_input_tokens。既定 128000）。
    # 章節の拡張の根拠の入力の上限をこの割合で決める。
    answer_max_input_tokens: int = 128_000
    generation_reasoning_effort: str = ""
    # 工程ごとの推論の量(1 回の呼び出しの要求の値)。用途の上書きより後に、その工程の呼び出しにだけ当てる。
    # 値の意味は用途の上書きと同じ(空はモデルの登録値のまま。登録値が none のモデルには送らない)。
    # 質問の対象(#4351): 抽出の段(原文の写しと同じ写しの出現の順番を返す)と、役割の判定の段(文の中での
    # 問い・前提・例)。それぞれの段の呼び出しにだけ当てる。
    # 工程ごとのモデル(1 回の呼び出しの provider ID。`llm_providers` のキー)。空は呼び出し側が渡した provider
    # (回答のモデル)のまま。`get_settings` は RAG_ENGINE_<段>_MODEL があれば段の provider を作ってこれにする
    # 確認を通らなかったときのモデルの provider ID(モデルのカスケード。#4581)。空はカスケードしない。
    # 回答のモデルの出力が機械で判定できる確認を通らなかったときだけ、失敗した段をこのモデルでやり直す。
    escalation_llm: str = ""
    # そのモデルの入力の上限(tokens。回答のモデルの `answer_max_input_tokens` の代わりに使う)。
    escalation_max_input_tokens: int = 128_000
    # 回答の期限のある LLM の呼び出しを streaming で受け、応答の event が来なくなったら打ち切って送り直す
    # (#1926・#4736。既定 off。backend が RAG_LLM_STALL_DETECTION_ENABLED で ON にする)。止まった判定は無音だけ。
    llm_stall_detection: bool = False
    llm_max_retries: int = 1
    # 止まったとする無音の秒数（最初の event を含む）。送り直しはやり直しの共通の枠で数える。
    llm_stall_idle_seconds: float = 120.0
    # streaming で受ける呼び出しで、出力が同じ並びの繰り返し・空白だけで続いたら打ち切って送り直す(#2966。既定 off。
    # backend が RAG_LLM_RUNAWAY_DETECTION_ENABLED で ON にする)。streaming しない呼び出しは、出力の上限で未完了の
    # 応答に同じ暴走があれば、上限を広げずに同じ送り直しにする(#3835)。
    llm_runaway_detection: bool = False
    # 分かっている条件（業務ガイドの条件の名前と値。MCP の conditions・質問で決まった条件。#1492）。backend が
    # 業務ガイドの照合の結果から渡す。回答の確認の項目のうち、これを確かめる項目を決定的に除く。
    known_conditions: tuple[tuple[str, str], ...] = ()
    # backendが利用者の確定した回答・質問から選別した前提。設定保存や環境変数からは読み込まない。
    confirmed_premises: tuple[tuple[str, str], ...] = ()
    confirmed_premise_sources: tuple[tuple[str, str, str], ...] = ()
    # 業務ガイドの照合で分からなかった選択の条件（#2404）。(条件の id, 名前, ((選択肢, (言い換え, ...)), ...))。
    # 選択肢が項目の種類に対応する条件だけを、根拠の画面の図の項目の種類で決める（generation.form_field_kinds）。
    evidence_conditions: tuple[tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = ()
    # 根拠で条件が決まったときの業務ガイドの手順の計画（#2689）。backend が決まり方ごとに前もって作る。
    # (決まった条件の (id, 値) の列, 残る手順の (id, 必須か) の列, 業務ガイドのルールの id, そのルールの本文)。
    evidence_plans: tuple[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, bool], ...], str, str], ...] = ()
    # 「今日」(検索の基準日の既定)を決める IANA の時間帯(#3249。`PLATFORM_TIMEZONE`)。OS の時間帯は使わない。
    timezone: str = DEFAULT_TIMEZONE

    @property
    def vision_model(self) -> str:
        """既定 Vision provider に設定された model 名を返します。"""
        return get_llm_provider(self, self.default_vision_llm).model


def get_settings(*, environ: Mapping[str, str] | None = None, dotenv_path: str | Path | None = ".env", **overrides) -> Settings:
    """設定 snapshot を構築する。明示値 > 環境変数 > .env の順。

    environ と dotenv_path=None で外部環境を読み込まずに利用できる。
    os.environ は変更せず、ネットワーク接続やディレクトリ作成も行わない。
    """
    env = dict(os.environ if environ is None else environ)
    if dotenv_path is not None and Path(dotenv_path).is_file():
        for key, value in _dotenv_items(Path(dotenv_path).read_text(encoding="utf-8")):
            env.setdefault(key, value)
    enabled = env.get(
        "RAG_ENGINE_ENABLED_ENGINES",
        "docling",
    )
    enabled_engines = [part.strip() for part in enabled.split(",") if part.strip()]
    llm_api_mode = normalize_llm_api_mode(env.get("LLM_API_MODE"), DEFAULT_LLM_API_MODE)
    # 接続先・認証は backend のモデル設定(OCI_ENTERPRISE_AI_*)を共有する。model ID は既定値を持たず、
    # 未設定は呼び出し時に _validated_llm_provider() が不足として報告する。
    endpoint = env.get("OCI_ENTERPRISE_AI_ENDPOINT", "").strip()
    project = env.get("OCI_ENTERPRISE_AI_PROJECT_OCID", "").strip()
    api_key = env.get("OCI_ENTERPRISE_AI_API_KEY", "").strip()
    answer_model = env.get("OCI_ENTERPRISE_AI_DEFAULT_MODEL", "").strip()
    vision_model = env.get("OCI_ENTERPRISE_AI_VLM_MODEL", "").strip() or answer_model
    # Vision のモデルが別の接続のとき(#533)は OCI_ENTERPRISE_AI_VLM_* を使う。無ければ回答と同じ。
    vision_endpoint = env.get("OCI_ENTERPRISE_AI_VLM_ENDPOINT", "").strip() or endpoint
    vision_project = env.get("OCI_ENTERPRISE_AI_VLM_PROJECT_OCID", "").strip() or project
    vision_api_key = env.get("OCI_ENTERPRISE_AI_VLM_API_KEY", "").strip() or api_key
    # 推論(思考)の量はモデルごと(#1425)。Vision のモデルを指定しないときは回答のモデルの値。
    answer_reasoning = env.get("OCI_ENTERPRISE_AI_REASONING_EFFORT", "").strip()
    vision_reasoning = (
        env.get("OCI_ENTERPRISE_AI_VLM_REASONING_EFFORT", "").strip()
        if env.get("OCI_ENTERPRISE_AI_VLM_MODEL", "").strip()
        else answer_reasoning
    )
    # 出力の上限もモデルごと(#1719)。backend が platform の `enterprise_ai_output_token_budget` で決めて渡す。
    # Vision のモデルを指定しないときは回答のモデルの値。
    answer_output_tokens = _positive_int(env, "OCI_ENTERPRISE_AI_MAX_OUTPUT_TOKENS", DEFAULT_MODEL_OUTPUT_TOKENS)
    vision_output_tokens = (
        _positive_int(env, "OCI_ENTERPRISE_AI_VISION_MAX_OUTPUT_TOKENS", DEFAULT_MODEL_OUTPUT_TOKENS)
        if env.get("OCI_ENTERPRISE_AI_VLM_MODEL", "").strip()
        else answer_output_tokens
    )
    llm_providers = {
        ENTERPRISE_AI_LLM_PROVIDER: LlmProviderSettings(
            provider_id=ENTERPRISE_AI_LLM_PROVIDER,
            label=llm_provider_label("Enterprise AI", answer_model),
            region="",
            project_id=project,
            api_key=api_key,
            model=answer_model,
            # 回答のモデルが画像を読めるか(#3444。backend がモデル設定の登録モデルの vision_enabled で決めて渡す)。
            supports_vision=_env_bool(env, "OCI_ENTERPRISE_AI_DEFAULT_MODEL_VISION", False),
            endpoint=endpoint,
            reasoning_effort=answer_reasoning,
            max_output_tokens=answer_output_tokens,
        ),
        ENTERPRISE_AI_VISION_LLM_PROVIDER: LlmProviderSettings(
            provider_id=ENTERPRISE_AI_VISION_LLM_PROVIDER,
            label=llm_provider_label("Enterprise AI Vision", vision_model),
            region="",
            project_id=vision_project,
            api_key=vision_api_key,
            model=vision_model,
            supports_vision=True,
            endpoint=vision_endpoint,
            reasoning_effort=vision_reasoning,
            max_output_tokens=vision_output_tokens,
        ),
    }
    escalation_model = env.get(f"{ESCALATION_ENV_PREFIX}_MODEL", "").strip()
    if escalation_model:
        llm_providers[ESCALATION_LLM_PROVIDER] = LlmProviderSettings(
            provider_id=ESCALATION_LLM_PROVIDER,
            label=llm_provider_label("Enterprise AI 確認を通らなかったときのモデル", escalation_model),
            region="",
            project_id=env.get(f"{ESCALATION_ENV_PREFIX}_PROJECT_OCID", "").strip(),
            api_key=env.get(f"{ESCALATION_ENV_PREFIX}_API_KEY", "").strip(),
            model=escalation_model,
            supports_vision=_env_bool(env, f"{ESCALATION_ENV_PREFIX}_VISION", False),
            endpoint=env.get(f"{ESCALATION_ENV_PREFIX}_ENDPOINT", "").strip(),
            reasoning_effort=env.get(f"{ESCALATION_ENV_PREFIX}_REASONING_EFFORT", "").strip(),
            max_output_tokens=_positive_int(env, f"{ESCALATION_ENV_PREFIX}_MAX_OUTPUT_TOKENS", DEFAULT_MODEL_OUTPUT_TOKENS),
        )
    default_vision_llm = normalize_llm_provider_id(
        env.get("DEFAULT_VISION_LLM"),
        ENTERPRISE_AI_VISION_LLM_PROVIDER,
    )
    default_answer_llm = normalize_llm_provider_id(
        env.get("DEFAULT_ANSWER_LLM"),
        ENTERPRISE_AI_LLM_PROVIDER,
    )
    runtime_knowledge_raw = env.get("RUNTIME_KNOWLEDGE_PATH", "").strip()
    settings = Settings(
        hybrid_search_backend=_hybrid_backend(env.get("RAG_HYBRID_SEARCH_BACKEND", "oracle_hybrid_index")),
        rrf_method_ratio=_rrf_ratio(env.get("RAG_RRF_METHOD_RATIO", "[7,3]")),
        rrf_source_ratio=_rrf_ratio(env.get("RAG_RRF_SOURCE_RATIO", "[7,3]")),
        rerank_slot_ratio=_slot_ratio(env.get("RAG_RERANK_SLOT_RATIO", "[7,3]")),
        runtime_terms_search_enabled=_env_bool(env, "RUNTIME_TERMS_SEARCH_ENABLED", False),
        runtime_terms_answer_enabled=_env_bool(env, "RUNTIME_TERMS_ANSWER_ENABLED", False),
        original_query_only=_env_bool(env, "RETRIEVAL_ORIGINAL_QUERY_ONLY", False),
        auth_user=env.get("RAG_ENGINE_AUTH_USER", "").strip() or "admin",
        # password は前後の空白も値の一部になり得るため strip しない。
        auth_password=env.get("RAG_ENGINE_AUTH_PASSWORD", ""),
        auth_disabled=_env_bool(env, "RAG_ENGINE_DISABLE_AUTH", False),
        host=env.get("RAG_ENGINE_HOST", "0.0.0.0"),
        port=_env_int(env, "RAG_ENGINE_PORT", 8080),
        output_dir=Path(env.get("RAG_ENGINE_OUTPUT_DIR", ".runs")),
        runtime_knowledge_path=Path(runtime_knowledge_raw).expanduser() if runtime_knowledge_raw else None,
        render_dpi=_env_int(env, "RAG_ENGINE_RENDER_DPI", 300),
        max_default_pages=_env_int(env, "RAG_ENGINE_MAX_DEFAULT_PAGES", 1),
        enabled_engines=enabled_engines,
        docling_device=env.get("DOCLING_DEVICE", "cpu"),
        docling_num_threads=_env_int(env, "DOCLING_NUM_THREADS", _env_int(env, "OMP_NUM_THREADS", 4)),
        docling_do_ocr=_env_bool(env, "DOCLING_DO_OCR", True),
        docling_do_table_structure=_env_bool(env, "DOCLING_DO_TABLE_STRUCTURE", True),
        docling_keep_picture_child_text=_env_bool(env, "DOCLING_KEEP_PICTURE_CHILD_TEXT", False),
        oci_config_file=env.get("OCI_CONFIG_FILE", DEFAULT_OCI_CONFIG_FILE),
        oci_profile=env.get("OCI_PROFILE", DEFAULT_OCI_PROFILE),
        oci_compartment_id=env.get("OCI_COMPARTMENT_ID", "").strip(),
        oci_vision_endpoint=env.get("OCI_VISION_ENDPOINT", DEFAULT_OCI_VISION_ENDPOINT).strip(),
        # 旧名 VISION_* は Vision 限定に見えるため改名した。リポジトリ外の既存 .env が黙って既定値へ
        # 戻らないよう、新しい名前が未設定のときだけ旧名を読む (#912)。
        llm_request_timeout_seconds=_env_int(env, "LLM_REQUEST_TIMEOUT_SECONDS", _env_int(env, "VISION_REQUEST_TIMEOUT_SECONDS", 90)),
        answer_max_tokens=_positive_int(env, "ANSWER_MAX_TOKENS", DEFAULT_MODEL_OUTPUT_TOKENS),
        online_max_output_tokens=_positive_int(env, "RAG_ENGINE_ONLINE_MAX_OUTPUT_TOKENS", DEFAULT_ONLINE_MAX_OUTPUT_TOKENS),
        default_rerank_enabled=_env_bool(env, "RAG_ENGINE_DEFAULT_RERANK_ENABLED", True),
        approved_faq_semantic_matching_enabled=_env_bool(env, "APPROVED_FAQ_SEMANTIC_MATCHING_ENABLED", True),
        approved_faq_semantic_suggestions_enabled=_env_bool(env, "APPROVED_FAQ_SEMANTIC_SUGGESTIONS_ENABLED", False),
        rerank_model=env.get("RERANK_MODEL", "cohere.rerank-v4.0-fast").strip(),
        oci_rerank_endpoint=env.get("OCI_RERANK_ENDPOINT", "").strip(),
        rerank_request_timeout_seconds=_env_int(env, "RERANK_REQUEST_TIMEOUT_SECONDS", 90),
        rerank_retries=_env_int(env, "RERANK_RETRIES", 5),
        rerank_retry_initial_wait_seconds=_env_float(env, "RERANK_RETRY_INITIAL_WAIT_SECONDS", 1.0),
        rerank_retry_max_wait_seconds=_env_float(env, "RERANK_RETRY_MAX_WAIT_SECONDS", 16.0),
        rerank_min_relevance_score=max(
            0.0,
            _env_float(env, "RERANK_MIN_RELEVANCE_SCORE", DEFAULT_RERANK_MIN_RELEVANCE_SCORE),
        ),
        document_selection_enabled=_env_bool(env, "RAG_ENGINE_DOCUMENT_SELECTION", True),
        document_selection_score_ratio=max(0.0, min(1.0, _env_float(env, "RAG_ENGINE_DOCUMENT_SELECTION_SCORE_RATIO", 0.5))),
        embedding_region=env.get("EMBEDDING_REGION", DEFAULT_EMBEDDING_REGION).strip() or DEFAULT_EMBEDDING_REGION,
        embedding_endpoint=env.get("EMBEDDING_ENDPOINT", "").strip(),
        embedding_model=env.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip() or DEFAULT_EMBEDDING_MODEL,
        embedding_output_dimensions=_env_int(
            env,
            "EMBEDDING_OUTPUT_DIMENSIONS",
            DEFAULT_EMBEDDING_OUTPUT_DIMENSIONS,
        ),
        embedding_batch_size=_env_int(env, "EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE),
        image_embedding_enabled=_env_bool(env, "IMAGE_EMBEDDING_ENABLED", False),
        image_embedding_rrf_weight=max(
            0.0,
            _env_float(env, "IMAGE_EMBEDDING_RRF_WEIGHT", DEFAULT_IMAGE_EMBEDDING_RRF_WEIGHT),
        ),
        text_search_query_variant_limit=max(
            1,
            _env_int(env, "TEXT_SEARCH_QUERY_VARIANT_LIMIT", DEFAULT_TEXT_SEARCH_QUERY_VARIANT_LIMIT),
        ),
        max_anchors_per_parent=max(1, _env_int(env, "RAG_ENGINE_MAX_ANCHORS_PER_PARENT", DEFAULT_MAX_ANCHORS_PER_PARENT)),
        max_anchors_per_document=max(0, _env_int(env, "RAG_ENGINE_MAX_ANCHORS_PER_DOCUMENT", DEFAULT_MAX_ANCHORS_PER_DOCUMENT)),
        retrieval_top_k=max(1, _env_int(env, "RAG_ENGINE_RETRIEVAL_TOP_K", DEFAULT_RETRIEVAL_TOP_K_SETTING)),
        retrieval_candidate_limit=max(1, _env_int(env, "RAG_ENGINE_RETRIEVAL_CANDIDATE_LIMIT", 140)),
        rerank_candidate_limit=max(0, _env_int(env, "RAG_ENGINE_RERANK_CANDIDATE_LIMIT", 200)),
        rerank_top_n=max(0, _env_int(env, "RAG_ENGINE_RERANK_TOP_N", 60)),
        retrieval_stage_limits={
            str(key): max(0, int(value)) for key, value in json.loads(env.get("RAG_ENGINE_RETRIEVAL_STAGE_LIMITS", "{}")).items()
        },
        max_context_records=max(1, _env_int(env, "RAG_ENGINE_MAX_CONTEXT_RECORDS", DEFAULT_MAX_CONTEXT_RECORDS)),
        evidence_budget_chars=max(1000, _env_int(env, "RAG_ENGINE_EVIDENCE_BUDGET_CHARS", DEFAULT_EVIDENCE_BUDGET_CHARS)),
        batch_read_workers=min(8, max(1, _env_int(env, "RAG_ENGINE_BATCH_READ_WORKERS", 1))),
        request_coverage_max_queries=max(0, _env_int(env, "RAG_ENGINE_REQUEST_COVERAGE_MAX_QUERIES", 2)),
        request_coverage_max_chunks=max(0, _env_int(env, "RAG_ENGINE_REQUEST_COVERAGE_MAX_CHUNKS", 4)),
        request_coverage_deadline_seconds=max(0.0, _env_float(env, "RAG_ENGINE_REQUEST_COVERAGE_DEADLINE_SECONDS", 10.0)),
        request_coverage_max_subquestions=max(0, _env_int(env, "RAG_ENGINE_REQUEST_COVERAGE_MAX_SUBQUESTIONS", 5)),
        request_coverage_chunks_per_subquestion=max(0, _env_int(env, "RAG_ENGINE_REQUEST_COVERAGE_CHUNKS_PER_SUBQUESTION", 2)),
        extra_retrieval_max_queries=max(0, _env_int(env, "RAG_ENGINE_EXTRA_RETRIEVAL_MAX_QUERIES", 8)),
        guide_step_retrieval_enabled=_env_bool(env, "RAG_ENGINE_GUIDE_STEP_RETRIEVAL", True),
        guide_step_max_queries=max(0, _env_int(env, "RAG_ENGINE_GUIDE_STEP_MAX_QUERIES", 4)),
        guide_step_chunks_per_step=max(1, _env_int(env, "RAG_ENGINE_GUIDE_STEP_CHUNKS_PER_STEP", 2)),
        guide_reference_max_chunks=max(0, _env_int(env, "RAG_ENGINE_GUIDE_REFERENCE_MAX_CHUNKS", 4)),
        guide_step_deadline_seconds=max(0.0, _env_float(env, "RAG_ENGINE_GUIDE_STEP_DEADLINE_SECONDS", 60.0)),
        section_expansion_enabled=_env_bool(env, "RAG_ENGINE_SECTION_EXPANSION", True),
        section_expansion_max_levels=max(0, _env_int(env, "RAG_ENGINE_SECTION_EXPANSION_MAX_LEVELS", 4)),
        section_expansion_input_token_percent=max(1, min(100, _env_int(env, "RAG_ENGINE_SECTION_EXPANSION_INPUT_TOKEN_PERCENT", 50))),
        section_expansion_followup_reserve_percent=max(
            0, min(50, _env_int(env, "RAG_ENGINE_SECTION_EXPANSION_FOLLOWUP_RESERVE_PERCENT", 20))
        ),
        answer_max_input_tokens=max(1, _env_int(env, "OCI_ENTERPRISE_AI_MAX_INPUT_TOKENS", 128_000)),
        generation_reasoning_effort=normalize_reasoning_effort_override(env.get("RAG_ENGINE_GENERATION_REASONING_EFFORT")),
        llm_stall_detection=_env_bool(env, "RAG_ENGINE_LLM_STALL_DETECTION", False),
        llm_max_retries=max(0, _env_int(env, "RAG_ENGINE_LLM_MAX_RETRIES", 1)),
        llm_stall_idle_seconds=max(1.0, _env_float(env, "RAG_ENGINE_LLM_STALL_IDLE_SECONDS", 120.0)),
        llm_runaway_detection=_env_bool(env, "RAG_ENGINE_LLM_RUNAWAY_DETECTION", False),
        text_search_tokenizer_sudachi_dict=env.get(
            "TEXT_SEARCH_TOKENIZER_SUDACHI_DICT",
            DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT,
        ).strip()
        or DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT,
        text_search_tokenizer_sudachi_config=env.get(
            "TEXT_SEARCH_TOKENIZER_SUDACHI_CONFIG",
            "",
        ).strip(),
        text_search_tokenizer_latin_stemmer=env.get(
            "TEXT_SEARCH_TOKENIZER_LATIN_STEMMER",
            DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER,
        ).strip()
        or DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER,
        llm_api_mode=llm_api_mode,
        llm_providers=llm_providers,
        default_vision_llm=default_vision_llm,
        default_answer_llm=default_answer_llm,
        escalation_llm=ESCALATION_LLM_PROVIDER if escalation_model else "",
        escalation_max_input_tokens=max(1, _env_int(env, f"{ESCALATION_ENV_PREFIX}_MAX_INPUT_TOKENS", 128_000)),
        timezone=env.get("PLATFORM_TIMEZONE", "").strip() or DEFAULT_TIMEZONE,
    )

    if "adb_settings" not in overrides and all(env.get(key) for key in ("ADB_OCID", "ADB_WALLET_PASSWORD", "ADB_DB_PASSWORD")):
        from rag_engine.adapters.oracle.connection import load_adb_settings

        settings = replace(settings, adb_settings=load_adb_settings(environ=env, env_path=dotenv_path or ".env"))
    return replace(settings, **overrides)


# 回答の LLM の呼び出しの用途(#1499)。用途ごとに推論の量を上書きできる。
# 回答文の生成(generation)。
LLM_PURPOSE_GENERATION = "generation"
_PURPOSE_EFFORT_FIELDS = {
    LLM_PURPOSE_GENERATION: "generation_reasoning_effort",
}
_PURPOSE_LABELS = {
    LLM_PURPOSE_GENERATION: "回答文の生成",
}
# 上書きに使える値(platform の推論の量の選択肢と同じ。none は送らない)。
REASONING_EFFORT_OVERRIDES = ("none", "low", "medium", "high")
REASONING_EFFORT_OMIT = "none"


def normalize_reasoning_effort_override(value: str | None) -> str:
    """用途ごとの推論の量の上書きの値。選択肢に無い値(空・model など)は "" (モデルの登録値のまま)。"""
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in REASONING_EFFORT_OVERRIDES else ""


def settings_for_llm_purpose(settings: Settings, purpose: str) -> Settings:
    """用途の推論の量を反映した設定(#1499)。上書きが無ければ同じ設定を返す。"""
    return settings_with_reasoning_effort(settings, getattr(settings, _PURPOSE_EFFORT_FIELDS.get(purpose, ""), ""))


def step_provider(settings: Any, field_name: str, provider_id: str | None) -> str | None:
    """段の呼び出しの provider ID。段のモデルの設定(`Settings` の ``field_name``)があればそれ、無ければ呼び出し側の provider。"""
    return str(getattr(settings, field_name, "") or "") or provider_id


def settings_with_reasoning_effort(settings: Settings, value: str | None) -> Settings:
    """推論の量の上書き ``value`` を反映した設定。上書きが無ければ同じ設定を返す。

    LLM の呼び出しに渡す設定だけを差し替える(provider の `reasoning_effort` は送る値)。モデルの登録値が
    送らない(空)の provider は、上書きしても送らない。用途の上書き(`settings_for_llm_purpose`)と、工程の
    呼び出しの上書きが共有する。
    """
    override = normalize_reasoning_effort_override(value)
    if not override:
        return settings
    current = getattr(settings, "llm_providers", None)
    if not isinstance(settings, Settings) or not isinstance(current, dict):
        return settings
    to_send = "" if override == REASONING_EFFORT_OMIT else override
    providers = {
        key: replace(provider, reasoning_effort=to_send) if provider.reasoning_effort else provider for key, provider in current.items()
    }
    if providers == current:
        return settings
    return replace(settings, llm_providers=providers)


def llm_reasoning_effort_note(settings: Settings, purpose: str, provider_id: str | None = None) -> str:
    """実行記録に残す、その用途で送る推論の量と根拠(#1499)。例: 「推論の量: low（根拠の評価の設定）」。"""
    if not isinstance(settings, Settings):
        return ""
    try:
        registered = get_llm_provider(settings, provider_id or settings.default_answer_llm).reasoning_effort
    except RuntimeError:
        return ""
    override = normalize_reasoning_effort_override(getattr(settings, _PURPOSE_EFFORT_FIELDS.get(purpose, ""), ""))
    label = _PURPOSE_LABELS.get(purpose, purpose)
    if not registered:
        return "推論の量: 送らない（モデルの登録値が none）"
    if not override:
        return f"推論の量: {registered}（モデルの登録値）"
    if override == REASONING_EFFORT_OMIT:
        return f"推論の量: 送らない（{label}の設定）"
    return f"推論の量: {override}（{label}の設定。モデルの登録値は {registered}）"


def get_llm_provider(settings: Settings, provider_id: str | None, default: str = DEFAULT_LLM_PROVIDER) -> LlmProviderSettings:
    """provider ID を解決し、利用する LlmProviderSettings を返します。"""
    normalized = normalize_llm_provider_id(provider_id, default)
    provider = settings.llm_providers.get(normalized)
    if provider is None:
        raise RuntimeError(f"Unknown LLM provider: {provider_id}")
    return provider


def answer_model_reads_images(settings: Settings, provider_id: str | None = None) -> bool:
    """回答のモデル(``provider_id``。None は既定の回答のモデル)が画像を読めるか(#3444)。"""
    try:
        provider = get_llm_provider(settings, provider_id or settings.default_answer_llm)
    except RuntimeError:
        return False
    return bool(provider.supports_vision and provider.model)


def llm_output_token_limit(settings: Settings, provider: Any) -> int:
    """構造化出力の呼び出し 1 回の出力の上限(#1719)。

    回答の上限(`answer_max_tokens`。既定 32768)と、呼ぶモデルの出力の上限(`max_output_tokens`。backend が
    platform の `enterprise_ai_output_token_budget` で決めて渡す)の小さい方。
    """
    model_limit = int(getattr(provider, "max_output_tokens", DEFAULT_MODEL_OUTPUT_TOKENS) or DEFAULT_MODEL_OUTPUT_TOKENS)
    return max(1, min(int(settings.answer_max_tokens), model_limit))


def _rrf_ratio(raw: str) -> tuple[float, float]:
    from rag_engine.retrieval.weighted_rrf import RrfWeights

    parsed = json.loads(raw)
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise ValueError("RRF の比重は 2 要素で指定してください。")
    left, right = float(parsed[0]), float(parsed[1])
    RrfWeights(vector=left, keyword=right)
    return left, right


def _slot_ratio(raw: str) -> tuple[int, int]:
    from rag_engine.retrieval.rerank_order import validate_slot_ratio

    return validate_slot_ratio(json.loads(raw))


def _hybrid_backend(value: str) -> str:
    if value not in {"oracle_hybrid_index", "oracle_separate_sql"}:
        raise ValueError("検索方式はoracle_hybrid_indexまたはoracle_separate_sqlを指定してください。")
    return value
