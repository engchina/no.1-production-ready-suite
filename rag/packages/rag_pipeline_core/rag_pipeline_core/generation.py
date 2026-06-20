"""Generation プロファイルの決定論解決(backend / サービス共有)。

profile → OCI Enterprise AI へ渡す system prompt 変種 + 構造化出力フラグを決定論で解決する。
custom(prompt version store)/ persona override は backend 固有のため backend 側で上乗せする。
Settings 非依存(profile 名のみ受け取る)。外部 LLM provider は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

GENERATION_PROFILES: tuple[str, ...] = (
    "grounded_concise",
    "detailed_cited",
    "strict_extractive",
    "structured_json",
    "bilingual_ja_en",
    "inline_cited",
    "custom",
)
DEFAULT_GENERATION_PROFILE = "grounded_concise"

_DETAILED_CITED_PROMPT = (
    "あなたは社内ナレッジ検索アシスタントです。検索根拠(context)だけを使って日本語で回答し、"
    "主張ごとに対応する出典を [Evidence n | source#chunk_id] の形式で本文末尾に明示してください。"
    "context にない情報は補わず、確証がない点は不明と述べてください。"
)
_STRICT_EXTRACTIVE_PROMPT = (
    "あなたは厳密な抽出型 QA アシスタントです。検索根拠(context)に明示的に書かれている"
    "事実だけを日本語で簡潔に回答してください。推測・一般知識による補完は禁止します。"
    "context に答えがない場合は「提供された根拠には該当する情報がありません。」と回答してください。"
)
_STRUCTURED_JSON_PROMPT = (
    "あなたは社内ナレッジ検索アシスタントです。検索根拠(context)だけを使い、次の JSON だけを"
    'output してください(前後に説明文を付けない): {"answer": string, "evidence": [string], '
    '"sources": [string]}。answer は日本語、sources は使用した source#chunk_id。'
    "context にない情報は含めないでください。"
)
_BILINGUAL_PROMPT = (
    "あなたは社内ナレッジ検索アシスタントです。検索根拠(context)だけを使って回答してください。"
    "まず日本語で回答し、続けて「English summary:」として 1-2 文の英語要約を付けてください。"
    "context にない情報は補わないでください。"
)
# SAFE 型 inline citation: 生成後回溯ではなく、各文に対応 source chunk を即時付与する。
_INLINE_CITED_PROMPT = (
    "あなたは社内ナレッジ検索アシスタントです。検索根拠(context)だけを使って日本語で回答し、"
    "**各文の直後**に対応する出典を [source#chunk_id] の形式で逐句付与してください"
    "(生成後にまとめてではなく、文ごとに即時付与)。context にない情報は書かないでください。"
)


@dataclass(frozen=True)
class GenerationSpec:
    name: str
    origin: str
    recommended_for: tuple[str, ...]
    system_prompt: str | None  # None は client 既定 system prompt を使う
    structured_output: bool = False


GENERATION_SPECS: dict[str, GenerationSpec] = {
    "grounded_concise": GenerationSpec(
        "grounded_concise", "default_rag_prompt", ("general", "faq"), None
    ),
    "detailed_cited": GenerationSpec(
        "detailed_cited", "ragflow_cited_answer", ("audit", "policy"), _DETAILED_CITED_PROMPT
    ),
    "strict_extractive": GenerationSpec(
        "strict_extractive", "extractive_qa", ("compliance", "factual"), _STRICT_EXTRACTIVE_PROMPT
    ),
    "structured_json": GenerationSpec(
        "structured_json",
        "dify_structured_output",
        ("integration", "api"),
        _STRUCTURED_JSON_PROMPT,
        structured_output=True,
    ),
    "bilingual_ja_en": GenerationSpec(
        "bilingual_ja_en", "multilingual", ("global", "bilingual"), _BILINGUAL_PROMPT
    ),
    "inline_cited": GenerationSpec(
        "inline_cited", "safe_sentence_attribution", ("audit", "traceable"), _INLINE_CITED_PROMPT
    ),
    "custom": GenerationSpec(
        "custom", "prompt_version_store", ("custom", "tuning"), None
    ),
}


@dataclass(frozen=True)
class GenerationResolved:
    profile: str
    system_prompt: str | None
    structured_output: bool


def normalize_generation_profile(value: object) -> str:
    normalized = str(value).casefold()
    return normalized if normalized in GENERATION_SPECS else DEFAULT_GENERATION_PROFILE


def resolve_generation(profile: object) -> GenerationResolved:
    """profile から静的な system prompt + 構造化出力フラグを解決する(custom/override は backend)。"""
    name = normalize_generation_profile(profile)
    spec = GENERATION_SPECS[name]
    return GenerationResolved(
        profile=name, system_prompt=spec.system_prompt, structured_output=spec.structured_output
    )
