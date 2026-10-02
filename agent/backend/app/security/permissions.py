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
（3 製品共通の画面。#206）。権限管理は「セキュリティ設定」の製品固有メニュー
（セクション名は 3 製品で同じ。#658）。
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
    # 説明はナビの名前（label）と同じ用語で書く（#580）。
    return _permission(code, group, label, f"「{label}」の画面を表示し、関連操作を利用できます。")


# ---- メニュー権限（agent/frontend の nav-config.ts と同じ並び） ----

MENU_AGENTS = "menu.agents"
MENU_SKILLS = "menu.skills"
MENU_RUNTIMES = "menu.runtimes"
MENU_RUNS = "menu.runs"
MENU_APPROVALS = "menu.approvals"
MENU_AUDIT = "menu.audit"
MENU_PLUGIN_MARKETPLACES = "menu.plugin_marketplaces"
MENU_SECURITY_PERMISSIONS = "menu.security_permissions"
MENU_SETTINGS_SYSTEM_TABLES = "menu.settings_system_tables"
# MCP 接続（#757。旧「外部 MCP」。権限コードは保存値なので変えない）。
MENU_SETTINGS_EXTERNAL_MCP = "menu.settings_external_mcp"
MENU_SETTINGS_RUNTIME_SNAPSHOT = "menu.settings_runtime_snapshot"
MENU_SECURITY_USERS = "menu.security_users"
MENU_SECURITY_ROLES = "menu.security_roles"
MENU_SETTINGS_OCI = "menu.settings_oci"
MENU_SETTINGS_UPLOAD_STORAGE = "menu.settings_upload_storage"
MENU_SETTINGS_MODEL = "menu.settings_model"
MENU_SETTINGS_DATABASE = "menu.settings_database"
MENU_SETTINGS_APPEARANCE = "menu.settings_appearance"

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

# グループ・名前・並び順は左のナビ（frontend の nav-config.ts と、i18n の
# サイドナビの表示名）と同じにする（#567 / #580。一致は
# tests/test_permission_catalog_nav.py が確かめる）。ナビに無い capability は後ろに置く。
_GROUP_CONTROL_PLANE = "Control Plane"
_GROUP_SECURITY = "セキュリティ設定"
_GROUP_OPERATIONS = "運用設定"
_GROUP_USERS_ROLES = "ユーザーとロール"
_GROUP_SETTINGS = "システム設定"
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
    MENU_AGENTS,
    MENU_SKILLS,
    MENU_RUNTIMES,
    MENU_RUNS,
    MENU_APPROVALS,
    MENU_AUDIT,
    MENU_PLUGIN_MARKETPLACES,
    MENU_SETTINGS_SYSTEM_TABLES,
    MENU_SETTINGS_EXTERNAL_MCP,
    MENU_SETTINGS_RUNTIME_SNAPSHOT,
    *_SYSTEM_SETTINGS_MENUS,
)


