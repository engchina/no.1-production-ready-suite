import { expect, test, type Page, type Route } from "@playwright/test";

import { systemAdminMe } from "./_helpers/database-gate";

const NOTICE_TITLE = "データベースを起動してください";
const NOTICE_MESSAGE =
  "データベースが起動していないか、ネットワーク経由で到達できません。データベースを起動してから再試行してください。接続情報の確認・変更もデータベース設定から行えます。";
const NOTICE_HINT =
  "OCI 認証・アップロード保存先・モデル・データベース・外観の各設定ページは引き続き利用できます。";
const RAW_DATABASE_ERROR = "Oracle に接続できませんでした (ORA-12514)。";

function envelope(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

async function fulfill(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(
      status >= 400
        ? { data: null, error_messages: [String(data)], warning_messages: [] }
        : envelope(data)
    ),
  });
}

async function mockAuthenticatedGate(page: Page, databaseUnavailable: () => boolean) {
  await page.route("**/api/auth/me", (route) => fulfill(route, systemAdminMe));
  await page.route("**/api/ready/database", (route) =>
    fulfill(route, {
      status: databaseUnavailable() ? "unreachable" : "ok",
      check: "ok",
      detail: databaseUnavailable() ? "Oracle connection probe failed (ORA-12514)." : null,
    })
  );
  await page.route("**/api/nl2sql/persistence", (route) =>
    fulfill(route, {
      mode: "oracle",
      ready: true,
      durable: true,
      writable: true,
      snapshot_loaded: true,
      reason_code: null,
      checked_at: "2026-07-21T00:00:00Z",
    })
  );
}

async function expectFullPageGate(page: Page) {
  await expect(page.getByRole("heading", { name: NOTICE_TITLE })).toBeVisible();
  await expect(page.getByText(NOTICE_MESSAGE, { exact: true })).toBeVisible();
  await expect(page.getByText(NOTICE_HINT, { exact: true })).toBeVisible();
  await expect(page.getByText(/ORA-12514/)).toHaveCount(0);
  await expect(page.getByText(RAW_DATABASE_ERROR, { exact: true })).toHaveCount(0);
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toBeVisible();

  const settingsLink = page.getByRole("link", { name: "データベース設定を開く" });
  const retry = page.getByRole("button", { name: "再試行" });
  await expect(settingsLink).toHaveAttribute("href", "/settings/database#adb-management");
  await settingsLink.focus();
  await expect(settingsLink).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(retry).toBeFocused();

  await expect
    .poll(() =>
      page.evaluate(
        () =>
          document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1 &&
          document.body.scrollWidth <= document.body.clientWidth + 1
      )
    )
    .toBeTruthy();
}

type Scenario = {
  name: string;
  path: string;
  heading: string;
  setup?: (page: Page, countRequest: () => void) => Promise<void>;
};

const databaseIndependentScenarios: Scenario[] = [
  {
    name: "OCI 認証設定",
    path: "/settings/oci",
    heading: "OCI 認証設定",
    setup: async (page, countRequest) => {
      await page.route("**/api/settings/oci", (route) => {
        countRequest();
        return fulfill(route, "OCI 設定を読み込めませんでした。", 500);
      });
      await page.route("**/api/settings/upload-storage", (route) =>
        fulfill(route, "保存先設定を読み込めませんでした。", 500)
      );
    },
  },
  {
    name: "アップロード保存先",
    path: "/settings/upload-storage",
    heading: "アップロード保存先",
    setup: async (page, countRequest) => {
      await page.route("**/api/settings/upload-storage", (route) => {
        countRequest();
        return fulfill(route, "保存先設定を読み込めませんでした。", 500);
      });
    },
  },
  {
    name: "モデル設定",
    path: "/settings/model",
    heading: "モデル設定",
    setup: async (page, countRequest) => {
      await page.route("**/api/settings/model", (route) => {
        countRequest();
        return fulfill(route, "モデル設定を読み込めませんでした。", 500);
      });
    },
  },
  {
    name: "外観",
    path: "/settings/appearance",
    heading: "外観",
  },
];

