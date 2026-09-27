import type { Page } from "@playwright/test";

import { ALL_PERMISSION_CODES, dbUser } from "./fixtures/auth";
import { expect, test, type MockApi } from "./fixtures/mock-api";

// 共通認証のログイン・パスワード変更・セッション切れ（#215）。
// production（`AGENT_AUTH_MODE=production`）の DB ユーザーでログインし、失敗・強制パスワード変更・
// 401 でログインへ戻る流れと、状態を変える API に CSRF header（Cookie `agent_csrf`）を付けることを確かめる。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile-375", width: 375, height: 812 },
] as const;

const CSRF_TOKEN = "csrf-e2e-token";

const ADMIN_USER = dbUser({
  login_user_id: "admin.user",
  display_name: "管理 太郎",
  role_codes: ["AGENT_ADMIN"],
  permissions: ALL_PERMISSION_CODES,
  allowed_agent_ids: null,
  allowed_business_view_ids: null,
});

const FIRST_USER = dbUser({
  login_user_id: "first.user",
  display_name: "初回 利用者",
  force_password_change: true,
  permissions: ["agent.runs.view"],
  allowed_agent_ids: ["default"],
});

function registerAccounts(mockApi: MockApi) {
  mockApi.state.auth.accounts["admin.user"] = { password: "CorrectPass!123", user: ADMIN_USER };
  mockApi.state.auth.accounts["first.user"] = { password: "TemporaryPass!123", user: FIRST_USER };
}

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

function sidebar(page: Page) {
  return page.getByRole("complementary", { name: "サイドナビゲーション" });
}

/** 375px ではサイドバーが icon 幅に折りたたまれる。展開して文言付きの操作を使う。 */
async function expandSidebarOnMobile(page: Page) {
  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 640) {
    await page.getByRole("button", { name: "サイドバーを展開" }).click();
  }
}

