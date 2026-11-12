"""新規の自動下書きを確認済み条件にせず、明示 session/回答/認可を保つ。"""

from __future__ import annotations

from typing import Any, cast

import pytest
from test_mcp import (  # noqa: F401
    _call,
    _FakeNl2SqlService,
    _run,
    _structured,
    _token,
    _Users,
    fake_service,
    users,
)

from app.features.nl2sql import ontology_router
from app.features.nl2sql.models import (
    AllowedObjects,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.ontology_models import ClarificationMode
from app.features.nl2sql.ontology_router import QuerySessionApiCreate, RequirementApplyRequest
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.store import MemoryNl2SqlStore


@pytest.mark.parametrize("mode", ["assume", "skip"])
@pytest.mark.parametrize("use_glossary", [True, False])
def test_fresh_automatic_request_keeps_original_question_and_scope(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, mode: str, use_glossary: bool
) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    user = people.create(
        "synthetic.fresh", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    runtime = cast(Any, ontology_router.ontology_runtime)
    question = "項目 A、B、C、D を表示してください。"
    ready = runtime.create_session(
        QuerySessionApiCreate(
            question=question, profile_id="default", clarification_mode=ClarificationMode.GUIDED
        ),
        actor_user_uuid=user.user_uuid,
        profile_confirmed=True,
    )
    assert ready.requirement_state.can_apply is True
    ready.session.requirement.sql_skeleton = "SELECT VALUE_A, VALUE_B FROM APP.EMPLOYEE"
    assert ready.session.requirement.sql_skeleton.count("VALUE_") == 2
    runtime.created.clear()

    def must_not_plan(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fresh assume/skip must not create an authoritative two-column draft")

    monkeypatch.setattr(runtime, "create_session", must_not_plan)
    monkeypatch.setattr(runtime, "apply_requirement", must_not_plan)
    if mode == "assume":
        stub.profiles[0] = stub.profiles[0].model_copy(update={"clarification_policy": "assume"})
    args: dict[str, Any] = {
        "question": question,
        "profile_id": "default",
        "use_glossary": use_glossary,
    }
    if mode == "skip":
        args["skip_clarification"] = True
    error, result = _structured(_run(_call("nl2sql_query", args), token=_token(user.user_uuid)))
    assert not error and result["requirement"]["status"] == "skipped"
    job = stub.started[-1][0]
    assert job.question == job.generation_question == question
    assert job.sql_requirement is None and job.requirement_session_id is None
    assert job.allowed_objects == AllowedObjects() and job.use_glossary is use_glossary
    # 原質問と全許可 schema が生成に渡る。意図の推測で列・表を取り除かない。
    service = Nl2SqlService(MemoryNl2SqlStore())
    profile = Nl2SqlProfile(
        id="synthetic", name="synthetic", allowed_tables=["APP.EMPLOYEE", "APP.SOURCE_B"]
    )
    catalog = SchemaCatalog(
        refreshed_at="2026-01-01T00:00:00Z",
        tables=[
            SchemaTable(
                owner="APP",
                table_name=name,
                logical_name=name,
                columns=[
                    SchemaColumn(column_name=col, logical_name=col, data_type="NUMBER")
                    for col in ["VALUE_A", "VALUE_B", "VALUE_C", "VALUE_D"]
                ],
            )
            for name in ["EMPLOYEE", "SOURCE_B"]
        ],
    )
    context = service._enterprise_ai_schema_context(
        profile=profile, allowed=job.allowed_objects, catalog=catalog
    )
    assert (
        "APP.EMPLOYEE" in context
        and "APP.SOURCE_B" in context
        and "VALUE_C" in context
        and "VALUE_D" in context
    )
    assert "利用者と確かめた" not in context


@pytest.mark.parametrize("applied", [True, False])
def test_explicit_session_is_not_bypassed_under_assume(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, applied: bool
) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    user = people.create(
        "synthetic.explicit", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    runtime = cast(Any, ontology_router.ontology_runtime)
    data = runtime.create_session(
        QuerySessionApiCreate(
            question="合成の表示", profile_id="default", clarification_mode=ClarificationMode.GUIDED
        ),
        actor_user_uuid=user.user_uuid,
        profile_confirmed=True,
    )
    stub.profiles[0] = stub.profiles[0].model_copy(update={"clarification_policy": "assume"})
    args: dict[str, Any] = {
        "question": data.session.original_question,
        "profile_id": "default",
        "requirement_session_id": data.session.id,
    }
    if applied:
        runtime.apply_requirement(
            data.session.id,
            RequirementApplyRequest(base_version=data.session.current_intent_version),
        )
    else:
        answered = []

        def answer(session_id: str, payload: Any) -> Any:
            answered.append(payload)
            return runtime.get_session(session_id)

        monkeypatch.setattr(runtime, "answer_clarification", answer, raising=False)
        args["answer"] = {"item_id": "synthetic", "free_text": "利用者の指定"}
    error, _result = _structured(_run(_call("nl2sql_query", args), token=_token(user.user_uuid)))
    assert not error
    job = stub.started[-1][0]
    assert job.requirement_session_id == data.session.id and job.sql_requirement is not None
    if not applied:
        assert len(answered) == 1


@pytest.mark.parametrize("skip", [False, True])
def test_fresh_guard_does_not_bypass_cross_user_session(
    request: pytest.FixtureRequest, skip: bool
) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    owner = people.create(
        "synthetic.owner", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    other = people.create(
        "synthetic.other", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    runtime = cast(Any, ontology_router.ontology_runtime)
    data = runtime.create_session(
        QuerySessionApiCreate(
            question="合成の表示", profile_id="default", clarification_mode=ClarificationMode.GUIDED
        ),
        actor_user_uuid=owner.user_uuid,
        profile_confirmed=True,
    )
    stub.profiles[0] = stub.profiles[0].model_copy(update={"clarification_policy": "assume"})
    error, _result = _structured(
        _run(
            _call(
                "nl2sql_query",
                {
                    "question": data.session.original_question,
                    "profile_id": "default",
                    "requirement_session_id": data.session.id,
                    "skip_clarification": skip,
                },
            ),
            token=_token(other.user_uuid),
        )
    )
    assert error and not stub.started


def test_default_guided_request_still_applies_requirement(request: pytest.FixtureRequest) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    user = people.create(
        "synthetic.guided", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    error, _result = _structured(
        _run(
            _call("nl2sql_query", {"question": "合成の表示", "profile_id": "default"}),
            token=_token(user.user_uuid),
        )
    )
    assert not error and stub.started[-1][0].sql_requirement is not None


def test_assume_does_not_bypass_session_profile_mismatch(request: pytest.FixtureRequest) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    user = people.create(
        "synthetic.mismatch", permissions={"menu.query"}, allowed_profile_ids={"default", "sales"}
    )
    runtime = cast(Any, ontology_router.ontology_runtime)
    data = runtime.create_session(
        QuerySessionApiCreate(
            question="合成の表示", profile_id="default", clarification_mode=ClarificationMode.GUIDED
        ),
        actor_user_uuid=user.user_uuid,
        profile_confirmed=True,
    )
    stub.profiles[1] = stub.profiles[1].model_copy(update={"clarification_policy": "assume"})
    error, _result = _structured(
        _run(
            _call(
                "nl2sql_query",
                {
                    "question": data.session.original_question,
                    "profile_id": "sales",
                    "requirement_session_id": data.session.id,
                    "skip_clarification": True,
                },
            ),
            token=_token(user.user_uuid),
        )
    )
    assert error and not stub.started


def test_fresh_skip_does_not_bypass_token_profile_scope(request: pytest.FixtureRequest) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    user = people.create(
        "synthetic.scope", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    stub.profiles[1] = stub.profiles[1].model_copy(update={"clarification_policy": "assume"})
    error, _result = _structured(
        _run(
            _call(
                "nl2sql_query",
                {"question": "合成の表示", "profile_id": "sales", "skip_clarification": True},
            ),
            token=_token(user.user_uuid, profile_ids=["default"]),
        )
    )
    assert error and not stub.started
