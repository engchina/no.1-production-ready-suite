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
  data_entitlements: [],
};

const deepSecTargetObject = {
  name: "EMPLOYEES",
  owner: "HR",
  qualified_name: "HR.EMPLOYEES",
  object_type: "TABLE",
  comment: "社員",
};

const deepSecTargetObjectDetail = {
  ...deepSecTargetObject,
  columns: [
    {
      column_name: "EMPLOYEE_ID",
      logical_name: "社員ID",
      data_type: "NUMBER",
      nullable: false,
      comment: "",
      sample_values: [],
    },
    {
      column_name: "DISPLAY_NAME",
      logical_name: "氏名",
      data_type: "VARCHAR2(80)",
      nullable: false,
      comment: "",
      sample_values: [],
    },
    {
      column_name: "DEPARTMENT_CODE",
      logical_name: "部門",
      data_type: "VARCHAR2(32 CHAR)",
      nullable: false,
      comment: "",
      sample_values: [],
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
    data_user: "DEEPSEC_DATA_USER",
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
          "CREATE END USER DEEPSEC_DATA_USER IDENTIFIED BY <secret:ORACLE_DEEPSEC_DATA_USER_PASSWORD>",
        ],
      },
      {
        step_no: 2,
        key: "application_context",
        title: "アプリケーションコンテキスト",
        description: "認証済み利用者を session context へ設定します。",
        checksum: "b".repeat(64),
        status: applied ? "APPLIED" : "PENDING",
        error_message: "",
        executed_at: applied ? "2026-07-19T00:01:00Z" : null,
        sql: ["CREATE OR REPLACE CONTEXT NL2SQL_APP_USER_CTX USING NL2SQL_DEEPSEC_CTX_PKG"],
      },
    ],
  };
}

async function mockDeepSecDataEntitlements(page: Page, rows: unknown[] = [systemRole]) {
  await mockDeepSecTargetObjects(page);
  await page.route("**/api/security/deepsec/data-entitlements", (route) => fulfill(route, rows));
}

async function mockDeepSecTargetObjects(page: Page) {
  await page.route("**/api/nl2sql/db-admin/objects**", (route) =>
    fulfill(route, {
      runtime: "oracle",
      owner: "",
      items: [deepSecTargetObject],
      total: 1,
      table_count: 1,
      view_count: 0,
      counts_included: false,
      next_cursor: null,
      refreshed_at: "2026-07-19T00:00:00Z",
      catalog_version: 1,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/EMPLOYEES**", (route) =>
    fulfill(route, deepSecTargetObjectDetail)
  );
}

test("ローカル DEBUG はログインせず SYSTEM_ADMIN として入り、状態を明示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      user_uuid: "00000000-0000-0000-0000-000000000000",
      login_user_id: "local-debug",
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
      user_uuid: "appearance-viewer",
      login_user_id: "appearance.viewer",
      display_name: "外観閲覧ユーザー",
      role_codes: ["APPEARANCE_VIEWER"],
      is_system_admin: false,
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
      user_uuid: "no-access",
      login_user_id: "no.access",
      display_name: "権限なしユーザー",
      role_codes: ["NO_ACCESS"],
      is_system_admin: false,
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
      await fulfill(route, "ログインユーザーIDまたはパスワードを確認してください。", 401);
      return;
    }
    await fulfill(route, { ...systemAdminMe, force_password_change: true, password_change_allowed: true });
  });
  await page.route("**/api/auth/password/change", (route) => fulfill(route, { changed: true }));

  await page.goto("/login");
  await page.getByLabel("ログインユーザーID").fill("SYSTEM");
  await page.getByLabel("パスワード").fill("WrongPass!123");
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByText("ログインユーザーIDまたはパスワードを確認してください。", { exact: true })).toBeVisible();

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
      user_uuid: "query-user",
      login_user_id: "query.user",
      display_name: "SQL 生成ユーザー",
      role_codes: ["QUERY_MENU"],
      is_system_admin: false,
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
      user_uuid: "forced-user",
      login_user_id: "forced.user",
      display_name: "初回ユーザー",
      role_codes: ["QUERY_MENU"],
      is_system_admin: false,
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
    user_uuid: "limited",
    login_user_id: "limited.user",
    display_name: "SQL 生成ユーザー",
    role_codes: ["QUERY_MENU"],
    is_system_admin: false,
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
    user_uuid: "limited-empty-profile",
    login_user_id: "limited.empty",
    display_name: "SQL 生成ユーザー",
    role_codes: ["QUERY_MENU"],
    is_system_admin: false,
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
    user_uuid: "query-only-sample-readonly",
    login_user_id: "query.only.sample",
    display_name: "SQL 生成ユーザー",
    role_codes: ["QUERY_MENU"],
    is_system_admin: false,
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
  let createdPayloadLoginUserId = "";
  let users = [
    {
      user_uuid: "admin-user",
      login_user_id: "SYSTEM",
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
      login_user_id: string;
      display_name: string;
      role_ids: string[];
    };
    createdPayloadLoginUserId = payload.login_user_id;
    createdPayloadRoleIds = payload.role_ids;
    const user = {
      user_uuid: "new-user",
      login_user_id: payload.login_user_id,
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
  await page.getByLabel("ログインユーザーID").fill("001");
  await page.getByLabel("表示名").fill("短いログインユーザーIDユーザー");
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
  expect(createdPayloadLoginUserId).toBe("001");
  expect(users.some((user) => user.login_user_id === "001")).toBe(true);
  expect(createdPayloadRoleIds).toEqual(["role-viewer"]);
});