for (const viewport of VIEWPORTS) {
  test.describe(`ログインとセッション (${viewport.name})`, () => {
    test.beforeEach(async ({ page, mockApi }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      registerAccounts(mockApi);
    });

    test("未ログインで開くとログイン画面へ移り、DB ユーザーでログインすると元の画面を開く", async ({ page, mockApi }) => {
      mockApi.setCurrentUser(null);

      await page.goto("/runs");
      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByRole("heading", { name: "システムにログイン" })).toBeVisible();
      // wordmark は Agent。ログイン画面は AppShell の外（サイドナビなし）。
      await expect(page.getByText("Production Ready", { exact: true })).toBeVisible();
      await expect(page.getByText("Agent", { exact: true })).toBeVisible();
      await expect(sidebar(page)).toHaveCount(0);
      await expectNoPageOverflow(page);

      await page.getByLabel("ログインユーザーID").fill("admin.user");
      await page.getByLabel("パスワード").fill("CorrectPass!123");
      await page.getByRole("button", { name: "ログイン" }).click();

      await expect(page).toHaveURL(/\/runs$/);
      expect(mockApi.lastRequest("POST", "/api/auth/login")?.body).toEqual({
        login_user_id: "admin.user",
        password: "CorrectPass!123",
      });
      await expect(sidebar(page)).toBeVisible();
      await expect(page.getByRole("heading", { name: "Run", level: 1 })).toBeVisible();
    });

    test("ログイン失敗は入力ミスの文言だけを出し、ログイン画面に留まる", async ({ page, mockApi }) => {
      mockApi.setCurrentUser(null);

      await page.goto("/login");
      // 未入力の送信は API を呼ばずに案内する。
      await page.getByRole("button", { name: "ログイン" }).click();
      await expect(page.getByText("ログインユーザーIDとパスワードを入力してください。")).toBeVisible();
      expect(mockApi.lastRequest("POST", "/api/auth/login")).toBeUndefined();

      await page.getByLabel("ログインユーザーID").fill("admin.user");
      await page.getByLabel("パスワード").fill("WrongPass!123");
      await page.getByRole("button", { name: "ログイン" }).click();

      await expect(page.getByText("ログインユーザーIDまたはパスワードを確認してください。")).toBeVisible();
      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByLabel("ログインユーザーID")).toHaveValue("admin.user");
    });

    test("初回ログインは強制パスワード変更へ移り、変更は CSRF header 付きで送ってログインへ戻る", async ({
      page,
      context,
      baseURL,
      mockApi,
    }) => {
      mockApi.setCurrentUser(null);
      await context.addCookies([{ name: "agent_csrf", value: CSRF_TOKEN, url: baseURL ?? "http://127.0.0.1:3042" }]);

      await page.goto("/login");
      await page.getByLabel("ログインユーザーID").fill("first.user");
      await page.getByLabel("パスワード").fill("TemporaryPass!123");
      await page.getByRole("button", { name: "ログイン" }).click();

      await expect(page).toHaveURL(/\/password\/change$/);
      await expect(page.getByRole("heading", { name: "パスワードの変更" })).toBeVisible();
      await expect(sidebar(page)).toHaveCount(0);
      // 強制変更中は他の画面を開いてもパスワード変更へ戻す。
      await page.goto("/runs");
      await expect(page).toHaveURL(/\/password\/change$/);
      await expectNoPageOverflow(page);

      await page.getByLabel("現在のパスワード").fill("TemporaryPass!123");
      await page.locator("#auth-password-new").fill("IndependentPass!456");
      await page.getByLabel("新しいパスワード（確認）").fill("IndependentPass!456");
      await page.getByRole("button", { name: "パスワードを変更" }).click();

      await expect(page.getByText("パスワードを変更しました。新しいパスワードでログインしてください。")).toBeVisible();
      await expect(page).toHaveURL(/\/login$/);
      const change = mockApi.lastRequest("POST", "/api/auth/password/change");
      expect(change?.body).toEqual({ current_password: "TemporaryPass!123", new_password: "IndependentPass!456" });
      expect(change?.headers["x-csrf-token"]).toBe(CSRF_TOKEN);
      // 参照（GET）には CSRF header を付けない。
      expect(mockApi.lastRequest("GET", "/api/auth/me")?.headers["x-csrf-token"]).toBeUndefined();
    });

    test("ログイン中に API が 401 を返したらログイン画面へ戻す", async ({ page, mockApi }) => {
      mockApi.setCurrentUser(ADMIN_USER);

      await page.goto("/runs");
      await expect(page.getByRole("heading", { name: "Run", level: 1 })).toBeVisible();

      // セッションが切れた（backend が 401 を返す）状態で別の画面を開く。
      mockApi.setCurrentUser(null);
      await page.route("**/api/skills", (route) =>
        route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({ data: null, error_messages: ["ログインが必要です。"], warning_messages: [] }),
        })
      );
      await expandSidebarOnMobile(page);
      await sidebar(page).locator('a[href="/skills"]').click();

      await expect(page).toHaveURL(/\/login$/);
      await expect(page.getByRole("heading", { name: "システムにログイン" })).toBeVisible();
    });

    test("アカウント欄のパスワード変更とログアウト（ログアウトも CSRF header 付き）", async ({
      page,
      context,
      baseURL,
      mockApi,
    }) => {
      mockApi.setCurrentUser(ADMIN_USER);
      await context.addCookies([{ name: "agent_csrf", value: CSRF_TOKEN, url: baseURL ?? "http://127.0.0.1:3042" }]);

      await page.goto("/runs");
      await expandSidebarOnMobile(page);
      await expect(sidebar(page).getByText("管理 太郎")).toBeVisible();
      await sidebar(page).getByRole("button", { name: "パスワード変更" }).click();
      await expect(page).toHaveURL(/\/password\/change$/);
      // 戻るは開いていた画面へ戻す。
      await page.getByRole("button", { name: "戻る" }).click();
      await expect(page).toHaveURL(/\/runs$/);

      await expandSidebarOnMobile(page);
      await sidebar(page).getByRole("button", { name: "ログアウト" }).click();
      await expect(page).toHaveURL(/\/login$/);
      expect(mockApi.lastRequest("POST", "/api/auth/logout")?.headers["x-csrf-token"]).toBe(CSRF_TOKEN);
    });

    test("ローカル（AGENT_AUTH_MODE=local）はログインせずに全画面を使え、アカウント欄を出さない", async ({ page }) => {
      // fixture の既定はローカルの全権限の利用者。
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "ダッシュボード", level: 1 })).toBeVisible();
      await expandSidebarOnMobile(page);
      await expect(sidebar(page).getByRole("button", { name: "ログアウト" })).toHaveCount(0);
      await expect(sidebar(page).getByRole("button", { name: "パスワード変更" })).toHaveCount(0);
      await expect(sidebar(page).getByText("ローカル利用者")).toHaveCount(0);

      // ログイン画面を開いても既定の画面へ戻す。
      await page.goto("/login");
      await expect(page).toHaveURL(/\/$/);
      await expect(page.getByRole("heading", { name: "ダッシュボード", level: 1 })).toBeVisible();
    });
  });
}
