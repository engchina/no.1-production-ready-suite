/**
 * e2e の認証・権限のデータ（#215）。
 *
 * 権限コードとカタログは backend `app/security/permissions.py` の `PERMISSION_CATALOG` と同じ
 * （`permission-catalog.spec.ts` が backend の定義・frontend の `lib/permissions.ts` と一致することを確かめる）。
 * mock-api.ts の既定の利用者はローカル（`AGENT_AUTH_MODE=local`）の全権限の利用者なので、
 * 認証を扱わない既存の spec はログインなしで全画面を使える。
 */

type Json = Record<string, unknown>;

export const MENU_PERMISSION_CODES = [
  "menu.dashboard",
  "menu.agents",
  "menu.skills",
  "menu.runtimes",
  "menu.runs",
  "menu.approvals",
  "menu.audit",
  "menu.plugin_marketplaces",
  "menu.settings_connection",
  "menu.settings_external_rag",
  "menu.settings_external_nl2sql",
  "menu.settings_external_mcp",
  "menu.settings_runtime_snapshot",
  "menu.security_users",
  "menu.security_roles",
  "menu.security_permissions",
  "menu.settings_oci",
  "menu.settings_upload_storage",
  "menu.settings_model",
  "menu.settings_database",
  "menu.settings_appearance",
] as const;

export const CAPABILITY_PERMISSION_CODES = [
  "agent.runs.view",
  "agent.runs.operate",
  "agent.approvals.decide",
  "agent.audit.view",
  "agent.admin",
] as const;

export const ALL_PERMISSION_CODES: string[] = [...MENU_PERMISSION_CODES, ...CAPABILITY_PERMISSION_CODES];

const ADMIN_MENUS = [
  "menu.dashboard",
  "menu.agents",
  "menu.skills",
  "menu.runtimes",
  "menu.runs",
  "menu.approvals",
  "menu.audit",
  "menu.plugin_marketplaces",
  "menu.settings_connection",
  "menu.settings_external_rag",
  "menu.settings_external_nl2sql",
  "menu.settings_external_mcp",
  "menu.settings_runtime_snapshot",
  "menu.settings_oci",
  "menu.settings_upload_storage",
  "menu.settings_model",
  "menu.settings_database",
  "menu.settings_appearance",
];

const MENU_LABELS: Record<(typeof MENU_PERMISSION_CODES)[number], [group: string, label: string]> = {
  "menu.dashboard": ["概要", "ダッシュボード"],
  "menu.agents": ["Control Plane", "業務 Agent"],
  "menu.skills": ["Control Plane", "スキル"],
  "menu.runtimes": ["Control Plane", "Runtime"],
  "menu.runs": ["Control Plane", "Run"],
  "menu.approvals": ["Control Plane", "承認・監査"],
  "menu.audit": ["Control Plane", "監査"],
  "menu.plugin_marketplaces": ["Control Plane", "マーケットプレイス"],
  "menu.settings_connection": ["運用設定", "Agent 接続設定"],
  "menu.settings_external_rag": ["運用設定", "外部 RAG"],
  "menu.settings_external_nl2sql": ["運用設定", "外部 NL2SQL"],
  "menu.settings_external_mcp": ["運用設定", "外部 MCP"],
  "menu.settings_runtime_snapshot": ["運用設定", "Control Plane バックアップ"],
  "menu.security_users": ["ユーザーとロール", "ユーザー管理"],
  "menu.security_roles": ["ユーザーとロール", "ロール管理"],
  "menu.security_permissions": ["Agent セキュリティ", "権限管理"],
  "menu.settings_oci": ["システム設定", "OCI 認証"],
  "menu.settings_upload_storage": ["システム設定", "アップロード保存先"],
  "menu.settings_model": ["システム設定", "モデル"],
  "menu.settings_database": ["システム設定", "データベース"],
  "menu.settings_appearance": ["システム設定", "外観"],
};

const CAPABILITY_GROUP = "実行・承認・管理の権限";

/** `GET /api/security/permissions` の応答（backend の PERMISSION_CATALOG と同じ並び・implies）。 */
export const PERMISSION_CATALOG: Json[] = [
  ...MENU_PERMISSION_CODES.map((code) => {
    const [group, label] = MENU_LABELS[code];
    return { code, group, label, description: `${label}を表示し、関連操作を利用できます。`, implies: [] };
  }),
  {
    code: "agent.runs.view",
    group: CAPABILITY_GROUP,
    label: "Run の閲覧（viewer）",
    description: "利用できるエージェント・業務ビューの Run・イベント・成果物を表示できます。",
    implies: ["menu.dashboard", "menu.runs"],
  },
  {
    code: "agent.runs.operate",
    group: CAPABILITY_GROUP,
    label: "Run の実行・操作（operator）",
    description: "利用できるエージェント・業務ビューで Run の作成・取消・再開・再実行ができます。",
    implies: ["menu.dashboard", "menu.runs"],
  },
  {
    code: "agent.approvals.decide",
    group: CAPABILITY_GROUP,
    label: "承認の判断（approver）",
    description: "利用できるエージェント・業務ビューの Run の承認・却下ができます。",
    implies: ["menu.dashboard", "menu.approvals"],
  },
  {
    code: "agent.audit.view",
    group: CAPABILITY_GROUP,
    label: "監査の閲覧（auditor）",
    description: "利用できるエージェント・業務ビューの Run の監査記録を表示できます。",
    implies: ["menu.dashboard", "menu.audit"],
  },
  {
    code: "agent.admin",
    group: CAPABILITY_GROUP,
    label: "Agent 管理（admin）",
    description: "業務 Agent・スキル・Runtime・運用設定・システム設定の変更と、すべての操作ができます。",
    implies: ADMIN_MENUS,
  },
];

