import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { expectSplitPaneReservedTrack } from "./_helpers/fixed-split-pane";

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

async function topLevelPanelStyle(page: Page, id: string, prefix: "security-users" | "security-roles") {
  const panel = page.locator(`#${prefix}-panel-${id}`);
  await expect(panel).toBeVisible();
  return panel.evaluate((node) => {
    const style = window.getComputedStyle(node);
    return {
      backgroundColor: style.backgroundColor,
      borderTopWidth: style.borderTopWidth,
      borderRadius: style.borderRadius,
      paddingTop: style.paddingTop,
      boxShadow: style.boxShadow,
    };
  });
}

async function expectNoPageHorizontalScroll(page: Page) {
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

async function expectFloatingMenuInsideViewport(page: Page, menu: Locator) {
  await expect(menu).toBeVisible();
  const [box, viewport] = await Promise.all([menu.boundingBox(), page.viewportSize()]);
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width + 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport!.height + 1);
  await expect
    .poll(() =>
      menu.evaluate((node) => ({
        constrained: node.getAttribute("data-floating-menu-constrained"),
        fitsWithoutScrollbar: node.scrollHeight <= node.clientHeight + 1,
      }))
    )
    .toEqual({ constrained: null, fitsWithoutScrollbar: true });
}

async function sidebarComparableStyle(locator: Locator) {
  await expect(locator).toBeVisible();
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return {
      height: Math.round(rect.height),
      borderRadius: style.borderRadius,
      fontSize: style.fontSize,
      paddingLeft: style.paddingLeft,
      paddingRight: style.paddingRight,
      transitionProperty: style.transitionProperty,
    };
  });
}

async function expectSidebarActionMatchesMenuItem(action: Locator, menuItem: Locator) {
  const [actionStyle, menuStyle] = await Promise.all([
    sidebarComparableStyle(action),
    sidebarComparableStyle(menuItem),
  ]);
  expect(actionStyle).toEqual(menuStyle);
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

function deepSecPlan(
  applied = false,
  driverMode: "thin" | "thick" = "thin",
  deepsecEnabled = true,
  hasDataUserPassword = true
) {
  return {
    version: "V001",
    driver_mode: driverMode,
    connection_security: "wallet_mtls",
    deepsec_enabled: deepsecEnabled,
    data_user: "NL2SQL_DEEPSEC_DATA_USER",
    has_data_user_password: hasDataUserPassword,
    steps: [
      {
        step_no: 1,
        key: "principals_and_roles",
        title: "共有 DATA USER とロール",
        description: "共有 DATA USER と最小権限ロールを構成します。",
        checksum: "a".repeat(64),
        status: applied ? "APPLIED" : "PENDING",
        error_message: "",
        executed_at: applied ? "2026-07-19T00:00:00Z" : null,
        sql: [
          "CREATE END USER NL2SQL_DEEPSEC_DATA_USER IDENTIFIED BY <secret:ORACLE_DEEPSEC_DATA_USER_PASSWORD>",
        ],
      },
    ],
  };
}

async function mockDeepSecDataEntitlements(page: Page, rows: unknown[] = [systemRole]) {
  await page.route("**/api/security/deepsec/data-entitlements", (route) => fulfill(route, rows));
}

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
      name: "ログイン省略",
    })
  ).toBeVisible();
  const debugColors = await sidebar
    .getByRole("status", { name: "ログイン省略" })
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

