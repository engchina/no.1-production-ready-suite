"""確認を通らなかったときのモデル（モデルのカスケード。#4581）の設定を、合成の設定で確かめる。

検索・回答プロファイルの `escalation_model_id` を登録モデルだけに限り（登録の無いモデルは明示の
失敗）、要求の単位で回答エンジンへ渡し、利用者がモデルを明示した実行と回答のモデルと同じモデル
では渡さない。
"""

from pathlib import Path
from typing import Any

import pytest
from rag_engine.config import ESCALATION_LLM_PROVIDER

from app.api.routes import search as search_route
from app.api.routes import search_answer_profiles as profile_route
from app.config import EnterpriseAiConfiguredModel, Settings
from app.main import app
from app.rag.answer_engine import build_engine_settings
from app.rag.search_answer_profile_config import (
    ProfileAnswerModelUnavailableError,
    SearchAnswerProfileConfig,
    resolve_search_answer_profile_settings,
)
from app.schemas.search import SearchRequest
from tests.support import AsgiTestClient

PRIMARY = "https://primary.example/openai/v1"
SECONDARY = "https://secondary.example/openai/v1"


def _settings(**overrides: Any) -> Settings:
    # 確認を通らなかったときのモデルは要求の単位の値だけ（構築・環境変数では決めない）。
    escalation = overrides.pop("rag_answer_escalation_model", None)
    settings = Settings(
        oci_enterprise_ai_endpoint=PRIMARY,
        oci_enterprise_ai_api_key="sk-primary",
        oci_enterprise_ai_secondary_endpoint=SECONDARY,
        oci_enterprise_ai_secondary_project_ocid="ocid1.project.secondary",
        oci_enterprise_ai_secondary_api_key="sk-secondary",
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(model_id="model-light", display_name="Light"),
            EnterpriseAiConfiguredModel(
                model_id="model-strong",
                display_name="Strong",
                connection_id="secondary",
                reasoning_effort="high",
                max_output_tokens=16_000,
                vision_enabled=True,
            ),
        ],
        oci_enterprise_ai_default_text_model="model-light",
        oci_enterprise_ai_default_vision_model="model-light",
        **overrides,
    )
    if escalation is not None:
        settings = settings.model_copy(update={"rag_answer_escalation_model": escalation})
    return settings


def test_there_is_no_global_escalation_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """全体の既定は持たない: 環境変数・構築の値では有効にならず、既存のプロファイルは変わらない。"""
    monkeypatch.setenv("RAG_ANSWER_ESCALATION_MODEL", "model-strong")
    assert Settings().rag_answer_escalation_model == ""
    assert Settings(rag_answer_escalation_model="model-strong").rag_answer_escalation_model == ""
    # 項目の無い保存済みのプロファイル（既存）はカスケードしない。
    from app.rag.search_answer_profile_config import parse_search_answer_profile_config

    legacy = parse_search_answer_profile_config({"version": 1, "knowledge_base_ids": ["kb-1"]})
    assert legacy.escalation_model_id is None
    effective, _ = resolve_search_answer_profile_settings(_settings(), legacy)
    assert effective.rag_answer_escalation_model == ""


def test_profile_escalation_model_is_request_scoped() -> None:
    original = _settings()
    effective, applied = resolve_search_answer_profile_settings(
        original, SearchAnswerProfileConfig(escalation_model_id=" model-strong ")
    )
    assert applied
    assert effective.rag_answer_escalation_model == "model-strong"
    # 全体の既定は持たない（プロファイルの設定が無ければ空）。
    assert original.rag_answer_escalation_model == ""
    empty, _ = resolve_search_answer_profile_settings(original, SearchAnswerProfileConfig())
    assert empty.rag_answer_escalation_model == ""


def test_unregistered_escalation_model_is_an_explicit_error() -> None:
    config = SearchAnswerProfileConfig(escalation_model_id="removed-model")
    with pytest.raises(ProfileAnswerModelUnavailableError, match="確認を通らなかったときのモデル"):
        resolve_search_answer_profile_settings(_settings(), config)
    # 回答を作らない検索（モデルを適用しない）では止めず、カスケードもしない。
    effective, _ = resolve_search_answer_profile_settings(
        _settings(), config, apply_answer_model=False
    )
    assert effective.rag_answer_escalation_model == ""


