"""Select AI プロビジョニング(NL2SQL 中核)。

Oracle Autonomous Database の Select AI / Select AI Agent 資産
(credential/profile/tool/agent/task/team)を **データソース/業務ドメイン単位**で
冪等にプロビジョニングするための決定論ロジックを提供する。

本パッケージは **非 network・非実行**(決定論)。実際の `DBMS_CLOUD_AI(_AGENT)` 呼び出しは
ingestion/clients 層が ``ProvisioningPlan`` の statement を順に実行する。命名・attributes・
config_hash(drift 検知)はここで一意に決め、CI では決定論的にテストする。
"""

from app.select_ai.provisioning import (
    AGENT_ROLE,
    DEFAULT_RESPONSE_LANGUAGE,
    MAX_IDENTIFIER_LENGTH,
    ProvisioningPlan,
    ProvisioningStatement,
    SelectAiAssetNames,
    SelectAiProvisioningSpec,
    build_agent_attributes,
    build_asset_names,
    build_identifier,
    build_profile_attributes,
    build_provisioning_plan,
    build_task_attributes,
    build_team_attributes,
    build_tool_attributes,
    config_hash,
    provisioning_spec_from_settings,
)

__all__ = [
    "AGENT_ROLE",
    "DEFAULT_RESPONSE_LANGUAGE",
    "MAX_IDENTIFIER_LENGTH",
    "ProvisioningPlan",
    "ProvisioningStatement",
    "SelectAiAssetNames",
    "SelectAiProvisioningSpec",
    "build_agent_attributes",
    "build_asset_names",
    "build_identifier",
    "build_profile_attributes",
    "build_provisioning_plan",
    "build_task_attributes",
    "build_team_attributes",
    "build_tool_attributes",
    "config_hash",
    "provisioning_spec_from_settings",
]
