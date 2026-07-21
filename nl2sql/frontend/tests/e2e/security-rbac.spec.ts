import { expect, test, type Route } from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";

function envelope(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

async function fulfill(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(status >= 400 ? { detail: data } : envelope(data)),
  });
}

const systemRole = {
  role_id: "role-system",
  role_code: "SYSTEM_ADMIN",
  display_name: "システム管理者",
  description: "組み込み",
  is_built_in: true,
  archived: false,
  version: 1,
  permissions: [],
  data_entitlements: [
    {
      entitlement_id: "ent-full",
      resource_code: "NL2SQL_DEEPSEC_PROBE",
      scope_code: "*",
      capability: "FULL",
    },
  ],
};

test("ローカル DEBUG はログインせず SYSTEM_ADMIN として入り、状態を明示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      user_id: "00000000-0000-0000-0000-000000000000",
      login_name: "local-debug",
      display_name: "ローカル DEBUG 管理者",
      debug_mode: true,
    })
  );

  await page.goto("/settings/appearance");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await expect(page).toHaveURL(/\/settings\/appearance$/);
  await expect(page.getByRole("heading", { name: "システムにログイン" })).toHaveCount(0);
  await expect(
    sidebar.getByRole("status", {
      name: "ローカル DEBUG：ログイン省略・SYSTEM_ADMIN 権限",
    })
  ).toBeVisible();
  const debugColors = await sidebar
    .getByRole("status", { name: "ローカル DEBUG：ログイン省略・SYSTEM_ADMIN 権限" })
    .evaluate((element) => {
      const style = getComputedStyle(element);
      return { backgroundColor: style.backgroundColor, color: style.color };
    });
  expect(debugColors).toEqual({ backgroundColor: "rgb(255, 251, 235)", color: "rgb(120, 53, 15)" });
  await expect(sidebar.getByRole("button", { name: "パスワード変更" })).toHaveCount(0);
  await expect(sidebar.getByRole("button", { name: "ログアウト" })).toHaveCount(0);
  const viewport = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.clientWidth);
});

