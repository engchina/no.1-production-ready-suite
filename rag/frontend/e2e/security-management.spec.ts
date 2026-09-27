import { expect, test, type Page } from "@playwright/test";

import {
  ALL_PERMISSION_CODES,
  apiEnvelope,
  expectNoPageOverflow,
  mockAuthUser,
  mockDatabaseReady,
} from "./_helpers";

// ユーザー管理・ロール管理（3製品共通の画面）と RAG の権限管理（#214）。
// 権限管理は機能権限に加えて、利用できる業務ビュー・ナレッジベースを PUT /api/security/roles/{id}/access で保存する。

const SYSTEM_ADMIN_ROLE = {
  role_id: "role-admin",
  role_code: "SYSTEM_ADMIN",
  display_name: "システム管理者",
  description: "すべての権限",
  is_built_in: true,
  archived: false,
  version: 1,
  permissions: [],
  business_view_ids: [],
  knowledge_base_ids: [],
};

const HR_ROLE = {
  role_id: "role-hr",
  role_code: "HR_USER",
  display_name: "人事利用者",
  description: "人事 FAQ を使う",
  is_built_in: false,
  archived: false,
  version: 4,
  permissions: ["menu.search", "menu.chat"],
  business_view_ids: [],
  knowledge_base_ids: [],
};

const USERS = [
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
    user_uuid: "u-hr",
    login_user_id: "hr.user",
    display_name: "人事 花子",
    status: "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 2,
    role_ids: ["role-hr"],
    assigned_roles: [
      { role_id: "role-hr", role_code: "HR_USER", display_name: "人事利用者", is_built_in: false, archived: false },
    ],
    is_bootstrap_admin: false,
  },
];

const PERMISSION_CATALOG = [
  { code: "menu.search", group: "業務ビュー", label: "RAG 検索", description: "RAG 検索を表示します。", implies: [] },
  { code: "menu.chat", group: "業務ビュー", label: "チャット", description: "チャットを表示します。", implies: [] },
  {
    code: "menu.knowledge_bases",
    group: "ナレッジ構築",
    label: "ナレッジベース",
    description: "ナレッジベースを表示します。",
    implies: [],
  },
  {
    code: "rag.knowledge_bases.manage",
    group: "管理権限",
    label: "ナレッジベース管理",
    description: "ナレッジベースの作成・アーカイブと、すべてのナレッジベースの利用ができます。",
    implies: ["menu.knowledge_bases"],
  },
];

const ACCESS_TARGETS = {
  business_views: [
    { id: "bv-hr", name: "人事 FAQ", status: "ACTIVE", description: "人事規程の問い合わせ" },
    { id: "bv-old", name: "旧経理", status: "ARCHIVED", description: null },
  ],
  knowledge_bases: [
    { id: "kb-hr", name: "人事規程", status: "ACTIVE", description: null },
    { id: "kb-finance", name: "経理規程", status: "ACTIVE", description: null },
  ],
};

async function mockSecurityApi(page: Page) {
  const saved: Record<string, unknown>[] = [];
  let accessTargetCalls = 0;
  let roleListCalls = 0;
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 404, json: { data: null, error_messages: ["not mocked"], warning_messages: [] } })
  );
  await mockDatabaseReady(page);
  await mockAuthUser(page, {
    login_user_id: "admin",
    display_name: "管理 太郎",
    role_codes: ["SYSTEM_ADMIN"],
    is_system_admin: true,
    permissions: ALL_PERMISSION_CODES,
    allowed_business_view_ids: null,
    allowed_knowledge_base_ids: null,
  });
  await page.route("**/api/security/users**", (route) => route.fulfill({ json: apiEnvelope(USERS) }));
  await page.route("**/api/security/roles**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/security/roles/role-hr/access") {
      expect(request.method()).toBe("PUT");
      const body = request.postDataJSON() as Record<string, unknown>;
      saved.push(body);
      await route.fulfill({
        json: apiEnvelope({
          ...HR_ROLE,
          version: HR_ROLE.version + 1,
          permissions: body.permissions,
          business_view_ids: body.business_view_ids,
          knowledge_base_ids: body.knowledge_base_ids,
        }),
      });
      return;
    }
    roleListCalls += 1;
    await route.fulfill({ json: apiEnvelope([SYSTEM_ADMIN_ROLE, HR_ROLE]) });
  });
  await page.route("**/api/security/permissions", (route) => route.fulfill({ json: apiEnvelope(PERMISSION_CATALOG) }));
  await page.route("**/api/security/access-targets", (route) => {
    accessTargetCalls += 1;
    return route.fulfill({ json: apiEnvelope(ACCESS_TARGETS) });
  });
  return { saved, accessTargetCalls: () => accessTargetCalls, roleListCalls: () => roleListCalls };
}

