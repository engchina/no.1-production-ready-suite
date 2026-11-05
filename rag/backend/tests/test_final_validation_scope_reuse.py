"""最終検証の指示変更を回答の来歴と再利用依存へ反映する。"""

from typing import Any

import pytest
from rag_engine.evaluation import answer_validation
from rag_engine.resources.runtime import bind_prompt_overrides, resolve_prompt

from app.config import get_settings
from app.rag.answer_provenance import _code_prompts, answer_prompt_version
from app.rag.answer_validation import validation_reuse_context


@pytest.mark.parametrize("saved_override", [False, True], ids=["default", "saved-override"])
def test_final_validation_system_change_invalidates_reuse_context(
    monkeypatch: pytest.MonkeyPatch, saved_override: bool
) -> None:
    arguments: dict[str, Any] = {
        "question": "項目Aの値は何ですか。",
        "answer": "項目Aの値は42です。",
        "settings": get_settings(),
        "evidence": [{"id": "synthetic-source-a", "text": "項目Aの値は42です。"}],
        "premises": [],
        "data_results": [],
        "guide": None,
        "requests": None,
        "gaps": [],
    }
    original_system = answer_validation.VALIDATION_SYSTEM_PROMPT
    overrides = {"answer_validation.system": original_system} if saved_override else {}
    _code_prompts.cache_clear()
    try:
        original_version = answer_prompt_version(overrides)
        original_context = validation_reuse_context(**arguments, prompt_version=original_version)
        with monkeypatch.context() as scoped:
            scoped.setattr(
                answer_validation,
                "VALIDATION_SYSTEM_PROMPT",
                answer_validation.VALIDATION_SYSTEM_PROMPT + "\n合成の指示差分。",
            )
            _code_prompts.cache_clear()
            with bind_prompt_overrides(overrides):
                actual = resolve_prompt(
                    "answer_validation.system", answer_validation.VALIDATION_SYSTEM_PROMPT
                )
            assert actual == (
                original_system if saved_override else answer_validation.VALIDATION_SYSTEM_PROMPT
            )
            changed_version = answer_prompt_version(overrides)
            changed_context = validation_reuse_context(**arguments, prompt_version=changed_version)
        assert changed_version != original_version
        assert changed_context != original_context
    finally:
        # 同じprocessのほかのtestへ差し替えた指示のcacheを残さない。
        _code_prompts.cache_clear()
    assert answer_prompt_version(overrides) == original_version