PERMISSION_CATALOG: tuple[PermissionDefinition, ...] = (
    _menu_permission(MENU_AGENTS, _GROUP_CONTROL_PLANE, "業務 Agent"),
    _menu_permission(MENU_SKILLS, _GROUP_CONTROL_PLANE, "スキル (Skills)"),
    _menu_permission(MENU_RUNTIMES, _GROUP_CONTROL_PLANE, "Runtime"),
    _menu_permission(MENU_RUNS, _GROUP_CONTROL_PLANE, "Run"),
    _menu_permission(MENU_APPROVALS, _GROUP_CONTROL_PLANE, "承認・監査"),
    _menu_permission(MENU_AUDIT, _GROUP_CONTROL_PLANE, "監査"),
    _menu_permission(MENU_PLUGIN_MARKETPLACES, _GROUP_CONTROL_PLANE, "マーケットプレイス"),
    # 権限管理は Agent 固有。ユーザー管理・ロール管理は 3 製品共通の画面（#206）。
    _menu_permission(MENU_SECURITY_PERMISSIONS, _GROUP_SECURITY, "権限管理"),
    # 並びはサイドナビと同じ
    # （セキュリティ設定 → ユーザーとロール → 運用設定 → システム設定。#658）。
    _menu_permission(MENU_SECURITY_USERS, _GROUP_USERS_ROLES, "ユーザー管理"),
    _menu_permission(MENU_SECURITY_ROLES, _GROUP_USERS_ROLES, "ロール管理"),
    # 運用設定の先頭はシステムテーブル（RAG / NL2SQL と同じ。#658 / #751）。
    _menu_permission(MENU_SETTINGS_SYSTEM_TABLES, _GROUP_OPERATIONS, "システムテーブル"),
    _menu_permission(MENU_SETTINGS_EXTERNAL_MCP, _GROUP_OPERATIONS, "MCP 接続"),
    _menu_permission(
        MENU_SETTINGS_RUNTIME_SNAPSHOT, _GROUP_OPERATIONS, "Control Plane バックアップ"
    ),
    _menu_permission(MENU_SETTINGS_OCI, _GROUP_SETTINGS, "OCI 認証"),
    _menu_permission(MENU_SETTINGS_UPLOAD_STORAGE, _GROUP_SETTINGS, "アップロード保存先"),
    _menu_permission(MENU_SETTINGS_MODEL, _GROUP_SETTINGS, "モデル"),
    _menu_permission(MENU_SETTINGS_DATABASE, _GROUP_SETTINGS, "データベース"),
    _menu_permission(MENU_SETTINGS_APPEARANCE, _GROUP_SETTINGS, "外観"),
    _permission(
        RUNS_VIEW,
        _GROUP_CAPABILITIES,
        "Run の閲覧（viewer）",
        "利用できるエージェントの Run・イベント・成果物を表示できます。",
        implies=(MENU_RUNS,),
    ),
    _permission(
        RUNS_OPERATE,
        _GROUP_CAPABILITIES,
        "Run の実行・操作（operator）",
        "利用できるエージェントで Run の作成・取消・再開・再実行ができます"
        "（Run の閲覧を含みます）。",
        implies=(MENU_RUNS,),
    ),
    _permission(
        APPROVALS_DECIDE,
        _GROUP_CAPABILITIES,
        "承認の判断（approver）",
        "利用できるエージェントの Run の承認・却下ができます（Run の閲覧を含みます）。",
        implies=(MENU_APPROVALS,),
    ),
    _permission(
        AUDIT_VIEW,
        _GROUP_CAPABILITIES,
        "監査の閲覧（auditor）",
        "利用できるエージェントの Run の監査記録・ツール呼出し履歴を表示できます"
        "（Run の閲覧を含みます）。",
        implies=(MENU_AUDIT,),
    ),
    _permission(
        ADMIN,
        _GROUP_CAPABILITIES,
        "Agent 管理（admin）",
        "業務 Agent・スキル・プラグイン・運用設定・システム設定の変更と、"
        "すべての操作ができます（エージェントの対象範囲の制限を受けません）。",
        implies=_ADMIN_MENUS,
    ),
)

ALL_PERMISSION_CODES = frozenset(item.code for item in PERMISSION_CATALOG)

# 廃止した権限コード。既存ロールに残る行は、システムテーブルの migration
# （`app.system_schema`）が削除する。
# 削除前でも `normalize_permission_codes` が捨てるため、実効権限・権限管理の表示と保存には現れない。
# - `menu.dashboard`: ダッシュボード機能の廃止（#262）。
# - `menu.settings_external_rag` / `menu.settings_external_nl2sql`: 外部 RAG / 外部 NL2SQL の画面を
#   MCP 接続（`menu.settings_external_mcp`）へまとめた（#757）。
# - `menu.settings_connection`: 中身のない「Agent 接続設定」の画面を削除した（#762）。
RETIRED_PERMISSION_CODES: tuple[str, ...] = (
    "menu.dashboard",
    "menu.settings_external_rag",
    "menu.settings_external_nl2sql",
    "menu.settings_connection",
)
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
    """`agent.admin` はすべてのエージェントを利用できる（対象範囲の制限を受けない）。"""
    return ADMIN in expand_permissions(codes)


