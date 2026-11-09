"""最終回答も LLM 監査のスイッチに従い MCP と同じ照合を使う。"""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome, AnswerScope
from app.schemas.search import SearchRequest


def test_disabled_guide_and_unstructured_scope_do_not_create_confirmed_facts() -> None:
    from app.rag.answer_engine import _confirmed_validation_premises

    settings = Settings(
        rag_support_guide_enabled=False,
        rag_support_guide={
            "known_conditions": [
                {"label": "条件", "value": "値", "source": "user", "state": "known"},
            ]
        },
    )
    assert _confirmed_validation_premises(settings, AnswerScope(context="前提: 推測の文章")) == []


