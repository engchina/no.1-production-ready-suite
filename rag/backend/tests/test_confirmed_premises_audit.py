"""利用者が確定した前提だけを、正式入口から同次監査へ渡す。実モデル呼出0。"""

from pathlib import Path

import pytest
from rag_engine.config import LLM_PURPOSE_AUDIT, settings_for_llm_purpose

from app.config import Settings
from app.rag.answer_engine import (
    AnswerScope,
    _confirmed_validation_premises,
    build_engine_settings,
)
from app.rag.history_question import HistoryConfirmation
from app.rag.pipeline import RagPipeline, scope_with_history_confirmations
from app.schemas.search import SearchRequest

LABEL = "操作担当の種別"
VALUE = "担当A"
QUESTION = "登録方法は?"


def guide_settings(source: str, state: str) -> Settings:
    return Settings(
        rag_support_guide_enabled=True,
        rag_support_guide={
            "known_conditions": [{"label": LABEL, "value": VALUE, "source": source, "state": state}]
        },
        rag_query_strategy="simple_retrieval",
        rag_section_expansion_enabled=False,
    )


@pytest.mark.parametrize(
    "source,state,expected",
    [
        ("user", "known", True),
        ("question", "known", True),
        ("assumption", "known", False),
        ("user", "unknown", False),
    ],
)
def test_settings_keep_confirmed_sources_and_preserve_them_for_model_purpose(
    source: str, state: str, expected: bool, tmp_path: Path
) -> None:
    settings = build_engine_settings(guide_settings(source, state), output_dir=tmp_path)
    assert settings.confirmed_premises == (((LABEL, VALUE),) if expected else ())
    assert settings.confirmed_premise_sources == (((LABEL, VALUE, source),) if expected else ())
    assert (
        settings_for_llm_purpose(settings, LLM_PURPOSE_AUDIT).confirmed_premise_sources
        == settings.confirmed_premise_sources
    )
    assert (
        settings_for_llm_purpose(settings, LLM_PURPOSE_AUDIT).confirmed_premises
        == settings.confirmed_premises
    )


def test_scope_premises_are_explicit_and_do_not_parse_assumptions(tmp_path: Path) -> None:
    plain = build_engine_settings(
        Settings(), output_dir=tmp_path, scope=AnswerScope(context=f"仮定: {LABEL}={VALUE}")
    )
    assert plain.confirmed_premises == ()
    explicit = build_engine_settings(
        Settings(),
        output_dir=tmp_path,
        scope=AnswerScope(context="利用者が選んだ値", confirmed_premises=((LABEL, VALUE),)),
    )
    assert explicit.confirmed_premises == ((LABEL, VALUE),)
    assert explicit.confirmed_premise_sources == ((LABEL, VALUE, "user"),)


@pytest.mark.parametrize(
    "source,accepted",
    [("user", True), ("question", True), ("assumption", False), ("unknown", False)],
)
def test_scope_source_is_preserved_without_promoting_untrusted_source(
    source: str, accepted: bool, tmp_path: Path
) -> None:
    scope = AnswerScope(
        context="表示用",
        confirmed_premises=((LABEL, VALUE),),
        confirmed_premise_sources=((LABEL, VALUE, source),),
    )
    settings = build_engine_settings(Settings(), output_dir=tmp_path, scope=scope)
    assert settings.confirmed_premise_sources == (((LABEL, VALUE, source),) if accepted else ())
    assert _confirmed_validation_premises(Settings(), scope) == (
        [{"question": LABEL, "answer": VALUE}] if accepted else []
    )


def test_history_preserves_question_source_and_marks_actual_confirmation_user(
    tmp_path: Path,
) -> None:
    scope = scope_with_history_confirmations(
        AnswerScope(
            context="現質問",
            confirmed_premises=((LABEL, VALUE),),
            confirmed_premise_sources=((LABEL, VALUE, "question"),),
        ),
        [HistoryConfirmation(id="saved-1", question="確認項目B", answer="選択B")],
    )
    settings = build_engine_settings(Settings(), output_dir=tmp_path, scope=scope)
    assert settings.confirmed_premise_sources == (
        ("確認項目B", "選択B", "user"),
        (LABEL, VALUE, "question"),
    )


async def test_pipeline_keeps_recognized_pairs_and_explicit_confirmation_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StopBeforeSearch(Exception):
        pass

    def stop() -> str:
        raise StopBeforeSearch()

    monkeypatch.setattr("app.rag.pipeline.new_trace_id", stop)
    request = SearchRequest(query=QUESTION)
    request._rule_selection_scope = {
        "context": "質問で特定した選択",
        "confirmed_premises": ((LABEL, VALUE), ("項目B", "値B")),
        "confirmed_premise_sources": ((LABEL, VALUE, "question"), ("項目B", "値B", "question")),
    }
    pipeline = RagPipeline(
        settings=Settings(),
        scope=AnswerScope(context="確認済み", confirmed_premises=((LABEL, VALUE),)),
    )
    with pytest.raises(StopBeforeSearch):
        await pipeline.run(request)
    settings = build_engine_settings(Settings(), output_dir=tmp_path, scope=pipeline._scope)
    assert settings.confirmed_premises == ((LABEL, VALUE), ("項目B", "値B"))
    assert settings.confirmed_premise_sources == (
        (LABEL, VALUE, "user"),
        ("項目B", "値B", "question"),
    )