const databaseDependentScenarios: Scenario[] = [
  {
    name: "ユーザー管理",
    path: "/settings/security/users",
    heading: "ユーザー管理",
    setup: async (page, countRequest) => {
      await page.route("**/api/security/users", (route) => {
        countRequest();
        return fulfill(route, []);
      });
      await page.route("**/api/security/roles?include_archived=false", (route) =>
        fulfill(route, [])
      );
    },
  },
  {
    name: "ロール・権限管理",
    path: "/settings/security/roles",
    heading: "ロール・権限管理",
    setup: async (page, countRequest) => {
      await page.route("**/api/security/roles?include_archived=true", (route) => {
        countRequest();
        return fulfill(route, []);
      });
      await page.route("**/api/security/permissions", (route) => fulfill(route, []));
    },
  },
  {
    name: "監査ログ",
    path: "/settings/security/audit",
    heading: "監査ログ",
    setup: async (page, countRequest) => {
      await page.route(/\/api\/security\/audit\/page(?:\?.*)?$/, (route) => {
        countRequest();
        return fulfill(route, {
          items: [],
          page: 1,
          page_size: 10,
          total: 0,
          total_pages: 1,
        });
      });
    },
  },
  {
    name: "Deep Data Security",
    path: "/settings/security/deepsec",
    heading: "Deep Data Security",
    setup: async (page, countRequest) => {
      await page.route("**/api/security/deepsec/status", (route) => {
        countRequest();
        return fulfill(route, {
          configured: false,
          driver_mode: "thin",
          deepsec_enabled: false,
          end_user: "NL2SQL_APP_END_USER",
          objects: {},
          message: "未適用です。",
        });
      });
      await page.route("**/api/security/deepsec/plan", (route) =>
        fulfill(route, {
          version: "V001",
          driver_mode: "thin",
          deepsec_enabled: false,
          end_user: "NL2SQL_APP_END_USER",
          steps: [],
        })
      );
    },
  },
  {
    name: "NL2SQL 安全境界・Readiness",
    path: "/settings/nl2sql-database",
    heading: "NL2SQL 安全境界・Readiness",
    setup: async (page, countRequest) => {
      await page.route("**/api/schema/catalog", (route) => {
        countRequest();
        return fulfill(route, { refreshed_at: "2026-07-21T00:00:00Z", tables: [] });
      });
      await page.route("**/api/nl2sql/diagnostics", (route) =>
        fulfill(route, { checks: [], readiness: [] })
      );
    },
  },
];

for (const scenario of databaseIndependentScenarios) {
  test(`${scenario.name}はDB未到達でも元の画面を表示する`, async ({ page }) => {
    let pageRequests = 0;
    await mockAuthenticatedGate(page, () => true);
    await scenario.setup?.(page, () => {
      pageRequests += 1;
    });

    await page.goto(scenario.path);

    await expect(
      page.getByRole("heading", { name: scenario.heading, exact: true, level: 1 })
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: NOTICE_TITLE })).toHaveCount(0);
    if (scenario.setup) await expect.poll(() => pageRequests).toBeGreaterThan(0);
    await expect
      .poll(() =>
        page.evaluate(
          () =>
            document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1 &&
            document.body.scrollWidth <= document.body.clientWidth + 1
        )
      )
      .toBeTruthy();
  });
}

test("設定入口はDB未到達でもOCI認証設定へ遷移する", async ({ page }) => {
  await mockAuthenticatedGate(page, () => true);
  await page.route("**/api/settings/oci", (route) =>
    fulfill(route, "OCI 設定を読み込めませんでした。", 500)
  );
  await page.route("**/api/settings/upload-storage", (route) =>
    fulfill(route, "保存先設定を読み込めませんでした。", 500)
  );

  await page.goto("/settings");

  await expect(page).toHaveURL(/\/settings\/oci$/);
  await expect(
    page.getByRole("heading", { name: "OCI 認証設定", exact: true, level: 1 })
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: NOTICE_TITLE })).toHaveCount(0);
});

for (const scenario of databaseDependentScenarios) {
  test(`${scenario.name}ではDB未到達時にページ内容を描画せず、再試行後に復帰する`, async ({
    page,
  }) => {
    let unavailable = true;
    let pageRequests = 0;
    await mockAuthenticatedGate(page, () => unavailable);
    await scenario.setup?.(page, () => {
      pageRequests += 1;
    });

    await page.goto(scenario.path);

    await expectFullPageGate(page);
    await expect(
      page.getByRole("heading", { name: scenario.heading, exact: true, level: 1 })
    ).toHaveCount(0);
    expect(pageRequests).toBe(0);

    unavailable = false;
    await page.getByRole("button", { name: "再試行" }).click();
    await expect(page.getByRole("heading", { name: NOTICE_TITLE })).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: scenario.heading, exact: true, level: 1 })
    ).toBeVisible();
    if (scenario.setup) await expect.poll(() => pageRequests).toBeGreaterThan(0);
  });
}

test("稼働中の読込5xxでDB停止を確認した場合も表示中ページを全画面ゲートへ置換する", async ({
  page,
}) => {
  let unavailable = false;
  let rolesFail = false;
  await mockAuthenticatedGate(page, () => unavailable);
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    rolesFail ? fulfill(route, RAW_DATABASE_ERROR, 503) : fulfill(route, [])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));

  await page.goto("/settings/security/roles");
  await expect(page.getByRole("heading", { name: "ロール・権限管理" })).toBeVisible();

  unavailable = true;
  rolesFail = true;
  await page.getByRole("button", { name: "再読込" }).click();

  await expectFullPageGate(page);
  await expect(page.getByRole("heading", { name: "ロール・権限管理" })).toHaveCount(0);
  await expect(page.getByText(RAW_DATABASE_ERROR, { exact: true })).toHaveCount(0);

  unavailable = false;
  rolesFail = false;
  await page.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByRole("heading", { name: "ロール・権限管理" })).toBeVisible();
});

test("DB が正常な一般500は画面固有エラーを維持する", async ({ page }) => {
  const serverError = "サーバー内部でエラーが発生しました。時間をおいて再度お試しください。";
  await mockAuthenticatedGate(page, () => false);
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, serverError, 500)
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));

  await page.goto("/settings/security/roles");

  await expect(page.getByText(serverError, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: NOTICE_TITLE })).toHaveCount(0);
});