test("ユーザー管理は一覧・作成・編集をテーブル管理型パネルで統一する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const users = [
    {
      user_uuid: "admin-user",
      login_user_id: "SYSTEM",
      display_name: "システム管理者",
      status: "ACTIVE",
      force_password_change: false,
      locked_until: null,
      version: 1,
      role_ids: ["role-system"],
      is_bootstrap_admin: true,
    },
    {
      user_uuid: "sales-user",
      login_user_id: "sales.user",
      display_name: "あ営業ユーザー",
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
  const salesUserRow = page.getByTestId("security-users-grid").locator("tbody tr").filter({ hasText: "営業ユーザー" });
  await expect(salesUserRow).toHaveAttribute("data-selected", "true");
  await expect(page.getByRole("region", { name: "あ営業ユーザー" })).toBeVisible();
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
        user_uuid: "archived-role-user",
        login_user_id: "archive.user",
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
        user_uuid: "restored-role-user",
        login_user_id: "restore.user",
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
    display_name: "あアプリ閲覧",
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
  const viewerRoleRow = grid.locator("tbody tr").filter({ hasText: "アプリ閲覧" });
  await expect(viewerRoleRow).toHaveAttribute("data-selected", "true");
  await expect(page.getByRole("region", { name: "あアプリ閲覧" })).toBeVisible();
  const systemRoleAction = page.getByTestId("security-roles-row-actions-role-system-trigger");
  await expect(systemRoleAction).toBeVisible();
  await expect(grid.getByRole("button", { name: "編集" })).toHaveCount(0);
  await expect(page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" })).toBeVisible();
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
  await expect(grid.getByText("アプリ閲覧")).toBeVisible();
  await expect(grid.getByText("システム管理者")).toHaveCount(0);
  await expect(viewerRoleRow).toHaveAttribute("data-selected", "true");
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
  let markdownRequests = 0;
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
  await page.route("**/api/nl2sql/profiles/default/ontology-markdown", async (route) => {
    markdownRequests += 1;
    await proposalsGate;
    try {
      await fulfill(route, {
        draft_markdown: "",
        published_markdown: "",
        draft_revision: null,
        published_revision: null,
        draft_etag: "",
        published_at: null,
      });
    } catch {
      // 画面遷移で abort 済みの request は fulfill できない場合がある。
    }
  });
  await page.route("**/api/nl2sql/profiles/default/ontology-build-jobs**", (route) =>
    fulfill(route, { jobs: [] })
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
    await expect.poll(() => markdownRequests).toBeGreaterThan(0);

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

test("DeepSec は3つの管理タブで認証・基盤構成・データ権限を分ける", async ({ page }, testInfo) => {
  if (testInfo.project.name === "desktop") {
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await mockDatabaseGateReady(page);
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: true,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: { data_grants: 2 },
      message: "構成済みです。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan(true)));
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");

  const dataUserTab = page.getByRole("tab", { name: "DATA USER 認証" });
  const foundationTab = page.getByRole("tab", { name: "基盤構成" });
  const dataPermissionsTab = page.getByRole("tab", { name: "データ権限" });
  await expect(page.getByRole("tab")).toHaveCount(3);
  await expect(dataUserTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "DATA USER 認証情報" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "V001.1 共有 DATA USER とロール" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "構造化データ権限" })).toHaveCount(0);

  await dataUserTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(foundationTab).toBeFocused();
  await expect(foundationTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "実行計画", exact: true })).toBeVisible();
  await expect(page.getByText("実行計画 V001.1–V001.2", { exact: true })).toHaveCount(0);
  await expect(page.getByText("共有 DATA USER とアプリケーションコンテキストを、定義済みの順序で準備します。", { exact: true })).toBeVisible();
  const step1 = page.getByTestId("security-deepsec-step-1");
  const step2 = page.getByTestId("security-deepsec-step-2");
  await expect(step1.getByText("V001.1", { exact: true })).toBeVisible();
  await expect(step1.getByRole("heading", { name: "V001.1 共有 DATA USER とロール" })).toBeVisible();
  await expect(step2.getByText("V001.2", { exact: true })).toBeVisible();
  await expect(step2.getByRole("heading", { name: "V001.2 アプリケーションコンテキスト" })).toBeVisible();
  await expect(page.getByTestId("security-deepsec-step-3")).toHaveCount(0);
  await expect(step1.getByRole("button", { name: "このステップを適用" })).toHaveCount(0);
  await expect(step2.getByRole("button", { name: "このステップを適用" })).toHaveCount(0);
  await expect(step1.getByText("適用日時", { exact: true })).toBeVisible();
  await expect(step1.locator("time")).toHaveAttribute("datetime", "2026-07-19T00:00:00Z");
  await expect(step1.locator("time")).toHaveText(/^2026\/07\/19 \d{2}:\d{2}$/);
  await expect(page.locator("pre:visible")).toHaveCount(0);
  await expect(page.getByText("SQL とチェックサムを表示", { exact: true })).toHaveCount(2);

  await page.keyboard.press("End");
  await expect(dataPermissionsTab).toBeFocused();
  await expect(dataPermissionsTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "構造化データ権限" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "ロール別 Data Grant ポリシー" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "実行計画", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("security-deepsec-step-3")).toHaveCount(0);
  await expect(page.getByText("NL2SQL_DEEPSEC_PROBE", { exact: false })).toHaveCount(0);
  await expect(page.getByTestId("security-deepsec-step-1")).toHaveCount(0);

  await page.keyboard.press("Home");
  await expect(dataUserTab).toBeFocused();
  await expect(dataUserTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("構成済み", { exact: true })).toBeVisible();
  await expectNoPageHorizontalScroll(page);
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
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: {},
      message: "未適用です。",
    });
  });
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan()));
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");
  await expect(page.getByText("構成状態を確認中", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "基盤構成" }).click();
  await expect(page.getByRole("heading", { name: "V001.1 共有 DATA USER とロール" })).toBeVisible();
  await expect(page.locator("pre:visible")).toHaveCount(0);
  await page.getByText("SQL とチェックサムを表示", { exact: true }).first().click();
  await expect(page.locator("pre:visible")).toHaveCount(1);
  await expect(page.getByText("CREATE END USER DEEPSEC_DATA_USER", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "データ権限" }).click();
  await expect(page.getByText("Data Grant を適用する前に", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "基盤構成へ" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Data Grant を検証" })).toBeDisabled();

  releaseStatus();
  await expect(page.getByText("未構成", { exact: true })).toBeVisible();
  await expectNoPageHorizontalScroll(page);
});

test("DeepSec は構成状態の取得失敗を Header Badge と再読込導線で示す", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, "構成状態を取得できませんでした。接続を確認して再試行してください。", 503)
  );
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan()));
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");

  await expect(page.getByText("状態取得失敗", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "再読込" })).toBeVisible();
  await expect(page.getByText("構成状態を取得できませんでした。", { exact: false })).toBeVisible();
  await expect(page.getByRole("tab", { name: "DATA USER 認証" })).toHaveAttribute("aria-selected", "true");
});

