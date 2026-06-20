"""Select AI 決定論プロビジョニングのテスト(非 network)。"""

import json

from app.config import Settings
from app.select_ai import (
    MAX_IDENTIFIER_LENGTH,
    SelectAiProvisioningSpec,
    build_asset_names,
    build_identifier,
    build_profile_attributes,
    build_provisioning_plan,
    provisioning_spec_from_settings,
)


def _spec(**overrides: object) -> SelectAiProvisioningSpec:
    base: dict[str, object] = {
        "config_fingerprint": "hr:tokyo",
        "model": "meta.llama",
        "region": "ap-osaka-1",
        "compartment_id": "ocid1.compartment.oc1..aaa",
        "object_list": (("ADMIN", "EMPLOYEE"), ("ADMIN", "DEPARTMENT")),
        "endpoint_id": "ocid1.endpoint.oc1..bbb",
    }
    base.update(overrides)
    return SelectAiProvisioningSpec(**base)  # type: ignore[arg-type]


def test_identifier_is_deterministic_and_within_oracle_limit() -> None:
    a = build_identifier("N2SPR_", "profile:hr:tokyo")
    b = build_identifier("N2SPR_", "profile:hr:tokyo")
    assert a == b
    assert a.startswith("N2SPR_")
    assert len(a) <= MAX_IDENTIFIER_LENGTH


def test_asset_names_are_stable_and_distinct() -> None:
    names = build_asset_names("hr:tokyo")
    assert names == build_asset_names("hr:tokyo")
    distinct = {
        names.profile_name,
        names.tool_name,
        names.agent_name,
        names.task_name,
        names.team_name,
        names.credential_name,
    }
    assert len(distinct) == 6


def test_explicit_credential_name_is_preferred() -> None:
    names = build_asset_names("hr:tokyo", credential_name="MY_CRED")
    assert names.credential_name == "MY_CRED"


def test_profile_attributes_include_endpoint_and_enforce() -> None:
    spec = _spec()
    names = build_asset_names(spec.config_fingerprint)
    attrs = json.loads(build_profile_attributes(spec, names))
    assert attrs["provider"] == "oci"
    assert attrs["enforce_object_list"] is True
    assert attrs["oci_endpoint_id"] == "ocid1.endpoint.oc1..bbb"
    assert attrs["object_list"] == [
        {"owner": "ADMIN", "name": "EMPLOYEE"},
        {"owner": "ADMIN", "name": "DEPARTMENT"},
    ]


def test_profile_attributes_omit_empty_optional_fields() -> None:
    spec = _spec(endpoint_id="", max_tokens=0, api_format="")
    names = build_asset_names(spec.config_fingerprint)
    attrs = json.loads(build_profile_attributes(spec, names))
    assert "oci_endpoint_id" not in attrs
    assert "max_tokens" not in attrs
    assert "oci_apiformat" not in attrs


def test_plan_is_ordered_and_deterministic() -> None:
    plan_a = build_provisioning_plan(_spec())
    plan_b = build_provisioning_plan(_spec())
    assert plan_a.config_hash == plan_b.config_hash
    assert tuple(s.kind for s in plan_a.statements) == (
        "profile",
        "tool",
        "agent",
        "task",
        "team",
    )
    # 各 statement は drop+create で名前/attributes を bind する。
    profile_stmt = plan_a.statements[0]
    assert "DBMS_CLOUD_AI.CREATE_PROFILE" in profile_stmt.sql
    assert profile_stmt.binds["name"] == plan_a.names.profile_name


def test_config_hash_changes_when_object_list_changes() -> None:
    base = build_provisioning_plan(_spec())
    changed = build_provisioning_plan(_spec(object_list=(("ADMIN", "EMPLOYEE"),)))
    assert base.config_hash != changed.config_hash


def test_config_hash_changes_when_endpoint_changes() -> None:
    base = build_provisioning_plan(_spec())
    changed = build_provisioning_plan(_spec(endpoint_id="ocid1.endpoint.oc1..zzz"))
    assert base.config_hash != changed.config_hash


def test_spec_from_settings_normalizes_object_list_and_reads_endpoint() -> None:
    settings = Settings(
        select_ai_model="meta.llama",
        select_ai_oci_endpoint_id="ocid1.endpoint.oc1..bbb",
        select_ai_region="ap-osaka-1",
    )
    spec = provisioning_spec_from_settings(
        settings,
        config_fingerprint="hr:tokyo",
        object_list=(("admin", "employee"), ("ADMIN", "EMPLOYEE"), ("admin", "department")),
    )
    # 重複は除去され (owner, name) 昇順・大文字に正規化される。
    assert spec.object_list == (("ADMIN", "DEPARTMENT"), ("ADMIN", "EMPLOYEE"))
    assert spec.endpoint_id == "ocid1.endpoint.oc1..bbb"
    assert spec.model == "meta.llama"
