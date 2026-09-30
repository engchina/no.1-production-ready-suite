import type { Page } from "@playwright/test";

import { ALL_PERMISSION_CODES, dbUser } from "./fixtures/auth";
import { expect, test } from "./fixtures/mock-api";

// ユーザー管理・ロール管理（3製品共通の画面）と Agent の権限管理（#215）。
// 権限管理は機能権限に加えて、利用できるエージェント・業務ビューを PUT /api/security/roles/{id}/access で保存する。
// 業務ビューは Agent にマスタがないため、一覧（Run に現れた ID と割り当て済みの ID）にない ID を直接入力して足せる。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
] as const;

const CSRF_TOKEN = "csrf-security-token";

const SECURITY_ADMIN = dbUser({
  login_user_id: "admin",
  display_name: "管理 太郎",
  role_codes: ["SYSTEM_ADMIN"],
  is_system_admin: true,
  permissions: ALL_PERMISSION_CODES,
  allowed_agent_ids: null,
  allowed_business_view_ids: null,
});

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

/** 編集の画面の保存は PageHeader の右端（#618）。 */
function editActions(page: Page) {
  return page.locator("[data-page-header-actions]");
}

for (const viewport of VIEWPORTS) {
  test.describe(`ユーザー・ロール・権限管理 (${viewport.name})`, () => {
    test.beforeEach(async ({ page, mockApi, context, baseURL }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      mockApi.setCurrentUser(SECURITY_ADMIN);
      await context.addCookies([{ name: "agent_csrf", value: CSRF_TOKEN, url: baseURL ?? "http://127.0.0.1:3042" }]);
    });

    test("ユーザー管理とロール管理を開け、ロールの詳細から権限管理へ移れる", async ({ page }) => {
      await page.goto("/settings/security/users");
      await expect(page.getByRole("heading", { name: "ユーザー管理", level: 1 })).toBeVisible();
      const users = page.getByTestId("security-users-grid");
      await expect(users.getByText("実行 花子")).toBeVisible();
      await expect(users.getByText("管理 太郎")).toBeVisible();
      await expectNoPageOverflow(page);

      await page.goto("/settings/security/roles");
      await expect(page.getByRole("heading", { name: "ロール管理", level: 1 })).toBeVisible();
      await page
        .getByTestId("security-roles-grid")
        .locator("tbody tr")
        .filter({ hasText: "Agent 実行担当" })
        .locator("td")
        .first()
        .click();
      const summary = page.getByTestId("security-roles-permission-summary");
      await expect(summary).toContainText("付与している機能権限: 2 件 / エージェント: 1 件 / 業務ビュー: 1 件");
      await expectNoPageOverflow(page);
      await summary.getByTestId("security-roles-open-permissions").click();

      await expect(page).toHaveURL(/\/settings\/security\/permissions\?role=role-operator$/);
      await expect(page.getByRole("heading", { name: "権限管理", level: 1 })).toBeVisible();
      await expectNoPageOverflow(page);
    });

    test("権限管理でエージェントと業務ビュー（直接入力の ID を含む）を保存し、Agent 管理では全件が対象になる", async ({
      page,
      mockApi,
    }) => {
      await page.goto("/settings/security/permissions?role=role-operator");
      await expect(page.getByRole("heading", { name: "権限管理", level: 1 })).toBeVisible();
      await page.getByTestId("security-permissions-detail-actions").getByRole("button", { name: "権限を編集" }).click();

      const agents = page.getByTestId("security-roles-agent-access-list");
      const views = page.getByTestId("security-roles-business-view-access-list");
      await expect(agents).toHaveAccessibleName("利用できるエージェント");
      await expect(views).toHaveAccessibleName("利用できる業務ビュー");
      // 無効なエージェントは状態を示す。保存済みの対象は選択済み。
      await expect(agents.getByText("無効")).toBeVisible();
      await expect(agents.getByRole("option", { name: /汎用業務 Agent/ })).toBeChecked();
      await expect(views.getByRole("option", { name: /sales-east/ })).toBeChecked();

      await agents.getByRole("option", { name: /経理 Agent/ }).check();

      // 一覧にない業務ビューは ID を入力して足す。形式に合わない ID は追加せず、入力欄の直下に理由を出す。
      const customId = page.getByTestId("security-roles-business-view-access-custom-id");
      await customId.fill("営業 西日本");
      await page.getByTestId("security-roles-business-view-access-custom-add").click();
      await expect(page.getByText("業務ビュー ID は英数字と . _ : - の 1～64 文字で入力してください。")).toBeVisible();
      await expect(customId).toHaveAttribute("aria-invalid", "true");
      await customId.fill("sales-west");
      // Enter でも追加でき、フォームは送信しない。
      await customId.press("Enter");
      await expect(customId).toHaveValue("");
      await expect(views.getByRole("option", { name: /sales-west/ })).toBeChecked();
      await expect(views.getByText("直接入力")).toBeVisible();
      expect(mockApi.lastRequest("PUT", "/api/security/roles/role-operator/access")).toBeUndefined();
      await expectNoPageOverflow(page);

      await editActions(page).getByRole("button", { name: "保存" }).click();
      await expect(page.getByText("変更を保存しました。")).toBeVisible();
      const first = mockApi.lastRequest("PUT", "/api/security/roles/role-operator/access");
      expect(first?.headers["x-csrf-token"]).toBe(CSRF_TOKEN);
      expect(first?.body).toMatchObject({
        version: 3,
        agent_ids: expect.arrayContaining(["default", "finance"]),
        business_view_ids: expect.arrayContaining(["sales-east", "sales-west"]),
      });
      const firstBody = first?.body as Record<string, string[]>;
      expect(firstBody.agent_ids).toHaveLength(2);
      expect(firstBody.business_view_ids).toHaveLength(2);
      expect([...firstBody.permissions].sort()).toEqual(["agent.runs.operate", "menu.agents"]);
      // 共通のロール API には送らない（権限は Agent の保存 API だけが変える）。
      expect(mockApi.requests.some((request) => request.method === "PATCH" && request.path.startsWith("/api/security/roles"))).toBe(false);

      // 保存後も編集を続けられる。Agent 管理を付けるとエージェント・業務ビューは全件が対象になり、空の一覧を送る。
      await page.getByRole("checkbox", { name: /^Agent 管理（admin）/ }).check();
      await expect(page.getByText("Agent 管理の権限により、すべてのエージェントを利用できます。個別選択は不要です。")).toBeVisible();
      await expect(page.getByText("Agent 管理の権限により、すべての業務ビューを利用できます。個別選択は不要です。")).toBeVisible();
      await expect(page.getByTestId("security-roles-agent-access-list")).toHaveCount(0);
      await expect(page.getByTestId("security-roles-business-view-access-custom-id")).toHaveCount(0);
      await editActions(page).getByRole("button", { name: "保存" }).click();

      await expect
        .poll(() => mockApi.requests.filter((request) => request.method === "PUT").length)
        .toBe(2);
      const second = mockApi.lastRequest("PUT", "/api/security/roles/role-operator/access")?.body as Record<
        string,
        unknown
      >;
      expect(second).toMatchObject({ version: 4, agent_ids: [], business_view_ids: [] });
      // implies で付くメニュー権限は送らない（backend が展開する）。
      expect([...(second.permissions as string[])].sort()).toEqual(["agent.admin", "agent.runs.operate", "menu.agents"]);
    });

    test("保存済みで候補にない業務ビューも一覧と詳細に ID のまま出す", async ({ page, mockApi }) => {
      const operator = mockApi.state.security.roles.find((role) => role.role_id === "role-operator");
      if (operator) operator.business_view_ids = ["sales-east", "legacy-view"];

      await page.goto("/settings/security/permissions?role=role-operator");
      await expect(page.getByRole("heading", { name: "権限管理", level: 1 })).toBeVisible();
      // 詳細の件数と一覧は、候補にない保存済みの ID も数える。
      await expect(page.getByText("legacy-view").first()).toBeVisible();
      await page.getByTestId("security-permissions-detail-actions").getByRole("button", { name: "権限を編集" }).click();
      const views = page.getByTestId("security-roles-business-view-access-list");
      await expect(views.getByRole("option", { name: /legacy-view/ })).toBeChecked();
    });

    test("エージェントが大量でも全件を読まず、サーバー側で検索して 50 件ずつ読む（#608）", async ({ page, mockApi }) => {
      mockApi.state.security.accessTargets.agents = Array.from({ length: 180 }, (_, index) => ({
        id: `agent-${String(index + 1).padStart(3, "0")}`,
        name: `業務 Agent ${String(index + 1).padStart(3, "0")}`,
        description: null,
        status: "enabled",
      }));

      await page.goto("/settings/security/permissions?role=role-operator");
      await expect(page.getByRole("heading", { name: "権限管理", level: 1 })).toBeVisible();
      await page.getByTestId("security-permissions-detail-actions").getByRole("button", { name: "権限を編集" }).click();
      const agents = page.getByTestId("security-roles-agent-access-list");
      await expect(agents.getByRole("option")).toHaveCount(50);
      await expect(agents).toContainText("50 / 180 件、選択 1 件");
      await agents.getByRole("button", { name: "さらに読み込む" }).click();
      await expect(agents.getByRole("option")).toHaveCount(100);
      await agents.getByRole("searchbox").fill("177");
      await expect(agents.getByRole("option")).toHaveCount(1);
      const searched = mockApi.requests.filter((request) => request.path === "/api/security/access-targets/agents").at(-1);
      expect(searched?.searchParams.get("q")).toBe("177");
      expect(searched?.searchParams.get("offset")).toBe("0");
      await agents.getByRole("option", { name: /業務 Agent 177/ }).click();
      await editActions(page).getByRole("button", { name: "保存" }).click();
      await expect
        .poll(() => (mockApi.lastRequest("PUT", "/api/security/roles/role-operator/access")?.body as { agent_ids?: string[] })?.agent_ids)
        .toEqual(["default", "agent-177"]);
      await expectNoPageOverflow(page);
    });

    test("RAG の業務ビューを読めなかったときは、候補を出したまま理由を警告で表示する（#240）", async ({
      page,
      mockApi,
    }) => {
      const warning = "RAG の業務ビューを取得できませんでした（RAG の MCP の URL が設定されていません）。";
      mockApi.state.security.accessTargets.business_view_warnings = [warning];

      await page.goto("/settings/security/permissions?role=role-operator");
      await expect(page.getByRole("heading", { name: "権限管理", level: 1 })).toBeVisible();
      // 色だけに頼らない警告（Banner はアイコン付き）と、読めた候補の一覧を両方出す。
      await expect(page.getByText(warning)).toBeVisible();
      await page.getByTestId("security-permissions-detail-actions").getByRole("button", { name: "権限を編集" }).click();
      await expect(page.getByTestId("security-roles-business-view-access-list").getByRole("option").first()).toBeVisible();
      await expectNoPageOverflow(page);
    });
  });
}