def test_saving_unregistered_escalation_model_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_route, "get_settings", _settings)
    response = AsgiTestClient(app).post(
        "/api/search-answer-profiles",
        json={
            "name": "回答設定 A",
            "description": "操作の質問に回答する",
            "config": {"escalation_model_id": "removed-model"},
        },
    )
    assert response.status_code == 422
    assert "確認を通らなかったときのモデル" in "".join(response.json()["error_messages"])


def test_engine_receives_the_escalation_connection(tmp_path: Path) -> None:
    settings = _settings(rag_answer_escalation_model="model-strong")
    engine = build_engine_settings(settings, output_dir=tmp_path)
    assert engine.escalation_llm == ESCALATION_LLM_PROVIDER
    provider = engine.llm_providers[ESCALATION_LLM_PROVIDER]
    assert provider.model == "model-strong"
    # そのモデルの接続・推論の量・出力の上限・画像の可否で呼ぶ（回答のモデルの接続へ切り替えない）。
    assert provider.base_url == SECONDARY
    assert provider.project_id == "ocid1.project.secondary"
    assert provider.reasoning_effort == "high"
    assert provider.supports_vision is True
    assert engine.llm_providers[engine.default_answer_llm].model == "model-light"


@pytest.mark.parametrize(
    "overrides",
    [
        {"rag_answer_escalation_model": ""},
        # 回答のモデルと同じモデルは使わない。
        {"rag_answer_escalation_model": "model-light"},
        # 登録の無いモデルは渡さない（設定の解決で明示の失敗にする）。
        {"rag_answer_escalation_model": "removed-model"},
    ],
    ids=["empty", "same-as-answer", "unregistered"],
)
def test_engine_has_no_cascade_without_a_usable_escalation_model(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    engine = build_engine_settings(_settings(**overrides), output_dir=tmp_path)
    assert engine.escalation_llm == ""
    assert ESCALATION_LLM_PROVIDER not in engine.llm_providers


@pytest.mark.usefixtures("system_access")
@pytest.mark.parametrize(
    ("explicit", "expected"),
    [(None, "model-strong"), ("model-light", "")],
    ids=["profile", "explicit-model"],
)
async def test_explicit_model_disables_the_cascade(
    monkeypatch: pytest.MonkeyPatch, explicit: str | None, expected: str
) -> None:
    """利用者がモデルを明示した実行（RAG 検索のモデルの選択・チャットのモデル）は使わない。"""
    from app.schemas.search_answer_profile import SearchAnswerProfileDetail
    from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

    class Oracle(FakeKnowledgeOracle):
        async def get_search_answer_profile(
            self, profile_id: str
        ) -> SearchAnswerProfileDetail | None:
            detail = await super().get_search_answer_profile(profile_id)
            if detail is not None:
                detail.config.escalation_model_id = "model-strong"
            return detail

    async def no_guides(_oracle: object, _profile: str) -> tuple[list[object], bool]:
        return [], False

    fake = Oracle()
    monkeypatch.setattr(search_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(search_route, "load_published_guides", no_guides)
    request = SearchRequest(query="操作方法", search_answer_profile_id="bv-1", model_id=explicit)
    _, effective, _, _ = await search_route._resolve_query_context(request, _settings())
    assert effective.rag_answer_escalation_model == expected


def test_cascade_record_survives_answer_conversion() -> None:
    """回答の診断（「処理の詳細」と実行履歴の詳細が読む）に、カスケードの記録を残す。"""
    from rag_engine.generation.answer_models import AnswerQuestionResult

    from app.rag.answer_engine import _outcome_from_result, _SearchState

    record = {
        "primary_model": "model-light",
        "escalation_model": "model-strong",
        "escalated": True,
        "reasons": ["citation_mismatch"],
        "stage": "answer.generate",
    }
    result = AnswerQuestionResult(
        answer="合成の回答",
        question_display="合成の質問",
        original_question="合成の質問",
        generation_trace={"model_cascade": record},
    )
    assert _outcome_from_result(result, _SearchState()).diagnostics["model_cascade"] == record