test("ユーザー管理とロール管理を開け、ロールの詳細から権限管理へ移れる", async ({ page }) => {
  await mockSecurityApi(page);

  await page.goto("/settings/security/users");
  await expect(page.getByRole("heading", { name: "ユーザー管理", level: 1 })).toBeVisible();
  const users = page.getByTestId("security-users-grid");
  await expect(users.getByText("人事 花子")).toBeVisible();
  await expect(users.getByText("管理 太郎")).toBeVisible();
  await expectNoPageOverflow(page);

  await page.goto("/settings/security/roles");
  await expect(page.getByRole("heading", { name: "ロール管理", level: 1 })).toBeVisible();
  await page.getByTestId("security-roles-grid").locator("tbody tr").filter({ hasText: "人事利用者" }).locator("td").first().click();
  const summary = page.getByTestId("security-roles-permission-summary");
  await expect(summary).toContainText("付与している機能権限: 2 件");
  await summary.getByTestId("security-roles-open-permissions").click();

  await expect(page).toHaveURL(/\/settings\/security\/permissions\?role=role-hr$/);
  await expect(page.getByRole("heading", { name: "権限管理", level: 1 })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("権限管理で業務ビューと KB を選んで保存し、KB 管理権限では KB が全件対象になる", async ({ page }) => {
  const api = await mockSecurityApi(page);

  await page.goto("/settings/security/permissions?role=role-hr");
  await expect(page.getByRole("heading", { name: "権限管理", level: 1 })).toBeVisible();
  // 業務ビューと KB の候補は 1 回の読み込みにつき 1 回の応答から作る（dev の StrictMode では読み込みが 2 回走る）。
  await expect.poll(() => api.accessTargetCalls()).toBeGreaterThan(0);
  expect(api.accessTargetCalls()).toBe(api.roleListCalls());

  await page.getByTestId("security-permissions-detail-actions").getByRole("button", { name: "権限を編集" }).click();

  const views = page.getByTestId("security-roles-business-view-access-list");
  const bases = page.getByTestId("security-roles-knowledge-base-access-list");
  await expect(views).toHaveAccessibleName("利用できる業務ビュー");
  await expect(bases).toHaveAccessibleName("利用できるナレッジベース");
  // アーカイブ済みの対象は状態を示す。
  await expect(views.getByText("アーカイブ済み")).toBeVisible();

  await views.getByRole("checkbox", { name: /人事 FAQ/ }).check();
  await bases.getByRole("checkbox", { name: /人事規程/ }).check();
  await page.getByRole("checkbox", { name: /^チャット/ }).uncheck();
  await expectNoPageOverflow(page);
  await page.getByRole("group", { name: "権限編集操作" }).getByRole("button", { name: "保存" }).click();

  await expect.poll(() => api.saved.length).toBe(1);
  expect(api.saved[0]).toEqual({
    version: 4,
    permissions: ["menu.search"],
    business_view_ids: ["bv-hr"],
    knowledge_base_ids: ["kb-hr"],
  });

  // 保存後も編集を続けられる。ナレッジベース管理を付けると KB は全件が対象になり、
  // 個別選択は出さずに空の一覧を送る。
  await expect(page.getByText("変更を保存しました。")).toBeVisible();
  await page.getByRole("checkbox", { name: /^ナレッジベース管理/ }).check();
  await expect(page.getByText("ナレッジベース管理の権限により、すべてのナレッジベースを利用できます。")).toBeVisible();
  await expect(page.getByTestId("security-roles-knowledge-base-access-list")).toHaveCount(0);
  await page.getByRole("group", { name: "権限編集操作" }).getByRole("button", { name: "保存" }).click();

  await expect.poll(() => api.saved.length).toBe(2);
  expect(api.saved[1]).toMatchObject({
    version: 5,
    business_view_ids: ["bv-hr"],
    knowledge_base_ids: [],
  });
  // implies で付く menu.knowledge_bases は送らない（backend が展開する）。
  expect((api.saved[1].permissions as string[]).sort()).toEqual(
    ["menu.search", "rag.knowledge_bases.manage"].sort()
  );
});