test("DeepSec は Thick mode でも SQL step をキーボード操作できる", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockDatabaseGateReady(page);
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: false,
      driver_mode: "thick",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
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
  await page.getByRole("tab", { name: "基盤構成" }).click();
  await expect(page.getByTestId("security-deepsec-step-1").getByTestId("execution-confirmation-field")).toHaveCount(0);
  await expect(page.getByTestId("security-deepsec-step-2").getByTestId("execution-confirmation-field")).toHaveCount(0);
  const applySection = page.getByTestId("security-deepsec-foundation-apply-section");
  const confirmationField = applySection.getByTestId("execution-confirmation-field");
  const confirmationInput = confirmationField.getByRole("textbox", { name: "実行確認語" });
  const applyButton = confirmationField.getByRole("button", { name: "基盤構成を適用" });
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
        data_user: "DEEPSEC_DATA_USER",
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
    await expect(page.getByText("未構成", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "基盤構成" }).click();
    await expect(page.getByText("基盤構成を始める前に、DATA USER パスワードを保存してください。", { exact: true })).toBeVisible();
    const applySection = page.getByTestId("security-deepsec-foundation-apply-section");
    await expect(applySection.getByRole("button", { name: "基盤構成を適用" })).toBeDisabled();
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
      data_user: "DEEPSEC_DATA_USER",
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
      data_user: "DEEPSEC_DATA_USER",
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
  await page.getByRole("tab", { name: "基盤構成" }).click();
  await expect(page.getByText("基盤構成を始める前に、DATA USER パスワードを保存してください。", { exact: true })).toBeVisible();
  await expect(page.getByTestId("security-deepsec-foundation-apply-section").getByRole("button", { name: "基盤構成を適用" })).toBeDisabled();
  await page.getByRole("button", { name: "DATA USER 認証へ" }).click();

  const password = page.getByLabel("DATA USER パスワード");
  await password.fill("DeepSecret!789");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  expect(savedPassword).toBe("DeepSecret!789");
  await expect(password).toHaveValue("");
  await expect(page.getByText("保存済み", { exact: true })).toBeVisible();
  await expect(page.getByText("API を再起動")).toHaveCount(0);
  await page.getByRole("tab", { name: "基盤構成" }).click();
  await expect(page.getByText("基盤構成を始める前に、DATA USER パスワードを保存してください。", { exact: true })).toHaveCount(0);
  const applySection = page.getByTestId("security-deepsec-foundation-apply-section");
  const confirmationField = applySection.getByTestId("execution-confirmation-field");
  const confirmationInput = confirmationField.getByRole("textbox", { name: "実行確認語" });
  const applyButton = confirmationField.getByRole("button", { name: "基盤構成を適用" });
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
    display_name: "あアプリ検索閲覧",
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
  const extraRoles = Array.from({ length: 5 }, (_, index) => ({
    ...queryRole,
    role_id: `role-extra-${index}`,
    role_code: `EXTRA_${index}`,
    display_name: `追加ロール${index + 1}`,
    version: 1,
    data_entitlements: [],
  }));
  let entitlementRoles: unknown[] = [systemRole, queryRole, archivedRole, ...extraRoles];
  let savedPayload: {
    version: number;
    data_entitlements: Array<{
      entitlement_id?: string;
      resource_code: string;
      scope_code: string;
      capability: string;
      target_owner: string;
      target_object: string;
      target_type: string;
      column_names: string[];
      scope_mode: string;
      scope_column: string;
      scope_filters: Array<Record<string, unknown>>;
    }>;
  } | null = null;
  let previewPayload: { data_entitlements: Array<Record<string, unknown>> } | null = null;
  const applyPayloads: Array<{ confirmation: string; entitlement_ids: string[] }> = [];
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: true,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: { data_grants: 2 },
      message: "構成済みです。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan(true)));
  const salesObject = {
    name: "ORDERS",
    owner: "SALES",
    qualified_name: "SALES.ORDERS",
    object_type: "TABLE",
    comment: "受注",
  };
  const salesObjectDetail = {
    ...salesObject,
    columns: [
      {
        column_name: "ORDER_ID",
        logical_name: "受注ID",
        data_type: "NUMBER",
        nullable: false,
        comment: "",
        sample_values: [],
      },
      {
        column_name: "CUSTOMER_NAME",
        logical_name: "顧客名",
        data_type: "VARCHAR2(120)",
        nullable: false,
        comment: "",
        sample_values: [],
      },
      {
        column_name: "REGION_CODE",
        logical_name: "地域",
        data_type: "VARCHAR2(32)",
        nullable: false,
        comment: "",
        sample_values: [],
      },
    ],
  };
  const expectedScopeFilters = [
    {
      column_name: "REGION_CODE",
      operator: "IN",
      value_type: "TEXT",
      value_source: "LITERAL",
      value: "",
      value_to: "",
      values: ["SALES", "HR"],
    },
    {
      column_name: "ORDER_ID",
      operator: "EQ",
      value_type: "NUMBER",
      value_source: "LOGIN_USER_ID",
      value: "",
      value_to: "",
      values: [],
    },
  ];
  const objectRequests: Array<{
    limit: string | null;
    cursor: string | null;
    owner: string | null;
    q: string | null;
  }> = [];
  await page.route("**/api/nl2sql/db-admin/objects**", async (route) => {
    const url = new URL(route.request().url());
    const cursor = url.searchParams.get("cursor");
    const owner = url.searchParams.get("owner");
    const q = url.searchParams.get("q");
    objectRequests.push({
      limit: url.searchParams.get("limit"),
      cursor,
      owner,
      q,
    });
    const items =
      owner === "SALES" || q === "ORDERS"
        ? [salesObject]
        : cursor === "deepsec-page-2"
          ? [salesObject]
          : [deepSecTargetObject];
    await fulfill(route, {
      runtime: "oracle",
      owner: owner ?? "",
      items,
      total: 2,
      table_count: 2,
      view_count: 0,
      counts_included: false,
      next_cursor: owner || q || cursor ? null : "deepsec-page-2",
      refreshed_at: "2026-07-19T00:00:00Z",
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/EMPLOYEES**", (route) =>
    fulfill(route, deepSecTargetObjectDetail)
  );
  await page.route("**/api/nl2sql/db-admin/tables/ORDERS**", (route) =>
    fulfill(route, salesObjectDetail)
  );
  await page.route("**/api/security/deepsec/data-entitlements", (route) =>
    fulfill(route, entitlementRoles)
  );
  await page.route("**/api/security/deepsec/data-entitlements/role-query/preview", async (route) => {
    const payload = route.request().postDataJSON() as {
      data_entitlements: Array<Record<string, unknown>>;
    };
    previewPayload = payload;
    await fulfill(route, {
      role_id: "role-query",
      data_entitlements: payload.data_entitlements.map((item) => ({
        entitlement_id: item.entitlement_id ?? "preview-0",
        data_grant_name: "NL2SQL_DG_PREVIEW",
        sql_checksum: "f".repeat(64),
        apply_status: "PENDING",
        apply_error_message: "",
        applied_at: null,
        sql: [`GRANT SELECT ON ${String(item.resource_code)} TO NL2SQL_APP_DB_ROLE`],
        checksum: "f".repeat(64),
        ...item,
      })),
    });
  });
  await page.route("**/api/security/deepsec/data-entitlements/role-query", async (route) => {
    savedPayload = route.request().postDataJSON();
    const updated = {
      ...queryRole,
      version: 4,
      data_entitlements:
        savedPayload?.data_entitlements.map((item, index) => ({
          entitlement_id: item.entitlement_id ?? `saved-${index}`,
          data_grant_name: item.entitlement_id ? "NL2SQL_DG_PREVIEW" : "NL2SQL_DG_SAVED",
          sql_checksum: "",
          apply_status: "PENDING",
          apply_error_message: "",
          applied_at: null,
          sql: [`GRANT SELECT ON ${item.resource_code} TO NL2SQL_APP_DB_ROLE`],
          checksum: "e".repeat(64),
          ...item,
        })) ?? [],
    };
    entitlementRoles = [systemRole, updated, archivedRole];
    await fulfill(route, updated);
  });
  await page.route("**/api/security/deepsec/data-entitlements/role-query/apply", async (route) => {
    const payload = route.request().postDataJSON() as {
      confirmation: string;
      entitlement_ids: string[];
    };
    applyPayloads.push(payload);
    const currentRole = entitlementRoles.find(
      (role) => (role as { role_id?: string }).role_id === "role-query"
    ) as Record<string, unknown> & { data_entitlements: Array<Record<string, unknown>> };
    entitlementRoles = [
      systemRole,
      {
        ...currentRole,
        data_entitlements: currentRole.data_entitlements.map((item) => ({
          ...item,
          apply_status: "APPLIED",
          apply_error_message: "",
        })),
      },
      archivedRole,
    ];
    await fulfill(route, {
      role_id: "role-query",
      status: "APPLIED",
      entitlement_ids: payload.entitlement_ids,
    });
  });

  await page.goto("/settings/security/deepsec");

  await page.getByRole("tab", { name: "データ権限" }).click();
  await expect(page.getByRole("heading", { name: "構造化データ権限" })).toBeVisible();
  const roleList = page.getByTestId("security-deepsec-entitlement-roles");
  await expect
    .poll(() =>
      roleList.evaluate((node) => ({
        scrolls: node.scrollHeight > node.clientHeight + 1,
        bounded: node.clientHeight <= 300,
      }))
    )
    .toEqual({ scrolls: true, bounded: true });
  await expect.poll(() => objectRequests[0]).toMatchObject({
    limit: "50",
    cursor: null,
    owner: null,
  });
  const entitlementForm = page.getByTestId("security-deepsec-entitlement-form");
  await expect(page.getByTestId("security-deepsec-entitlement-role-role-query")).toHaveAttribute(
    "aria-pressed",
    "true"
  );
  await expect(
    entitlementForm.getByText("組み込みロールの構造化データ権限は変更できません。", {
      exact: true,
    })
  ).toHaveCount(0);
  await expect(entitlementForm.getByRole("button", { name: "Data Grant を適用" })).toBeDisabled();

  await page.getByTestId("security-deepsec-entitlement-role-role-query").click();
  await entitlementForm.getByRole("button", { name: "データ権限を追加" }).click();
  const firstRule = entitlementForm.getByTestId("security-deepsec-entitlement-rule-0");
  await expect(firstRule).toHaveAttribute("open", "");
  await expect(firstRule.getByText("Data Grant", { exact: true })).toBeVisible();
  await expect(firstRule.getByText("Data Grant 1", { exact: true })).toHaveCount(0);
  const objectPicker = firstRule.getByTestId("security-deepsec-object-picker-0");
  await firstRule.locator("summary").click();
  await expect(objectPicker).toBeHidden();
  await firstRule.locator("summary").click();
  await expect(objectPicker).toBeVisible();
  const loadMoreButton = objectPicker.getByTestId("security-deepsec-object-picker-load-more-0");
  await expect(loadMoreButton).toBeVisible();
  await expect
    .poll(async () => {
      const [pickerBox, buttonBox] = await Promise.all([
        objectPicker.boundingBox(),
        loadMoreButton.boundingBox(),
      ]);

      return Boolean(
        pickerBox &&
          buttonBox &&
          buttonBox.x >= pickerBox.x - 1 &&
          buttonBox.x + buttonBox.width <= pickerBox.x + pickerBox.width + 1
      );
    })
    .toBeTruthy();
  await loadMoreButton.click();
  await expect.poll(() => objectRequests.some((request) => request.cursor === "deepsec-page-2")).toBeTruthy();
  await objectPicker.getByLabel("schema").fill("SALES");
  await expect.poll(() => objectRequests.some((request) => request.owner === "SALES")).toBeTruthy();
  await objectPicker.getByLabel("検索").fill("ORDERS");
  await expect.poll(() => objectRequests.some((request) => request.q === "ORDERS")).toBeTruthy();
  await objectPicker.getByRole("option", { name: /SALES\.ORDERS/ }).click();
  await expect(entitlementForm.getByText("CUSTOMER_NAME", { exact: true })).toBeVisible();
  const scopeModeSelect = entitlementForm.getByLabel("行 scope");
  const columnsFieldset = entitlementForm.getByRole("group", { name: /許可列/ });
  const columnsLegend = columnsFieldset.locator("legend");
  const columnActions = firstRule.getByTestId("security-deepsec-entitlement-column-selection-actions-0");
  const columnsGrid = firstRule.getByTestId("security-deepsec-entitlement-columns-grid-0");
  const selectAllColumnsButton = columnActions.getByTestId(
    "security-deepsec-entitlement-column-selection-actions-0-select"
  );
  const clearAllColumnsButton = columnActions.getByTestId(
    "security-deepsec-entitlement-column-selection-actions-0-clear"
  );
  await expect(scopeModeSelect).toBeVisible();
  await expect(columnsFieldset).toBeVisible();
  await expect(columnActions).toBeVisible();
  await expect(columnsGrid).toBeVisible();
  await expect(selectAllColumnsButton).toBeEnabled();
  await expect(clearAllColumnsButton).toBeDisabled();
  await selectAllColumnsButton.click();
  await expect(selectAllColumnsButton).toBeDisabled();
  await expect(clearAllColumnsButton).toBeEnabled();
  await expect(firstRule.getByRole("checkbox", { name: /ORDER_ID/ })).toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /CUSTOMER_NAME/ })).toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /REGION_CODE/ })).toBeChecked();
  await clearAllColumnsButton.click();
  await expect(selectAllColumnsButton).toBeEnabled();
  await expect(clearAllColumnsButton).toBeDisabled();
  await expect(firstRule.getByRole("checkbox", { name: /ORDER_ID/ })).not.toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /CUSTOMER_NAME/ })).not.toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /REGION_CODE/ })).not.toBeChecked();
  await expect(objectPicker.locator("#deepsec-entitlement-resource-0 [aria-hidden='true']")).toHaveText("*");
  await expect(firstRule.locator("summary").getByText("SALES.ORDERS", { exact: true })).toBeVisible();
  await expect(firstRule.getByText("Data Grant 1", { exact: true })).toHaveCount(0);
  const scopeModeLabelText = entitlementForm.getByTestId("security-deepsec-scope-mode-label-text-0");
  const scopeModeRequired = entitlementForm.locator(
    "label[for='deepsec-entitlement-scope-mode-0'] [aria-hidden='true']"
  );
  await expect(scopeModeLabelText).toHaveText("行 scope");
  await expect(scopeModeRequired).toHaveText("*");
  await expect(columnsFieldset.locator("legend [aria-hidden='true']")).toHaveText("*");
  await expect
    .poll(async () => {
      const [
        targetBox,
        columnsBox,
        columnsLegendBox,
        columnActionsBox,
        columnsGridBox,
        scopeBox,
        scopeLabelTextBox,
        scopeRequiredBox,
      ] = await Promise.all([
        objectPicker.boundingBox(),
        columnsFieldset.boundingBox(),
        columnsLegend.boundingBox(),
        columnActions.boundingBox(),
        columnsGrid.boundingBox(),
        scopeModeSelect.boundingBox(),
        scopeModeLabelText.boundingBox(),
        scopeModeRequired.boundingBox(),
      ]);
      const labelCenterY = scopeLabelTextBox
        ? scopeLabelTextBox.y + scopeLabelTextBox.height / 2
        : null;
      const requiredCenterY = scopeRequiredBox
        ? scopeRequiredBox.y + scopeRequiredBox.height / 2
        : null;

      return {
        columnsBelowTarget: Boolean(targetBox && columnsBox && columnsBox.y >= targetBox.y + targetBox.height - 1),
        columnActionsBelowLegend: Boolean(
          columnsLegendBox &&
            columnActionsBox &&
            columnActionsBox.y >= columnsLegendBox.y + columnsLegendBox.height + 3
        ),
        columnsGridBelowActions: Boolean(
          columnActionsBox &&
            columnsGridBox &&
            columnsGridBox.y >= columnActionsBox.y + columnActionsBox.height - 1
        ),
        scopeBelowColumns: Boolean(columnsBox && scopeBox && scopeBox.y >= columnsBox.y + columnsBox.height - 1),
        scopeStartsWithTarget: Boolean(targetBox && scopeBox && scopeBox.x <= targetBox.x + 1),
        scopeRequiredAboveSelect: Boolean(scopeRequiredBox && scopeBox && scopeRequiredBox.y + scopeRequiredBox.height <= scopeBox.y),
        scopeRequiredInlineWithLabel: Boolean(
          labelCenterY !== null &&
          requiredCenterY !== null &&
          Math.abs(labelCenterY - requiredCenterY) <= 3
        ),
      };
    })
    .toEqual({
      columnsBelowTarget: true,
      columnActionsBelowLegend: true,
      columnsGridBelowActions: true,
      scopeBelowColumns: true,
      scopeStartsWithTarget: true,
      scopeRequiredAboveSelect: true,
      scopeRequiredInlineWithLabel: true,
    });
  await expect(scopeModeSelect).not.toContainText("列値で制限");
  await scopeModeSelect.selectOption("FILTERS");
  const filterRow = entitlementForm.getByTestId("security-deepsec-scope-filter-0-0");
  await expect(filterRow.locator("#deepsec-scope-filter-column-0-0")).toContainText(
    "REGION_CODE · VARCHAR2(32)"
  );
  await filterRow.locator("#deepsec-scope-filter-column-0-0").selectOption("REGION_CODE");
  const valueSourceSelect = filterRow.locator("#deepsec-scope-filter-value-source-0-0");
  await expect(valueSourceSelect).toContainText("ログインユーザーID");
  await valueSourceSelect.selectOption("LOGIN_USER_ID");
  await expect(
    filterRow.getByTestId("security-deepsec-scope-filter-login-user-id-0-0")
  ).toBeVisible();
  await valueSourceSelect.selectOption("LITERAL");
  await expect
    .poll(async () => {
      const columnSelect = filterRow.locator("#deepsec-scope-filter-column-0-0");
      const operatorSelect = filterRow.locator("#deepsec-scope-filter-operator-0-0");
      const sourceSelect = filterRow.locator("#deepsec-scope-filter-value-source-0-0");
      const valueInput = filterRow.locator("#deepsec-scope-filter-value-0-0");
      const removeButton = filterRow.getByRole("button", { name: "条件を削除" });
      const [rowBox, columnBox, operatorBox, sourceBox, valueBox, removeBox] = await Promise.all([
        filterRow.boundingBox(),
        columnSelect.boundingBox(),
        operatorSelect.boundingBox(),
        sourceSelect.boundingBox(),
        valueInput.boundingBox(),
        removeButton.boundingBox(),
      ]);
      const separated = (
        left: NonNullable<typeof columnBox> | null,
        right: NonNullable<typeof columnBox> | null
      ) =>
        Boolean(
          left &&
            right &&
            (left.x + left.width <= right.x + 1 ||
              right.x + right.width <= left.x + 1 ||
              left.y + left.height <= right.y + 1 ||
              right.y + right.height <= left.y + 1)
        );

      return {
        columnInsideRow: Boolean(
          rowBox &&
            columnBox &&
            columnBox.x >= rowBox.x - 1 &&
            columnBox.x + columnBox.width <= rowBox.x + rowBox.width + 1
        ),
        columnReadableWidth: Boolean(
          rowBox && columnBox && columnBox.width >= Math.min(240, rowBox.width - 24)
        ),
        columnOperatorSeparated: separated(columnBox, operatorBox),
        operatorSourceSeparated: separated(operatorBox, sourceBox),
        sourceValueSeparated: separated(sourceBox, valueBox),
        valueDeleteSeparated: separated(valueBox, removeBox),
        removeInsideRow: Boolean(
          rowBox &&
            removeBox &&
            removeBox.x >= rowBox.x - 1 &&
            removeBox.x + removeBox.width <= rowBox.x + rowBox.width + 1
        ),
      };
    })
    .toEqual({
      columnInsideRow: true,
      columnReadableWidth: true,
      columnOperatorSeparated: true,
      operatorSourceSeparated: true,
      sourceValueSeparated: true,
      valueDeleteSeparated: true,
      removeInsideRow: true,
    });
  await filterRow.locator("#deepsec-scope-filter-operator-0-0").selectOption("IN");
  await filterRow.locator("#deepsec-scope-filter-values-0-0").fill("SALES, HR");
  await entitlementForm.getByRole("button", { name: "条件を追加" }).click();
  const numberFilterRow = entitlementForm.getByTestId("security-deepsec-scope-filter-0-1");
  await expect(numberFilterRow.locator("#deepsec-scope-filter-column-0-1")).toContainText(
    "ORDER_ID · NUMBER"
  );
  await numberFilterRow.locator("#deepsec-scope-filter-column-0-1").selectOption("ORDER_ID");
  const numberValueSourceSelect = numberFilterRow.locator(
    "#deepsec-scope-filter-value-source-0-1"
  );
  await expect(numberValueSourceSelect).toContainText("ログインユーザーID");
  await expect(numberFilterRow.locator("#deepsec-scope-filter-value-0-1")).toHaveAttribute(
    "inputmode",
    "numeric"
  );
  await numberValueSourceSelect.selectOption("LOGIN_USER_ID");
  await expect(
    numberFilterRow.getByTestId("security-deepsec-scope-filter-login-user-id-0-1")
  ).toBeVisible();
  await entitlementForm.getByRole("checkbox", { name: /CUSTOMER_NAME/ }).check();
  await entitlementForm.getByRole("checkbox", { name: /REGION_CODE/ }).check();
  await entitlementForm.getByText("SQL プレビュー", { exact: true }).click();
  const sqlPreview = entitlementForm.getByTestId("security-deepsec-sql-preview");
  const sqlPreviewButton = entitlementForm.getByTestId("security-deepsec-sql-preview-generate");
  await expect(sqlPreviewButton).toBeVisible();
  await expect
    .poll(async () => {
      const [previewBox, buttonBox] = await Promise.all([
        sqlPreview.boundingBox(),
        sqlPreviewButton.boundingBox(),
      ]);

      return Boolean(
        previewBox &&
          buttonBox &&
          buttonBox.x >= previewBox.x - 1 &&
          buttonBox.x + buttonBox.width <= previewBox.x + previewBox.width + 1
      );
    })
    .toBeTruthy();
  await expectNoPageHorizontalScroll(page);
  await sqlPreviewButton.click();

  await expect.poll(() => previewPayload).toEqual({
    data_entitlements: [
      {
        resource_code: "SALES.ORDERS",
        scope_code: "FILTERS",
        capability: "SELECT",
        target_owner: "SALES",
        target_object: "ORDERS",
        target_type: "TABLE",
        column_names: ["CUSTOMER_NAME", "REGION_CODE"],
        scope_mode: "FILTERS",
        scope_column: "",
        scope_filters: expectedScopeFilters,
      },
    ],
  });
  expect(savedPayload).toBeNull();
  await expect(page.getByText("SQL プレビューを生成しました。", { exact: true })).toBeVisible();
  await expect(entitlementForm.getByText("GRANT SELECT ON SALES.ORDERS TO NL2SQL_APP_DB_ROLE")).toBeVisible();
  await expect(entitlementForm.getByRole("button", { name: "ポリシーを保存" })).toHaveCount(0);
  const applyField = entitlementForm.getByTestId("execution-confirmation-field");
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN");
  await expect(applyField.getByRole("button", { name: "Data Grant を適用" })).toBeDisabled();
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await expect(applyField.getByRole("button", { name: "Data Grant を適用" })).toBeEnabled();
  await applyField.getByRole("button", { name: "Data Grant を適用" }).click();
  await expect.poll(() => savedPayload).toEqual({
    version: 3,
    data_entitlements: [
      {
        entitlement_id: "preview-0",
        resource_code: "SALES.ORDERS",
        scope_code: "FILTERS",
        capability: "SELECT",
        target_owner: "SALES",
        target_object: "ORDERS",
        target_type: "TABLE",
        column_names: ["CUSTOMER_NAME", "REGION_CODE"],
        scope_mode: "FILTERS",
        scope_column: "",
        scope_filters: expectedScopeFilters,
      },
    ],
  });
  await expect
    .poll(() => applyPayloads.at(-1))
    .toEqual({ confirmation: "ADMIN_EXECUTE", entitlement_ids: ["preview-0"] });
  await expect(page.getByText("Data Grant を適用しました。", { exact: true }).last()).toBeVisible();

  await entitlementForm.getByRole("button", { name: "データ権限を削除" }).click();
  await expect(entitlementForm.getByText("Data Grant 1", { exact: true })).toHaveCount(0);
  savedPayload = null;
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await expect(applyField.getByRole("button", { name: "Data Grant を適用" })).toBeEnabled();
  await applyField.getByRole("button", { name: "Data Grant を適用" }).click();
  await expect.poll(() => savedPayload).toEqual({
    version: 4,
    data_entitlements: [],
  });
  await expect
    .poll(() => applyPayloads.at(-1))
    .toEqual({ confirmation: "ADMIN_EXECUTE", entitlement_ids: [] });
  await expect(page.getByText("Data Grant を適用しました。", { exact: true }).last()).toBeVisible();

  await entitlementForm.getByRole("button", { name: "データ権限を追加" }).click();
  const replacementRule = entitlementForm.getByTestId("security-deepsec-entitlement-rule-0");
  const replacementPicker = replacementRule.getByTestId("security-deepsec-object-picker-0");
  await expect(replacementRule.getByText("Data Grant", { exact: true })).toBeVisible();
  await replacementPicker.getByRole("option", { name: /SALES\.ORDERS/ }).click();
  await replacementRule.getByRole("checkbox", { name: /ORDER_ID/ }).check();
  savedPayload = null;
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await applyField.getByRole("button", { name: "Data Grant を適用" }).click();
  await expect.poll(() => savedPayload).toEqual({
    version: 4,
    data_entitlements: [
      {
        resource_code: "SALES.ORDERS",
        scope_code: "*",
        capability: "SELECT",
        target_owner: "SALES",
        target_object: "ORDERS",
        target_type: "TABLE",
        column_names: ["ORDER_ID"],
        scope_mode: "ALL",
        scope_column: "",
        scope_filters: [],
      },
    ],
  });
  await expect
    .poll(() => applyPayloads.at(-1))
    .toEqual({ confirmation: "ADMIN_EXECUTE", entitlement_ids: ["saved-0"] });
  await expect(page.getByText("Data Grant を適用しました。", { exact: true }).last()).toBeVisible();

  await page.getByTestId("security-deepsec-entitlement-role-role-archived").click();
  await expect(
    entitlementForm.getByText("アーカイブ済みロールの構造化データ権限は変更できません。", {
      exact: true,
    })
  ).toBeVisible();
  await expect(entitlementForm.getByRole("button", { name: "Data Grant を適用" })).toBeDisabled();

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
});