test("外観ページは専用権限だけで表示でき、権限なしでは直達できない", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      user_id: "appearance-viewer",
      login_name: "appearance.viewer",
      display_name: "外観閲覧ユーザー",
      role_codes: ["APPEARANCE_VIEWER"],
      permissions: ["menu.settings_appearance"],
      password_change_allowed: true,
    })
  );

  await page.goto("/");
  await expect(page).toHaveURL(/\/settings\/appearance$/);
  await expect(page.getByRole("heading", { name: "外観" })).toBeVisible();
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await expect(sidebar.getByRole("link", { name: "外観" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "SQL 生成" })).toHaveCount(0);
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);

  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      user_id: "no-access",
      login_name: "no.access",
      display_name: "権限なしユーザー",
      role_codes: ["NO_ACCESS"],
      permissions: [],
      password_change_allowed: true,
    })
  );

  await page.goto("/settings/appearance");
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();
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
    await fulfill(route, { ...systemAdminMe, force_password_change: true, password_change_allowed: true });
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
  await expect(page.getByRole("button", { name: "ログインへ戻る" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toHaveCount(0);

  await page.getByLabel("現在のパスワード").fill("BootstrapPass!123");
  await page.locator("#auth-password-new").fill("IndependentPass!456");
  await page.getByLabel("新しいパスワード（確認）").fill("IndependentPass!456");
  await page.getByRole("button", { name: "パスワードを変更" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("構成管理者はパスワード変更入口を表示し、サイドバー操作は安定した高さを保つ", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.goto("/query");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const menuItem = sidebar.getByRole("link", { name: "SQL 生成" });
  const passwordButton = sidebar.getByRole("button", { name: "パスワード変更" });
  const logoutButton = sidebar.getByRole("button", { name: "ログアウト" });
  await expect(passwordButton).toBeVisible();
  await expectSidebarActionMatchesMenuItem(passwordButton, menuItem);
  await expectSidebarActionMatchesMenuItem(logoutButton, menuItem);

  await page.goto("/password/change");
  await expect(page.getByRole("heading", { name: "パスワードの変更" })).toBeVisible();
  await expect(page.getByLabel("現在のパスワード")).toBeVisible();
  await expect(page.locator("#auth-password-new")).toBeVisible();
  await expect(page.getByLabel("新しいパスワード（確認）")).toBeVisible();
  await expect(page.getByRole("button", { name: "戻る" })).toBeVisible();
});

test("通常ユーザーはパスワード変更ページから元の画面へ戻れる", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      user_id: "query-user",
      login_name: "query.user",
      display_name: "SQL 生成ユーザー",
      role_codes: ["QUERY_MENU"],
      permissions: ["menu.query"],
      password_change_allowed: true,
    })
  );
  await page.route("**/api/nl2sql/history", (route) => fulfill(route, { items: [], next_cursor: null }));
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfill(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-v1",
      refreshed_at: "2026-08-20T00:00:00Z",
      object_count: 1,
      column_count: 2,
      change_token: 1,
      etag: "schema-v1",
    })
  );
  await page.route("**/api/schema/objects**", (route) =>
    fulfill(route, {
      items: [],
      next_cursor: null,
      total: 0,
      table_count: 0,
      view_count: 0,
      counts_included: true,
      refreshed_at: "2026-08-20T00:00:00Z",
      catalog_version: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/search*", (route) =>
    fulfill(route, {
      items: [
        {
          id: "default",
          name: "標準プロファイル",
          category: "",
          description: "",
          archived: false,
          allowed_table_count: 0,
          allowed_view_count: 0,
          glossary_count: 0,
          few_shot_count: 0,
          version: 1,
          etag: "profile-default-v1",
          updated_at: "2026-08-20T00:00:00Z",
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/default/usage-context", (route) =>
    fulfill(route, {
      id: "default",
      name: "標準プロファイル",
      category: "",
      description: "",
      allowed_tables: [],
      allowed_views: [],
      archived: false,
      object_scope_version: 1,
      version: 1,
      etag: "profile-default-v1",
      updated_at: "2026-08-20T00:00:00Z",
    })
  );

  await page.goto("/query");
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const menuItem = sidebar.getByRole("link", { name: "SQL 生成" });
  const passwordButton = sidebar.getByRole("button", { name: "パスワード変更" });
  const logoutButton = sidebar.getByRole("button", { name: "ログアウト" });
  await expect(passwordButton).toBeVisible();
  await expect(logoutButton).toBeVisible();
  await expectSidebarActionMatchesMenuItem(passwordButton, menuItem);
  await expectSidebarActionMatchesMenuItem(logoutButton, menuItem);

  const footerLayout = await Promise.all([passwordButton, logoutButton].map((button) => button.boundingBox()));
  expect((footerLayout[1]?.y ?? 0) - ((footerLayout[0]?.y ?? 0) + (footerLayout[0]?.height ?? 0))).toBeGreaterThanOrEqual(0);
  await expect(passwordButton.locator("span").last()).toHaveCSS("white-space", "nowrap");
  await expect(logoutButton.locator("span").last()).toHaveCSS("white-space", "nowrap");

  await passwordButton.click();
  await expect(page.getByRole("heading", { name: "パスワードの変更" })).toBeVisible();
  await page.getByRole("button", { name: "戻る" }).click();
  await expect(page).toHaveURL(/\/query$/);
});

test("強制パスワード変更中の戻る操作はログインへ戻す", async ({ page }) => {
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      user_id: "forced-user",
      login_name: "forced.user",
      display_name: "初回ユーザー",
      role_codes: ["QUERY_MENU"],
      permissions: ["menu.query"],
      force_password_change: true,
      password_change_allowed: true,
    })
  );
  await page.route("**/api/auth/logout", (route) => fulfill(route, { logged_out: true }));

  await page.goto("/password/change");
  await expect(page.getByRole("button", { name: "ログインへ戻る" })).toBeVisible();
  await page.getByRole("button", { name: "ログインへ戻る" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("SQL 生成だけのユーザーは profile を利用できるが管理メニューには入れない", async ({ page }) => {
  test.slow();
  await mockDatabaseGateReady(page);
  const limited = {
    ...systemAdminMe,
    user_id: "limited",
    login_name: "limited.user",
    display_name: "SQL 生成ユーザー",
    role_codes: ["QUERY_MENU"],
    permissions: ["menu.query"],
    password_change_allowed: true,
  };
  let usageContextRequested = false;
  let fullProfileRequested = false;
  let persistenceRequested = false;
  await page.route("**/api/auth/me", (route) => fulfill(route, limited));
  await page.route("**/api/nl2sql/persistence", (route) => {
    persistenceRequested = true;
    return fulfill(route, {
      mode: "oracle",
      ready: true,
      durable: true,
      writable: true,
      snapshot_loaded: true,
      reason_code: null,
      checked_at: "2026-08-20T00:00:00Z",
    });
  });
  await page.route("**/api/nl2sql/history", (route) => fulfill(route, { items: [], next_cursor: null }));
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfill(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-v1",
      refreshed_at: "2026-08-20T00:00:00Z",
      object_count: 1,
      column_count: 2,
      change_token: 1,
      etag: "schema-v1",
    })
  );
  await page.route("**/api/schema/objects**", (route) =>
    fulfill(route, {
      items: [
        {
          owner: "APP",
          object_name: "DEPARTMENT",
          object_type: "TABLE",
          logical_name: "部署",
          comment: "部署情報",
          row_count: 3,
          column_count: 2,
          last_ddl_at: "",
        },
      ],
      next_cursor: null,
      total: 1,
      table_count: 1,
      view_count: 0,
      counts_included: true,
      refreshed_at: "2026-08-20T00:00:00Z",
      catalog_version: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/search*", (route) =>
    fulfill(route, {
      items: [
        {
          id: "default",
          name: "標準プロファイル",
          category: "",
          description: "",
          archived: false,
          allowed_table_count: 1,
          allowed_view_count: 0,
          glossary_count: 0,
          few_shot_count: 0,
          version: 1,
          etag: "profile-default-v1",
          updated_at: "2026-08-20T00:00:00Z",
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/default/usage-context", (route) => {
    usageContextRequested = true;
    return fulfill(route, {
      id: "default",
      name: "標準プロファイル",
      category: "",
      description: "",
      allowed_tables: ["APP.DEPARTMENT"],
      allowed_views: [],
      archived: false,
      object_scope_version: 1,
      version: 1,
      etag: "profile-default-v1",
      updated_at: "2026-08-20T00:00:00Z",
    });
  });
  await page.route("**/api/nl2sql/profiles/default", (route) => {
    fullProfileRequested = true;
    return fulfill(route, "この機能を利用する権限がありません。", 403);
  });
  await page.route("**/api/security/users", (route) =>
    fulfill(route, "この機能を利用する権限がありません。", 403)
  );
  await page.goto("/query");
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await expect(sidebar.getByRole("link", { name: "SQL 生成" })).toBeVisible();
  await expect(page.getByRole("button", { name: "実行" })).toBeVisible();
  await expect(page.locator("#nl2sql-profile-select")).toContainText("標準プロファイル");
  await expect(page).toHaveURL(/\/query$/);
  await expect(sidebar.getByText("業務プロファイル", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("ユーザー管理", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("セキュリティ管理", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByRole("link", { name: "SELECT SQL を実行" })).toHaveCount(0);
  await expect(sidebar.getByText("管理 SQL を実行", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "スキーマを更新" })).toHaveCount(0);
  await expect.poll(() => persistenceRequested).toBeTruthy();
  await expect.poll(() => usageContextRequested).toBeTruthy();
  expect(fullProfileRequested).toBe(false);

  await page.goto("/direct-sql");
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();

  await page.goto("/admin-sql");
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();

  await page.evaluate(() => {
    window.history.pushState({}, "", "/profiles");
    window.dispatchEvent(new PopStateEvent("popstate", { state: {} }));
  });
  await expect(page).toHaveURL(/\/forbidden$/);
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();

  const apiStatus = await page.evaluate(async () => (await fetch("/api/security/users")).status);
  expect(apiStatus).toBe(403);
  const profileSearchStatus = await page.evaluate(
    async () => (await fetch("/api/nl2sql/profiles/search")).status
  );
  expect(profileSearchStatus).toBe(200);
  const fullProfileStatus = await page.evaluate(
    async () => (await fetch("/api/nl2sql/profiles/default")).status
  );
  expect(fullProfileStatus).toBe(403);

  await page.goto("/settings/security/users");
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();
});

test("SQL 生成だけのユーザーは profile 未作成時に管理作成ボタンを表示しない", async ({ page }) => {
  test.slow();
  await mockDatabaseGateReady(page);
  const limited = {
    ...systemAdminMe,
    user_id: "limited-empty-profile",
    login_name: "limited.empty",
    display_name: "SQL 生成ユーザー",
    role_codes: ["QUERY_MENU"],
    permissions: ["menu.query"],
    password_change_allowed: true,
  };
  await page.route("**/api/auth/me", (route) => fulfill(route, limited));
  await page.route("**/api/nl2sql/history", (route) => fulfill(route, { items: [], next_cursor: null }));
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfill(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-v1",
      refreshed_at: "2026-08-20T00:00:00Z",
      object_count: 0,
      column_count: 0,
      change_token: 1,
      etag: "schema-v1",
    })
  );
  await page.route("**/api/nl2sql/profiles/search*", (route) =>
    fulfill(route, { items: [], next_cursor: null, total: 0, change_token: 1 })
  );

  await page.goto("/query");

  await expect(
    page.getByText("利用できる業務プロファイルがありません。管理者に作成を依頼してください。")
  ).toBeVisible({ timeout: 45_000 });
  await expect(page.getByRole("button", { name: "業務プロファイルを作成" })).toHaveCount(0);
});

test("SQL 生成だけのユーザーは空 schema 失敗時にサンプルデータ投入ボタンを表示しない", async ({ page }) => {
  test.slow();
  await mockDatabaseGateReady(page);
  const limited = {
    ...systemAdminMe,
    user_id: "query-only-sample-readonly",
    login_name: "query.only.sample",
    display_name: "SQL 生成ユーザー",
    role_codes: ["QUERY_MENU"],
    permissions: ["menu.query"],
    password_change_allowed: true,
  };
  let sampleImportRequested = false;
  await page.route("**/api/auth/me", (route) => fulfill(route, limited));
  await page.route("**/api/nl2sql/history", (route) => fulfill(route, { items: [], next_cursor: null }));
  await page.route("**/api/schema/catalog", (route) =>
    fulfill(route, { refreshed_at: "2026-08-20T00:00:00Z", tables: [] })
  );
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfill(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-empty",
      refreshed_at: "2026-08-20T00:00:00Z",
      object_count: 0,
      column_count: 0,
      change_token: 1,
      etag: "schema-empty",
    })
  );
  await page.route("**/api/schema/objects**", (route) =>
    fulfill(route, {
      items: [],
      next_cursor: null,
      total: 0,
      table_count: 0,
      view_count: 0,
      counts_included: true,
      refreshed_at: "2026-08-20T00:00:00Z",
      catalog_version: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/search*", (route) =>
    fulfill(route, {
      items: [
        {
          id: "default",
          name: "標準プロファイル",
          category: "",
          description: "",
          archived: false,
          allowed_table_count: 0,
          allowed_view_count: 0,
          glossary_count: 0,
          few_shot_count: 0,
          version: 1,
          etag: "profile-default-v1",
          updated_at: "2026-08-20T00:00:00Z",
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/default/usage-context", (route) =>
    fulfill(route, {
      id: "default",
      name: "標準プロファイル",
      category: "",
      description: "",
      allowed_tables: [],
      allowed_views: [],
      archived: false,
      object_scope_version: 1,
      version: 1,
      etag: "profile-default-v1",
      updated_at: "2026-08-20T00:00:00Z",
    })
  );
  await page.route("**/api/nl2sql/sample-data/import", (route) => {
    sampleImportRequested = true;
    return fulfill(route, { executed: true });
  });

  const createdAt = "2026-08-20T00:00:00Z";
  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfill(route, {
      job_id: "job-empty-readonly-001",
      status: "running",
      created_at: createdAt,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "running", elapsed_ms: null },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );
  await page.route("**/api/nl2sql/jobs/job-empty-readonly-001", (route) =>
    fulfill(route, {
      job_id: "job-empty-readonly-001",
      status: "error",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 30,
      result: null,
      error_message:
        "NL2SQL ジョブに失敗しました: Schema catalog が空です。Oracle schema を refresh するか、Data Tools から sample data を明示的に import してください。",
      timing: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "error", elapsed_ms: 5 },
        { stage: "safety_check", status: "pending", elapsed_ms: null },
        { stage: "execute_sql", status: "pending", elapsed_ms: null },
        { stage: "format_results", status: "pending", elapsed_ms: null },
      ],
    })
  );

  await page.goto("/query");
  await expect(page.locator("#nl2sql-profile-select")).toContainText("標準プロファイル");
  await page.locator("#nl2sql-question-input").fill("すべてプロジェクトを教えてください。");
  await page.getByRole("button", { name: "検索を実行" }).click();

  const progress = page.getByTestId("nl2sql-job-progress");
  await expect(progress).toHaveAttribute("data-job-status", "error");
  await expect(progress.getByRole("button", { name: "サンプルデータを投入" })).toHaveCount(0);
  await expect(
    progress.getByText("スキーマが空です。管理者にサンプルデータの投入またはスキーマ更新を依頼してください。")
  ).toBeVisible();
  expect(sampleImportRequested).toBe(false);
});

test("管理者がユーザーを作成して単一ロールを割り当て、一時パスワードを一度だけ確認する", async ({ page, context }) => {
  await mockDatabaseGateReady(page);
  await context.addCookies([{ name: "nl2sql_csrf", value: "csrf-token", url: "http://127.0.0.1:3101" }]);
  let csrfObserved = false;
  let createRequestCount = 0;
  let createdPayloadRoleIds: string[] | null = null;
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
      is_bootstrap_admin: true,
    },
  ];
  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "QUERY_VIEWER",
    display_name: "検索閲覧",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };
  const runnerRole = {
    ...systemRole,
    role_id: "role-runner",
    role_code: "QUERY_RUNNER",
    display_name: "検索実行",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [systemRole, viewerRole, runnerRole])
  );
  await page.route("**/api/security/users", async (route) => {
    if (route.request().method() === "GET") {
      await fulfill(route, users);
      return;
    }
    createRequestCount += 1;
    csrfObserved = route.request().headers()["x-csrf-token"] === "csrf-token";
    const payload = route.request().postDataJSON() as {
      login_name: string;
      display_name: string;
      role_ids: string[];
    };
    createdPayloadRoleIds = payload.role_ids;
    const user = {
      user_id: "new-user",
      login_name: payload.login_name,
      display_name: payload.display_name,
      status: "ACTIVE",
      force_password_change: true,
      locked_until: null,
      version: 1,
      role_ids: payload.role_ids,
      is_bootstrap_admin: false,
    };
    users = [...users, user];
    await fulfill(route, { user, temporary_password: "RandomStrong!Pass123" });
  });

  await page.goto("/settings/security/users");
  await page.getByTestId("security-users-actions").getByRole("button", { name: "新規作成" }).click();
  await page.getByLabel("ログイン名").fill("sales.user");
  await page.getByLabel("表示名").fill("営業ユーザー");
  await expect(page.getByTestId("security-users-role-selection-actions")).toHaveCount(0);
  await expect(page.getByRole("radio", { name: /システム管理者/ })).toBeDisabled();
  await expect(page.getByText("SYSTEM_ADMIN は初期システム管理者にのみ割り当てできます。", { exact: true })).toBeVisible();
  const createButton = page.locator("#security-users-panel-create").getByRole("button", { name: "新規作成", exact: true });
  await createButton.click();
  await expect(page.getByText("ロールを1つ選択してください。", { exact: true })).toBeVisible();
  expect(createRequestCount).toBe(0);

  const viewerRadio = page.getByRole("radio", { name: /検索閲覧/ });
  const runnerRadio = page.getByRole("radio", { name: /検索実行/ });
  await expect(viewerRadio).toHaveAttribute("type", "radio");
  await expect(runnerRadio).toHaveAttribute("type", "radio");
  await viewerRadio.check();
  await expect(viewerRadio).toBeChecked();
  await expect(runnerRadio).not.toBeChecked();
  await runnerRadio.check();
  await expect(runnerRadio).toBeChecked();
  await expect(viewerRadio).not.toBeChecked();
  await viewerRadio.check();
  await page.locator("#security-users-panel-create").getByRole("button", { name: "新規作成", exact: true }).click();

  await expect(page.getByText("一時パスワードは今回だけ表示されます。安全な方法で利用者へ伝えてください。", { exact: true })).toBeVisible();
  await expect(page.getByText("RandomStrong!Pass123", { exact: true })).toBeVisible();
  expect(csrfObserved).toBe(true);
  expect(createdPayloadRoleIds).toEqual(["role-viewer"]);
});

test("ユーザー管理は一覧・作成・編集をテーブル管理型パネルで統一する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const users = [
    {
      user_id: "admin-user",
      login_name: "SYSTEM",
      display_name: "システム管理者",
      status: "ACTIVE",
      force_password_change: false,
      locked_until: null,
      version: 1,
      role_ids: ["role-system"],
      is_bootstrap_admin: true,
    },
    {
      user_id: "sales-user",
      login_name: "sales.user",
      display_name: "営業ユーザー",
      status: "DISABLED",
      force_password_change: true,
      locked_until: "2026-07-21T03:00:00Z",
      version: 2,
      role_ids: ["role-viewer"],
      is_bootstrap_admin: false,
    },
  ];
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [
      systemRole,
      {
        ...systemRole,
        role_id: "role-viewer",
        role_code: "QUERY_VIEWER",
        display_name: "検索閲覧",
        is_built_in: false,
        permissions: ["menu.query"],
        data_entitlements: [],
      },
    ])
  );
  await page.route("**/api/security/users", (route) => fulfill(route, users));

  await page.setViewportSize({ width: 2048, height: 1000 });
  await page.goto("/settings/security/users");

  const listStyle = await topLevelPanelStyle(page, "list", "security-users");
  const usersSplitPane = page.getByTestId("fixed-split-pane-security-users-list");
  await expect(usersSplitPane).toHaveAttribute("data-split-layout", "split");
  await expectSplitPaneReservedTrack(usersSplitPane);
  await expect(page.getByTestId("security-users-grid")).toBeVisible();
  await expect(page.getByTestId("security-users-grid").locator("tbody tr")).toHaveCount(2);
  const adminUserRowAction = page.getByTestId("security-users-row-actions-admin-user-trigger");
  await expect(adminUserRowAction).toBeVisible();
  await expect(page.getByTestId("security-users-grid").getByRole("button", { name: "編集" })).toHaveCount(0);
  await adminUserRowAction.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitem", { name: "編集" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "パスワードをリセット" })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("menuitem", { name: "無効化" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(adminUserRowAction).toBeFocused();
  await expect(page.getByTestId("security-users-detail-actions").getByRole("button", { name: "編集" })).toBeVisible();
  await expect(page.getByTestId("security-users-detail-actions").getByRole("button", { name: "パスワードをリセット" })).toBeVisible();
  await expect(page.getByTestId("security-users-detail-actions").getByRole("button", { name: "その他の操作" })).toBeVisible();
  const salesUserRow = page.getByTestId("security-users-grid").locator("tbody tr").filter({ hasText: "営業ユーザー" });
  await salesUserRow.locator("td").nth(1).click();
  await expect(salesUserRow).toHaveAttribute("data-selected", "true");
  await expect(page.locator("dl").getByText("ロック中", { exact: true })).toBeVisible();
  await expect(page.getByText("ロック期限", { exact: true })).toHaveCount(0);
  await page.getByTestId("security-users-detail-actions").getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "ロック解除" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByTestId("security-users-detail-actions").getByRole("button", { name: "編集" }).click();
  await expect(page.getByLabel("システム管理者")).toBeDisabled();
  await expect(page.getByText("SYSTEM_ADMIN は初期システム管理者にのみ割り当てできます。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await page.getByTestId("security-users-search").fill("sales");
  await expect(page.getByTestId("security-users-grid").getByText("営業ユーザー")).toBeVisible();
  await expect(page.getByTestId("security-users-grid").getByText("システム管理者")).toHaveCount(0);
  await page.getByTestId("security-users-search").fill("");

  await page.getByTestId("security-users-actions").getByRole("button", { name: "新規作成" }).click();
  expect(await topLevelPanelStyle(page, "create", "security-users")).toEqual(listStyle);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-users-panel-list")).toBeVisible();

  await page.getByTestId("security-users-row-actions-admin-user-trigger").click();
  await page.getByRole("menuitem", { name: "編集" }).click();
  expect(await topLevelPanelStyle(page, "edit", "security-users")).toEqual(listStyle);
  await expect(page.getByLabel("システム管理者")).toBeEnabled();
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-users-panel-list")).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
  await expect(page.getByTestId("security-users-row-actions-admin-user-trigger")).toBeVisible();
});

test("ユーザー管理はアーカイブ済み割り当てロールを無効として表示し、新規選択肢には出さない", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "QUERY_VIEWER",
    display_name: "検索閲覧",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };
  const archivedAssignedRole = {
    role_id: "role-archived-user",
    role_code: "DATA_ADMIN",
    display_name: "データ管理者",
    is_built_in: false,
    archived: true,
  };
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [systemRole, viewerRole])
  );
  await page.route("**/api/security/users", (route) =>
    fulfill(route, [
      {
        user_id: "archived-role-user",
        login_name: "archive.user",
        display_name: "アーカイブロール利用者",
        status: "ACTIVE",
        force_password_change: false,
        locked_until: null,
        version: 1,
        role_ids: [archivedAssignedRole.role_id],
        assigned_roles: [archivedAssignedRole],
        is_bootstrap_admin: false,
      },
    ])
  );

  await page.goto("/settings/security/users");

  const grid = page.getByTestId("security-users-grid");
  await expect(grid.getByText("データ管理者（アーカイブ済み・無効）", { exact: true })).toBeVisible();
  await expect(
    page.getByText("アーカイブ済みロールの権限は、このユーザーの実アクセス権には反映されません。", {
      exact: true,
    })
  ).toBeVisible();

  const inactiveRoleBadge = page.getByText("データ管理者（アーカイブ済み・無効）", { exact: true }).last();
  await expect(inactiveRoleBadge).toHaveClass(/bg-slate-100/);
  await page.getByTestId("security-users-detail-actions").getByRole("button", { name: "編集" }).click();
  await expect(page.getByRole("radio", { name: /検索閲覧/ })).toBeVisible();
  await expect(page.getByRole("radio", { name: /データ管理者/ })).toHaveCount(0);
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
});