def roles_for_permissions(codes: Iterable[str]) -> set[str]:
    """実効権限の capability → 従来のロール名（router の `require_*` が使う）。"""
    return {CAPABILITY_ROLES[code] for code in codes if code in CAPABILITY_ROLES}


# ---- API の権限 manifest ----

# 公開 path。`/ready/database` は画面の DB ゲートがログイン前に使う（接続先・資格情報は返さない。
# #325）。外部 Runtime の Binding の MCP（`/mcp/{binding_id}`）は #754 で削除した。
PUBLIC_API_PATHS = frozenset({"/health", "/ready", "/ready/database", "/auth/login"})
AUTHENTICATED_WITHOUT_PERMISSION = frozenset({"/auth/me", "/auth/logout", "/auth/password/change"})
# 権限なしで通す操作（method × path）。公開・ログインだけ・MCP の path でも、ここにない method は
# 通常の認証と権限の判定にする（共通認証の `open_operations` にも渡す。#490）。
OPEN_API_OPERATIONS = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/ready/database"),
        ("POST", "/auth/login"),
        ("GET", "/auth/me"),
        ("POST", "/auth/logout"),
        ("POST", "/auth/password/change"),
    }
)


def _any(*codes: str) -> frozenset[str]:
    return frozenset(codes)


# router の `require_*` が受け付ける capability（manifest の書込み・操作 API に使う）。
_ADMIN_ONLY = _any(ADMIN)
_OPERATE = _any(RUNS_OPERATE, ADMIN)
_DECIDE = _any(APPROVALS_DECIDE, ADMIN)
# 画面の読み取り（メニュー権限。capability は関連メニューを暗黙に含む）。
_RUN_LIST = _any(MENU_RUNS, MENU_APPROVALS)
_RUN_DETAIL = _any(MENU_RUNS, MENU_APPROVALS, MENU_AUDIT)
_AGENT_READ = _any(MENU_AGENTS, MENU_RUNS, MENU_SETTINGS_RUNTIME_SNAPSHOT)
_TOOL_READ = _any(MENU_AUDIT, ADMIN)
_PLUGIN_READ = _any(MENU_PLUGIN_MARKETPLACES)
_SECURITY_ROLE_READ = _any(MENU_SECURITY_USERS, MENU_SECURITY_ROLES, MENU_SECURITY_PERMISSIONS)

_RUN = "/runs/{run_id}"

