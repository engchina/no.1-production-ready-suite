"""MCP の用語集選択が全ジョブ経路からモデル要求へ伝わる。"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
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

from app.features.mcp import tools as module
from app.features.nl2sql import ontology_router
from app.features.nl2sql.enterprise_ai_client import OciEnterpriseAiDirectClient
from app.features.nl2sql.models import (
    AllowedObjects,
    JobCreateRequest,
    Nl2SqlProfile,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)
from app.features.nl2sql.ontology_models import (
    ClarificationMode,
    RequirementPhase,
    RequirementState,
)
from app.features.nl2sql.ontology_router import QuerySessionApiCreate, RequirementApplyRequest
from app.features.nl2sql.service import Nl2SqlService
from app.features.nl2sql.sql_requirement import RequirementAiUnavailableError
from app.features.nl2sql.store import MemoryNl2SqlStore
from app.settings import EnterpriseAiConfiguredModel, Settings


@pytest.mark.parametrize("choice", [None, True, False])
@pytest.mark.parametrize("path", ["applied", "unconfigured", "assumed", "reused"])
def test_mcp_glossary_reaches_real_generation_payload(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    choice: bool | None,
    path: str,
) -> None:
    user_store: _Users = request.getfixturevalue("users")
    service_stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    user = user_store.create(
        "synthetic.glossary", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    runtime = cast(Any, ontology_router.ontology_runtime)
    arguments: dict[str, Any] = {"question": "合成項目を表示", "profile_id": "default"}
    if choice is not None:
        arguments["use_glossary"] = choice
    if path == "unconfigured":

        def unavailable(*args: Any, **kwargs: Any) -> Any:
            raise RequirementAiUnavailableError(
                module.REQUIREMENT_AI_NOT_CONFIGURED_CODE, "synthetic"
            )

        monkeypatch.setattr(runtime, "create_session", unavailable)
    elif path == "assumed":
        original = runtime._data

        def data(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
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
        service_stub.profiles[0] = service_stub.profiles[0].model_copy(
            update={"clarification_policy": "assume"}
        )
    elif path == "reused":
        created = runtime.create_session(
            QuerySessionApiCreate(
                question=arguments["question"],
                profile_id="default",
                clarification_mode=ClarificationMode.GUIDED,
            ),
            actor_user_uuid=user.user_uuid,
            profile_confirmed=True,
        )
        runtime.apply_requirement(
            created.session.id,
            RequirementApplyRequest(base_version=created.session.current_intent_version),
        )
        arguments["requirement_session_id"] = created.session.id
    is_error, _result = _structured(
        _run(_call("nl2sql_query", arguments), token=_token(user.user_uuid))
    )
    assert not is_error
    job_request = service_stub.started[-1][0]
    assert job_request.use_glossary is (choice is not False)
    assert JobCreateRequest(question="合成").use_glossary is False
    sent: list[dict[str, Any]] = []

    def handler(wire: httpx.Request) -> httpx.Response:
        sent.append(json.loads(wire.content))
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {"sql": "SELECT VALUE_A FROM APP.SOURCE_A", "explanation": "合成の説明"}
                )
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings = Settings(
        _env_file=None,
        oci_enterprise_ai_endpoint="https://synthetic.invalid/v1",
        oci_enterprise_ai_api_key="synthetic",
        oci_enterprise_ai_default_text_model="synthetic",
        oci_enterprise_ai_models=[EnterpriseAiConfiguredModel(model_id="synthetic")],
    )
    service = Nl2SqlService(store=MemoryNl2SqlStore())
    service._enterprise_ai_client = OciEnterpriseAiDirectClient(settings)
    profile = Nl2SqlProfile(
        id="synthetic",
        name="synthetic",
        allowed_tables=["APP.SOURCE_A"],
        glossary={"合成項目": "設定済みの用語定義_A"},
    )
    catalog = SchemaCatalog(
        refreshed_at="2026-01-01T00:00:00Z",
        tables=[
            SchemaTable(
                owner="APP",
                table_name="SOURCE_A",
                logical_name="A",
                columns=[SchemaColumn(column_name="VALUE_A", logical_name="A", data_type="NUMBER")],
            )
        ],
    )
    service._generate_enterprise_ai_direct_sql(
        question=job_request.question,
        profile=profile,
        allowed=AllowedObjects(table_names=["APP.SOURCE_A"]),
        row_limit=10,
        fallback_messages=[],
        meta={},
        learning_examples=[],
        catalog=catalog,
        use_glossary=job_request.use_glossary,
    )
    assert len(sent) == 1
    assert ("設定済みの用語定義_A" in sent[0]["input"][0]["content"]) is (choice is not False)