test("ユーザー管理は復元済みロールを通常表示し、割り当て選択肢に戻す", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const restoredRole = {
    ...systemRole,
    role_id: "role-restored-user",
    role_code: "DATA_ADMIN",
    display_name: "データ管理者",
    is_built_in: false,
    archived: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };
  const restoredAssignedRole = {
    role_id: restoredRole.role_id,
    role_code: restoredRole.role_code,
    display_name: restoredRole.display_name,
    is_built_in: false,
    archived: false,
  };
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [systemRole, restoredRole])
  );
  await page.route("**/api/security/users", (route) =>
    fulfill(route, [
      {
        user_id: "restored-role-user",
        login_name: "restore.user",
        display_name: "復元ロール利用者",
        status: "ACTIVE",
        force_password_change: false,
        locked_until: null,
        version: 1,
        role_ids: [restoredRole.role_id],
        assigned_roles: [restoredAssignedRole],
        is_bootstrap_admin: false,
      },
    ])
  );

  await page.goto("/settings/security/users");

  const grid = page.getByTestId("security-users-grid");
  await expect(grid.getByText("データ管理者", { exact: true })).toBeVisible();
  await expect(page.getByText("データ管理者（アーカイブ済み・無効）", { exact: true })).toHaveCount(0);
  await expect(
    page.getByText("アーカイブ済みロールの権限は、このユーザーの実アクセス権には反映されません。", {
      exact: true,
    })
  ).toHaveCount(0);
  await expect(page.getByText("データ管理者", { exact: true }).last()).toHaveClass(/bg-sky-100/);
  await page.getByTestId("security-users-detail-actions").getByRole("button", { name: "編集" }).click();
  await expect(page.getByRole("radio", { name: /データ管理者/ })).toBeVisible();
});

