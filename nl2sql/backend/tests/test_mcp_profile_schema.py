"""schema の MCP は照会と同じ actor・claim 範囲で毎ページ認可する。"""

from __future__ import annotations

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

from app.features.mcp.tools import MCP_OUTPUT_SCHEMA_VERSION
from app.features.nl2sql.profile_schema import ProfileSchemaInput, ProfileSchemaPage


def test_authorized_schema_is_read_only_and_uses_actual_profile(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    seen: list[ProfileSchemaInput] = []

    def read_schema(arguments: ProfileSchemaInput) -> ProfileSchemaPage:
        seen.append(arguments)
        return ProfileSchemaPage(profile_id=arguments.profile_id)

    monkeypatch.setattr(stub, "get_profile_schema", read_schema, raising=False)
    actor = people.create(
        "schema.reader", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    error, result = _structured(
        _run(
            _call("nl2sql_get_profile_schema", {"profile_id": "default"}),
            token=_token(actor.user_uuid, profile_ids=["default"]),
        )
    )
    assert not error and result["schema_version"] == MCP_OUTPUT_SCHEMA_VERSION
    assert result["source"] == "cached" and result["status"] == "unavailable"
    assert seen[0].profile_id == "default" and not stub.started


@pytest.mark.parametrize("token_scope,profile", [(["sales"], "default"), (["default"], "sales")])
def test_actor_and_token_scope_intersect_before_schema_read(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    token_scope: list[str],
    profile: str,
) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    monkeypatch.setattr(
        stub,
        "get_profile_schema",
        lambda *a: pytest.fail("権限外の schema を読まない"),
        raising=False,
    )
    actor = people.create(
        "schema.scoped", permissions={"menu.query"}, allowed_profile_ids={"default"}
    )
    error, result = _structured(
        _run(
            _call(
                "nl2sql_get_profile_schema", {"profile_id": profile, "cursor": "opaque-old-cursor"}
            ),
            token=_token(actor.user_uuid, profile_ids=token_scope),
        )
    )
    assert error and result["status"] == 403


def test_read_profile_permission_alone_is_insufficient(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    people: _Users = request.getfixturevalue("users")
    stub: _FakeNl2SqlService = request.getfixturevalue("fake_service")
    monkeypatch.setattr(
        stub, "get_profile_schema", lambda *a: pytest.fail("照会権限なしで読まない"), raising=False
    )
    actor = people.create(
        "schema.profile-only", permissions={"nl2sql.profiles.read"}, allowed_profile_ids={"default"}
    )
    error, result = _structured(
        _run(
            _call("nl2sql_get_profile_schema", {"profile_id": "default"}),
            token=_token(actor.user_uuid),
        )
    )
    assert error and result["error_code"] == "MCP_TOOL_FORBIDDEN"


def test_new_tool_descriptor_is_readonly_and_has_no_arbitrary_sql() -> None:
    from typing import cast

    from fastapi import Request

    from app.features.mcp.tools import build_mcp_server

    tool = build_mcp_server(cast(Request, None)).tools["nl2sql_get_profile_schema"].descriptor()
    assert tool["annotations"]["readOnlyHint"] is True
    assert "sql" not in tool["inputSchema"]["properties"]