test("DeepSec は基盤構成からDB構成を確認語つきで解除する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let applied = true;
  let statusRequests = 0;
  let planRequests = 0;
  let entitlementRequests = 0;
  let resetRequests = 0;
  let resetPayload: unknown = null;
  await page.route("**/api/security/deepsec/status", async (route) => {
    statusRequests += 1;
    await fulfill(route, {
      configured: applied,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: applied ? { managed_data_grants: 1 } : {},
      message: applied ? "Deep Data Security の基盤構成は適用済みです。" : "未適用です。",
    });
  });
  await page.route("**/api/security/deepsec/plan", async (route) => {
    planRequests += 1;
    await fulfill(route, deepSecPlan(applied));
  });
  await mockDeepSecTargetObjects(page);
  await page.route("**/api/security/deepsec/plan/V001/reset", async (route) => {
    resetRequests += 1;
    resetPayload = route.request().postDataJSON();
    applied = false;
    await fulfill(route, { version: "V001", status: "RESET", step_numbers: [1, 2, 3, 4] });
  });
  await page.route("**/api/security/deepsec/data-entitlements", async (route) => {
    entitlementRequests += 1;
    await fulfill(route, [systemRole]);
  });

  await page.goto("/settings/security/deepsec");
  await page.getByRole("tab", { name: "基盤構成" }).click();

  const foundationPanel = page.locator("#security-deepsec-panel-foundation");
  await expect(foundationPanel.getByText("適用済み", { exact: true }).first()).toBeVisible();
  const foundationHeader = foundationPanel.locator(":scope > div").first();
  await expect(foundationHeader.getByRole("button", { name: "DeepSec 構成を解除" })).toHaveCount(0);

  const planTitle = foundationPanel.getByRole("heading", { name: "実行計画" });
  const resetSection = foundationPanel.getByTestId("security-deepsec-reset-section");
  await expect(resetSection).toBeVisible();
  await expect(resetSection).toHaveText(/構成解除/u);
  await expect(
    resetSection.getByTestId("execution-confirmation-field")
  ).toBeHidden();
  const planBox = await planTitle.boundingBox();
  const resetBox = await resetSection.boundingBox();
  expect(planBox).not.toBeNull();
  expect(resetBox).not.toBeNull();
  expect(resetBox!.y).toBeGreaterThan(planBox!.y);

  const resetSummary = resetSection.locator("summary");
  await resetSummary.click();
  expect(await resetSummary.evaluate((node) => node.matches(":focus-visible"))).toBe(false);
  await expect(
    resetSection.getByText("Data Grants、Data Grants Only、コンテキスト", { exact: false })
  ).toBeVisible();

  const resetConfirmationField = resetSection
    .getByTestId("execution-confirmation-field")
    .filter({ hasText: "ADMIN_RESET" });
  const resetInput = resetConfirmationField.getByRole("textbox", { name: "実行確認語" });
  const resetButton = resetConfirmationField.getByRole("button", { name: "DeepSec 構成を解除" });
  await expect(resetButton).toBeDisabled();
  await resetInput.fill("ADMIN");
  await expect(resetConfirmationField.getByText("不一致")).toBeVisible();
  await expect(resetButton).toBeDisabled();
  expect(resetRequests).toBe(0);

  await resetInput.fill("ADMIN_RESET");
  await expect(resetConfirmationField.getByText("確認済み")).toBeVisible();
  await expect(resetButton).toBeEnabled();
  await resetButton.click();

  await expect(page.getByText("DeepSec 構成を解除しました。", { exact: true })).toBeVisible();
  await expect(foundationPanel.getByText("未適用", { exact: true }).first()).toBeVisible();
  await expect(foundationPanel.getByTestId("security-deepsec-reset-section")).toHaveCount(0);
  expect(resetRequests).toBe(1);
  expect(resetPayload).toEqual({ confirmation: "ADMIN_RESET" });
  expect(statusRequests).toBeGreaterThanOrEqual(2);
  expect(planRequests).toBeGreaterThanOrEqual(2);
  expect(entitlementRequests).toBeGreaterThanOrEqual(2);
  await expectNoPageHorizontalScroll(page);
});