test("ロール・権限管理はカード型リストではなくテーブル一覧と詳細で表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const permissionRows = [
    {
      code: "menu.settings_appearance",
      group: "システム設定",
      label: "外観",
      description: "外観を表示し、関連操作を利用できます。",
      implies: [],
    },
    {
      code: "menu.security_users",
      group: "セキュリティ管理",
      label: "ユーザー管理",
      description: "ユーザー管理を表示し、関連操作を利用できます。",
      implies: [],
    },
    {
      code: "menu.security_roles",
      group: "セキュリティ管理",
      label: "ロール・権限管理",
      description: "ロール・権限管理を表示し、関連操作を利用できます。",
      implies: [],
    },
  ];
  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "SECURITY_VIEWER",
    display_name: "セキュリティ閲覧",
    description: "表示のみ",
    is_built_in: false,
    permissions: ["menu.security_users"],
    data_entitlements: [],
  };
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole, viewerRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, permissionRows));

  await page.setViewportSize({ width: 2048, height: 1000 });
  await page.goto("/settings/security/roles");

  const listStyle = await topLevelPanelStyle(page, "list", "security-roles");
  const rolesSplitPane = page.getByTestId("fixed-split-pane-security-roles-list");
  await expect(rolesSplitPane).toHaveAttribute("data-split-layout", "split");
  await expectSplitPaneReservedTrack(rolesSplitPane);
  const grid = page.getByTestId("security-roles-grid");
  await expect(grid).toBeVisible();
  await expect(grid.getByRole("columnheader", { name: "ロール" })).toBeVisible();
  await expect(grid.getByRole("columnheader", { name: "機能権限" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "機能権限" })).toHaveCount(0);
  await expect(page.getByText("構造化データ権限", { exact: true })).toHaveCount(0);
  await expect(grid.locator("tbody tr")).toHaveCount(2);
  const systemRoleAction = page.getByTestId("security-roles-row-actions-role-system-trigger");
  await expect(systemRoleAction).toBeVisible();
  await expect(grid.getByRole("button", { name: "編集" })).toHaveCount(0);
  await expect(page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" })).toBeVisible();
  const viewerRoleRow = grid.locator("tbody tr").filter({ hasText: "セキュリティ閲覧" });
  await viewerRoleRow.locator("td").nth(2).click();
  await expect(viewerRoleRow).toHaveAttribute("data-selected", "true");
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" }).click();
  const customRoleEditActions = page.getByRole("group", { name: "ロール編集操作" });
  await expect(customRoleEditActions.getByRole("button", { name: "保存" })).toBeVisible();
  await expect(customRoleEditActions.getByRole("button", { name: "キャンセル" })).toBeVisible();
  await expect(customRoleEditActions.getByRole("button", { name: "アーカイブ" })).toHaveCount(0);
  await customRoleEditActions.getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "アーカイブ" })).toHaveAttribute("data-form-action-tone", "danger");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await page.getByTestId("security-roles-search").fill("閲覧");
  await expect(grid.getByText("セキュリティ閲覧")).toBeVisible();
  await expect(grid.getByText("システム管理者")).toHaveCount(0);
  await page.getByTestId("security-roles-search").fill("");

  await page.getByTestId("security-roles-actions").getByRole("button", { name: "新規作成" }).click();
  expect(await topLevelPanelStyle(page, "create", "security-roles")).toEqual(listStyle);
  await expect(page.getByText("ダッシュボード表示", { exact: true })).toHaveCount(0);
  await expect(page.getByText("security.users.view", { exact: true })).toHaveCount(0);
  await expect(page.getByText("security.users.manage", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: /外観/ })).toBeVisible();
  const permissionBulkActions = page.getByTestId("security-roles-permission-selection-actions");
  await expect(permissionBulkActions.getByRole("button", { name: "すべて選択" })).toBeEnabled();
  await expect(permissionBulkActions.getByRole("button", { name: "すべて解除" })).toBeDisabled();
  await permissionBulkActions.getByRole("button", { name: "すべて選択" }).click();
  await expect(page.getByRole("checkbox", { name: /外観/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /ユーザー管理/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /ロール・権限管理/ })).toBeChecked();
  const securityGroupBulkActions = page.getByTestId("security-roles-セキュリティ管理-permission-selection-actions");
  await expect(securityGroupBulkActions.getByRole("button", { name: "セキュリティ管理 の選択を解除" })).toBeEnabled();
  await securityGroupBulkActions.getByRole("button", { name: "セキュリティ管理 の選択を解除" }).click();
  await expect(page.getByRole("checkbox", { name: /ユーザー管理/ })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: /ロール・権限管理/ })).not.toBeChecked();
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-roles-panel-list")).toBeVisible();

  await page.getByTestId("security-roles-row-actions-role-system-trigger").click();
  await page.getByRole("menuitem", { name: "編集" }).click();
  expect(await topLevelPanelStyle(page, "edit", "security-roles")).toEqual(listStyle);
  const roleEditActions = page.getByRole("group", { name: "ロール編集操作" });
  await expect(roleEditActions.getByRole("button", { name: "保存" })).toHaveCount(0);
  await expect(roleEditActions.getByRole("button", { name: "キャンセル" })).toBeVisible();
  await expect(roleEditActions.getByRole("button", { name: "アーカイブ" })).toHaveCount(0);
  await expect(roleEditActions.getByRole("button", { name: "その他の操作" })).toHaveCount(0);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-roles-panel-list")).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
  await expect(page.getByTestId("security-roles-row-actions-role-system-trigger")).toBeVisible();
});

