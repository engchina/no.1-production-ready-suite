"""利用者が選んだ類似の承認済み FAQ を、検索文と回答のプロンプトに渡す(#684)。"""
from pathlib import Path

from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext


def _context() -> RuntimeKnowledgeContext:
    return RuntimeKnowledgeContext(original_question="特典は？", expanded_question="特典は？", path=Path("x"))


def test_selected_faq_is_added_to_search_text_and_prompt() -> None:
    context = _context().with_approved_faq("  住宅ローンの特典は？ ", "QUO カード 5 千円を贈呈します。")
    assert context.has_matches
    assert context.expanded_question == "特典は？\n住宅ローンの特典は？"
    prompt = context.prompt_context()
    assert "[利用者が選んだ類似の承認済み FAQ]" in prompt
    assert "- 類似問: 住宅ローンの特典は？" in prompt
    assert "- 承認済みの回答: QUO カード 5 千円を贈呈します。" in prompt
    # 根拠文書を優先し、FAQ だけにある内容は出典を明示する。
    assert "根拠文書を優先" in prompt and "承認済み FAQ では" in prompt
    # 用語・ルールの見出しは、一致したものが無ければ出さない。
    assert "[Runtime Glossary / Rules]" not in prompt
    assert context.to_payload()["approved_faq_question"] == "住宅ローンの特典は？"


def test_blank_faq_question_is_ignored() -> None:
    context = _context()
    assert context.with_approved_faq(" ", "回答") is context
    assert not context.has_matches and context.prompt_context() == ""
