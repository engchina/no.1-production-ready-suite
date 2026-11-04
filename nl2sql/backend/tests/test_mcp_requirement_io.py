"""MCPの既存「回答前の確認」親工程の実入出力。SQL/DB/LLMはfakeだけを使う。"""

import json
from contextlib import contextmanager
from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import configure_tracing, observed_span, shutdown_tracing

from app.features.mcp import tools as module
from app.features.nl2sql import ontology_router
from app.features.nl2sql.ontology_models import RequirementPhase, RequirementState
from app.features.nl2sql.sql_requirement import RequirementAiUnavailableError
from tests.test_mcp import (
    _call,
    _FakeNl2SqlService,
    _run,
    _structured,
    _token,
    _Users,
    fake_service,  # noqa: F401 - fixture
    users,  # noqa: F401 - fixtures
)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("outcome", ["applied", "unanswerable", "not_configured", "error"])
def test_requirement_parent_records_decision_before_job_execution(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    enabled: bool,
    outcome: str,
) -> None:
    user_store: _Users = request.getfixturevalue("users")
    service: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    user = user_store.create(
        "synthetic.io", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    runtime = cast(Any, ontology_router.ontology_runtime)
    if outcome in {"error", "not_configured"}:

        def broken(*args: Any, **kwargs: Any) -> Any:
            if outcome == "error":
                raise RuntimeError("private-error-body")
            raise RequirementAiUnavailableError(
                module.REQUIREMENT_AI_NOT_CONFIGURED_CODE, "synthetic"
            )

        monkeypatch.setattr(runtime, "create_session", broken)
    elif outcome == "unanswerable":
        original = runtime._data

        def data(*args: Any) -> Any:
            result = original(*args)
            return result.model_copy(
                update={
                    "requirement_state": RequirementState(
                        status=RequirementPhase.UNANSWERABLE,
                        select_from_clear=False,
                        can_apply=False,
                    )
                }
            )

        monkeypatch.setattr(runtime, "_data", data)
    monkeypatch.setattr(runtime, "cancel_session", lambda *args: None, raising=False)
    records: list[Any] = []
    original_span = observed_span

    @contextmanager
    def capture(*args: Any, **kwargs: Any) -> Any:
        with original_span(*args, **kwargs) as span:
            try:
                yield span
            finally:
                records.append(span.display)

    monkeypatch.setattr(module, "observed_span", capture)
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        with call_content_policy(lambda: enabled):
            is_error, result = _structured(
                _run(
                    _call(
                        "nl2sql_query",
                        {"question": "合成の質問", "profile_id": "default", "row_limit": 7},
                    ),
                    token=_token(user.user_uuid),
                )
            )
            assert is_error is (outcome == "error")
        assert len(records) == 1
        call = records[0].details.call
        assert call is not None
        assert call.capture_enabled is enabled
        assert "private-error-body" not in call.model_dump_json()
        if not enabled:
            assert call.input.text == call.output.text == ""
            return
        saved_input = json.loads(call.input.text)
        assert saved_input["question"] == "合成の質問"
        assert saved_input["profile_id"] == "default"
        output = json.loads(call.output.text)
        if outcome == "error":
            assert output == {"returned": False, "error_type": "RuntimeError"}
        elif outcome == "unanswerable":
            assert output["next_step"] == "return_confirmation_result"
            assert output["result"]["status"] == result["status"] == "unanswerable"
            assert not service.started
        else:
            assert output["next_step"] == "create_generation_job"
            assert "generated_sql" not in output
            assert output["job_request"]["row_limit"] == 7
            assert output["requirement"]["status"] == (
                "skipped" if outcome == "not_configured" else "applied"
            )
    finally:
        shutdown_tracing()