test("ロール・権限管理は詳細で権限名を伏せ、編集では SQL 生成由来の参照権限を継承表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const permissionRows = [
    {
      code: "menu.query",
      group: "メニュー権限",
      label: "SQL 生成",
      description: "SQL 生成を表示し、関連操作を利用できます。",
      implies: ["nl2sql.profiles.read", "nl2sql.schema.read"],
    },
    {
      code: "nl2sql.profiles.read",
      group: "参照権限",
      label: "業務プロファイル参照",
      description: "SQL 生成で業務プロファイルの利用コンテキストを参照できます。",
      implies: [],
    },
    {
      code: "nl2sql.schema.read",
      group: "参照権限",
      label: "スキーマ参照",
      description: "SQL 生成でスキーマ情報を参照できます。",
      implies: [],
    },
  ];
  const queryRole = {
    ...systemRole,
    role_id: "role-query-only",
    role_code: "QUERY_ONLY",
    display_name: "SQL 利用者",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole, queryRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, permissionRows));

  await page.goto("/settings/security/roles");
  await page.getByTestId("security-roles-grid").locator("tbody tr").filter({ hasText: "SQL 利用者" }).locator("td").first().click();

  await expect(page.getByText("業務プロファイル参照 (SQL 生成により付与)")).toHaveCount(0);
  await expect(page.getByText("スキーマ参照 (SQL 生成により付与)")).toHaveCount(0);
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" }).click();
  await expect(page.getByText("SQL 生成により付与", { exact: true }).first()).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
});

