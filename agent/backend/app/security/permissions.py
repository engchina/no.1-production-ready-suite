"""Agent の権限カタログと API の権限 manifest（#215）。

- `PERMISSION_CATALOG`: ロールに付けられる権限（メニュー権限と capability）。
- `permission_for_route(method, path)`: API（method × route template）→ 必要な権限の集合
  （いずれか 1 つを持てばよい）。**既定は拒否**で、登録外の API は `UNCLASSIFIED_PERMISSION`
  を返し、認可で 403 にする。公開 API とログインだけで使える API は None を返す。
- capability は従来の 5 ロール（viewer / operator / approver / auditor / admin）に対応する
  （`CAPABILITY_ROLES`）。Cookie のセッションの利用者は capability から従来のロールを作り、
  router の既存の判定（`require_*`・対象範囲の絞り込み）にそのまま流す。

manifest の読み取り API は「その API を使う画面のメニュー権限」で許可し、Run・監査などの実データは
router の `require_*`（capability から作ったロール）でも確認する。つまり画面の表示にはメニュー権限、
実データの閲覧・操作には capability が必要になる。capability は関連するメニューを暗黙に含む
（implies）ため、capability だけを付けたロールでも画面を開ける。

システム設定・ユーザーとロールの共通メニューは NL2SQL / RAG と同じコード・グループ名を使う
（3 製品共通の画面。#206）。権限管理は「Agent セキュリティ」の製品固有メニュー。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pr_system_settings.auth.dependencies import UNCLASSIFIED_PERMISSION as UNCLASSIFIED_PERMISSION


@dataclass(frozen=True, slots=True)
class PermissionDefinition:
    code: str
    group: str
    label: str
    description: str
    implies: tuple[str, ...] = ()


def _permission(
    code: str,
    group: str,
    label: str,
    description: str,
    *,
    implies: tuple[str, ...] = (),
) -> PermissionDefinition:
    return PermissionDefinition(code, group, label, description, implies)


def _menu_permission(code: str, group: str, label: str) -> PermissionDefinition:
    return _permission(code, group, label, f"{label}を表示し、関連操作を利用できます。")


# ---- メニュー権限（agent/frontend の nav-config.ts と同じ並び） ----

MENU_DASHBOARD = "menu.dashboard"
MENU_AGENTS = "menu.agents"
MENU_SKILLS = "menu.skills"
MENU_RUNTIMES = "menu.runtimes"
MENU_RUNS = "menu.runs"
MENU_APPROVALS = "menu.approvals"
MENU_AUDIT = "menu.audit"
MENU_PLUGIN_MARKETPLACES = "menu.plugin_marketplaces"
MENU_SETTINGS_CONNECTION = "menu.settings_connection"
MENU_SETTINGS_EXTERNAL_RAG = "menu.settings_external_rag"
MENU_SETTINGS_EXTERNAL_NL2SQL = "menu.settings_external_nl2sql"
MENU_SETTINGS_EXTERNAL_MCP = "menu.settings_external_mcp"
MENU_SETTINGS_RUNTIME_SNAPSHOT = "menu.settings_runtime_snapshot"
MENU_SETTINGS_OCI = "menu.settings_oci"
MENU_SETTINGS_UPLOAD_STORAGE = "menu.settings_upload_storage"
MENU_SETTINGS_MODEL = "menu.settings_model"
MENU_SETTINGS_DATABASE = "menu.settings_database"
MENU_SETTINGS_APPEARANCE = "menu.settings_appearance"
MENU_SECURITY_USERS = "menu.security_users"
MENU_SECURITY_ROLES = "menu.security_roles"
MENU_SECURITY_PERMISSIONS = "menu.security_permissions"

# ---- capability（従来の 5 ロールに対応） ----

RUNS_VIEW = "agent.runs.view"
RUNS_OPERATE = "agent.runs.operate"
APPROVALS_DECIDE = "agent.approvals.decide"
AUDIT_VIEW = "agent.audit.view"
ADMIN = "agent.admin"

# capability → 従来のロール（router の `require_*` と `ActorPolicy.roles` が使う名前）。
CAPABILITY_ROLES: dict[str, str] = {
    RUNS_VIEW: "viewer",
    RUNS_OPERATE: "operator",
    APPROVALS_DECIDE: "approver",
    AUDIT_VIEW: "auditor",
    ADMIN: "admin",
}
ROLE_CAPABILITIES: dict[str, str] = {role: code for code, role in CAPABILITY_ROLES.items()}

_GROUP_OVERVIEW = "概要"
_GROUP_CONTROL_PLANE = "Control Plane"
_GROUP_OPERATIONS = "運用設定"
_GROUP_SETTINGS = "システム設定"
_GROUP_USERS_ROLES = "ユーザーとロール"
_GROUP_SECURITY = "Agent セキュリティ"
_GROUP_CAPABILITIES = "実行・承認・管理の権限"

_SYSTEM_SETTINGS_MENUS = (
    MENU_SETTINGS_OCI,
    MENU_SETTINGS_UPLOAD_STORAGE,
    MENU_SETTINGS_MODEL,
    MENU_SETTINGS_DATABASE,
    MENU_SETTINGS_APPEARANCE,
)
# agent.admin が暗黙に含むメニュー（ユーザーとロール・権限管理は含まない）。
_ADMIN_MENUS = (
    MENU_DASHBOARD,
    MENU_AGENTS,
    MENU_SKILLS,
    MENU_RUNTIMES,
    MENU_RUNS,
    MENU_APPROVALS,
    MENU_AUDIT,
    MENU_PLUGIN_MARKETPLACES,
    MENU_SETTINGS_CONNECTION,
    MENU_SETTINGS_EXTERNAL_RAG,
    MENU_SETTINGS_EXTERNAL_NL2SQL,
    MENU_SETTINGS_EXTERNAL_MCP,
    MENU_SETTINGS_RUNTIME_SNAPSHOT,
    *_SYSTEM_SETTINGS_MENUS,
)


PERMISSION_CATALOG: tuple[PermissionDefinition, ...] = (
    _menu_permission(MENU_DASHBOARD, _GROUP_OVERVIEW, "ダッシュボード"),
    _menu_permission(MENU_AGENTS, _GROUP_CONTROL_PLANE, "業務 Agent"),
    _menu_permission(MENU_SKILLS, _GROUP_CONTROL_PLANE, "スキル"),
    _menu_permission(MENU_RUNTIMES, _GROUP_CONTROL_PLANE, "Runtime"),
    _menu_permission(MENU_RUNS, _GROUP_CONTROL_PLANE, "Run"),
    _menu_permission(MENU_APPROVALS, _GROUP_CONTROL_PLANE, "承認・監査"),
    _menu_permission(MENU_AUDIT, _GROUP_CONTROL_PLANE, "監査"),
    _menu_permission(MENU_PLUGIN_MARKETPLACES, _GROUP_CONTROL_PLANE, "マーケットプレイス"),
    _menu_permission(MENU_SETTINGS_CONNECTION, _GROUP_OPERATIONS, "Agent 接続設定"),
    _menu_permission(MENU_SETTINGS_EXTERNAL_RAG, _GROUP_OPERATIONS, "外部 RAG"),
    _menu_permission(MENU_SETTINGS_EXTERNAL_NL2SQL, _GROUP_OPERATIONS, "外部 NL2SQL"),
    _menu_permission(MENU_SETTINGS_EXTERNAL_MCP, _GROUP_OPERATIONS, "外部 MCP"),
    _menu_permission(
        MENU_SETTINGS_RUNTIME_SNAPSHOT, _GROUP_OPERATIONS, "Control Plane バックアップ"
    ),
    # ユーザー管理・ロール管理は 3 製品共通の画面（#206）。権限管理は Agent 固有。
    _menu_permission(MENU_SECURITY_USERS, _GROUP_USERS_ROLES, "ユーザー管理"),
    _menu_permission(MENU_SECURITY_ROLES, _GROUP_USERS_ROLES, "ロール管理"),
    _menu_permission(MENU_SECURITY_PERMISSIONS, _GROUP_SECURITY, "権限管理"),
    _menu_permission(MENU_SETTINGS_OCI, _GROUP_SETTINGS, "OCI 認証"),
    _menu_permission(MENU_SETTINGS_UPLOAD_STORAGE, _GROUP_SETTINGS, "アップロード保存先"),
    _menu_permission(MENU_SETTINGS_MODEL, _GROUP_SETTINGS, "モデル"),
    _menu_permission(MENU_SETTINGS_DATABASE, _GROUP_SETTINGS, "データベース"),
    _menu_permission(MENU_SETTINGS_APPEARANCE, _GROUP_SETTINGS, "外観"),
    _permission(
        RUNS_VIEW,
        _GROUP_CAPABILITIES,
        "Run の閲覧（viewer）",
        "利用できるエージェント・業務ビューの Run・イベント・成果物を表示できます。",
        implies=(MENU_DASHBOARD, MENU_RUNS),
    ),
    _permission(
        RUNS_OPERATE,
        _GROUP_CAPABILITIES,
        "Run の実行・操作（operator）",
        "利用できるエージェント・業務ビューで Run の作成・取消・再開・再実行ができます"
        "（Run の閲覧を含みます）。",
        implies=(MENU_DASHBOARD, MENU_RUNS),
    ),
    _permission(
        APPROVALS_DECIDE,
        _GROUP_CAPABILITIES,
        "承認の判断（approver）",
        "利用できるエージェント・業務ビューの Run の承認・却下ができます（Run の閲覧を含みます）。",
        implies=(MENU_DASHBOARD, MENU_APPROVALS),
    ),
    _permission(
        AUDIT_VIEW,
        _GROUP_CAPABILITIES,
        "監査の閲覧（auditor）",
        "利用できるエージェント・業務ビューの Run の監査記録・ツール呼出し履歴を表示できます"
        "（Run の閲覧を含みます）。",
        implies=(MENU_DASHBOARD, MENU_AUDIT),
    ),
    _permission(
        ADMIN,
        _GROUP_CAPABILITIES,
        "Agent 管理（admin）",
        "業務 Agent・スキル・Runtime・Binding・プラグイン・運用設定・システム設定の変更と、"
        "すべての操作ができます（エージェント・業務ビューの対象範囲の制限を受けません）。",
        implies=_ADMIN_MENUS,
    ),
)

ALL_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)
PERMISSION_BY_CODE = {item.code: item for item in PERMISSION_CATALOG}
CAPABILITY_CODES = frozenset(CAPABILITY_ROLES)


def unknown_permission_codes(codes: Iterable[str]) -> set[str]:
    """カタログにない権限コード。"""
    return {
        code.strip() for code in codes if code.strip() and code.strip() not in PERMISSION_BY_CODE
    }


def normalize_permission_codes(codes: Iterable[str]) -> set[str]:
    """カタログにある権限コードだけを残す（空白除去）。"""
    return {code.strip() for code in codes if code.strip() in PERMISSION_BY_CODE}


def expand_permissions(codes: Iterable[str]) -> set[str]:
    """implies を閉包した実効権限。"""
    expanded = normalize_permission_codes(codes)
    pending = list(expanded)
    while pending:
        definition = PERMISSION_BY_CODE.get(pending.pop())
        if definition is None:
            continue
        for implied in definition.implies:
            if implied not in expanded:
                expanded.add(implied)
                pending.append(implied)
    return expanded


def grants_all_targets(codes: Iterable[str]) -> bool:
    """`agent.admin` はすべてのエージェント・業務ビューを利用できる（対象範囲の制限を受けない）。"""
    return ADMIN in expand_permissions(codes)


def roles_for_permissions(codes: Iterable[str]) -> set[str]:
    """実効権限の capability → 従来のロール名（router の `require_*` が使う）。"""
    return {CAPABILITY_ROLES[code] for code in codes if code in CAPABILITY_ROLES}


# 外部連携（header / JWT / 外部 policy）のロールが読めるメニュー。従来の header RBAC で
# `require_viewer` だけが守っていた Control Plane の読み取りを、ロールを持つ利用者に保つ。
# 運用設定・システム設定・ユーザーとロールは含めない
# （admin は implies で運用設定・システム設定を得る）。
EXTERNAL_ROLE_READ_MENUS = frozenset(
    {
        MENU_DASHBOARD,
        MENU_AGENTS,
        MENU_SKILLS,
        MENU_RUNTIMES,
        MENU_RUNS,
        MENU_APPROVALS,
        MENU_AUDIT,
        MENU_PLUGIN_MARKETPLACES,
    }
)


def permissions_for_roles(roles: Iterable[str]) -> set[str]:
    """外部連携（header / JWT / 外部 policy）のロール名 → 実効権限（manifest の判定に使う）。

    既知のロールを 1 つも持たなければ空（既定拒否）。
    """
    capabilities = {
        ROLE_CAPABILITIES[role.strip().lower()]
        for role in roles
        if role.strip().lower() in ROLE_CAPABILITIES
    }
    if not capabilities:
        return set()
    return expand_permissions(capabilities) | set(EXTERNAL_ROLE_READ_MENUS)


# ---- API の権限 manifest ----

# 公開 path。`/mcp/{binding_id}` は Runtime からの呼出し境界で、Binding 固有 token で認証する
# （Cookie・manifest の対象外）。
PUBLIC_API_PATHS = frozenset({"/health", "/ready", "/auth/login", "/mcp/{binding_id}"})
AUTHENTICATED_WITHOUT_PERMISSION = frozenset({"/auth/me", "/auth/logout", "/auth/password/change"})


def _any(*codes: str) -> frozenset[str]:
    return frozenset(codes)


# router の `require_*` が受け付ける capability（manifest の書込み・操作 API に使う）。
_ADMIN_ONLY = _any(ADMIN)
_OPERATE = _any(RUNS_OPERATE, ADMIN)
_DECIDE = _any(APPROVALS_DECIDE, ADMIN)
# 画面の読み取り（メニュー権限。capability は関連メニューを暗黙に含む）。
_RUN_LIST = _any(MENU_DASHBOARD, MENU_RUNS, MENU_APPROVALS)
_RUN_DETAIL = _any(MENU_RUNS, MENU_APPROVALS, MENU_AUDIT)
_AGENT_READ = _any(MENU_AGENTS, MENU_RUNS, MENU_SETTINGS_RUNTIME_SNAPSHOT)
_BINDING_READ = _any(MENU_AGENTS, MENU_RUNS, MENU_RUNTIMES, MENU_SETTINGS_RUNTIME_SNAPSHOT)
_TOOL_READ = _any(MENU_DASHBOARD, MENU_AUDIT, ADMIN)
_PLUGIN_READ = _any(MENU_PLUGIN_MARKETPLACES)
_EXTERNAL_SETTINGS_READ = _any(
    MENU_SETTINGS_EXTERNAL_RAG, MENU_SETTINGS_EXTERNAL_NL2SQL, MENU_SETTINGS_EXTERNAL_MCP
)
_SECURITY_ROLE_READ = _any(MENU_SECURITY_USERS, MENU_SECURITY_ROLES, MENU_SECURITY_PERMISSIONS)

_RUN = "/runs/{run_id}"

# (METHOD, route template) → 許可する権限（いずれか）。`/api` は付けない。
ROUTE_PERMISSIONS: dict[tuple[str, str], frozenset[str]] = {
    # ---- 概要: ダッシュボード ----
    ("GET", "/observability/status"): _any(MENU_DASHBOARD),
    ("GET", "/observability/events"): _any(MENU_AUDIT),
    ("POST", "/observability/export-retry/flush"): _ADMIN_ONLY,
    ("GET", "/settings/trace-policy"): _any(MENU_DASHBOARD, MENU_AUDIT),
    ("PATCH", "/settings/trace-policy"): _ADMIN_ONLY,
    # ツール定義はダッシュボード・監査・ツール権限（管理者だけの非表示画面）が読む。
    ("GET", "/tools"): _TOOL_READ,
    ("GET", "/agent/tools"): _TOOL_READ,
    ("GET", "/tools/external-mcp"): _EXTERNAL_SETTINGS_READ,
    ("POST", "/tools/invoke"): _OPERATE,
    ("POST", "/agent/tools/invoke"): _OPERATE,
    # ---- Control Plane: 業務 Agent ----
    ("GET", "/agents"): _AGENT_READ,
    ("POST", "/agents"): _ADMIN_ONLY,
    ("PATCH", "/agents/{agent_id}"): _ADMIN_ONLY,
    ("DELETE", "/agents/{agent_id}"): _ADMIN_ONLY,
    # ---- Control Plane: スキル ----
    ("GET", "/skills"): _any(MENU_SKILLS, MENU_AGENTS),
    ("GET", "/skills/{skill_id}"): _any(MENU_SKILLS, MENU_AGENTS),
    ("POST", "/skills/plan"): _any(MENU_SKILLS),
    ("POST", "/skills"): _ADMIN_ONLY,
    ("POST", "/skills/reload"): _ADMIN_ONLY,
    ("PATCH", "/skills/{skill_id}"): _ADMIN_ONLY,
    ("DELETE", "/skills/{skill_id}"): _ADMIN_ONLY,
    # ---- Control Plane: Runtime と Binding ----
    ("GET", "/runtimes"): _any(MENU_RUNTIMES, MENU_AGENTS),
    ("POST", "/runtimes"): _ADMIN_ONLY,
    ("PATCH", "/runtimes/{runtime_id}"): _ADMIN_ONLY,
    ("DELETE", "/runtimes/{runtime_id}"): _ADMIN_ONLY,
    ("GET", "/runtimes/{runtime_id}/status"): _any(MENU_RUNTIMES),
    ("POST", "/runtimes/services/{service_id}/{action}"): _ADMIN_ONLY,
    ("GET", "/runtimes/services/{service_id}/logs"): _ADMIN_ONLY,
    ("GET", "/runtime-bindings"): _BINDING_READ,
    ("POST", "/runtime-bindings"): _ADMIN_ONLY,
    ("PATCH", "/runtime-bindings/{binding_id}"): _ADMIN_ONLY,
    ("DELETE", "/runtime-bindings/{binding_id}"): _ADMIN_ONLY,
    ("POST", "/runtime-bindings/{binding_id}/sync"): _ADMIN_ONLY,
    # ---- Control Plane: Run・承認・監査 ----
    ("GET", "/runs"): _RUN_LIST,
    ("POST", "/runs"): _OPERATE,
    ("GET", _RUN): _RUN_DETAIL,
    ("GET", f"{_RUN}/audit"): _RUN_DETAIL,
    ("GET", f"{_RUN}/artifacts"): _RUN_DETAIL,
    ("GET", f"{_RUN}/artifacts/{{artifact_id}}"): _RUN_DETAIL,
    ("GET", f"{_RUN}/events"): _any(MENU_RUNS, MENU_APPROVALS),
    ("POST", f"{_RUN}/cancel"): _OPERATE,
    ("POST", f"{_RUN}/resume"): _OPERATE,
    ("POST", f"{_RUN}/replay"): _OPERATE,
    ("POST", "/approvals/{approval_id}/decision"): _DECIDE,
    ("GET", "/audit/tool-calls"): _any(MENU_AUDIT),
    ("GET", "/audit/tool-calls.csv"): _any(MENU_AUDIT),
    # legacy Memory（読取専用の export）は監査の閲覧と管理者だけ。
    ("GET", "/memory/search"): _any(AUDIT_VIEW, ADMIN),
    ("POST", "/memory/search"): _any(AUDIT_VIEW, ADMIN),
    ("POST", "/memory"): _OPERATE,
    # ---- Control Plane: プラグインとマーケットプレイス ----
    ("GET", "/plugins"): _PLUGIN_READ,
    ("GET", "/plugins/{plugin_id}"): _PLUGIN_READ,
    ("GET", "/plugins/marketplaces"): _PLUGIN_READ,
    ("GET", "/plugins/marketplaces/{marketplace_id}/plugins"): _PLUGIN_READ,
    ("POST", "/plugins"): _ADMIN_ONLY,
    ("PATCH", "/plugins/{plugin_id}"): _ADMIN_ONLY,
    ("DELETE", "/plugins/{plugin_id}"): _ADMIN_ONLY,
    ("POST", "/plugins/reload"): _ADMIN_ONLY,
    ("POST", "/plugins/marketplaces"): _ADMIN_ONLY,
    ("DELETE", "/plugins/marketplaces/{marketplace_id}"): _ADMIN_ONLY,
    ("POST", "/plugins/marketplaces/{marketplace_id}/refresh"): _ADMIN_ONLY,
    # ---- 運用設定 ----
    ("GET", "/settings/external-rag"): _any(MENU_DASHBOARD, MENU_SETTINGS_EXTERNAL_RAG),
    ("PATCH", "/settings/external-rag"): _ADMIN_ONLY,
    ("GET", "/settings/external-nl2sql"): _any(MENU_DASHBOARD, MENU_SETTINGS_EXTERNAL_NL2SQL),
    ("PATCH", "/settings/external-nl2sql"): _ADMIN_ONLY,
    ("GET", "/settings/external-mcp"): _any(MENU_SETTINGS_EXTERNAL_MCP),
    ("PATCH", "/settings/external-mcp"): _ADMIN_ONLY,
    ("GET", "/settings/external-mcp-servers"): _any(MENU_SETTINGS_EXTERNAL_MCP),
    ("POST", "/settings/external-mcp-servers"): _ADMIN_ONLY,
    ("PATCH", "/settings/external-mcp-servers/{server_id}"): _ADMIN_ONLY,
    ("DELETE", "/settings/external-mcp-servers/{server_id}"): _ADMIN_ONLY,
    ("POST", "/settings/external-mcp-servers/{server_id}/default"): _ADMIN_ONLY,
    ("GET", "/runtime/snapshot"): _ADMIN_ONLY,
    ("POST", "/runtime/snapshot/import"): _ADMIN_ONLY,
    # ナビに出さない設定（ツール権限・Command Policy・Runtime Safety・Planner）は管理者だけ。
    ("GET", "/settings/tool-policy"): _ADMIN_ONLY,
    ("PATCH", "/settings/tool-policy"): _ADMIN_ONLY,
    ("GET", "/settings/command-policy"): _ADMIN_ONLY,
    ("PATCH", "/settings/command-policy"): _ADMIN_ONLY,
    ("GET", "/settings/runtime-safety"): _ADMIN_ONLY,
    ("PATCH", "/settings/runtime-safety"): _ADMIN_ONLY,
    ("GET", "/settings/planner"): _ADMIN_ONLY,
    ("PATCH", "/settings/planner"): _ADMIN_ONLY,
    # ---- システム設定（3 製品共通。RAG / NL2SQL と同じくメニュー権限で保存・操作できる） ----
    # 外部連携（header / JWT）のロールでは、保存・操作は従来どおり admin だけ（router の
    # `require_system_settings_write`。admin は implies でシステム設定のメニューを持つ）。
    ("GET", "/settings/upload-storage"): _any(MENU_SETTINGS_UPLOAD_STORAGE, MENU_SETTINGS_OCI),
    ("PATCH", "/settings/upload-storage"): _any(MENU_SETTINGS_UPLOAD_STORAGE),
    ("GET", "/settings/oci"): _any(MENU_SETTINGS_OCI),
    ("PATCH", "/settings/oci"): _any(MENU_SETTINGS_OCI),
    ("PATCH", "/settings/oci/object-storage"): _any(
        MENU_SETTINGS_OCI, MENU_SETTINGS_UPLOAD_STORAGE
    ),
    ("POST", "/settings/oci/config/read"): _any(MENU_SETTINGS_OCI),
    ("POST", "/settings/oci/config/test"): _any(MENU_SETTINGS_OCI),
    ("POST", "/settings/oci/object-storage/namespace"): _any(
        MENU_SETTINGS_OCI, MENU_SETTINGS_UPLOAD_STORAGE
    ),
    ("POST", "/settings/oci/key-file"): _any(MENU_SETTINGS_OCI),
    ("GET", "/settings/model"): _any(MENU_SETTINGS_MODEL),
    ("PATCH", "/settings/model"): _any(MENU_SETTINGS_MODEL),
    ("POST", "/settings/model/test"): _any(MENU_SETTINGS_MODEL),
    ("GET", "/settings/database"): _any(MENU_SETTINGS_DATABASE),
    ("PATCH", "/settings/database"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/wallet"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/wallet/download"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/test"): _any(MENU_SETTINGS_DATABASE),
    ("GET", "/settings/database/adb"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/adb/settings"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/adb/start"): _any(MENU_SETTINGS_DATABASE),
    ("POST", "/settings/database/adb/stop"): _any(MENU_SETTINGS_DATABASE),
    # ---- Agent セキュリティ: 権限管理 ----
    ("GET", "/security/permissions"): _any(MENU_SECURITY_PERMISSIONS),
    ("GET", "/security/access-targets"): _any(MENU_SECURITY_PERMISSIONS),
    ("PUT", "/security/roles/{role_id}/access"): _any(MENU_SECURITY_PERMISSIONS),
}

# WebSocket（OpenAPI に出ない）。handler の中で同じ規則で確認する。
WEBSOCKET_PERMISSIONS: dict[str, frozenset[str]] = {
    "/runs/{run_id}/events/ws": _any(MENU_RUNS, MENU_APPROVALS),
}


def permission_for_route(method: str, route_path: str) -> frozenset[str] | None:
    """method + route template（`/api` なし）→ 許可する権限の集合。

    None は公開 API・ログインだけで使える API。登録外は `UNCLASSIFIED_PERMISSION`（拒否）。
    """
    method = method.upper()
    if route_path in PUBLIC_API_PATHS or route_path in AUTHENTICATED_WITHOUT_PERMISSION:
        return None
    exact = ROUTE_PERMISSIONS.get((method, route_path))
    if exact is not None:
        return exact
    # ユーザー管理・ロール管理（platform の共通 router）は NL2SQL / RAG と同じ割り当て。
    if route_path == "/security/users" or route_path.startswith("/security/users/"):
        return _any(MENU_SECURITY_USERS)
    if route_path == "/security/roles" or route_path.startswith("/security/roles/"):
        if method == "GET":
            return _SECURITY_ROLE_READ
        return _any(MENU_SECURITY_ROLES)
    return _any(UNCLASSIFIED_PERMISSION)