test("DeepSec は版管理 SQL を読み取り専用で順次適用し、検証結果を表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let applied = false;
  const applyRequests: number[] = [];
  const applyPayloads: Record<number, unknown> = {};
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: applied,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: applied ? { managed_data_grants: 1 } : {},
      message: applied ? "Deep Data Security の基盤構成は適用済みです。" : "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) =>
    fulfill(route, deepSecPlan(applied))
  );
  await page.route("**/api/security/deepsec/plan/V001/steps/1/apply", async (route) => {
    applyRequests.push(1);
    applyPayloads[1] = route.request().postDataJSON();
    await fulfill(route, { version: "V001", step_no: 1, status: "APPLIED" });
  });
  await page.route("**/api/security/deepsec/plan/V001/steps/2/apply", async (route) => {
    applyRequests.push(2);
    applyPayloads[2] = route.request().postDataJSON();
    applied = true;
    await fulfill(route, { version: "V001", step_no: 2, status: "APPLIED" });
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
  await page.getByRole("tab", { name: "基盤構成" }).click();
  await expect(page.locator("pre:visible")).toHaveCount(0);
  await expect(page.locator("textarea")).toHaveCount(0);
  await page.getByText("SQL とチェックサムを表示", { exact: true }).first().click();
  await expect(page.locator("pre:visible")).toHaveCount(1);
  await expect(page.getByText("<secret:ORACLE_DEEPSEC_DATA_USER_PASSWORD>", { exact: false })).toBeVisible();
  await expect(page.getByTestId("security-deepsec-step-1").getByTestId("execution-confirmation-field")).toHaveCount(0);
  await expect(page.getByTestId("security-deepsec-step-2").getByTestId("execution-confirmation-field")).toHaveCount(0);
  const applySection = page.getByTestId("security-deepsec-foundation-apply-section");
  await expect(applySection).toBeVisible();
  const confirmationField = applySection.getByTestId("execution-confirmation-field");
  const confirmationInput = confirmationField.getByRole("textbox", { name: "実行確認語" });
  const applyButton = confirmationField.getByRole("button", { name: "基盤構成を適用" });
  await expect(applyButton).toBeDisabled();
  await confirmationInput.fill("ADMIN");
  await expect(confirmationField.getByText("不一致")).toBeVisible();
  await expect(applyButton).toBeDisabled();
  expect(applyRequests).toEqual([]);
  await confirmationInput.fill("ADMIN_EXECUTE");
  await expect(confirmationField.getByText("確認済み")).toBeVisible();
  await expect(applyButton).toBeEnabled();
  await applyButton.click();
  const appliedStep = page.getByTestId("security-deepsec-step-1");
  await expect(appliedStep.getByText("適用済み", { exact: true })).toBeVisible();
  await expect(appliedStep.getByTestId("execution-confirmation-field")).toHaveCount(0);
  await expect(appliedStep.getByText("適用日時", { exact: true })).toBeVisible();
  await expect(appliedStep.locator("time")).toHaveAttribute("datetime", "2026-07-19T00:00:00Z");
  await expect(appliedStep.locator("time")).toHaveText(/^2026\/07\/19 \d{2}:\d{2}$/);
  await expect(page.getByTestId("security-deepsec-foundation-apply-section")).toHaveCount(0);
  expect(applyRequests).toEqual([1, 2]);
  expect(applyPayloads[1]).toEqual({
    checksum: "a".repeat(64),
    confirmation: "ADMIN_EXECUTE",
  });
  expect(applyPayloads[2]).toEqual({
    checksum: "b".repeat(64),
    confirmation: "ADMIN_EXECUTE",
  });

  await page.getByRole("tab", { name: "データ権限" }).click();
  await page.getByRole("button", { name: "Data Grant を検証" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "実行" }).click();
  await expect(page.getByText("no_context", { exact: true })).toBeVisible();
  await expect(page.getByText("sensitive_masked=true", { exact: true })).toBeVisible();

  const hasHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  expect(hasHorizontalOverflow).toBe(false);
});

