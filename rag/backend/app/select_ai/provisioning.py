"""Select AI 資産の決定論プロビジョニング(非 network・非実行)。

参照実装 ``no.1-denpyo-toroku-kun`` の Select AI Agent 連携を踏襲し、
``credential → profile → tool(SQL) → agent → task → team`` を **決定論ハッシュ命名**で
冪等に drop+create する計画(``ProvisioningPlan``)を組み立てる。実 DB 実行は別層が担う。

設計上の不変条件:
- 同じ ``SelectAiProvisioningSpec`` からは **常に同じ命名・attributes・config_hash** が出る。
- 識別子は Oracle 30 byte 上限へ丸める(prefix + SHA1 先頭桁)。
- profile の model は ``oci_endpoint_id`` 経由で OCI Enterprise AI / 専用エンドポイントを指す。
- 機密(credential の secret)は **ここに埋め込まない**。credential は名前参照のみ。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 型のみ参照(循環 import 回避)
    from app.config import Settings

# 命名 prefix(N2S = NL2SQL)。
CREDENTIAL_PREFIX = "N2SCR_"
PROFILE_PREFIX = "N2SPR_"
TOOL_PREFIX = "N2STL_"
AGENT_PREFIX = "N2SAG_"
TASK_PREFIX = "N2STS_"
TEAM_PREFIX = "N2STM_"

MAX_IDENTIFIER_LENGTH = 30
DEFAULT_RESPONSE_LANGUAGE = "日本語"

# Select AI Agent の役割・指示(日本語 NL2SQL 前提)。
AGENT_ROLE = (
    "あなたは日本語の自然言語をデータベースの正しい SQL へ変換し、許可された表のみを"
    "参照して安全に回答する SQL アシスタントです。"
)
TASK_INSTRUCTION = (
    "ユーザの質問を解析し、profile の object_list に含まれる表のみを使って SQL を生成・実行し、"
    "結果を簡潔に説明してください。破壊的な文(DDL/DML)は生成しないでください。"
)


@dataclass(frozen=True)
class SelectAiAssetNames:
    """1 ドメイン分の Select AI 資産名(決定論)。"""

    credential_name: str
    profile_name: str
    tool_name: str
    agent_name: str
    task_name: str
    team_name: str


@dataclass(frozen=True)
class SelectAiProvisioningSpec:
    """Select AI プロビジョニングの入力(決定論の単一ソース)。

    ``config_fingerprint`` は「どのドメイン/設定の資産か」を一意に表す文字列。
    同じ spec からは常に同じ命名・attributes・config_hash が導出される。
    """

    config_fingerprint: str
    model: str
    region: str
    compartment_id: str
    object_list: tuple[tuple[str, str], ...]  # (owner, name) の昇順タプル
    embedding_model: str = ""
    endpoint_id: str = ""
    credential_name: str = ""
    max_tokens: int = 0
    api_format: str = ""
    enforce_object_list: bool = True
    use_annotations: bool = True
    use_comments: bool = True
    use_constraints: bool = True
    response_language: str = DEFAULT_RESPONSE_LANGUAGE
    process: str = "sequential"


@dataclass(frozen=True)
class ProvisioningStatement:
    """1 つの DB 文(PL/SQL ブロック)。binds は名前→値。"""

    kind: str  # "credential" / "profile" / "tool" / "agent" / "task" / "team"
    action: str  # "drop_create"
    sql: str
    binds: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProvisioningPlan:
    """冪等プロビジョニング計画(順序付き statement + drift 検知用 hash)。"""

    names: SelectAiAssetNames
    config_hash: str
    statements: tuple[ProvisioningStatement, ...]


def _sanitize_prefix(prefix: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", (prefix or "").upper())


def build_identifier(prefix: str, fingerprint: str) -> str:
    """prefix + fingerprint の SHA1 先頭桁から 30 byte 以内の決定論識別子を作る。"""
    normalized_prefix = _sanitize_prefix(prefix)
    digest = hashlib.sha1((fingerprint or "").encode("utf-8")).hexdigest().upper()
    # prefix を残しつつ 30 文字に収まる長さの digest を採用する。
    budget = max(MAX_IDENTIFIER_LENGTH - len(normalized_prefix), 8)
    identifier = f"{normalized_prefix}{digest[:budget]}"
    return identifier[:MAX_IDENTIFIER_LENGTH]


def build_asset_names(config_fingerprint: str, *, credential_name: str = "") -> SelectAiAssetNames:
    """1 ドメイン分の決定論資産名を生成する(credential は明示名があれば優先)。"""
    fp = config_fingerprint or ""
    return SelectAiAssetNames(
        credential_name=(
            credential_name.strip() or build_identifier(CREDENTIAL_PREFIX, f"cred:{fp}")
        ),
        profile_name=build_identifier(PROFILE_PREFIX, f"profile:{fp}"),
        tool_name=build_identifier(TOOL_PREFIX, f"tool:{fp}"),
        agent_name=build_identifier(AGENT_PREFIX, f"agent:{fp}"),
        task_name=build_identifier(TASK_PREFIX, f"task:{fp}"),
        team_name=build_identifier(TEAM_PREFIX, f"team:{fp}"),
    )


def _object_list_payload(object_list: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    return [{"owner": owner, "name": name} for owner, name in object_list]


def build_profile_attributes(spec: SelectAiProvisioningSpec, names: SelectAiAssetNames) -> str:
    """profile attributes を決定論 JSON 文字列で返す。

    キー順を固定し、同じ spec から常に同一文字列が出る(config_hash の安定化)。
    """
    attributes: dict[str, object] = {
        "provider": "oci",
        "credential_name": names.credential_name,
        "model": spec.model,
        "region": spec.region,
        "oci_compartment_id": spec.compartment_id,
        "object_list": _object_list_payload(spec.object_list),
        "enforce_object_list": bool(spec.enforce_object_list),
        "annotations": bool(spec.use_annotations),
        "comments": bool(spec.use_comments),
        "constraints": bool(spec.use_constraints),
    }
    if spec.embedding_model.strip():
        attributes["embedding_model"] = spec.embedding_model.strip()
    if spec.endpoint_id.strip():
        attributes["oci_endpoint_id"] = spec.endpoint_id.strip()
    if int(spec.max_tokens or 0) > 0:
        attributes["max_tokens"] = int(spec.max_tokens)
    api_format = spec.api_format.strip().upper()
    if api_format:
        attributes["oci_apiformat"] = api_format
    return json.dumps(attributes, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_tool_attributes(profile_name: str) -> str:
    """tool(SQL)attributes。"""
    return json.dumps(
        {"tool_type": "SQL", "tool_params": {"profile_name": profile_name}},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def build_agent_attributes(profile_name: str, *, role: str = AGENT_ROLE) -> str:
    """agent attributes(profile + role)。"""
    return json.dumps(
        {"profile_name": profile_name, "role": role},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def build_task_attributes(
    tool_name: str, *, response_language: str = DEFAULT_RESPONSE_LANGUAGE
) -> str:
    """task attributes(instruction + tools + human tool 無効)。"""
    instruction = f"{TASK_INSTRUCTION} 説明は必ず{response_language}で記述してください。"
    return json.dumps(
        {"instruction": instruction, "tools": [tool_name], "enable_human_tool": False},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def build_team_attributes(agent_name: str, task_name: str, *, process: str = "sequential") -> str:
    """team attributes(agents + process)。"""
    return json.dumps(
        {"agents": [{"name": agent_name, "task": task_name}], "process": process},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def config_hash(spec: SelectAiProvisioningSpec, names: SelectAiAssetNames) -> str:
    """drift 検知用の決定論 hash(命名 + 全 attributes の正準 JSON の SHA256)。"""
    payload = {
        "names": {
            "credential": names.credential_name,
            "profile": names.profile_name,
            "tool": names.tool_name,
            "agent": names.agent_name,
            "task": names.task_name,
            "team": names.team_name,
        },
        "profile": build_profile_attributes(spec, names),
        "tool": build_tool_attributes(names.profile_name),
        "agent": build_agent_attributes(names.profile_name),
        "task": build_task_attributes(names.tool_name, response_language=spec.response_language),
        "team": build_team_attributes(names.agent_name, names.task_name, process=spec.process),
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# 各資産の drop+create PL/SQL テンプレート(bind で名前・attributes を渡す)。
_PROFILE_SQL = (
    "BEGIN\n"
    "  BEGIN DBMS_CLOUD_AI.DROP_PROFILE(profile_name => :name); "
    "EXCEPTION WHEN OTHERS THEN NULL; END;\n"
    "  DBMS_CLOUD_AI.CREATE_PROFILE(profile_name => :name, attributes => :attributes);\n"
    "END;"
)
_TOOL_SQL = (
    "BEGIN\n"
    "  BEGIN DBMS_CLOUD_AI_AGENT.DROP_TOOL(tool_name => :name); "
    "EXCEPTION WHEN OTHERS THEN NULL; END;\n"
    "  DBMS_CLOUD_AI_AGENT.CREATE_TOOL(tool_name => :name, attributes => :attributes);\n"
    "END;"
)
_AGENT_SQL = (
    "BEGIN\n"
    "  BEGIN DBMS_CLOUD_AI_AGENT.DROP_AGENT(agent_name => :name); "
    "EXCEPTION WHEN OTHERS THEN NULL; END;\n"
    "  DBMS_CLOUD_AI_AGENT.CREATE_AGENT(agent_name => :name, attributes => :attributes);\n"
    "END;"
)
_TASK_SQL = (
    "BEGIN\n"
    "  BEGIN DBMS_CLOUD_AI_AGENT.DROP_TASK(task_name => :name); "
    "EXCEPTION WHEN OTHERS THEN NULL; END;\n"
    "  DBMS_CLOUD_AI_AGENT.CREATE_TASK(task_name => :name, attributes => :attributes);\n"
    "END;"
)
_TEAM_SQL = (
    "BEGIN\n"
    "  BEGIN DBMS_CLOUD_AI_AGENT.DROP_TEAM(team_name => :name); "
    "EXCEPTION WHEN OTHERS THEN NULL; END;\n"
    "  DBMS_CLOUD_AI_AGENT.CREATE_TEAM(team_name => :name, attributes => :attributes);\n"
    "END;"
)


def build_provisioning_plan(spec: SelectAiProvisioningSpec) -> ProvisioningPlan:
    """spec から冪等プロビジョニング計画を組み立てる(profile→tool→agent→task→team)。

    credential は名前参照のみ(secret は埋め込まない)。実行は別層が statements を順に処理する。
    """
    names = build_asset_names(spec.config_fingerprint, credential_name=spec.credential_name)
    statements = (
        ProvisioningStatement(
            kind="profile",
            action="drop_create",
            sql=_PROFILE_SQL,
            binds={"name": names.profile_name, "attributes": build_profile_attributes(spec, names)},
        ),
        ProvisioningStatement(
            kind="tool",
            action="drop_create",
            sql=_TOOL_SQL,
            binds={
                "name": names.tool_name,
                "attributes": build_tool_attributes(names.profile_name),
            },
        ),
        ProvisioningStatement(
            kind="agent",
            action="drop_create",
            sql=_AGENT_SQL,
            binds={
                "name": names.agent_name,
                "attributes": build_agent_attributes(names.profile_name),
            },
        ),
        ProvisioningStatement(
            kind="task",
            action="drop_create",
            sql=_TASK_SQL,
            binds={
                "name": names.task_name,
                "attributes": build_task_attributes(
                    names.tool_name, response_language=spec.response_language
                ),
            },
        ),
        ProvisioningStatement(
            kind="team",
            action="drop_create",
            sql=_TEAM_SQL,
            binds={
                "name": names.team_name,
                "attributes": build_team_attributes(
                    names.agent_name, names.task_name, process=spec.process
                ),
            },
        ),
    )
    return ProvisioningPlan(
        names=names, config_hash=config_hash(spec, names), statements=statements
    )


def provisioning_spec_from_settings(
    settings: Settings,
    *,
    config_fingerprint: str,
    object_list: tuple[tuple[str, str], ...],
) -> SelectAiProvisioningSpec:
    """Settings と対象テーブル群から ``SelectAiProvisioningSpec`` を組み立てる。

    model/region/compartment/endpoint/embedding は Select AI 用設定を優先し、空欄は
    汎用 OCI 設定へフォールバックする。object_list は (owner, name) を昇順で正規化する。
    """
    region = (
        getattr(settings, "select_ai_region", "") or getattr(settings, "oci_region", "")
    ).strip()
    compartment_id = (
        getattr(settings, "select_ai_compartment_id", "")
        or getattr(settings, "oci_compartment_id", "")
    ).strip()
    normalized_objects = tuple(
        sorted({(str(owner).upper(), str(name).upper()) for owner, name in object_list})
    )
    return SelectAiProvisioningSpec(
        config_fingerprint=config_fingerprint,
        model=str(getattr(settings, "select_ai_model", "") or "").strip(),
        region=region,
        compartment_id=compartment_id,
        object_list=normalized_objects,
        embedding_model=str(getattr(settings, "select_ai_embedding_model", "") or "").strip(),
        endpoint_id=str(getattr(settings, "select_ai_oci_endpoint_id", "") or "").strip(),
        credential_name=str(getattr(settings, "select_ai_credential_name", "") or "").strip(),
        max_tokens=int(getattr(settings, "select_ai_max_tokens", 0) or 0),
        api_format=str(getattr(settings, "select_ai_api_format", "") or "").strip(),
        response_language=str(
            getattr(settings, "select_ai_response_language", DEFAULT_RESPONSE_LANGUAGE)
            or DEFAULT_RESPONSE_LANGUAGE
        ),
    )