test("ロール編集の下端メニューは viewport 下端では上方向に開く", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.setViewportSize({ width: 1365, height: 720 });
  const permissionRows = [
    {
      code: "menu.query",
      group: "RAG",
      label: "SQL 生成",
      description: "SQL 生成を表示し、関連操作を利用できます。",
      implies: [],
    },
    ...Array.from({ length: 28 }, (_, index) => ({
      code: `menu.bottom_menu_check_${index + 1}`,
      group: index % 2 === 0 ? "システム設定" : "管理権限",
      label: `下端検証 ${String(index + 1).padStart(2, "0")}`,
      description: "下端メニューの表示位置を検証するための権限です。",
      implies: [],
    })),
  ];
  const activeRole = {
    ...systemRole,
    role_id: "role-bottom-menu",
    role_code: "BOTTOM_MENU",
    display_name: "下端メニュー検証ロール",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };

  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole, activeRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, permissionRows));

  await page.goto("/settings/security/roles");
  await page.getByTestId("security-roles-grid").locator("tbody tr").filter({ hasText: "下端メニュー検証ロール" }).locator("td").first().click();
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" }).click();

  const editActions = page.getByRole("group", { name: "ロール編集操作" });
  await expect(editActions.getByRole("button", { name: "保存" })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect
    .poll(() =>
      page.evaluate(
        () => window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2
      )
    )
    .toBeTruthy();

  const trigger = editActions.getByRole("button", { name: "その他の操作" });
  const triggerBox = await trigger.boundingBox();
  expect(triggerBox).not.toBeNull();
  await trigger.click();

  const menu = page.getByRole("menu");
  await expect(menu).toHaveAttribute("data-floating-menu-placement", "top");
  await expectFloatingMenuInsideViewport(page, menu);
  const menuBox = await menu.boundingBox();
  expect(menuBox).not.toBeNull();
  expect(menuBox!.y + menuBox!.height).toBeLessThanOrEqual(triggerBox!.y + 1);
});

test("ロール管理の compact header menu は短い viewport 内に収まる", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.setViewportSize({ width: 375, height: 360 });
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));

  await page.goto("/settings/security/roles");
  const actions = page.getByTestId("security-roles-actions");
  const moreButton = actions.getByRole("button", { name: "その他の操作", exact: true });
  await expect(actions.getByRole("button")).toHaveText(["新規作成", "その他の操作"]);
  await moreButton.click();

  const menu = page.getByRole("menu");
  await expect(menu).toHaveAttribute("data-floating-menu-placement", "bottom");
  await expect(menu.getByRole("menuitem", { name: "表示を更新" })).toBeVisible();
  await expectFloatingMenuInsideViewport(page, menu);
});

test("ロール・権限管理はアーカイブ済みロールの権限が無効であることを明示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const permissionRows = [
    {
      code: "menu.query",
      group: "AI 活用",
      label: "SQL 生成",
      description: "SQL 生成を表示し、関連操作を利用できます。",
      implies: [],
    },
    {
      code: "menu.direct_sql",
      group: "AI 活用",
      label: "SELECT SQL を実行",
      description: "SELECT SQL 実行を表示し、関連操作を利用できます。",
      implies: [],
    },
  ];
  const activeRole = {
    ...systemRole,
    role_id: "role-active-data",
    role_code: "DATA_USER",
    display_name: "データユーザー",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };
  const archivedRole = {
    ...systemRole,
    role_id: "role-archived-data",
    role_code: "DATA_ADMIN",
    display_name: "データ管理者",
    is_built_in: false,
    archived: true,
    permissions: ["menu.query", "menu.direct_sql"],
    data_entitlements: [],
  };
  let roles: unknown[] = [systemRole, activeRole, archivedRole];
  await page.route("**/api/security/roles?include_archived=true", (route) => fulfill(route, roles));
  await page.route("**/api/security/roles/role-active-data/archive", async (route) => {
    const updated = { ...activeRole, archived: true, version: activeRole.version + 1 };
    roles = [systemRole, updated, archivedRole];
    await fulfill(route, updated);
  });
  await page.route("**/api/security/roles/role-archived-data/restore", async (route) => {
    const restored = { ...archivedRole, archived: false, version: archivedRole.version + 1 };
    roles = [systemRole, activeRole, restored];
    await fulfill(route, restored);
  });
  await page.route("**/api/security/permissions", (route) => fulfill(route, permissionRows));

  await page.goto("/settings/security/roles");

  const grid = page.getByTestId("security-roles-grid");
  const archivedRow = grid.locator("tbody tr").filter({ hasText: "データ管理者" });
  await archivedRow.locator("td").first().click();
  await expect(archivedRow.getByText("アーカイブ済み・権限無効", { exact: true })).toBeVisible();
  await expect(
    page.getByText(
      "このロールはアーカイブ済みです。保存済みの権限は利用者の実アクセス権には反映されません。",
      { exact: true }
    )
  ).toBeVisible();

  const archivedDetail = page.locator("section", { has: page.getByRole("heading", { name: "データ管理者" }) });
  await expect(archivedDetail.getByText("SQL 生成", { exact: true })).toHaveCount(0);

  await page.getByTestId("security-roles-row-actions-role-archived-data-trigger").click();
  await expect(page.getByRole("menuitem", { name: "復元" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "復元" })).toBeVisible();
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" }).click();
  const archivedEditActions = page.getByRole("group", { name: "ロール編集操作" });
  await expect(archivedEditActions.getByRole("button", { name: "復元" })).toBeVisible();
  await archivedEditActions.getByRole("button", { name: "復元" }).click();
  const restoreDialog = page.getByRole("alertdialog");
  await expect(restoreDialog).toBeVisible();
  await expect(
    restoreDialog.getByText(
      "このロールを復元すると、このロールに紐づくユーザーへ、このロール由来の権限が次回リクエストから反映されます。",
      { exact: true }
    )
  ).toBeVisible();
  await restoreDialog.getByRole("button", { name: "実行" }).click();
  await expect(page.locator("#security-roles-panel-list")).toBeVisible();
  const restoredRow = grid.locator("tbody tr").filter({ hasText: "データ管理者" });
  await expect(restoredRow.getByText("カスタム", { exact: true })).toBeVisible();
  await expect(restoredRow.getByText("アーカイブ済み・権限無効", { exact: true })).toHaveCount(0);
  await expect(
    page.getByText(
      "このロールはアーカイブ済みです。保存済みの権限は利用者の実アクセス権には反映されません。",
      { exact: true }
    )
  ).toHaveCount(0);
  await expect(page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "復元" })).toHaveCount(0);

  const activeRow = grid.locator("tbody tr").filter({ hasText: "データユーザー" });
  await activeRow.locator("td").first().click();
  await expect(page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "復元" })).toHaveCount(0);
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "アーカイブ" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" }).click();
  await expect(page.getByRole("group", { name: "ロール編集操作" }).getByRole("button", { name: "復元" })).toHaveCount(0);
  await page.getByRole("group", { name: "ロール編集操作" }).getByRole("button", { name: "その他の操作" }).click();
  await page.getByRole("menuitem", { name: "アーカイブ" }).click();
  const confirmDialog = page.getByRole("alertdialog");
  await expect(confirmDialog).toBeVisible();
  await expect(
    confirmDialog.getByText(
      "このロールをアーカイブすると、このロール由来の権限は利用者に反映されなくなります。ユーザー自体は無効化されません。",
      { exact: true }
    )
  ).toBeVisible();
});