test("ログイン失敗を一般化して表示し、初回パスワード変更へ誘導する", async ({ page }) => {
  let loginAttempts = 0;
  await page.route("**/api/auth/me", (route) => fulfill(route, "ログインしてください。", 401));
  await page.route("**/api/auth/login", async (route) => {
    loginAttempts += 1;
    if (loginAttempts === 1) {
      await fulfill(route, "ログイン名またはパスワードを確認してください。", 401);
      return;
    }
    await fulfill(route, { ...systemAdminMe, force_password_change: true });
  });
  await page.route("**/api/auth/password/change", (route) => fulfill(route, { changed: true }));

  await page.goto("/login");
  await page.getByLabel("ログイン名").fill("SYSTEM");
  await page.getByLabel("パスワード").fill("WrongPass!123");
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByText("ログイン名またはパスワードを確認してください。", { exact: true })).toBeVisible();

  await page.getByLabel("パスワード").fill("BootstrapPass!123");
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByRole("heading", { name: "パスワードの変更" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toHaveCount(0);

  await page.getByLabel("現在のパスワード").fill("BootstrapPass!123");
  await page.getByLabel("新しいパスワード", { exact: true }).fill("IndependentPass!456");
  await page.getByLabel("新しいパスワード（確認）").fill("IndependentPass!456");
  await page.getByRole("button", { name: "パスワードを変更" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("表示権限だけのユーザーはメニューと直達 URL/API の双方で制限される", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const limited = {
    ...systemAdminMe,
    user_id: "limited",
    login_name: "limited.user",
    display_name: "検索閲覧ユーザー",
    role_codes: ["QUERY_VIEWER"],
    permissions: ["search.view"],
  };
  await page.route("**/api/auth/me", (route) => fulfill(route, limited));
  await page.route("**/api/security/users", (route) =>
    fulfill(route, "この機能を利用する権限がありません。", 403)
  );
  await page.goto("/query");
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await expect(sidebar.getByRole("link", { name: "SQL 生成" })).toBeVisible();
  await expect(page.getByText("このページは参照できますが、SQL の生成・実行には「検索を実行」権限が必要です。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "実行" })).toHaveCount(0);
  await expect(sidebar.getByText("ユーザー管理", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("セキュリティ管理", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("SELECT SQL を直接実行", { exact: true })).toHaveCount(0);

  const apiStatus = await page.evaluate(async () => (await fetch("/api/security/users")).status);
  expect(apiStatus).toBe(403);

  await page.goto("/settings/security/users");
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();
});

test("管理者がユーザーを作成して複数ロールを割り当て、一時パスワードを一度だけ確認する", async ({ page, context }) => {
  await mockDatabaseGateReady(page);
  await context.addCookies([{ name: "nl2sql_csrf", value: "csrf-token", url: "http://127.0.0.1:3101" }]);
  let csrfObserved = false;
  let users = [
    {
      user_id: "admin-user",
      login_name: "SYSTEM",
      display_name: "システム管理者",
      status: "ACTIVE",
      force_password_change: false,
      locked_until: null,
      version: 1,
      role_ids: ["role-system"],
    },
  ];
  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "QUERY_VIEWER",
    display_name: "検索閲覧",
    is_built_in: false,
    permissions: ["search.view"],
    data_entitlements: [],
  };
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [systemRole, viewerRole])
  );
  await page.route("**/api/security/users", async (route) => {
    if (route.request().method() === "GET") {
      await fulfill(route, users);
      return;
    }
    csrfObserved = route.request().headers()["x-csrf-token"] === "csrf-token";
    const payload = route.request().postDataJSON() as {
      login_name: string;
      display_name: string;
      role_ids: string[];
    };
    const user = {
      user_id: "new-user",
      login_name: payload.login_name,
      display_name: payload.display_name,
      status: "ACTIVE",
      force_password_change: true,
      locked_until: null,
      version: 1,
      role_ids: payload.role_ids,
    };
    users = [...users, user];
    await fulfill(route, { user, temporary_password: "RandomStrong!Pass123" });
  });

  await page.goto("/settings/security/users");
  await page.getByLabel("ログイン名").fill("sales.user");
  await page.getByLabel("表示名").fill("営業ユーザー");
  await page.getByLabel("システム管理者").check();
  await page.getByLabel("検索閲覧").check();
  await page.getByRole("button", { name: "新規作成", exact: true }).last().click();

  await expect(page.getByText("一時パスワードは今回だけ表示されます。安全な方法で利用者へ伝えてください。", { exact: true })).toBeVisible();
  await expect(page.getByText("RandomStrong!Pass123", { exact: true })).toBeVisible();
  expect(csrfObserved).toBe(true);
});

test("DeepSec は版管理 SQL を読み取り専用で順次適用し、検証結果を表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let applied = false;
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: applied,
      driver_mode: "thin",
      deepsec_enabled: true,
      end_user: "NL2SQL_APP_END_USER",
      objects: applied ? { data_grants: 2 } : {},
      message: applied ? "Deep Data Security の検証オブジェクトは構成済みです。" : "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) =>
    fulfill(route, {
      version: "V001",
      driver_mode: "thin",
      deepsec_enabled: true,
      end_user: "NL2SQL_APP_END_USER",
      steps: [
        {
          step_no: 1,
          key: "principals_and_roles",
          title: "共有 END USER とロール",
          description: "共有 local END USER と最小権限ロールを構成します。",
          checksum: "a".repeat(64),
          status: applied ? "APPLIED" : "PENDING",
          error_message: "",
          executed_at: applied ? "2026-07-19T00:00:00Z" : null,
          sql: ["CREATE END USER NL2SQL_APP_END_USER IDENTIFIED BY <secret:ORACLE_DEEPSEC_END_USER_PASSWORD>"],
        },
      ],
    })
  );
  await page.route("**/api/security/deepsec/plan/V001/steps/1/apply", async (route) => {
    applied = true;
    await fulfill(route, { version: "V001", step_no: 1, status: "APPLIED" });
  });
  await page.route("**/api/security/deepsec/verify", (route) =>
    fulfill(route, {
      version: "V001",
      passed: true,
      checked_at: "2026-07-19T00:00:00Z",
      checks: [
        { key: "no_context", passed: true, detail: "context 未設定の取得行数: 0" },
        { key: "limited_subject", passed: true, detail: "sensitive_masked=true" },
      ],
    })
  );

  await page.goto("/settings/security/deepsec");
  await expect(page.locator("pre")).toHaveCount(1);
  await expect(page.locator("textarea")).toHaveCount(0);
  await expect(page.getByText("<secret:ORACLE_DEEPSEC_END_USER_PASSWORD>", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "このステップを適用" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "実行" }).click();
  await expect(page.getByText("適用済み", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Data Grant を検証" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "実行" }).click();
  await expect(page.getByText("no_context", { exact: true })).toBeVisible();
  await expect(page.getByText("sensitive_masked=true", { exact: true })).toBeVisible();

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});
