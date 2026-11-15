"""承認済み FAQ を質問と回答だけにした保存値の一度限りの migration（#5107）。

FAQ の項目を質問（QUESTION）と回答（ANSWER）だけにし、自動の直接回答と、選んだ FAQ を LLM に
渡す回答をやめた。検索・回答プロファイルの FAQ の payload（``RAG_SEARCH_ANSWER_PROFILE_KNOWLEDGE``
の ``KIND = 'approved_faq'`` の ``PAYLOAD_JSON``）から、読まなくなった項目を消す。消した項目は
読み替えない（後方互換を持たない）。semantic index の cache は署名が変わるので消して作り直させる。
FAQ の回答の指示（``RAG_ANSWER_PROMPTS`` の ``faq_answer.system``）の保存値も消す。
"""

from __future__ import annotations

# FAQ の payload から消す項目（JSON の path）。
REMOVED_APPROVED_FAQ_PATHS = (
    "$.direct_answer_enabled",
    "$.semantic_index_cache",
    "$.records[*].negative_questions",
    "$.records[*].target_knowledge_base_ids",
    "$.records[*].source_documents",
    "$.records[*].approval",
)
REMOVED_PROMPT_KEY = "faq_answer.system"


def removed_approved_faq_fields_sql() -> str:
    removes = ",\n    ".join(f"REMOVE '{path}'" for path in REMOVED_APPROVED_FAQ_PATHS)
    exists = "\n   OR ".join(
        f"JSON_EXISTS(payload_json, '{path}')" for path in REMOVED_APPROVED_FAQ_PATHS
    )
    return f"""
UPDATE rag_search_answer_profile_knowledge
SET payload_json = JSON_TRANSFORM(
    payload_json,
    {removes}
),
    revision = revision + 1,
    updated_at = SYSTIMESTAMP
WHERE kind = 'approved_faq'
  AND ({exists})
""".strip()


def removed_faq_answer_prompt_sql() -> str:
    return f"DELETE FROM RAG_ANSWER_PROMPTS\nWHERE PROMPT_KEY = '{REMOVED_PROMPT_KEY}'"