test("DeepSec 基盤構成の一括適用は未適用 step だけを順番に実行する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let step2Applied = false;
  const applyRequests: number[] = [];
  const partialPlan = () => {
    const nextPlan = deepSecPlan(false);
    nextPlan.steps[0] = {
      ...nextPlan.steps[0],
      status: "APPLIED",
      executed_at: "2026-07-19T00:00:00Z",
    };
    if (step2Applied) {
      nextPlan.steps[1] = {
        ...nextPlan.steps[1],
        status: "APPLIED",
        executed_at: "2026-07-19T00:01:00Z",
      };
    }
    return nextPlan;
  };

  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: step2Applied,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: step2Applied ? { managed_data_grants: 1 } : {},
      message: step2Applied ? "Deep Data Security の基盤構成は適用済みです。" : "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, partialPlan()));
  await page.route("**/api/security/deepsec/plan/V001/steps/1/apply", async (route) => {
    applyRequests.push(1);
    await fulfill(route, { version: "V001", step_no: 1, status: "APPLIED" });
  });
  await page.route("**/api/security/deepsec/plan/V001/steps/2/apply", async (route) => {
    applyRequests.push(2);
    step2Applied = true;
    await fulfill(route, { version: "V001", step_no: 2, status: "APPLIED" });
  });
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");
  await page.getByRole("tab", { name: "基盤構成" }).click();

  const applySection = page.getByTestId("security-deepsec-foundation-apply-section");
  const confirmationField = applySection.getByTestId("execution-confirmation-field");
  await confirmationField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await confirmationField.getByRole("button", { name: "基盤構成を適用" }).click();

  expect(applyRequests).toEqual([2]);
  await expect(page.getByTestId("security-deepsec-step-2").getByText("適用済み", { exact: true })).toBeVisible();
  await expect(page.getByTestId("security-deepsec-foundation-apply-section")).toHaveCount(0);
  await expectNoPageHorizontalScroll(page);
});

