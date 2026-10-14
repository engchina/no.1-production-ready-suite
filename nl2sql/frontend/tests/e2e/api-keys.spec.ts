import { expect, test, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { openSidebarNav } from "./_helpers/sidebar-nav";

// 外部の MCP クライアント向けの API キー（RAG / NL2SQL 共通の画面。#1805）。
// 自分のキーはアカウントのメニューから開き（メニュー権限なし）、全員のキーは「ユーザーとロール」の管理の画面。

const TOKEN = "prak_0123456789abcdef_" + "S".repeat(43);

function envelope(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

async function fulfill(route: Route, data: unknown) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(envelope(data)) });
}

function apiKey(overrides: Record<string, unknown> = {}) {
  return {
    id: "0123456789abcdef",
    name: "会社の PC の Claude Code",
    token_prefix: "prak_0123456789abcdef_SSSS",
    products: ["nl2sql"],
    owner_user_uuid: "u-1",
    owner_display_name: "営業 一郎",
    created_at: "2026-10-14T00:00:00Z",
    expires_at: null,
    last_used_at: "2026-10-14T03:00:00Z",
    expired: false,
    ...overrides,
  };
}

async function mockUser(page: Page, permissions: string[]) {
  await mockDatabaseGateReady(page);
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      user_uuid: "u-1",
      login_user_id: "sales.user",
      display_name: "営業 一郎",
      role_codes: ["SALES"],
      role_display_names: ["営業"],
      is_system_admin: false,
      permissions,
    })
  );
}

test.describe("API キー", { tag: "@responsive" }, () => {
  test("アカウントのメニューから自分のキーを発行し、秘密を 1 回だけ見る（メニュー権限なし）", async ({ page }) => {
    await mockUser(page, []);
    const created: unknown[] = [];
    const keys: ReturnType<typeof apiKey>[] = [];
    await page.route("**/api/account/api-keys**", async (route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as { name: string; products: string[] };
        created.push(body);
        const item = apiKey({ name: body.name, products: body.products, last_used_at: null });
        keys.unshift(item);
        return fulfill(route, { key: item, token: TOKEN });
      }
      return fulfill(route, { keys });
    });

    // 自分のキーの画面はアカウントのメニューの項目（ナビのリンクではない）。メニュー権限を問わずに開ける。
    await page.goto("/account/api-keys");
    const sidebar = await openSidebarNav(page);
    await expect(sidebar.getByRole("button", { name: "API キー" })).toHaveAttribute("aria-current", "page");
    await expect(sidebar.getByRole("link", { name: "API キー" })).toHaveCount(0);
    if (await page.getByTestId("nav-drawer-trigger").isVisible()) await page.keyboard.press("Escape");
    await expect(page.getByRole("heading", { name: "API キー", level: 1 })).toBeVisible();
    await expect(page.getByTestId("api-keys-endpoint-nl2sql")).toContainText("/api/mcp");
    // 発行の既定の製品は、開いている製品によらず RAG と NL2SQL の両方（#1869）。
    await expect(page.getByTestId("api-key-product-nl2sql")).toBeChecked();
    await expect(page.getByTestId("api-key-product-rag")).toBeChecked();

    await page.getByLabel("名前（用途）").fill("会社の PC の Claude Code");
    await page.getByTestId("api-key-create").click();
    await expect(page.getByTestId("api-key-token")).toHaveValue(TOKEN);
    expect(created).toEqual([{ name: "会社の PC の Claude Code", products: ["rag", "nl2sql"], expires_in_days: 90 }]);
    const list = page.getByRole("table", { name: "API キーの一覧" });
    await expect(list.getByText("会社の PC の Claude Code")).toBeVisible();
    await expect(list).not.toContainText(TOKEN);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1))
      .toBe(true);
  });

  test("管理の画面は全員のキーを出し、失効できる", async ({ page }) => {
    await mockUser(page, ["menu.security_api_keys"]);
    const revoked: string[] = [];
    let keys = [apiKey(), apiKey({ id: "fedcba9876543210", name: "CI", owner_display_name: "運用 花子" })];
    await page.route("**/api/security/api-keys**", async (route) => {
      if (route.request().method() === "DELETE") {
        const id = new URL(route.request().url()).pathname.split("/").pop() ?? "";
        revoked.push(id);
        keys = keys.filter((item) => item.id !== id);
        return fulfill(route, { id });
      }
      return fulfill(route, { keys });
    });

    await page.goto("/settings/security/api-keys");
    await expect(page.getByRole("heading", { name: "API キー", level: 1 })).toBeVisible();
    const list = page.getByRole("table", { name: "API キーの一覧" });
    await expect(list.getByText("運用 花子")).toBeVisible();
    await expect(page.getByTestId("api-key-create")).toHaveCount(0);

    await page.getByTestId("api-key-row-actions-fedcba9876543210-trigger").click();
    await page.getByRole("menuitem", { name: "失効" }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("運用 花子 さんのキーです。");
    await dialog.getByRole("button", { name: "失効" }).click();
    await expect(list.getByText("運用 花子")).toHaveCount(0);
    expect(revoked).toEqual(["fedcba9876543210"]);
  });
});
