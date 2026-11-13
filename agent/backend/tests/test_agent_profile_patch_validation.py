"""PATCH の省略可能な型を、保存状態へ未検証で代入しない。"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from security_support import client

from app.features.agent import router
from app.features.agent.data_scope import AgentDataScope
from app.features.agent.output_fields import AgentOutputField
from app.features.agent.report_contract import ReportOutputContract
from app.features.agent.runtime import AgentProfile, AgentProfilePatch, AgentRuntimeRepository
from app.features.agent.skills import PinnedSkill

NON_NULL_FIELDS = [
    name
    for name in AgentProfilePatch.model_fields
    if type(None) not in get_args(AgentProfile.model_fields[name].annotation)
]


def profile(repository: AgentRuntimeRepository) -> AgentProfile:
    return next(item for item in repository.list_agents() if item.id == "patch-synthetic")


@pytest.mark.parametrize("field", NON_NULL_FIELDS)
@pytest.mark.parametrize("persistent", [False, True])
def test_null_patch_is_atomic_and_saved_state_still_loads(
    field: str, persistent: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runtime.json" if persistent else None
    repository = AgentRuntimeRepository(path)
    repository.create_agent(AgentProfile(id="patch-synthetic", name="保存前の名前"))
    before = profile(repository).model_dump(mode="json")
    before_file = path.read_bytes() if path else None
    persisted: list[bool] = []
    monkeypatch.setattr(repository, "_persist_locked", lambda: persisted.append(True))
    with pytest.raises(ValueError):
        repository.patch_agent(
            "patch-synthetic",
            AgentProfilePatch.model_validate({"name": "変更後の名前", field: None}),
        )
    assert profile(repository).model_dump(mode="json") == before
    assert persisted == []
    # メモリの現在値でも、保存済み JSON でも最終モデルとして再読込できる。
    AgentProfile.model_validate(profile(repository).model_dump())
    if path:
        assert path.read_bytes() == before_file
        assert profile(AgentRuntimeRepository(path)).model_dump(mode="json") == before


@pytest.mark.parametrize("field", NON_NULL_FIELDS)
def test_API_null_patch_cannot_partially_modify_agent(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = AgentRuntimeRepository()
    repository.create_agent(AgentProfile(id="patch-synthetic", name="保存前の名前"))
    monkeypatch.setattr(router, "runtime_repository", repository)
    before = profile(repository).model_dump(mode="json")
    response = client.patch(
        "/api/agents/patch-synthetic", json={"name": "変更後の名前", field: None}
    )
    assert response.status_code in {400, 422}, response.text
    assert profile(repository).model_dump(mode="json") == before


def test_valid_patch_keeps_typed_fields_and_internal_excluded_values() -> None:
    repository = AgentRuntimeRepository()
    repository.create_agent(
        AgentProfile(
            id="patch-synthetic",
            name="保存前の名前",
            output_contract=ReportOutputContract(),
            data_scopes={"nl2sql": AgentDataScope(profile_ids=["profile-a"])},
            pinned_skills=[PinnedSkill(id="pinned-a", name="固定スキル")],
            caller_planning_allowed=False,
        )
    )
    previous = profile(repository)
    changed = repository.patch_agent(
        "patch-synthetic",
        AgentProfilePatch(
            name="変更後の名前",
            output_contract=None,
            output_fields=[AgentOutputField(key="value", label="値")],
        ),
    )
    assert changed.output_contract is None
    assert isinstance(changed.output_fields[0], AgentOutputField)
    assert isinstance(changed.data_scopes["nl2sql"], AgentDataScope)
    assert changed.data_scopes == previous.data_scopes
    assert changed.pinned_skills == previous.pinned_skills
    assert changed.caller_planning_allowed is False
    assert changed.versions == previous.versions and changed.created_at == previous.created_at
    assert changed.draft_updated_at == changed.updated_at


def test_combination_failure_preserves_all_previous_fields() -> None:
    repository = AgentRuntimeRepository()
    repository.create_agent(
        AgentProfile(id="patch-synthetic", name="保存前", output_contract=ReportOutputContract())
    )
    before = profile(repository).model_dump(mode="json")
    with pytest.raises(ValueError, match="同時"):
        repository.patch_agent(
            "patch-synthetic",
            AgentProfilePatch(
                name="変更後", output_fields=[AgentOutputField(key="value", label="値")]
            ),
        )
    assert profile(repository).model_dump(mode="json") == before