# (METHOD, route template) → 許可する権限（いずれか）。`/api` は付けない。
ROUTE_PERMISSIONS: dict[tuple[str, str], frozenset[str]] = {
    # ---- 観測性（画面からは使わない。運用スクリプトの確認と監査の閲覧者向け） ----
    ("GET", "/observability/status"): _any(MENU_AUDIT),
    ("GET", "/observability/events"): _any(MENU_AUDIT),
    ("POST", "/observability/export-retry/flush"): _ADMIN_ONLY,
    ("GET", "/settings/trace-policy"): _any(MENU_AUDIT),
    ("PATCH", "/settings/trace-policy"): _ADMIN_ONLY,
    # ツール定義は監査・ツール一覧・ツール権限（管理者だけの非表示画面）が読む。
    ("GET", "/tools"): _TOOL_READ,
    ("POST", "/tools/invoke"): _OPERATE,
    # ---- Control Plane: 業務 Agent ----
    ("GET", "/agents"): _AGENT_READ,
    ("POST", "/agents"): _ADMIN_ONLY,
    ("PATCH", "/agents/{agent_id}"): _ADMIN_ONLY,
    ("DELETE", "/agents/{agent_id}"): _ADMIN_ONLY,
    # 業種テンプレート（#780。業務 Agent の新規作成の画面が読む）。
    ("GET", "/agent-templates"): _any(MENU_AGENTS),
    # ---- Control Plane: スキル ----
    ("GET", "/skills"): _any(MENU_SKILLS, MENU_AGENTS),
    ("GET", "/skills/{skill_id}"): _any(MENU_SKILLS, MENU_AGENTS),
    ("POST", "/skills"): _ADMIN_ONLY,
    ("POST", "/skills/reload"): _ADMIN_ONLY,
    ("PATCH", "/skills/{skill_id}"): _ADMIN_ONLY,
    ("DELETE", "/skills/{skill_id}"): _ADMIN_ONLY,
    # ---- Control Plane: 組み込み Runtime の状態 ----
    # ---- Control Plane: Run・承認・監査 ----
    # 組み込み Runtime の状態（#754）。
    ("GET", "/runtime/status"): _any(MENU_RUNTIMES, MENU_AGENTS),
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
    # システムテーブル（#751。NL2SQL と同じくメニュー権限で状態の確認と作成・更新）。
    ("GET", "/settings/database/system-tables"): _any(MENU_SETTINGS_SYSTEM_TABLES),
    ("POST", "/settings/database/system-tables/initialize"): _any(MENU_SETTINGS_SYSTEM_TABLES),
    # MCP 接続（#757）。一覧は Skill の編集（使う接続を選ぶ）からも読む。ツールの取得は接続の確認。
    ("GET", "/settings/mcp-connections"): _any(MENU_SETTINGS_EXTERNAL_MCP, MENU_SKILLS),
    ("POST", "/settings/mcp-connections"): _ADMIN_ONLY,
    ("PATCH", "/settings/mcp-connections/{server_id}"): _ADMIN_ONLY,
    ("DELETE", "/settings/mcp-connections/{server_id}"): _ADMIN_ONLY,
    ("GET", "/settings/mcp-connections/{server_id}/tools"): _any(MENU_SETTINGS_EXTERNAL_MCP),
    ("GET", "/runtime/snapshot"): _ADMIN_ONLY,
    ("POST", "/runtime/snapshot/import"): _ADMIN_ONLY,
    # ナビに出さない設定（ツール権限）は管理者だけ。
    ("GET", "/settings/tool-policy"): _ADMIN_ONLY,
    ("PATCH", "/settings/tool-policy"): _ADMIN_ONLY,
    # ---- システム設定（3 製品共通。RAG / NL2SQL と同じくメニュー権限で保存・操作できる） ----
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
    # ---- ユーザーとロール（platform の共通 router。RAG / NL2SQL と同じ割り当て）----
    # 前方一致で割り当てず、route ごとに登録する（新しい route は登録するまで拒否。#503）。
    ("GET", "/security/users"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users"): _any(MENU_SECURITY_USERS),
    ("GET", "/security/users/{user_uuid}"): _any(MENU_SECURITY_USERS),
    ("PATCH", "/security/users/{user_uuid}"): _any(MENU_SECURITY_USERS),
    ("DELETE", "/security/users/{user_uuid}"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/disable"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/enable"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/reset-password"): _any(MENU_SECURITY_USERS),
    ("POST", "/security/users/{user_uuid}/unlock"): _any(MENU_SECURITY_USERS),
    ("GET", "/security/roles"): _SECURITY_ROLE_READ,
    ("GET", "/security/roles/{role_id}"): _SECURITY_ROLE_READ,
    ("POST", "/security/roles"): _any(MENU_SECURITY_ROLES),
    ("PATCH", "/security/roles/{role_id}"): _any(MENU_SECURITY_ROLES),
    ("DELETE", "/security/roles/{role_id}"): _any(MENU_SECURITY_ROLES),
    ("POST", "/security/roles/{role_id}/archive"): _any(MENU_SECURITY_ROLES),
    ("POST", "/security/roles/{role_id}/restore"): _any(MENU_SECURITY_ROLES),
    # ---- セキュリティ設定: 権限管理 ----
    ("GET", "/security/permissions"): _any(MENU_SECURITY_PERMISSIONS),
    ("GET", "/security/access-targets/agents"): _any(MENU_SECURITY_PERMISSIONS),
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
    if (method, route_path) in OPEN_API_OPERATIONS:
        return None
    exact = ROUTE_PERMISSIONS.get((method, route_path))
    if exact is not None:
        return exact
    return _any(UNCLASSIFIED_PERMISSION)