/** implies を展開した実効権限（backend の `expand_permissions` と同じ）。 */
export function expandPermissions(codes: readonly string[]): string[] {
  const byCode = new Map(PERMISSION_CATALOG.map((item) => [item.code as string, item.implies as string[]]));
  const expanded = new Set<string>();
  const pending = [...codes];
  while (pending.length) {
    const code = pending.pop() as string;
    if (expanded.has(code)) continue;
    expanded.add(code);
    pending.push(...(byCode.get(code) ?? []));
  }
  return [...expanded].sort();
}

export interface CurrentUserPayload {
  user_uuid: string;
  login_user_id: string;
  display_name: string;
  status: string;
  force_password_change: boolean;
  role_codes: string[];
  is_system_admin: boolean;
  permissions: string[];
  allowed_agent_ids: string[] | null;
  allowed_business_view_ids: string[] | null;
  debug_mode: boolean;
  password_change_allowed: boolean;
}

/** ローカル（`AGENT_AUTH_MODE=local`）の利用者。ログインなしで全画面を使える。 */
export const LOCAL_CURRENT_USER: CurrentUserPayload = {
  user_uuid: "00000000-0000-0000-0000-000000000000",
  login_user_id: "local",
  display_name: "ローカル利用者",
  status: "ACTIVE",
  force_password_change: false,
  role_codes: ["SYSTEM_ADMIN"],
  is_system_admin: true,
  permissions: ALL_PERMISSION_CODES,
  allowed_agent_ids: null,
  allowed_business_view_ids: null,
  debug_mode: true,
  password_change_allowed: false,
};

/**
 * ログイン済みの DB ユーザー。既定は権限なし・範囲なし。`permissions` は直接付けたコードを渡せば
 * implies を展開する（backend の `/auth/me` と同じ）。
 */
export function dbUser(overrides: Partial<CurrentUserPayload> = {}): CurrentUserPayload {
  const user: CurrentUserPayload = {
    user_uuid: "11111111-1111-1111-1111-111111111111",
    login_user_id: "user01",
    display_name: "利用者 一郎",
    status: "ACTIVE",
    force_password_change: false,
    role_codes: ["AGENT_USER"],
    is_system_admin: false,
    permissions: [],
    allowed_agent_ids: [],
    allowed_business_view_ids: [],
    debug_mode: false,
    password_change_allowed: true,
    ...overrides,
  };
  return { ...user, permissions: expandPermissions(user.permissions) };
}

export const SYSTEM_ADMIN_ROLE: Json = {
  role_id: "role-admin",
  role_code: "SYSTEM_ADMIN",
  display_name: "システム管理者",
  description: "すべての権限",
  is_built_in: true,
  archived: false,
  version: 1,
  permissions: [],
  agent_ids: [],
  business_view_ids: [],
};

export const OPERATOR_ROLE: Json = {
  role_id: "role-operator",
  role_code: "AGENT_OPERATOR",
  display_name: "Agent 実行担当",
  description: "Run の実行と閲覧",
  is_built_in: false,
  archived: false,
  version: 3,
  permissions: ["agent.runs.operate", "menu.agents"],
  agent_ids: ["default"],
  business_view_ids: ["sales-east"],
};

export const SECURITY_USERS: Json[] = [
  {
    user_uuid: "u-admin",
    login_user_id: "admin",
    display_name: "管理 太郎",
    status: "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 1,
    role_ids: ["role-admin"],
    assigned_roles: [
      { role_id: "role-admin", role_code: "SYSTEM_ADMIN", display_name: "システム管理者", is_built_in: true, archived: false },
    ],
    is_bootstrap_admin: true,
  },
  {
    user_uuid: "u-operator",
    login_user_id: "operator.user",
    display_name: "実行 花子",
    status: "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 2,
    role_ids: ["role-operator"],
    assigned_roles: [
      {
        role_id: "role-operator",
        role_code: "AGENT_OPERATOR",
        display_name: "Agent 実行担当",
        is_built_in: false,
        archived: false,
      },
    ],
    is_bootstrap_admin: false,
  },
];

/** `GET /api/security/access-targets`（業務ビューは Run に現れた ID とロールに割り当て済みの ID）。 */
export const ACCESS_TARGETS: Json = {
  agents: [
    { id: "default", name: "汎用業務 Agent", description: "既定 Agent", status: "enabled" },
    { id: "finance", name: "経理 Agent", description: null, status: "disabled" },
  ],
  business_views: [{ id: "sales-east", name: "sales-east" }],
};
