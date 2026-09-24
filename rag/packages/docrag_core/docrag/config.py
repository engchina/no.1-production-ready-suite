"""環境変数からアプリ設定と LLM provider 設定を構築する。"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from docrag.adapters.oracle.connection import AdbSettings

from docrag.knowledge.query_history import (
    QUERY_HISTORY_DEFAULT_MIN_COUNT,
    QUERY_HISTORY_DEFAULT_MIN_UNIQUE_RUNS,
    QUERY_HISTORY_DEFAULT_RETENTION_DAYS,
    QUERY_HISTORY_DEFAULT_SUGGESTION_LIMIT,
)
from docrag.retrieval.text_search_tokenizer import (
    DEFAULT_TEXT_SEARCH_TOKENIZER,
    DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER,
    DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT,
)


# LLM は本リポジトリのモデル設定(OCI Enterprise AI, OpenAI 互換 Responses API)に一本化する。
# 回答系(生成・監査・CRAG・質問拡張)と Vision(図説明)で model だけを分ける。
ENTERPRISE_AI_LLM_PROVIDER = "enterprise-ai"
ENTERPRISE_AI_VISION_LLM_PROVIDER = "enterprise-ai-vision"
DEFAULT_LLM_PROVIDER = ENTERPRISE_AI_LLM_PROVIDER
DEFAULT_CHICAGO_REGION = "us-chicago-1"
DEFAULT_OSAKA_REGION = "ap-osaka-1"
DEFAULT_OCI_CONFIG_FILE = "~/.oci/config"
DEFAULT_OCI_PROFILE = "DEFAULT"
OCI_INFERENCE_ENDPOINT_TEMPLATE = "https://inference.generativeai.{region}.oci.oraclecloud.com"
OCI_OPENAI_BASE_URL_TEMPLATE = OCI_INFERENCE_ENDPOINT_TEMPLATE + "/openai/v1"
DEFAULT_OCI_VISION_ENDPOINT = OCI_INFERENCE_ENDPOINT_TEMPLATE.format(region=DEFAULT_CHICAGO_REGION)
OPENAI_RESPONSES_LLM_API_MODE = "openai_responses"
DEFAULT_LLM_API_MODE = OPENAI_RESPONSES_LLM_API_MODE
DEFAULT_EMBEDDING_REGION = DEFAULT_OSAKA_REGION
DEFAULT_EMBEDDING_MODEL = "cohere.embed-v4.0"
DEFAULT_EMBEDDING_OUTPUT_DIMENSIONS = 1536
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_IMAGE_EMBEDDING_RRF_WEIGHT = 0.75
DEFAULT_MAX_ANCHORS_PER_PARENT = 2
# 検索の起点 child 数（UI の既定値）、context に入れる parent 数、生成に渡す原文の総予算（文字）の既定値 (#1054)。
# chunking.constants.DEFAULT_RETRIEVAL_TOP_K / answer_records.MAX_CONTEXT_RECORDS / evidence_selection.EVIDENCE_BUDGET_CHARS
# と同じ値（循環 import を避けて写す。一致は tests/test_retrieval_budget_settings.py で確認する）。
DEFAULT_RETRIEVAL_TOP_K_SETTING = 20
DEFAULT_MAX_CONTEXT_RECORDS = 12
DEFAULT_EVIDENCE_BUDGET_CHARS = 48000
DEFAULT_TEXT_SEARCH_QUERY_VARIANT_LIMIT = 6
DEFAULT_RERANK_MIN_RELEVANCE_SCORE = 0.0
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
_VERBATIM_ENV_KEYS = frozenset({"DOCRAG_AUTH_PASSWORD"})


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


@dataclass(frozen=True)
class LlmProviderSettings:
    """1 つの LLM provider の接続先、認証、model 能力を保持します。"""
    provider_id: str
    label: str
    # endpoint を指定しない場合だけ base URL の組み立てに使う。
    region: str
    # OCI の OpenAI 互換 API が要求する project OCID。他の OpenAI 互換 API では空のままにする。
    project_id: str
    api_key: str
    model: str
    supports_vision: bool = False
    endpoint: str = ""

    @property
    def base_url(self) -> str:
        """root URL に API パスを補完し、空なら region から構築します。

        既存の OpenAI 互換 base URL に含まれるパスは維持します。
        """
        endpoint = self.endpoint.strip().rstrip("/")
        if not endpoint:
            return OCI_OPENAI_BASE_URL_TEMPLATE.format(region=self.region)
        parsed = urlsplit(endpoint)
        if not parsed.path:
            return parsed._replace(path="/openai/v1").geturl()
        return endpoint


@dataclass(frozen=True)
class Settings:
    """アプリ全体の環境変数由来設定を保持します。"""
    host: str
    port: int
    output_dir: Path
    runtime_knowledge_path: Path | None
    query_history_enabled: bool
    query_history_retention_days: int
    query_history_min_count: int
    query_history_min_unique_runs: int
    query_history_suggestion_limit: int
    query_history_blocklist_path: Path | None
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
    llm_retries: int
    llm_retry_initial_wait_seconds: float
    llm_retry_max_wait_seconds: float
    answer_max_tokens: int
    default_rerank_enabled: bool
    rerank_model: str
    oci_rerank_endpoint: str
    rerank_request_timeout_seconds: int
    rerank_retries: int
    rerank_retry_initial_wait_seconds: float
    rerank_retry_max_wait_seconds: float
    rerank_min_relevance_score: float
    # 粗→細の検索: rerank 後の候補を文書ごとに集約し、分数が明らかに低い文書を後回しにする (#1028)。既定 on (#1040)。
    document_selection_enabled: bool
    document_selection_score_ratio: float
    document_selection_min_hits: int
    # 質問から操作すべき画面を画面目録で選び、その画面の根拠を検索候補に加える (#1108)。1 質問につき LLM 呼出が
    # 1 回増える。効果を 83 問で確かめるまで既定 off。
    screen_linking_enabled: bool
    # 1 以上なら、選んだ画面ごとの最上位の child を rerank 後の順位 N 位（2 つ目は N+1 位…）より下に置かない。
    # 0（既定）は候補に加えるだけで順位は rerank に任せる。比較実験用 (#1108)。
    screen_linking_reserve_rank: int
    embedding_region: str
    embedding_endpoint: str
    embedding_model: str
    embedding_output_dimensions: int
    embedding_batch_size: int
    image_embedding_enabled: bool
    image_embedding_rrf_weight: float
    text_search_tokenizer: str
    text_search_query_variant_limit: int
    # 文脈構築で同じ親から起点（retrieved_anchor）に採る child の上限（#669、#889）。1 以上。
    max_anchors_per_parent: int
    text_search_tokenizer_sudachi_dict: str
    text_search_tokenizer_sudachi_config: str
    text_search_tokenizer_latin_stemmer: str
    llm_api_mode: str
    llm_providers: dict[str, LlmProviderSettings]
    default_vision_llm: str
    default_answer_llm: str

    # None は保存済み辞書、空 tuple は評価用の辞書無効化を表す。共有ファイルは変更しない。
    domain_keywords_override: tuple[str, ...] | None = None
    # 検索の比較実験用。重み付けを切ると全検索文が同じ票、original_query_only は原質問だけで検索する。
    original_query_weighting_enabled: bool = True
    original_query_only: bool = False
    profile_channel_enabled: bool = True
    workspace_dir: Path | None = None
    prompt_dir: Path | None = None
    faq_path: Path | None = None
    # 業務固有の問い合わせ規則(legacy profile)は既定 OFF。DOCRAG_PROFILE=legacy で opt-in する。
    profile: str = "generic"
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
    max_context_records: int = DEFAULT_MAX_CONTEXT_RECORDS
    evidence_budget_chars: int = DEFAULT_EVIDENCE_BUDGET_CHARS

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
        "DOCRAG_ENABLED_ENGINES",
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
    llm_providers = {
        ENTERPRISE_AI_LLM_PROVIDER: LlmProviderSettings(
            provider_id=ENTERPRISE_AI_LLM_PROVIDER,
            label=llm_provider_label("Enterprise AI", answer_model),
            region="",
            project_id=project,
            api_key=api_key,
            model=answer_model,
            # 回答モデルが画像入力に対応する場合だけ根拠画像を添付する(既定 OFF)。
            supports_vision=_env_bool(env, "DOCRAG_ANSWER_LLM_SUPPORTS_VISION", False),
            endpoint=endpoint,
        ),
        ENTERPRISE_AI_VISION_LLM_PROVIDER: LlmProviderSettings(
            provider_id=ENTERPRISE_AI_VISION_LLM_PROVIDER,
            label=llm_provider_label("Enterprise AI Vision", vision_model),
            region="",
            project_id=project,
            api_key=api_key,
            model=vision_model,
            supports_vision=True,
            endpoint=endpoint,
        ),
    }
    default_vision_llm = normalize_llm_provider_id(
        env.get("DEFAULT_VISION_LLM"),
        ENTERPRISE_AI_VISION_LLM_PROVIDER,
    )
    default_answer_llm = normalize_llm_provider_id(
        env.get("DEFAULT_ANSWER_LLM"),
        ENTERPRISE_AI_LLM_PROVIDER,
    )
    runtime_knowledge_raw = env.get("RUNTIME_KNOWLEDGE_PATH", "").strip()
    query_history_blocklist_raw = env.get("QUERY_HISTORY_BLOCKLIST_PATH", "").strip()
    settings = Settings(
        original_query_weighting_enabled=_env_bool(env, "RETRIEVAL_ORIGINAL_QUERY_WEIGHTING", True),
        original_query_only=_env_bool(env, "RETRIEVAL_ORIGINAL_QUERY_ONLY", False),
        profile_channel_enabled=_env_bool(env, "RETRIEVAL_PROFILE_CHANNEL", True),
        auth_user=env.get("DOCRAG_AUTH_USER", "").strip() or "admin",
        # password は前後の空白も値の一部になり得るため strip しない。
        auth_password=env.get("DOCRAG_AUTH_PASSWORD", ""),
        auth_disabled=_env_bool(env, "DOCRAG_DISABLE_AUTH", False),
        host=env.get("DOCRAG_HOST", "0.0.0.0"),
        port=_env_int(env, "DOCRAG_PORT", 8080),
        output_dir=Path(env.get("DOCRAG_OUTPUT_DIR", ".runs")),
        runtime_knowledge_path=Path(runtime_knowledge_raw).expanduser() if runtime_knowledge_raw else None,
        query_history_enabled=_env_bool(env, "QUERY_HISTORY_ENABLED", False),
        query_history_retention_days=max(0, _env_int(env, "QUERY_HISTORY_RETENTION_DAYS", QUERY_HISTORY_DEFAULT_RETENTION_DAYS)),
        query_history_min_count=max(1, _env_int(env, "QUERY_HISTORY_MIN_COUNT", QUERY_HISTORY_DEFAULT_MIN_COUNT)),
        query_history_min_unique_runs=max(1, _env_int(env, "QUERY_HISTORY_MIN_UNIQUE_RUNS", QUERY_HISTORY_DEFAULT_MIN_UNIQUE_RUNS)),
        query_history_suggestion_limit=max(1, _env_int(env, "QUERY_HISTORY_SUGGESTION_LIMIT", QUERY_HISTORY_DEFAULT_SUGGESTION_LIMIT)),
        query_history_blocklist_path=Path(query_history_blocklist_raw).expanduser()
        if query_history_blocklist_raw
        else None,
        render_dpi=_env_int(env, "DOCRAG_RENDER_DPI", 300),
        max_default_pages=_env_int(env, "DOCRAG_MAX_DEFAULT_PAGES", 1),
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
        llm_retries=_env_int(env, "LLM_RETRIES", _env_int(env, "VISION_RETRIES", 3)),
        llm_retry_initial_wait_seconds=_env_float(env, "LLM_RETRY_INITIAL_WAIT_SECONDS", _env_float(env, "VISION_RETRY_INITIAL_WAIT_SECONDS", 2.0)),
        llm_retry_max_wait_seconds=_env_float(env, "LLM_RETRY_MAX_WAIT_SECONDS", _env_float(env, "VISION_RETRY_MAX_WAIT_SECONDS", 30.0)),
        answer_max_tokens=_env_int(env, "ANSWER_MAX_TOKENS", 24576),
        default_rerank_enabled=_env_bool(env, "DOCRAG_DEFAULT_RERANK_ENABLED", True),
        approved_faq_semantic_matching_enabled=_env_bool(env, "APPROVED_FAQ_SEMANTIC_MATCHING_ENABLED", True),
        approved_faq_semantic_suggestions_enabled=_env_bool(env, "APPROVED_FAQ_SEMANTIC_SUGGESTIONS_ENABLED", False),
        rerank_model=env.get("RERANK_MODEL", "cohere.rerank-v4.0-fast").strip(),
        oci_rerank_endpoint=env.get("OCI_RERANK_ENDPOINT", "").strip(),
        rerank_request_timeout_seconds=_env_int(env, "RERANK_REQUEST_TIMEOUT_SECONDS", 90),
        rerank_retries=_env_int(env, "RERANK_RETRIES", 3),
        rerank_retry_initial_wait_seconds=_env_float(env, "RERANK_RETRY_INITIAL_WAIT_SECONDS", 2.0),
        rerank_retry_max_wait_seconds=_env_float(env, "RERANK_RETRY_MAX_WAIT_SECONDS", 30.0),
        rerank_min_relevance_score=max(
            0.0,
            _env_float(env, "RERANK_MIN_RELEVANCE_SCORE", DEFAULT_RERANK_MIN_RELEVANCE_SCORE),
        ),
        document_selection_enabled=_env_bool(env, "DOCRAG_DOCUMENT_SELECTION", True),
        document_selection_score_ratio=max(0.0, min(1.0, _env_float(env, "DOCRAG_DOCUMENT_SELECTION_SCORE_RATIO", 0.5))),
        document_selection_min_hits=max(1, _env_int(env, "DOCRAG_DOCUMENT_SELECTION_MIN_HITS", 2)),
        screen_linking_enabled=_env_bool(env, "DOCRAG_SCREEN_LINKING", False),
        screen_linking_reserve_rank=max(0, _env_int(env, "DOCRAG_SCREEN_LINKING_RESERVE_RANK", 0)),
        embedding_region=env.get("EMBEDDING_REGION", DEFAULT_EMBEDDING_REGION).strip()
        or DEFAULT_EMBEDDING_REGION,
        embedding_endpoint=env.get("EMBEDDING_ENDPOINT", "").strip(),
        embedding_model=env.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
        or DEFAULT_EMBEDDING_MODEL,
        embedding_output_dimensions=_env_int(env,
            "EMBEDDING_OUTPUT_DIMENSIONS",
            DEFAULT_EMBEDDING_OUTPUT_DIMENSIONS,
        ),
        embedding_batch_size=_env_int(env, "EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE),
        image_embedding_enabled=_env_bool(env, "IMAGE_EMBEDDING_ENABLED", False),
        image_embedding_rrf_weight=max(
            0.0,
            _env_float(env, "IMAGE_EMBEDDING_RRF_WEIGHT", DEFAULT_IMAGE_EMBEDDING_RRF_WEIGHT),
        ),
        text_search_tokenizer=env.get("TEXT_SEARCH_TOKENIZER", DEFAULT_TEXT_SEARCH_TOKENIZER).strip()
        or DEFAULT_TEXT_SEARCH_TOKENIZER,
        text_search_query_variant_limit=max(
            1,
            _env_int(env, "TEXT_SEARCH_QUERY_VARIANT_LIMIT", DEFAULT_TEXT_SEARCH_QUERY_VARIANT_LIMIT),
        ),
        max_anchors_per_parent=max(1, _env_int(env, "DOCRAG_MAX_ANCHORS_PER_PARENT", DEFAULT_MAX_ANCHORS_PER_PARENT)),
        retrieval_top_k=max(1, _env_int(env, "DOCRAG_RETRIEVAL_TOP_K", DEFAULT_RETRIEVAL_TOP_K_SETTING)),
        max_context_records=max(1, _env_int(env, "DOCRAG_MAX_CONTEXT_RECORDS", DEFAULT_MAX_CONTEXT_RECORDS)),
        evidence_budget_chars=max(1000, _env_int(env, "DOCRAG_EVIDENCE_BUDGET_CHARS", DEFAULT_EVIDENCE_BUDGET_CHARS)),
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
        profile=env.get("DOCRAG_PROFILE", "generic").strip() or "generic",
        llm_providers=llm_providers,
        default_vision_llm=default_vision_llm,
        default_answer_llm=default_answer_llm,
    )


    if "adb_settings" not in overrides and all(env.get(key) for key in ("ADB_OCID", "ADB_WALLET_PASSWORD", "ADB_DB_PASSWORD")):
        from docrag.adapters.oracle.connection import load_adb_settings
        settings = replace(settings, adb_settings=load_adb_settings(environ=env, env_path=dotenv_path or ".env"))
    return replace(settings, **overrides)


def get_llm_provider(settings: Settings, provider_id: str | None, default: str = DEFAULT_LLM_PROVIDER) -> LlmProviderSettings:
    """provider ID を解決し、利用する LlmProviderSettings を返します。"""
    normalized = normalize_llm_provider_id(provider_id, default)
    provider = settings.llm_providers.get(normalized)
    if provider is None:
        raise RuntimeError(f"Unknown LLM provider: {provider_id}")
    return provider