test("ロール・権限管理はオントロジー提案取得が遅延しても読み込み完了する", async ({ page }) => {
  test.slow();
  await mockDatabaseGateReady(page);
  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", (route) => fulfill(route, systemAdminMe));
  const profile = {
    id: "default",
    name: "標準プロファイル",
    category: "",
    description: "",
    archived: false,
    allowed_tables: [],
    allowed_views: [],
    glossary: {},
    few_shot_examples: [],
    version: 1,
    etag: "profile-etag",
    updated_at: "2026-07-21T00:00:00Z",
  };
  let proposalRequests = 0;
  let releaseProposals = () => {};
  const proposalsGate = new Promise<void>((resolve) => {
    releaseProposals = resolve;
  });

  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfill(route, {
      items: [
        {
          id: profile.id,
          name: profile.name,
          category: profile.category,
          description: profile.description,
          archived: profile.archived,
          allowed_table_count: 0,
          allowed_view_count: 0,
          glossary_count: 0,
          few_shot_count: 0,
          version: profile.version,
          etag: profile.etag,
          updated_at: profile.updated_at,
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/default", (route) => fulfill(route, profile));
  await page.route("**/api/nl2sql/profiles/default/ontology-view", (route) =>
    fulfill(route, { profile_ontology_view: null, ontology_graph: null, warnings_ja: [] })
  );
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfill(route, { revisions: [], active_revision_id: "" })
  );
  await page.route("**/api/nl2sql/profiles/default/ontology-proposals", async (route) => {
    proposalRequests += 1;
    await proposalsGate;
    try {
      await fulfill(route, { proposals: [] });
    } catch {
      // 画面遷移で abort 済みの request は fulfill できない場合がある。
    }
  });
  await page.route("**/api/nl2sql/profiles/default/ontology-build-jobs**", (route) =>
    fulfill(route, { jobs: [] })
  );
  await page.route("**/api/nl2sql/ontology-templates", (route) =>
    fulfill(route, { templates: [] })
  );
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));

  try {
    await page.goto("/ontology-build?profile=default");
    await expect(page.getByTestId("profile-ontology-build")).toBeVisible({
      timeout: 30_000,
    });
    await expect.poll(() => proposalRequests).toBeGreaterThan(0);

    await page.goto("/settings/security/roles");

    const grid = page.getByTestId("security-roles-grid");
    await expect(grid).toBeVisible();
    await expect(grid.locator("tbody tr")).toHaveCount(1);
    await expect(grid.locator(".animate-pulse")).toHaveCount(0);
    await expect(grid.getByText("システム管理者")).toBeVisible();
    await expectNoPageHorizontalScroll(page);
  } finally {
    releaseProposals();
  }
});

test("DeepSec は構成状態の確認中でも SQL plan を先に表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let releaseStatus = () => {};
  const statusGate = new Promise<void>((resolve) => {
    releaseStatus = resolve;
  });
  await page.route("**/api/security/deepsec/status", async (route) => {
    await statusGate;
    await fulfill(route, {
      configured: false,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "NL2SQL_DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: {},
      message: "未適用です。",
    });
  });
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan()));
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");
  await expect(page.getByText("構成状態を確認しています。", { exact: true }).nth(1)).toBeVisible();
  await expect(page.locator("pre")).toHaveCount(1);
  await expect(page.getByText("CREATE END USER NL2SQL_DEEPSEC_DATA_USER", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Data Grant を検証" })).toBeDisabled();

  releaseStatus();
  await expect(page.getByText("未適用です。", { exact: true })).toBeVisible();
  await expectNoPageHorizontalScroll(page);
});

test("DeepSec は Thick mode でも SQL step をキーボード操作できる", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockDatabaseGateReady(page);
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: false,
      driver_mode: "thick",
      deepsec_enabled: true,
      data_user: "NL2SQL_DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: {},
      message: "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) =>
    fulfill(route, deepSecPlan(false, "thick"))
  );
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");

  await expect(page.getByText("Deep Data Security が無効です。", { exact: false })).toHaveCount(0);
  const confirmationField = page.getByTestId("execution-confirmation-field");
  const confirmationInput = confirmationField.getByRole("textbox", { name: "実行確認語" });
  const applyButton = confirmationField.getByRole("button", { name: "このステップを適用" });
  await expect(applyButton).toBeDisabled();
  await confirmationInput.focus();
  await expect(confirmationInput).toBeFocused();
  await page.keyboard.type("ADMIN_EXECUTE");
  await expect(confirmationField.getByText("確認済み")).toBeVisible();
  await expect(applyButton).toBeEnabled();
  await applyButton.focus();
  await expect(applyButton).toBeFocused();
  await expectNoPageHorizontalScroll(page);
});

for (const driverMode of ["thin", "thick"] as const) {
  test(`DeepSec 無効時は ${driverMode} mode で有効化手順を表示する`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockDatabaseGateReady(page);
    await page.route("**/api/security/deepsec/status", (route) =>
      fulfill(route, {
        configured: false,
        driver_mode: driverMode,
        connection_security: "wallet_mtls",
        deepsec_enabled: false,
        data_user: "NL2SQL_DEEPSEC_DATA_USER",
        has_data_user_password: false,
        objects: {},
        message: "未適用です。",
      })
    );
    await page.route("**/api/security/deepsec/plan", (route) =>
      fulfill(route, deepSecPlan(false, driverMode, false, false))
    );
    await mockDeepSecDataEntitlements(page);

    await page.goto("/settings/security/deepsec");

    const disabledBanner = page
      .getByRole("status")
      .filter({ hasText: "Deep Data Security が無効です。" });
    await expect(disabledBanner).toBeVisible();
    await expect(page.getByRole("button", { name: "このステップを適用" })).toBeDisabled();
    await expectNoPageHorizontalScroll(page);
  });
}

