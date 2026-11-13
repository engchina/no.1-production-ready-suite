"""空根拠の固定文が公開済み全体設定と当該プロファイルだけから解決する。"""

from datetime import UTC, datetime

import pytest
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.service import GenerationService
from rag_engine.models.contracts import AnswerResult, GenerationRequest
from rag_parser_core.language import builtin_pack, current_pack, resolve

from app.config import get_settings
from app.rag.language_pack_releases import bind_release
from app.rag.profile_language import profile_language_scope
from app.rag.search_answer_profile_config import (
    SearchAnswerProfileConfig,
    resolve_search_answer_profile_settings,
)
from app.schemas.language_pack_release import LanguagePackRelease


@pytest.mark.parametrize(
    ("overrides", "summary", "guidance"),
    [
        ({}, "全体の固定文。", "全体の案内。"),
        (
            {"insufficient_evidence_summary": "個別の固定文。"},
            "個別の固定文。",
            "全体の案内。",
        ),
        (
            {"empty_search_guidance": "個別の案内。"},
            "全体の固定文。",
            "個別の案内。",
        ),
        (
            {
                "insufficient_evidence_summary": "個別の固定文。",
                "empty_search_guidance": "個別の案内。",
            },
            "個別の固定文。",
            "個別の案内。",
        ),
    ],
    ids=["inherit", "override-summary-only", "override-guidance-only", "override-both"],
)
def test_empty_evidence_uses_effective_reply_without_answer_model(
    overrides: dict[str, str], summary: str, guidance: str
) -> None:
    published = resolve(
        builtin_pack(),
        {
            "answer_labels": {
                "insufficient_evidence_summary": "全体の固定文。",
                "empty_search_guidance": "全体の案内。",
            }
        },
    )
    published_release = LanguagePackRelease(
        id="synthetic-fixed-response-release",
        version=1,
        effective=published.to_dict(),
        hash=published.hash,
        ingest_hash=published.ingest_hash,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    profile = SearchAnswerProfileConfig(language_pack={"answer_labels": overrides})
    settings, _ = resolve_search_answer_profile_settings(get_settings(), profile)
    previous_hash = current_pack().hash

    class UnusedGenerator:
        def generate(self, _request: GenerationRequest) -> AnswerResult:
            pytest.fail("空根拠の固定回答で回答モデルを呼んだ")

    with bind_release(published_release), profile_language_scope(settings):
        assert answer_labels().insufficient_evidence_summary == summary
        assert answer_labels().empty_search_guidance == guidance
        result = GenerationService(UnusedGenerator()).generate(
            GenerationRequest(question="合成の質問", evidence=())
        )
        assert result.text == summary
        assert result.needs_human_review
    assert current_pack().hash == previous_hash
    restored, _ = resolve_search_answer_profile_settings(
        get_settings(), SearchAnswerProfileConfig(language_pack={})
    )
    with bind_release(published_release), profile_language_scope(restored):
        assert answer_labels().insufficient_evidence_summary == "全体の固定文。"
        assert answer_labels().empty_search_guidance == "全体の案内。"
