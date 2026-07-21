import { expect, test, type Page, type Route } from "@playwright/test";
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
  deepsecEnabled = true
) {
  return {
    version: "V001",
    driver_mode: driverMode,
    deepsec_enabled: deepsecEnabled,
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
  };
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
  await expect(sidebar.getByRole("link", { name: "SELECT SQL を実行" })).toBeVisible();
  await expect(sidebar.getByText("管理 SQL を実行", { exact: true })).toHaveCount(0);

  await page.goto("/direct-sql");
  await expect(page.getByRole("heading", { level: 1, name: "SELECT SQL を実行" })).toBeVisible();
  await expect(page.getByText("このページは参照できますが、SQL の生成・実行には「検索を実行」権限が必要です。", { exact: true })).toBeVisible();
  await expect(page.getByLabel("SQL", { exact: true })).toHaveCount(0);

  await page.goto("/admin-sql");
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();

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
  await page.getByTestId("security-users-actions").getByRole("button", { name: "新規作成" }).click();
  await page.getByLabel("ログイン名").fill("sales.user");
  await page.getByLabel("表示名").fill("営業ユーザー");
  await page.getByLabel("システム管理者").check();
  await page.getByLabel("検索閲覧").check();
  await page.locator("#security-users-panel-create").getByRole("button", { name: "新規作成", exact: true }).click();

  await expect(page.getByText("一時パスワードは今回だけ表示されます。安全な方法で利用者へ伝えてください。", { exact: true })).toBeVisible();
  await expect(page.getByText("RandomStrong!Pass123", { exact: true })).toBeVisible();
  expect(csrfObserved).toBe(true);
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
        permissions: ["search.view"],
        data_entitlements: [],
      },
    ])
  );
  await page.route("**/api/security/users", (route) => fulfill(route, users));

  await page.goto("/settings/security/users");

  const listStyle = await topLevelPanelStyle(page, "list", "security-users");
  await expect(page.getByTestId("security-users-grid")).toBeVisible();
  await expect(page.getByTestId("security-users-grid").locator("tbody tr")).toHaveCount(2);
  await page.getByTestId("security-users-search").fill("sales");
  await expect(page.getByTestId("security-users-grid").getByText("営業ユーザー")).toBeVisible();
  await expect(page.getByTestId("security-users-grid").getByText("システム管理者")).toHaveCount(0);
  await page.getByTestId("security-users-search").fill("");

  await page.getByTestId("security-users-actions").getByRole("button", { name: "新規作成" }).click();
  expect(await topLevelPanelStyle(page, "create", "security-users")).toEqual(listStyle);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-users-panel-list")).toBeVisible();

  await page.getByTestId("security-users-grid").getByRole("button", { name: "編集" }).first().click();
  expect(await topLevelPanelStyle(page, "edit", "security-users")).toEqual(listStyle);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-users-panel-list")).toBeVisible();
  await expectNoPageHorizontalScroll(page);
});

test("ロール・権限管理はカード型リストではなくテーブル一覧と詳細で表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const permissionRows = [
    {
      code: "security.users.view",
      group: "security",
      label: "ユーザーを表示",
      description: "ユーザー管理を表示します。",
      implies: [],
    },
    {
      code: "security.users.manage",
      group: "security",
      label: "ユーザーを管理",
      description: "ユーザーを作成・更新します。",
      implies: ["security.users.view"],
    },
  ];
  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "SECURITY_VIEWER",
    display_name: "セキュリティ閲覧",
    description: "表示のみ",
    is_built_in: false,
    permissions: ["security.users.view"],
    data_entitlements: [],
  };
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole, viewerRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, permissionRows));

  await page.goto("/settings/security/roles");

  const listStyle = await topLevelPanelStyle(page, "list", "security-roles");
  const grid = page.getByTestId("security-roles-grid");
  await expect(grid).toBeVisible();
  await expect(grid.getByRole("columnheader", { name: "ロール" })).toBeVisible();
  await expect(grid.getByRole("columnheader", { name: "機能権限" })).toBeVisible();
  await expect(grid.locator("tbody tr")).toHaveCount(2);
  await page.getByTestId("security-roles-search").fill("閲覧");
  await expect(grid.getByText("セキュリティ閲覧")).toBeVisible();
  await expect(grid.getByText("システム管理者")).toHaveCount(0);
  await page.getByTestId("security-roles-search").fill("");

  await page.getByTestId("security-roles-actions").getByRole("button", { name: "新規作成" }).click();
  expect(await topLevelPanelStyle(page, "create", "security-roles")).toEqual(listStyle);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-roles-panel-list")).toBeVisible();

  await grid.getByRole("button", { name: "編集" }).first().click();
  expect(await topLevelPanelStyle(page, "edit", "security-roles")).toEqual(listStyle);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#security-roles-panel-list")).toBeVisible();
  await expectNoPageHorizontalScroll(page);
});

test("ロール・権限管理はオントロジー提案取得が遅延しても読み込み完了する", async ({ page }) => {
  await mockDatabaseGateReady(page);
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
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));

  try {
    await page.goto("/ontology-build?profile=default");
    await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
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
      deepsec_enabled: true,
      end_user: "NL2SQL_APP_END_USER",
      objects: {},
      message: "未適用です。",
    });
  });
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan()));

  await page.goto("/settings/security/deepsec");
  await expect(page.getByText("構成状態を確認しています。", { exact: true })).toBeVisible();
  await expect(page.locator("pre")).toHaveCount(1);
  await expect(page.getByText("CREATE END USER NL2SQL_APP_END_USER", { exact: false })).toBeVisible();
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
      end_user: "NL2SQL_APP_END_USER",
      objects: {},
      message: "未適用です。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) =>
    fulfill(route, deepSecPlan(false, "thick"))
  );

  await page.goto("/settings/security/deepsec");

  await expect(page.getByText("Deep Data Security が無効です。", { exact: false })).toHaveCount(0);
  const applyButton = page.getByRole("button", { name: "このステップを適用" });
  await expect(applyButton).toBeEnabled();
  await applyButton.focus();
  await expect(applyButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
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
        deepsec_enabled: false,
        end_user: "NL2SQL_APP_END_USER",
        objects: {},
        message: "未適用です。",
      })
    );
    await page.route("**/api/security/deepsec/plan", (route) =>
      fulfill(route, deepSecPlan(false, driverMode, false))
    );

    await page.goto("/settings/security/deepsec");

    const disabledBanner = page
      .getByRole("status")
      .filter({ hasText: "Deep Data Security が無効です。" });
    await expect(disabledBanner).toBeVisible();
    await expect(page.getByRole("button", { name: "このステップを適用" })).toBeDisabled();
    await expectNoPageHorizontalScroll(page);
  });
}

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
    fulfill(route, deepSecPlan(applied))
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