test("DeepSec 基盤構成の一括適用は step 失敗時に後続を実行しない", async ({ page }) => {
  await mockDatabaseGateReady(page);
  let step2Requests = 0;

  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: false,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: {},
      message: "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan(false)));
  await page.route("**/api/security/deepsec/plan/V001/steps/1/apply", (route) =>
    fulfill(route, "V001.1 の適用に失敗しました。", 500)
  );
  await page.route("**/api/security/deepsec/plan/V001/steps/2/apply", async (route) => {
    step2Requests += 1;
    await fulfill(route, { version: "V001", step_no: 2, status: "APPLIED" });
  });
  await mockDeepSecDataEntitlements(page);

  await page.goto("/settings/security/deepsec");
  await page.getByRole("tab", { name: "基盤構成" }).click();

  const applySection = page.getByTestId("security-deepsec-foundation-apply-section");
  const confirmationField = applySection.getByTestId("execution-confirmation-field");
  await confirmationField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await confirmationField.getByRole("button", { name: "基盤構成を適用" }).click();

  await expect(applySection.getByText("V001.1 の適用に失敗しました。", { exact: true })).toBeVisible();
  expect(step2Requests).toBe(0);
  await expect(applySection).toBeVisible();
  await expectNoPageHorizontalScroll(page);
});
