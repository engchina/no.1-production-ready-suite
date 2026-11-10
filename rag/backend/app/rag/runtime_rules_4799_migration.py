"""登録ルールの保存値を消す一度限りの migration（#4799）。

検索・回答プロファイルの用語・同義語と同じ payload（``RAG_SEARCH_ANSWER_PROFILE_KNOWLEDGE`` の
``KIND = 'runtime_knowledge'`` の ``PAYLOAD_JSON``）に保存していた登録ルール（``$.rules``）は、
登録・確認・回答のどれにも使わなくなったので消す。用語（``$.terms``）とほかの種別の行は変えない。
回答の規則は回答プロンプトの役割の文（``generation.role``）に書く（読み替えはしない）。
"""

from __future__ import annotations


def removed_runtime_rules_sql() -> str:
    return """
UPDATE rag_search_answer_profile_knowledge
SET payload_json = JSON_TRANSFORM(payload_json, REMOVE '$.rules'),
    revision = revision + 1,
    updated_at = SYSTIMESTAMP
WHERE kind = 'runtime_knowledge'
  AND JSON_EXISTS(payload_json, '$.rules')
""".strip()
