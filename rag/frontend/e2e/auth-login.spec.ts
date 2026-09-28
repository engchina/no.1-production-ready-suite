import { expect, test, type Page, type Route } from "@playwright/test";

import {
  ALL_PERMISSION_CODES,
  apiEnvelope,
  dbUser,
  expectNoPageOverflow,
  mockDatabaseReady,
  type CurrentUserPayload,
} from "./_helpers";

// 共通認証のログイン・パスワード変更・セッション切れ（#214）。
// DB ユーザーでログインし、失敗・強制パスワード変更・401 でログインへ戻る流れを確かめる。

const unauthorized = { data: null, error_messages: ["ログインが必要です。"], warning_messages: [] };

/** ログイン状態を持つ `/api/auth/*` の mock。null は未ログイン（me が 401）。 */
async function mockAuthApi(page: Page, initial: CurrentUserPayload | null) {
  const state = {
    current: initial,
    loginCalls: [] as Record<string, unknown>[],
    passwordCalls: [] as { body: Record<string, unknown>; csrf: string | null }[],
    logoutCalls: 0,
  };
  await page.route("**/api/auth/**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/auth/me") {
      await (state.current
        ? route.fulfill({ json: apiEnvelope(state.current) })
        : route.fulfill({ status: 401, json: unauthorized }));
      return;
    }
    if (path === "/api/auth/login") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.loginCalls.push(body);
      if (body.password === "WrongPass!123") {
        await route.fulfill({
          status: 401,
          json: {
            data: null,
            error_messages: ["ログインユーザーIDまたはパスワードを確認してください。"],
            warning_messages: [],
            error_code: "SECURITY_AUTHENTICATION_REQUIRED",
          },
        });
        return;
      }
      state.current = loginTarget(String(body.login_user_id));
      await route.fulfill({ json: apiEnvelope(state.current) });
      return;
    }
    if (path === "/api/auth/password/change") {
      state.passwordCalls.push({
        body: request.postDataJSON() as Record<string, unknown>,
        csrf: request.headers()["x-csrf-token"] ?? null,
      });
      // 変更後はセッションを失効させ、新しいパスワードでのログインを求める。
      state.current = null;
      await route.fulfill({ json: apiEnvelope({ changed: true }) });
      return;
    }
    if (path === "/api/auth/logout") {
      state.logoutCalls += 1;
      state.current = null;
      await route.fulfill({ json: apiEnvelope({ logged_out: true }) });
      return;
    }
    await route.fulfill({ status: 404, json: unauthorized });
  });
  return state;
}

function loginTarget(loginUserId: string): CurrentUserPayload {
  if (loginUserId === "first.user") {
    return dbUser({
      login_user_id: "first.user",
      display_name: "初回 利用者",
      force_password_change: true,
      permissions: ["menu.search"],
    });
  }
  return dbUser({
    login_user_id: loginUserId,
    display_name: "管理 太郎",
    role_codes: ["RAG_ADMIN"],
    permissions: ALL_PERMISSION_CODES,
    allowed_business_view_ids: null,
    allowed_knowledge_base_ids: null,
  });
}

test.beforeEach(async ({ page }) => {
  // 未モックの API はログインを求めない 200 の空応答にし、画面の描画だけを確かめる。
  await page.route("**/api/**", (route) =>
    route.fulfill({ json: { data: null, error_messages: [], warning_messages: [] } })
  );
  await mockDatabaseReady(page);
});

test("未ログインで開くとログイン画面へ移り、DB ユーザーでログインすると元の画面を開く", async ({ page }) => {
  const auth = await mockAuthApi(page, null);

  await page.goto("/file-list");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "システムにログイン" })).toBeVisible();
  // wordmark は RAG。
  await expect(page.getByText("Production Ready", { exact: true })).toBeVisible();
  await expect(page.getByText("RAG", { exact: true })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toHaveCount(0);
  await expectNoPageOverflow(page);

  await page.getByLabel("ログインユーザーID").fill("admin.user");
  await page.getByLabel("パスワード").fill("CorrectPass!123");
  await page.getByRole("button", { name: "ログイン" }).click();

  await expect(page).toHaveURL(/\/file-list$/);
  expect(auth.loginCalls).toEqual([{ login_user_id: "admin.user", password: "CorrectPass!123" }]);
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await expect(sidebar).toBeVisible();
});

test("ログイン失敗は入力ミスの文言だけを出し、ログイン画面に留まる", async ({ page }) => {
  const auth = await mockAuthApi(page, null);

  await page.goto("/login");
  // 未入力の送信は API を呼ばずに案内する。
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByText("ログインユーザーIDとパスワードを入力してください。")).toBeVisible();
  expect(auth.loginCalls).toHaveLength(0);

  await page.getByLabel("ログインユーザーID").fill("admin.user");
  await page.getByLabel("パスワード").fill("WrongPass!123");
  await page.getByRole("button", { name: "ログイン" }).click();

  await expect(page.getByText("ログインユーザーIDまたはパスワードを確認してください。")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("ログインユーザーID")).toHaveValue("admin.user");
});