test("DeepSec は DATA USER password をページから保存し再起動なしで適用可能にする", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let hasPassword = false;
  let savedPassword = "";
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: false,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "NL2SQL_DEEPSEC_DATA_USER",
      has_data_user_password: hasPassword,
      objects: {},
      message: "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) =>
    fulfill(route, deepSecPlan(false, "thin", true, hasPassword))
  );
  await page.route("**/api/security/deepsec/config", async (route) => {
    const payload = route.request().postDataJSON() as { data_user_password: string };
    savedPassword = payload.data_user_password;
    hasPassword = true;
    await fulfill(route, {
      configured: false,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "NL2SQL_DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: {},
      message: "未適用です。",
    });
  });
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");

  await expect(
    page.getByText("API を再起動せずに次の適用・検証から使用できます。", {
      exact: false,
    })
  ).toBeVisible();
  await expect(page.getByText("未設定", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "このステップを適用" })).toBeDisabled();

  const password = page.getByLabel("DATA USER パスワード");
  await password.fill("DeepSecret!789");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  expect(savedPassword).toBe("DeepSecret!789");
  await expect(password).toHaveValue("");
  await expect(page.getByText("保存済み", { exact: true })).toBeVisible();
  await expect(page.getByText("API を再起動")).toHaveCount(0);
  const confirmationField = page.getByTestId("execution-confirmation-field");
  const confirmationInput = confirmationField.getByRole("textbox", { name: "実行確認語" });
  const applyButton = confirmationField.getByRole("button", { name: "このステップを適用" });
  await expect(confirmationInput).toBeEnabled();
  await expect(applyButton).toBeDisabled();
  await confirmationInput.fill("ADMIN_EXECUTE");
  await expect(applyButton).toBeEnabled();
  await expectNoPageHorizontalScroll(page);
});

test("DeepSec は構造化データ権限をロール別に編集する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const queryRole = {
    role_id: "role-query",
    role_code: "QUERY_VIEWER",
    display_name: "検索閲覧",
    description: "業務データ参照",
    is_built_in: false,
    archived: false,
    version: 3,
    data_entitlements: [],
  };
  const archivedRole = {
    ...queryRole,
    role_id: "role-archived",
    role_code: "ARCHIVED_VIEWER",
    display_name: "廃止ロール",
    archived: true,
    version: 1,
  };
  let entitlementRoles: unknown[] = [systemRole, queryRole, archivedRole];
  let savedPayload: {
    version: number;
    data_entitlements: Array<{ resource_code: string; scope_code: string; capability: string }>;
  } | null = null;
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: true,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "NL2SQL_DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: { data_grants: 2 },
      message: "構成済みです。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan(true)));
  await page.route("**/api/security/deepsec/data-entitlements", (route) =>
    fulfill(route, entitlementRoles)
  );
  await page.route("**/api/security/deepsec/data-entitlements/role-query", async (route) => {
    savedPayload = route.request().postDataJSON();
    const updated = {
      ...queryRole,
      version: 4,
      data_entitlements: savedPayload?.data_entitlements.map((item, index) => ({
        entitlement_id: `saved-${index}`,
        ...item,
      })) ?? [],
    };
    entitlementRoles = [systemRole, updated, archivedRole];
    await fulfill(route, updated);
  });

  await page.goto("/settings/security/deepsec");

  await expect(page.getByRole("heading", { name: "構造化データ権限" })).toBeVisible();
  const entitlementForm = page.getByTestId("security-deepsec-entitlement-form");
  await expect(entitlementForm.getByText("組み込みロールの構造化データ権限は変更できません。", { exact: true })).toBeVisible();
  await expect(entitlementForm.getByRole("button", { name: "構造化データ権限を保存" })).toBeDisabled();

  await page.getByTestId("security-deepsec-entitlement-role-role-query").click();
  await entitlementForm.getByRole("button", { name: "データ権限を追加" }).click();
  await entitlementForm.getByLabel("範囲").fill("SALES");
  await entitlementForm.getByLabel("データ操作能力").selectOption("SENSITIVE_READ");
  await entitlementForm.getByRole("button", { name: "構造化データ権限を保存" }).click();

  expect(savedPayload).toEqual({
    version: 3,
    data_entitlements: [
      {
        resource_code: "NL2SQL_DEEPSEC_PROBE",
        scope_code: "SALES",
        capability: "SENSITIVE_READ",
      },
    ],
  });
  await expect(page.getByText("構造化データ権限を保存しました。", { exact: true })).toBeVisible();

  await page.getByTestId("security-deepsec-entitlement-role-role-archived").click();
  await expect(entitlementForm.getByText("アーカイブ済みロールの構造化データ権限は変更できません。", { exact: true })).toBeVisible();
  await expect(entitlementForm.getByRole("button", { name: "構造化データ権限を保存" })).toBeDisabled();

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
});

test("DeepSec は版管理 SQL を読み取り専用で順次適用し、検証結果を表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let applied = false;
  let applyRequests = 0;
  let applyPayload: unknown = null;
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: applied,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "NL2SQL_DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: applied ? { data_grants: 2 } : {},
      message: applied ? "Deep Data Security の検証オブジェクトは構成済みです。" : "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) =>
    fulfill(route, deepSecPlan(applied))
  );
  await page.route("**/api/security/deepsec/plan/V001/steps/1/apply", async (route) => {
    applyRequests += 1;
    applyPayload = route.request().postDataJSON();
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
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");
  await expect(page.locator("pre")).toHaveCount(1);
  await expect(page.locator("textarea")).toHaveCount(0);
  await expect(page.getByText("<secret:ORACLE_DEEPSEC_DATA_USER_PASSWORD>", { exact: false })).toBeVisible();
  const confirmationField = page.getByTestId("execution-confirmation-field");
  const confirmationInput = confirmationField.getByRole("textbox", { name: "実行確認語" });
  const applyButton = confirmationField.getByRole("button", { name: "このステップを適用" });
  await expect(applyButton).toBeDisabled();
  await confirmationInput.fill("ADMIN");
  await expect(confirmationField.getByText("不一致")).toBeVisible();
  await expect(applyButton).toBeDisabled();
  expect(applyRequests).toBe(0);
  await confirmationInput.fill("ADMIN_EXECUTE");
  await expect(confirmationField.getByText("確認済み")).toBeVisible();
  await expect(applyButton).toBeEnabled();
  await applyButton.click();
  await expect(page.getByText("適用済み", { exact: true }).first()).toBeVisible();
  expect(applyRequests).toBe(1);
  expect(applyPayload).toEqual({
    checksum: "a".repeat(64),
    confirmation: "ADMIN_EXECUTE",
  });

  await page.getByRole("button", { name: "Data Grant を検証" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "実行" }).click();
  await expect(page.getByText("no_context", { exact: true })).toBeVisible();
  await expect(page.getByText("sensitive_masked=true", { exact: true })).toBeVisible();

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});
