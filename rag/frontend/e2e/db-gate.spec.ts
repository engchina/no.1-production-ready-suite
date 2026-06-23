import { expect, test, type Page } from "@playwright/test";

/**
 * DB ゲート: 設定ページ以外は、DB 接続不可/未設定のとき
 * エラー画面ではなく「DB 接続を確認/設定してください」案内を表示し、
 * データベース設定への導線を出す。設定ページはゲートを通さない。
 */

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

function dbStatus(status: "ok" | "not_configured" | "unreachable") {
  return {
    data: { status, check: status === "not_configured" ? "missing" : "ok", detail: null },
    error_messages: [],
    warning_messages: [],
  };
}

async function routeAuth(page: Page) {
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
}

test("DB 接続不可時、機能ページはエラーではなく確認案内を表示する", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("unreachable") })
  );
  // ダッシュボード本体 API は叩かれない想定だが、保険で 500 を返しておく
  await page.route("**/api/dashboard/summary", (route) =>
    route.fulfill({ status: 500, json: { data: null, error_messages: ["boom"], warning_messages: [] } })
  );

  await page.goto("/dashboard");

  await expect(page.getByRole("heading", { name: "データベースに接続できません" })).toBeVisible();
  // 全画面エラー(サーバー内部エラー)ではないこと
  await expect(page.getByText("サーバー内部でエラーが発生しました")).toHaveCount(0);

  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  const settingsLink = page.getByRole("link", { name: /データベース設定を開く/ });
  await expect(settingsLink).toHaveAttribute("href", "/settings/database");
});

test("DB 未設定時は接続情報の設定を促す(再試行は出さない)", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("not_configured") })
  );

  await page.goto("/file-list");

  await expect(
    page.getByRole("heading", { name: "データベースの接続情報が未設定です" })
  ).toBeVisible();
  const settingsLink = page.getByRole("link", { name: /データベース設定を開く/ });
  await expect(settingsLink).toHaveAttribute("href", "/settings/database");
  // 未設定は再試行不要(設定すれば解消)
  await expect(page.getByRole("button", { name: "再試行" })).toHaveCount(0);
});

test("設定ページは DB が無くてもゲートを通って到達できる", async ({ page }) => {
  await routeAuth(page);
  // 設定ページではゲート用 API を叩かない（叩いたら失敗させて検知）
  await page.route("**/api/ready/database", (route) =>
    route.fulfill({ json: dbStatus("unreachable") })
  );

  await page.goto("/settings/database");

  // ゲートに塞がれず、データベース設定ページ自体が表示される
  await expect(page.getByRole("heading", { name: "データベース設定" })).toBeVisible();
});

test("DB 利用可能時は本来のページを表示する", async ({ page }) => {
  await routeAuth(page);
  await page.route("**/api/ready/database", (route) => route.fulfill({ json: dbStatus("ok") }));
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );

  await page.goto("/knowledge-bases");

  await expect(page.getByRole("heading", { name: "ナレッジベース" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "データベースに接続できません" })
  ).toHaveCount(0);
});