test("初回ログインは強制パスワード変更へ移り、変更後は CSRF 付きで送ってログインへ戻る", async ({
  page,
  context,
  baseURL,
}) => {
  const auth = await mockAuthApi(page, null);
  await context.addCookies([{ name: "rag_csrf", value: "csrf-e2e-token", url: baseURL ?? "http://127.0.0.1:3100" }]);

  await page.goto("/login");
  await page.getByLabel("ログインユーザーID").fill("first.user");
  await page.getByLabel("パスワード").fill("TemporaryPass!123");
  await page.getByRole("button", { name: "ログイン" }).click();

  await expect(page).toHaveURL(/\/password\/change$/);
  await expect(page.getByRole("heading", { name: "パスワードの変更" })).toBeVisible();
  await expect(page.getByRole("button", { name: "ログインへ戻る" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toHaveCount(0);
  // 強制変更中は他の画面を開いてもパスワード変更へ戻す。
  await page.goto("/search");
  await expect(page).toHaveURL(/\/password\/change$/);
  await expectNoPageOverflow(page);

  await page.getByLabel("現在のパスワード").fill("TemporaryPass!123");
  await page.locator("#auth-password-new").fill("IndependentPass!456");
  await page.getByLabel("新しいパスワード（確認）").fill("IndependentPass!456");
  await page.getByRole("button", { name: "パスワードを変更" }).click();

  await expect(page.getByText("パスワードを変更しました。新しいパスワードでログインしてください。")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
  expect(auth.passwordCalls).toEqual([
    {
      body: { current_password: "TemporaryPass!123", new_password: "IndependentPass!456" },
      csrf: "csrf-e2e-token",
    },
  ]);
});

test("ログイン中に API が 401 を返したらログイン画面へ戻す", async ({ page }) => {
  await mockAuthApi(page, loginTarget("admin.user"));
  let expired = false;
  await page.route("**/api/documents**", (route) =>
    expired
      ? route.fulfill({ status: 401, json: unauthorized })
      : route.fulfill({
          json: apiEnvelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }),
        })
  );

  await page.goto("/settings/appearance");
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await expect(sidebar).toBeVisible();

  expired = true;
  await sidebar.getByRole("link", { name: "文書インデックス" }).click();

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "システムにログイン" })).toBeVisible();
});

test("アカウント欄のパスワード変更とログアウト（ローカル DEBUG ではアカウント欄を出さない）", async ({ page }) => {
  const auth = await mockAuthApi(page, loginTarget("admin.user"));

  await page.goto("/settings/appearance");
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 640) {
    // 375px ではサイドバーが icon 幅に折りたたまれる。展開して文言付きの操作を使う。
    await page.getByRole("button", { name: "サイドバーを展開" }).click();
  }
  await expect(sidebar.getByText("管理 太郎")).toBeVisible();
  await sidebar.getByRole("button", { name: "パスワード変更" }).click();
  await expect(page).toHaveURL(/\/password\/change$/);
  await page.getByRole("button", { name: "戻る" }).click();
  await expect(page).toHaveURL(/\/settings\/appearance$/);

  if (viewport && viewport.width <= 640) {
    await page.getByRole("button", { name: "サイドバーを展開" }).click();
  }
  await sidebar.getByRole("button", { name: "ログアウト" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(auth.logoutCalls).toBe(1);
});

test("ローカル DEBUG はログインせずに全画面を使え、アカウント欄を出さない", async ({ page }) => {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: apiEnvelope({
        ...dbUser({
          user_uuid: "00000000-0000-0000-0000-000000000000",
          login_user_id: "local",
          display_name: "ローカル利用者",
          role_codes: ["SYSTEM_ADMIN"],
          is_system_admin: true,
          permissions: ALL_PERMISSION_CODES,
          allowed_business_view_ids: null,
          allowed_knowledge_base_ids: null,
          password_change_allowed: false,
        }),
        debug_mode: true,
      }),
    })
  );

  // `/` はナビの並びで最初に開ける画面（RAG 検索）へ。
  await page.goto("/");
  await expect(page).toHaveURL(/\/search$/);
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await expect(sidebar.getByRole("button", { name: "ログアウト" })).toHaveCount(0);
  await expect(sidebar.getByRole("button", { name: "パスワード変更" })).toHaveCount(0);
  await expect(sidebar.getByText("ローカル利用者")).toHaveCount(0);

  // ログイン画面を開いても既定の入口（RAG 検索）へ戻す。
  await page.goto("/login");
  await expect(page).toHaveURL(/\/search$/);
});
