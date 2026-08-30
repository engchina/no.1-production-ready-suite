import {
  expect,
  test,
  type Locator,
  type Page,
  type Route,
  type TestInfo,
} from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { expectSplitPaneReservedTrack } from "./_helpers/fixed-split-pane";

function envelope(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

function problemEnvelope({
  status,
  code,
  title,
  detail,
  requestId,
  retryable = false,
  fieldErrors = [],
}: {
  status: number;
  code: string;
  title: string;
  detail: string;
  requestId: string;
  retryable?: boolean;
  fieldErrors?: Array<{ pointer: string; code: string; message: string }>;
}) {
  return {
    data: null,
    error_messages: [detail],
    warning_messages: [],
    error_code: code,
    problem: {
      type: `urn:nl2sql:problem:${code.toLowerCase().replaceAll("_", "-")}`,
      title,
      status,
      detail,
      code,
      request_id: requestId,
      retryable,
      field_errors: fieldErrors,
    },
  };
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

async function expectNoElementHorizontalOverflow(locator: Locator) {
  await expect
    .poll(() =>
      locator.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)
    )
    .toBeTruthy();
}

function expectedInformationRows(testInfo: TestInfo) {
  return testInfo.project.name === "mobile-375" ? 5 : 8;
}

async function expectBoundedSecurityTableScroll(
  scrollRegion: Locator,
  visibleRowLimit: number
) {
  const metrics = await scrollRegion.evaluate((node, expectedRows) => {
    const regionRect = node.getBoundingClientRect();
    const header = node.querySelector("thead");
    const rows = Array.from(node.querySelectorAll("tbody tr"));
    if (!header) throw new Error("security table header is missing");
    const headerRect = header.getBoundingClientRect();
    const computed = window.getComputedStyle(node);
    const rootFontSize = Number.parseFloat(
      window.getComputedStyle(document.documentElement).fontSize
    );
    const visibleRows = rows.filter((row) => {
      const rect = row.getBoundingClientRect();
      return rect.top >= headerRect.bottom - 1 && rect.bottom <= regionRect.bottom + 1;
    });
    return {
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      maxHeight: Number.parseFloat(computed.maxHeight),
      overflowX: computed.overflowX,
      overflowY: computed.overflowY,
      headerPosition: window.getComputedStyle(header).position,
      visibleRowCount: visibleRows.length,
      rootFontSize,
      expectedRows,
    };
  }, visibleRowLimit);

  const expectedMaxHeight = metrics.rootFontSize * (2.5 + 3.5 * metrics.expectedRows);
  expect(metrics.maxHeight).toBeGreaterThanOrEqual(expectedMaxHeight - 2);
  expect(metrics.maxHeight).toBeLessThanOrEqual(expectedMaxHeight + 2);
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  expect(metrics.visibleRowCount).toBe(visibleRowLimit);
  expect(metrics.headerPosition).toBe("sticky");
  expect(metrics.overflowX).toBe("auto");
  expect(metrics.overflowY).toBe("auto");

  await scrollRegion.evaluate((node) => node.scrollTo({ top: 0, left: 0 }));
  await scrollRegion.focus();
  await expect(scrollRegion).toBeFocused();
  await scrollRegion.press("PageDown");
  await expect.poll(() => scrollRegion.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);

  await scrollRegion.evaluate((node) => node.scrollTo({ top: node.scrollHeight }));
  const bottomState = await scrollRegion.evaluate((node) => {
    const regionRect = node.getBoundingClientRect();
    const header = node.querySelector("thead");
    const lastRow = node.querySelector("tbody tr:last-child");
    if (!header || !lastRow) throw new Error("security table rows are missing");
    const headerRect = header.getBoundingClientRect();
    const lastRowRect = lastRow.getBoundingClientRect();
    return {
      headerOffset: Math.abs(headerRect.top - regionRect.top),
      lastRowVisible:
        lastRowRect.top >= headerRect.bottom - 1 &&
        lastRowRect.bottom <= regionRect.bottom + 1,
    };
  });
  expect(bottomState.headerOffset).toBeLessThanOrEqual(1);
  expect(bottomState.lastRowVisible).toBe(true);
}

async function expectEqualFilterWidths(search: Locator, owner: Locator) {
  await expect
    .poll(async () => {
      const [searchBox, ownerBox] = await Promise.all([search.boundingBox(), owner.boundingBox()]);
      if (!searchBox || !ownerBox) return Number.POSITIVE_INFINITY;
      return Math.abs(searchBox.width - ownerBox.width);
    })
    .toBeLessThanOrEqual(2);
}

async function waitForAnimationFrames(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => window.requestAnimationFrame(() => resolve()));
      })
  );
}

async function setMainScrollTop(page: Page, top: number) {
  const main = page.getByRole("main", { name: "メイン領域" });
  await main.evaluate((node, value) => {
    node.scrollTop = value;
  }, top);
  await waitForAnimationFrames(page);
}

async function expectMainScrollPreserved(page: Page, action: () => Promise<void>) {
  const main = page.getByRole("main", { name: "メイン領域" });
  const before = await main.evaluate((node) => node.scrollTop);
  await action();
  await waitForAnimationFrames(page);
  await expect
    .poll(async () => {
      const after = await main.evaluate((node) => node.scrollTop);
      return Math.abs(after - before);
    })
    .toBeLessThanOrEqual(2);
}

async function expectMainScrollPreservedAfterClick(page: Page, locator: Locator) {
  await locator.scrollIntoViewIfNeeded();
  await waitForAnimationFrames(page);
  await expectMainScrollPreserved(page, async () => {
    await locator.click();
  });
}

async function expectScrollPositionsPreserved(
  page: Page,
  containers: Locator[],
  action: () => Promise<void>
) {
  const before = await Promise.all(
    containers.map((container) =>
      container.evaluate((node) => ({ top: node.scrollTop, left: node.scrollLeft }))
    )
  );
  await action();
  await waitForAnimationFrames(page);
  await expect
    .poll(async () => {
      const after = await Promise.all(
        containers.map((container) =>
          container.evaluate((node) => ({ top: node.scrollTop, left: node.scrollLeft }))
        )
      );
      return Math.max(
        ...after.flatMap((position, index) => [
          Math.abs(position.top - before[index].top),
          Math.abs(position.left - before[index].left),
        ])
      );
    })
    .toBeLessThanOrEqual(2);
}

async function setElementScrollTop(locator: Locator, top: number) {
  await locator.evaluate((node, value) => {
    node.scrollTop = value;
  }, top);
  await expect
    .poll(() => locator.evaluate((node) => node.scrollTop >= 0))
    .toBeTruthy();
}

async function expectElementScrollTop(locator: Locator, top: number) {
  await expect
    .poll(() => locator.evaluate((node) => Math.round(node.scrollTop)))
    .toBe(top);
}

async function expectElementCenterUnobscured(locator: Locator) {
  await locator.scrollIntoViewIfNeeded();
  await expect(locator).toBeVisible();
  await expect
    .poll(() =>
      locator.evaluate((node) => {
        const box = node.getBoundingClientRect();
        const topElement = document.elementFromPoint(
          box.left + box.width / 2,
          box.top + box.height / 2
        );
        return Boolean(topElement && (topElement === node || node.contains(topElement)));
      })
    )
    .toBeTruthy();
}

async function expectDeepSecDataGrantRegionsDoNotOverlap(page: Page, selectedRuleTestId: string) {
  await expect
    .poll(() =>
      page.evaluate((ruleId) => {
        const selectors = [
          ["roles", '[data-testid="security-deepsec-entitlement-roles"]'],
          ["rules", '[data-testid="security-deepsec-entitlement-rules-list"]'],
          ["editor", `[data-testid="${ruleId}"]`],
          ["actions", '[data-testid="security-deepsec-entitlement-action-region"]'],
        ] as const;
        const boxes = selectors.flatMap(([name, selector]) => {
          const node = document.querySelector(selector);
          if (!node) return [];
          const rect = node.getBoundingClientRect();
          return [
            {
              name,
              left: rect.left,
              right: rect.right,
              top: rect.top,
              bottom: rect.bottom,
              width: rect.width,
              height: rect.height,
            },
          ];
        });
        const overlaps: string[] = [];
        for (let leftIndex = 0; leftIndex < boxes.length; leftIndex += 1) {
          for (let rightIndex = leftIndex + 1; rightIndex < boxes.length; rightIndex += 1) {
            const left = boxes[leftIndex];
            const right = boxes[rightIndex];
            if (left.width <= 0 || left.height <= 0 || right.width <= 0 || right.height <= 0) {
              overlaps.push(`${left.name}:${right.name}:zero-size`);
              continue;
            }
            const separated =
              left.right <= right.left + 1 ||
              right.right <= left.left + 1 ||
              left.bottom <= right.top + 1 ||
              right.bottom <= left.top + 1;
            if (!separated) {
              overlaps.push(`${left.name}:${right.name}`);
            }
          }
        }
        return overlaps;
      }, selectedRuleTestId)
    )
    .toEqual([]);
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
  allowed_profile_ids: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/security/profile-access/profiles**", (route) => fulfill(route, []));
});

test("ユーザー・ロール一覧はデスクトップ8行・モバイル5行で固定表頭の内部スクロールになる", async ({
  page,
}, testInfo) => {
  await mockDatabaseGateReady(page);
  const visibleRowLimit = expectedInformationRows(testInfo);
  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "SECURITY_VIEWER",
    display_name: "セキュリティ閲覧",
    description: "表示のみ",
    is_built_in: false,
    permissions: ["menu.security_users", "menu.security_roles"],
  };
  const users = Array.from({ length: visibleRowLimit + 1 }, (_, index) => {
    const sequence = String(index + 1).padStart(2, "0");
    return {
      user_uuid: `scroll-user-${sequence}`,
      login_user_id: `scroll.user.${sequence}`,
      display_name: `スクロールユーザー ${sequence}`,
      status: "ACTIVE",
      force_password_change: false,
      locked_until: null,
      version: 1,
      role_ids: [viewerRole.role_id],
      is_bootstrap_admin: false,
    };
  });
  const roles = Array.from({ length: visibleRowLimit + 1 }, (_, index) => {
    const sequence = String(index + 1).padStart(2, "0");
    return {
      ...viewerRole,
      role_id: `scroll-role-${sequence}`,
      role_code: `SCROLL_ROLE_${sequence}`,
      display_name: `スクロールロール ${sequence}`,
      version: index + 1,
    };
  });

  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [systemRole, viewerRole])
  );
  await page.route("**/api/security/users", (route) => fulfill(route, users));

  await page.goto("/settings/security/users");
  const userScrollRegion = page.getByTestId("security-users-scroll-region");
  await expect(userScrollRegion).toHaveAttribute("role", "region");
  await expect(userScrollRegion).toHaveAttribute(
    "aria-label",
    "ユーザー一覧。必要に応じて縦方向または横方向にスクロールできます。"
  );
  await expect(userScrollRegion.locator("tbody tr")).toHaveCount(visibleRowLimit + 1);

  const lastUser = users.at(-1)!;
  await page.getByTestId("security-users-search").fill(lastUser.display_name);
  await expect(userScrollRegion.locator("tbody tr")).toHaveCount(1);
  await expect
    .poll(() =>
      userScrollRegion.evaluate((node) => node.scrollHeight <= node.clientHeight + 1)
    )
    .toBeTruthy();
  await page.getByTestId("security-users-search").fill("");
  await expect(userScrollRegion.locator("tbody tr")).toHaveCount(visibleRowLimit + 1);
  await expectBoundedSecurityTableScroll(userScrollRegion, visibleRowLimit);

  const lastUserRow = userScrollRegion.locator("tbody tr").last();
  await lastUserRow.locator("td").nth(1).click();
  await expect(lastUserRow).toHaveAttribute("data-selected", "true");
  await expect(
    page.getByRole("heading", { name: lastUser.display_name, exact: true })
  ).toBeVisible();
  await expectNoPageHorizontalScroll(page);

  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, roles)
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));

  await page.goto("/settings/security/roles");
  const roleScrollRegion = page.getByTestId("security-roles-scroll-region");
  await expect(roleScrollRegion).toHaveAttribute("role", "region");
  await expect(roleScrollRegion).toHaveAttribute(
    "aria-label",
    "ロール一覧。必要に応じて縦方向または横方向にスクロールできます。"
  );
  await expect(roleScrollRegion.locator("tbody tr")).toHaveCount(visibleRowLimit + 1);

  const lastRole = roles.at(-1)!;
  await page.getByTestId("security-roles-search").fill(lastRole.display_name);
  await expect(roleScrollRegion.locator("tbody tr")).toHaveCount(1);
  await expect
    .poll(() =>
      roleScrollRegion.evaluate((node) => node.scrollHeight <= node.clientHeight + 1)
    )
    .toBeTruthy();
  await page.getByTestId("security-roles-search").fill("");
  await expect(roleScrollRegion.locator("tbody tr")).toHaveCount(visibleRowLimit + 1);
  await expectBoundedSecurityTableScroll(roleScrollRegion, visibleRowLimit);

  const lastRoleRow = roleScrollRegion.locator("tbody tr").last();
  await lastRoleRow.locator("td").nth(1).click();
  await expect(lastRoleRow).toHaveAttribute("data-selected", "true");
  await expect(
    page.getByRole("heading", { name: lastRole.display_name, exact: true })
  ).toBeVisible();
  await expectNoPageHorizontalScroll(page);
});

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
      // 実バックエンドと同じく request id 付きの 401 を返す(ログイン画面では非表示にする)
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        headers: { "X-Request-ID": "login-failed-request" },
        body: JSON.stringify(
          problemEnvelope({
            status: 401,
            code: "SECURITY_LOGIN_FAILED",
            title: "ログインできません",
            detail: "ログインユーザーIDまたはパスワードを確認してください。",
            requestId: "login-failed-request",
          })
        ),
      });
      return;
    }
    await fulfill(route, { ...systemAdminMe, force_password_change: true, password_change_allowed: true });
  });
  await page.route("**/api/auth/password/change", (route) => fulfill(route, { changed: true }));

  await page.goto("/login");
  await page.getByLabel("ログインユーザーID").fill("SYSTEM");
  await page.getByLabel("パスワード").fill("WrongPass!123");
  await page.getByRole("button", { name: "ログイン" }).click();
  const loginError = page.getByText("ログインユーザーIDまたはパスワードを確認してください。", { exact: true });
  await expect(loginError).toBeVisible();
  await expect(loginError).not.toContainText("リクエストID");
  await expect(loginError).not.toContainText("login-failed-request");

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
    allowed_profile_ids: ["default"],
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
  await expect(page.getByRole("button", { name: "検索を実行" })).toBeVisible();
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

test("SQL 生成だけのユーザーは結果接地で ontology 管理 API の 403 に遷移しない", async ({ page }) => {
  test.slow();
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/rewrite", (route) => {
    // rewrite 既定 ON。未モックだと submit が最初の rewrite で失敗する。
    const body = route.request().postDataJSON() as { question?: string };
    const original = String(body?.question ?? "");
    return fulfill(route, {
      original_question: original,
      rewritten_question: original,
      source: "deterministic",
      model: "",
      warnings: [],
    });
  });
  const limited = {
    ...systemAdminMe,
    user_uuid: "query-only-ontology-result",
    login_user_id: "query.only.ontology",
    display_name: "SQL 生成ユーザー",
    role_codes: ["QUERY_MENU"],
    is_system_admin: false,
    permissions: ["menu.query"],
    allowed_profile_ids: ["default"],
    password_change_allowed: true,
  };
  const createdAt = "2026-08-20T00:00:00Z";
  const question = "部署ごとの人数を表示";
  const generatedSql =
    'SELECT "d"."DEPARTMENT_NAME", COUNT(*) AS "EMPLOYEE_COUNT" FROM "APP"."DEPARTMENT" "d" GROUP BY "d"."DEPARTMENT_NAME"';
  const sqlGraph = {
    dialect: "oracle",
    statement_type: "SELECT",
    raw_sql: generatedSql,
    ctes: [],
    tables: [
      {
        id: "sql-table-department",
        owner: "APP",
        name: "DEPARTMENT",
        alias: "d",
        qualified_name: "APP.DEPARTMENT",
        source_sql: '"APP"."DEPARTMENT" "d"',
      },
    ],
    columns: [
      {
        id: "sql-column-department-name",
        table: "d",
        name: "DEPARTMENT_NAME",
        expression_sql: '"d"."DEPARTMENT_NAME"',
      },
    ],
    joins: [],
    projections: [],
    filters: [],
    aggregates: [{ id: "sql-aggregate-count", expression_sql: "COUNT(*)" }],
    groups: [],
    having: [],
    orders: [],
    windows: [],
    parse_warnings: [],
  };
  const ontologyGraph = {
    revision_id: "revision-query-result",
    revision: {
      id: "revision-query-result",
      version: 1,
      status: "published",
      schema_fingerprint: "schema-v1",
      etag: "revision-query-result-etag",
    },
    nodes: [
      {
        id: "department-table",
        kind: "table",
        technical_name: "APP.DEPARTMENT",
        business_name_ja: "部署情報",
        review_status: "approved",
        metadata: { owner: "APP", object_name: "DEPARTMENT" },
      },
      {
        id: "department-name",
        kind: "column",
        technical_name: "APP.DEPARTMENT.DEPARTMENT_NAME",
        business_name_ja: "部署名",
        review_status: "approved",
        metadata: { owner: "APP", object_name: "DEPARTMENT", column_name: "DEPARTMENT_NAME" },
      },
    ],
    edges: [
      {
        id: "department-name-column",
        kind: "column",
        source_node_id: "department-table",
        target_node_id: "department-name",
        relationship_name_ja: "列",
        review_status: "approved",
      },
    ],
  };
  let ontologyViewRequests = 0;

  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me**", (route) => fulfill(route, limited));
  await page.route("**/api/nl2sql/persistence", (route) =>
    fulfill(route, {
      mode: "oracle",
      ready: true,
      durable: true,
      writable: true,
      snapshot_loaded: true,
      reason_code: null,
      checked_at: createdAt,
    })
  );
  await page.route("**/api/nl2sql/history", (route) => fulfill(route, { items: [], next_cursor: null }));
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfill(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-v1",
      refreshed_at: createdAt,
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
      refreshed_at: createdAt,
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
          updated_at: createdAt,
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
      allowed_tables: ["APP.DEPARTMENT"],
      allowed_views: [],
      archived: false,
      object_scope_version: 1,
      version: 1,
      etag: "profile-default-v1",
      updated_at: createdAt,
    })
  );
  await page.route("**/api/nl2sql/profiles/default/ontology-view", (route) => {
    ontologyViewRequests += 1;
    return fulfill(route, "この機能を利用する権限がありません。", 403);
  });
  await page.route("**/api/nl2sql/jobs", (route) =>
    fulfill(route, {
      job_id: "job-query-ontology-403",
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
  await page.route("**/api/nl2sql/jobs/job-query-ontology-403", (route) =>
    fulfill(route, {
      job_id: "job-query-ontology-403",
      status: "done",
      created_at: createdAt,
      started_at: createdAt,
      finished_at: createdAt,
      elapsed_ms: 40,
      error_message: null,
      warning_message: null,
      steps: [
        { stage: "prepare_context", status: "done", elapsed_ms: 10 },
        { stage: "generate_sql", status: "done", elapsed_ms: 20 },
        { stage: "safety_check", status: "done", elapsed_ms: 4 },
        { stage: "execute_sql", status: "done", elapsed_ms: 4 },
        { stage: "format_results", status: "done", elapsed_ms: 2 },
      ],
      timing: null,
      result: {
        history_id: "hist-query-ontology-403",
        engine: "select_ai",
        engine_meta: { profile: "NL2SQL_DEFAULT_PROFILE" },
        fallback_reason: "",
        original_question: question,
        rewritten_question: question,
        generated_sql: generatedSql,
        executable_sql: generatedSql,
        explanation: "部署ごとの人数を集計します。",
        safety: {
          is_safe: true,
          is_select_only: true,
          row_limit_applied: null,
          blocked_reason: "",
          warnings: [],
          referenced_tables: ["APP.DEPARTMENT"],
          referenced_columns: ["APP.DEPARTMENT.DEPARTMENT_NAME"],
        },
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: {
          columns: ["DEPARTMENT_NAME", "EMPLOYEE_COUNT"],
          rows: [{ DEPARTMENT_NAME: "開発部", EMPLOYEE_COUNT: 3 }],
          total: 1,
        },
        timing: {
          created_at: createdAt,
          started_at: createdAt,
          finished_at: createdAt,
          elapsed_ms: 40,
          stage_timings: [],
        },
        interpretation: {
          available: true,
          question: {
            available: true,
            source: "deterministic",
            original_question: question,
            rewritten_question: question,
            profile_id: "default",
            profile_name: "標準プロファイル",
            profile_category: "",
            target_objects: ["APP.DEPARTMENT"],
            filters: [],
            group_by: ["DEPARTMENT_NAME"],
            order_by: [],
            aggregations: ["COUNT"],
            row_limit: null,
            confidence: 0.9,
            warnings: [],
          },
          sql: {
            available: true,
            source: "sql_semantics",
            summary: "APP.DEPARTMENT を参照し、部署ごとに集計します。",
            statement_type: "SELECT",
            tables: ["APP.DEPARTMENT"],
            columns: ["APP.DEPARTMENT.DEPARTMENT_NAME"],
            joins: [],
            filters: [],
            aggregations: ["COUNT"],
            group_by: ["APP.DEPARTMENT.DEPARTMENT_NAME"],
            order_by: [],
            limit: null,
            semantic_graph: sqlGraph,
            warnings: [],
          },
          ontology_graph: ontologyGraph,
          warnings: [],
        },
        show_prompt: null,
      },
    })
  );

  await page.goto("/query");
  await expect(page.locator("#nl2sql-profile-select")).toContainText("標準プロファイル");
  await page.locator("#nl2sql-question-input").fill(question);
  await page.getByRole("button", { name: "検索を実行" }).click();

  await expect(page).toHaveURL(/\/query$/);
  await expect(page.getByText("開発部")).toBeVisible();
  await expect(page.getByTestId("nl2sql-sql-grounding-panel")).toBeVisible();
  await expect(page).toHaveURL(/\/query$/);
  expect(ontologyViewRequests).toBe(0);
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
    allowed_profile_ids: [],
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
    page.getByText("利用可能な業務プロファイルがありません。管理者に権限付与を依頼してください。")
  ).toBeVisible({ timeout: 45_000 });
  await expect(page.getByRole("button", { name: "業務プロファイルを作成" })).toHaveCount(0);
});

test("SQL 生成だけのユーザーは空 schema 失敗時にサンプルデータ投入ボタンを表示しない", async ({ page }) => {
  test.slow();
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/rewrite", (route) => {
    // rewrite 既定 ON。未モックだと submit が最初の rewrite で失敗する。
    const body = route.request().postDataJSON() as { question?: string };
    const original = String(body?.question ?? "");
    return fulfill(route, {
      original_question: original,
      rewritten_question: original,
      source: "deterministic",
      model: "",
      warnings: [],
    });
  });
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
  await page.addInitScript(() => {
    const state = globalThis as typeof globalThis & {
      __copiedOneTimePassword?: string;
      __copyOneTimePasswordShouldFail?: boolean;
    };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          if (state.__copyOneTimePasswordShouldFail) {
            throw new Error("clipboard denied");
          }
          state.__copiedOneTimePassword = value;
        },
      },
    });
  });
  let csrfObserved = false;
  let createRequestCount = 0;
  let successfulCreateCount = 0;
  let failNextCreate = false;
  let holdNextCreate = false;
  let heldCreate = Promise.resolve();
  let releaseHeldCreate: () => void = () => undefined;
  const createdPayloads: Array<{ loginUserId: string; roleIds: string[] }> = [];
  const generatedPasswords = [
    "RandomStrong!Pass123-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ-abcdefghijklmnopqrstuvwxyz",
    "SecondStrong!Pass456-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ-abcdefghijklmnopqrstuvwxyz",
    "ThirdStrong!Pass789-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ-abcdefghijklmnopqrstuvwxyz",
  ];
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
    createdPayloads.push({
      loginUserId: payload.login_user_id,
      roleIds: payload.role_ids,
    });
    if (holdNextCreate) {
      holdNextCreate = false;
      await heldCreate;
    }
    if (failNextCreate) {
      failNextCreate = false;
      const response = problemEnvelope({
        status: 503,
        code: "SECURITY_SERVICE_UNAVAILABLE",
        title: "サービスを一時的に利用できません",
        detail: "ユーザーを作成できません。時間をおいて再試行してください。",
        requestId: "inline-password-create-failed",
        retryable: true,
      });
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        headers: { "X-Request-ID": response.problem.request_id },
        body: JSON.stringify(response),
      });
      return;
    }
    successfulCreateCount += 1;
    const user = {
      user_uuid: `new-user-${successfulCreateCount}`,
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
    await fulfill(route, {
      user,
      temporary_password: generatedPasswords[successfulCreateCount - 1],
    });
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
  heldCreate = new Promise<void>((resolve) => {
    releaseHeldCreate = resolve;
  });
  holdNextCreate = true;
  await createButton.click();
  await expect(createButton).toBeDisabled();
  await page.keyboard.press("Enter");
  expect(createRequestCount).toBe(1);
  releaseHeldCreate();

  const loginInput = page.getByLabel("ログインユーザーID");
  const displayNameInput = page.getByLabel("表示名");
  const temporaryPasswordInput = page.getByLabel("一時パスワード", { exact: true });
  const copyButton = page.getByRole("button", { name: "一時パスワードをコピー" });
  await expect(page.locator("#security-users-panel-create")).toHaveCount(0);
  await expect(page.locator("#security-users-panel-edit")).toBeVisible();
  await expect(page.getByRole("heading", { name: "ユーザーを編集" })).toBeVisible();
  await expect(page.getByTestId("security-users-one-time-password")).toHaveCount(0);
  await expect(loginInput).toHaveValue("001");
  await expect(loginInput).toBeDisabled();
  await expect(loginInput).toHaveClass(/disabled:bg-muted\/20/u);
  await expect(displayNameInput).toHaveValue("短いログインユーザーIDユーザー");
  await expect(temporaryPasswordInput).toHaveValue(generatedPasswords[0]);
  await expect(temporaryPasswordInput).toHaveAttribute("readonly", "");
  await expect(temporaryPasswordInput).toHaveClass(/read-only:bg-muted\/20/u);
  await expect(viewerRadio).toBeChecked();
  await expect(runnerRadio).not.toBeChecked();
  await expect(createButton).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeVisible();
  await expect(page.getByText("変更を保存しました。", { exact: true }).last()).toBeVisible();

  await copyButton.click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (globalThis as typeof globalThis & { __copiedOneTimePassword?: string })
            .__copiedOneTimePassword
      )
    )
    .toBe(generatedPasswords[0]);
  await expect(page.getByText("コピーしました", { exact: true }).last()).toBeVisible();

  await page.evaluate(() => {
    (
      globalThis as typeof globalThis & { __copyOneTimePasswordShouldFail?: boolean }
    ).__copyOneTimePasswordShouldFail = true;
  });
  await copyButton.click();
  await expect(page.getByTestId("security-user-temporary-password-error")).toContainText(
    "ブラウザのクリップボード権限を確認するか、表示内容を手動で選択してください。"
  );

  await page.evaluate(() => {
    (
      globalThis as typeof globalThis & { __copyOneTimePasswordShouldFail?: boolean }
    ).__copyOneTimePasswordShouldFail = false;
  });
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await page.getByTestId("security-users-actions").getByRole("button", { name: "新規作成" }).click();
  await loginInput.fill("002");
  await displayNameInput.fill("連続作成ユーザー");
  await viewerRadio.check();
  await createButton.click();
  await expect(page.locator("#security-users-panel-edit")).toBeVisible();
  await expect(loginInput).toHaveValue("002");
  await expect(displayNameInput).toHaveValue("連続作成ユーザー");
  await expect(temporaryPasswordInput).toHaveValue(generatedPasswords[1]);

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await page.getByTestId("security-users-actions").getByRole("button", { name: "新規作成" }).click();
  await loginInput.fill("003");
  await displayNameInput.fill("再試行ユーザー");
  await viewerRadio.check();
  failNextCreate = true;
  await createButton.click();
  await expect(page.getByTestId("security-users-form-actions")).toContainText(
    "ユーザーを作成できません。時間をおいて再試行してください。"
  );
  await expect(page.getByTestId("security-users-one-time-password")).toHaveCount(0);
  await expect(page.getByTestId("security-user-temporary-password")).toHaveValue("");
  await expect(loginInput).toHaveValue("003");
  await expect(displayNameInput).toHaveValue("再試行ユーザー");

  await createButton.click();
  await expect(page.locator("#security-users-panel-edit")).toBeVisible();
  await expect(loginInput).toHaveValue("003");
  await expect(displayNameInput).toHaveValue("再試行ユーザー");
  await expect(temporaryPasswordInput).toHaveValue(generatedPasswords[2]);
  await expect(temporaryPasswordInput).not.toHaveValue(generatedPasswords[1]);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
  await expectNoElementHorizontalOverflow(page.locator("#security-users-panel-edit"));
  await expect(copyButton).toBeVisible();
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeVisible();
  expect(csrfObserved).toBe(true);
  expect(createRequestCount).toBe(4);
  expect(successfulCreateCount).toBe(3);
  expect(createdPayloads[0]).toEqual({
    loginUserId: "001",
    roleIds: ["role-viewer"],
  });
  expect(createdPayloads.at(-1)).toEqual({
    loginUserId: "003",
    roleIds: ["role-viewer"],
  });
  expect(users.some((user) => user.login_user_id === "001")).toBe(true);
  expect(users.some((user) => user.login_user_id === "003")).toBe(true);
});

test("ユーザー作成の problem field error は入力直下だけに表示し、局所クリアと request ID を保つ", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "QUERY_VIEWER",
    display_name: "検索閲覧",
    is_built_in: false,
    permissions: ["menu.query"],
  };
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [viewerRole])
  );
  let submitCount = 0;
  await page.route("**/api/security/users", async (route) => {
    if (route.request().method() === "GET") {
      await fulfill(route, []);
      return;
    }
    submitCount += 1;
    const response =
      submitCount === 1
        ? problemEnvelope({
            status: 409,
            code: "SECURITY_USER_LOGIN_ID_CONFLICT",
            title: "ユーザーを作成できません",
            detail: "このログインユーザーIDは既に使用されています。別のIDを入力してください。",
            requestId: "user-conflict-request",
            fieldErrors: [
              {
                pointer: "/login_user_id",
                code: "already_exists",
                message: "別のログインユーザーIDを入力してください。",
              },
            ],
          })
        : submitCount === 2
          ? problemEnvelope({
              status: 422,
              code: "REQUEST_VALIDATION_FAILED",
              title: "入力内容を確認してください",
              detail: "入力内容に誤りがあります。該当項目を確認してください。",
              requestId: "user-validation-request",
              fieldErrors: [
                { pointer: "/login_user_id", code: "invalid", message: "IDを確認してください。" },
                { pointer: "/display_name", code: "invalid", message: "表示名を確認してください。" },
              ],
            })
          : problemEnvelope({
              status: 503,
              code: "SECURITY_SERVICE_UNAVAILABLE",
              title: "サービスを一時的に利用できません",
              detail: "ユーザーを作成できません。時間をおいて再試行してください。",
              requestId: "user-service-request",
              retryable: true,
            });
    await route.fulfill({
      status: response.problem.status,
      contentType: "application/json",
      headers: { "X-Request-ID": response.problem.request_id },
      body: JSON.stringify(response),
    });
  });

  await page.goto("/settings/security/users");
  await page.getByTestId("security-users-actions").getByRole("button", { name: "新規作成" }).click();
  const loginInput = page.getByLabel("ログインユーザーID");
  const displayNameInput = page.getByLabel("表示名");
  await loginInput.fill("duplicate.user");
  await displayNameInput.fill("重複ユーザー");
  await page.getByRole("radio", { name: /検索閲覧/ }).check();
  const submit = page.locator("#security-users-panel-create").getByRole("button", { name: "新規作成", exact: true });
  await submit.click();

  const conflictMessage = "このログインユーザーIDは既に使用されています。別のIDを入力してください。";
  await expect(page.getByText(conflictMessage, { exact: true })).toHaveCount(1);
  await expect(loginInput).toHaveAttribute("aria-invalid", "true");
  await expect(loginInput).toHaveAttribute("aria-describedby", "security-user-login-user-id-error");
  await expect(loginInput).toBeFocused();
  await expect(page.getByTestId("security-users-form-actions").getByText(conflictMessage, { exact: true })).toHaveCount(0);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
  await expectNoElementHorizontalOverflow(page.locator("#security-users-panel-create"));
  await loginInput.fill("available.user");
  await expect(loginInput).not.toHaveAttribute("aria-invalid", "true");
  await expect(page.getByText(conflictMessage, { exact: true })).toHaveCount(0);

  await submit.click();
  await expect(loginInput).toBeFocused();
  await expect(page.getByText("IDを確認してください。", { exact: true })).toBeVisible();
  await expect(page.getByText("表示名を確認してください。", { exact: true })).toBeVisible();
  await displayNameInput.fill("利用可能な表示名");
  await expect(page.getByText("表示名を確認してください。", { exact: true })).toHaveCount(0);
  await expect(page.getByText("IDを確認してください。", { exact: true })).toBeVisible();
  await loginInput.fill("final.user");
  await expect(page.getByText("IDを確認してください。", { exact: true })).toHaveCount(0);

  await submit.click();
  await expect(page.getByTestId("security-users-form-actions")).toContainText(
    "ユーザーを作成できません。時間をおいて再試行してください。"
  );
  await expect(page.getByTestId("security-users-form-actions")).toContainText(
    "リクエストID: user-service-request"
  );
  expect(submitCount).toBe(3);
});

test("ユーザー一覧と詳細のパスワードリセット結果を編集フォームに表示する", async ({ page }) => {
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
      display_name: "営業ユーザー",
      status: "ACTIVE",
      force_password_change: true,
      locked_until: null,
      version: 1,
      role_ids: ["role-viewer"],
      is_bootstrap_admin: false,
    },
  ];
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [systemRole, viewerRole])
  );
  await page.route("**/api/security/users", (route) => fulfill(route, users));
  let resetRequestCount = 0;
  await page.route("**/api/security/users/sales-user/reset-password", async (route) => {
    resetRequestCount += 1;
    await fulfill(route, {
      user: { ...users[1], force_password_change: true },
      temporary_password: `ListResetStrong!Pass-${resetRequestCount}`,
    });
  });

  await page.goto("/settings/security/users");
  const grid = page.getByTestId("security-users-grid");
  const adminRow = grid.locator("tbody tr").filter({ hasText: "システム管理者" });
  const adminRowAction = page.getByTestId("security-users-row-actions-admin-user-trigger");
  await adminRowAction.click();
  await expect(page.getByRole("menuitem", { name: "編集" })).toBeFocused();
  await expect(page.getByRole("menuitem", { name: "パスワードをリセット" })).toHaveCount(0);
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "無効化" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(adminRowAction).toBeFocused();

  await adminRow.locator("td").first().click();
  const detailActions = page.getByTestId("security-users-detail-actions");
  await expect(page.getByRole("heading", { name: "システム管理者", exact: true })).toBeVisible();
  await expect(detailActions.getByRole("button", { name: "編集" })).toBeVisible();
  await expect(
    detailActions.getByRole("button", { name: "パスワードをリセット" })
  ).toHaveCount(0);
  await expect(detailActions.getByRole("button", { name: "その他の操作" })).toBeVisible();
  await expectNoPageHorizontalScroll(page);

  const salesRow = grid.locator("tbody tr").filter({ hasText: "営業ユーザー" });
  const salesRowAction = page.getByTestId("security-users-row-actions-sales-user-trigger");
  await salesRowAction.click();
  await expect(page.getByRole("menuitem", { name: "編集" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "パスワードをリセット" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "無効化" })).toBeFocused();
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page
    .getByRole("alertdialog", { name: "パスワードをリセット" })
    .getByRole("button", { name: "実行" })
    .click();
  await expect(page.locator("#security-users-panel-edit")).toBeVisible();
  await expect(page.getByLabel("ログインユーザーID")).toHaveValue("sales.user");
  await expect(page.getByLabel("ログインユーザーID")).toBeDisabled();
  await expect(page.getByLabel("一時パスワード", { exact: true })).toHaveValue(
    "ListResetStrong!Pass-1"
  );
  await expect(page.getByLabel("一時パスワード", { exact: true })).toHaveAttribute(
    "readonly",
    ""
  );
  await expect(page.getByTestId("security-users-one-time-password")).toHaveCount(0);

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await salesRow.locator("td").first().click();
  const salesDetailActions = page.getByTestId("security-users-detail-actions");
  await expect(salesDetailActions.getByRole("button", { name: "編集" })).toBeVisible();
  await expect(salesDetailActions.getByRole("button", { name: "パスワードをリセット" })).toBeVisible();
  await expect(salesDetailActions.getByRole("button", { name: "その他の操作" })).toBeVisible();
  await salesDetailActions.getByRole("button", { name: "パスワードをリセット" }).click();
  await page
    .getByRole("alertdialog", { name: "パスワードをリセット" })
    .getByRole("button", { name: "実行" })
    .click();
  await expect(page.locator("#security-users-panel-edit")).toBeVisible();
  await expect(page.getByLabel("一時パスワード", { exact: true })).toHaveValue(
    "ListResetStrong!Pass-2"
  );
  expect(resetRequestCount).toBe(2);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoElementHorizontalOverflow(page.locator("#security-users-panel-edit"));
  await expectNoPageHorizontalScroll(page);
});

test("ユーザー編集はパスワードリセットと無効化・有効化をフォーム操作に統合する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.addInitScript(() => {
    const state = globalThis as typeof globalThis & { __copiedEditPassword?: string };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          state.__copiedEditPassword = value;
        },
      },
    });
  });

  const viewerRole = {
    ...systemRole,
    role_id: "role-viewer",
    role_code: "QUERY_VIEWER",
    display_name: "検索閲覧",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
  };
  let user = {
    user_uuid: "sales-user",
    login_user_id: "sales.user",
    display_name: "営業ユーザー",
    status: "ACTIVE" as "ACTIVE" | "DISABLED",
    force_password_change: false,
    locked_until: null,
    version: 1,
    role_ids: ["role-viewer"],
    is_bootstrap_admin: false,
  };
  let resetRequestCount = 0;
  let resetPayload: unknown = null;
  let failNextReset = false;
  let updateRequestCount = 0;
  const disableVersions: number[] = [];
  const enableVersions: number[] = [];
  let failNextDisable = false;

  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [systemRole, viewerRole])
  );
  await page.route("**/api/security/users", (route) => fulfill(route, [user]));
  await page.route("**/api/security/users/sales-user", async (route) => {
    updateRequestCount += 1;
    await fulfill(route, user);
  });
  await page.route("**/api/security/users/sales-user/*", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/reset-password")) {
      resetRequestCount += 1;
      resetPayload = route.request().postDataJSON();
      if (failNextReset) {
        failNextReset = false;
        await fulfill(
          route,
          "一時パスワードを再発行できませんでした。接続状態と権限を確認して再試行してください。",
          503
        );
        return;
      }
      user = { ...user, force_password_change: true };
      await fulfill(route, {
        user,
        temporary_password: "EditResetStrong!Pass-1",
      });
      return;
    }
    const payload = route.request().postDataJSON() as { version: number };
    if (pathname.endsWith("/disable")) {
      disableVersions.push(payload.version);
      if (failNextDisable) {
        failNextDisable = false;
        await fulfill(route, "最後のシステム管理者は無効化または権限解除できません。", 409);
        return;
      }
      user = { ...user, status: "DISABLED", version: user.version + 1 };
      await fulfill(route, user);
      return;
    }
    if (pathname.endsWith("/enable")) {
      enableVersions.push(payload.version);
      user = { ...user, status: "ACTIVE", version: user.version + 1 };
      await fulfill(route, user);
      return;
    }
    await fulfill(route, "未対応のユーザー操作です。", 404);
  });

  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/settings/security/users");
  await page.getByTestId("security-users-row-actions-sales-user-trigger").click();
  await page.getByRole("menuitem", { name: "編集" }).click();

  const editPanel = page.locator("#security-users-panel-edit");
  const editActions = page.getByRole("group", { name: "ユーザー編集操作" });
  const displayName = page.getByLabel("表示名");
  const temporaryPassword = page.getByLabel("一時パスワード", { exact: true });
  const copyTemporaryPassword = page.getByRole("button", {
    name: "一時パスワードをコピー",
  });
  const assignedRole = page.getByLabel("検索閲覧");
  await expect(editPanel).toBeVisible();
  await expect(editActions.getByRole("button", { name: "保存", exact: true })).toBeVisible();
  await expect(editActions.getByRole("button", { name: "パスワードをリセット" })).toBeVisible();
  await expect(editActions.getByRole("button", { name: "その他の操作" })).toBeVisible();
  await expect(temporaryPassword).toHaveValue("");
  await expect(temporaryPassword).toHaveAttribute("readonly", "");
  await expect(temporaryPassword).toHaveClass(/read-only:bg-muted\/20/u);
  await expect(copyTemporaryPassword).toBeDisabled();
  await displayName.fill("未保存の営業ユーザー");

  await editActions.getByRole("button", { name: "パスワードをリセット" }).click();
  await page
    .getByRole("alertdialog", { name: "パスワードをリセット" })
    .getByRole("button", { name: "キャンセル" })
    .click();
  expect(resetRequestCount).toBe(0);
  await expect(displayName).toHaveValue("未保存の営業ユーザー");

  await editActions.getByRole("button", { name: "パスワードをリセット" }).click();
  await page
    .getByRole("alertdialog", { name: "パスワードをリセット" })
    .getByRole("button", { name: "実行" })
    .click();
  await expect(page.getByTestId("security-users-edit-reset-password-region")).toHaveCount(0);
  await expect(temporaryPassword).toHaveValue("EditResetStrong!Pass-1");
  await expect(page.getByText("パスワードをリセットしました。", { exact: true }).last()).toBeVisible();
  expect(resetPayload).toEqual({ temporary_password: null });
  await expect(displayName).toHaveValue("未保存の営業ユーザー");
  await expect(copyTemporaryPassword).toBeEnabled();
  await copyTemporaryPassword.click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (globalThis as typeof globalThis & { __copiedEditPassword?: string })
            .__copiedEditPassword
      )
    )
    .toBe("EditResetStrong!Pass-1");

  failNextReset = true;
  await editActions.getByRole("button", { name: "パスワードをリセット" }).click();
  await page
    .getByRole("alertdialog", { name: "パスワードをリセット" })
    .getByRole("button", { name: "実行" })
    .click();
  await expect(page.getByTestId("security-user-temporary-password-error")).toContainText(
    "接続状態と権限を確認して再試行してください。"
  );
  await expect(temporaryPassword).toHaveValue("EditResetStrong!Pass-1");
  await expect(displayName).toHaveValue("未保存の営業ユーザー");
  await expect(page.locator("main > [role='alert']")).toHaveCount(0);

  const moreButton = editActions.getByRole("button", { name: "その他の操作" });
  await moreButton.focus();
  await page.keyboard.press("Enter");
  const disableItem = page.getByRole("menuitem", { name: "無効化" });
  await expect(disableItem).toBeFocused();
  await expect(disableItem).toHaveAttribute("data-form-action-tone", "danger");
  await page.keyboard.press("Escape");
  await expect(moreButton).toBeFocused();

  await moreButton.click();
  await page.getByRole("menuitem", { name: "無効化" }).click();
  await page
    .getByRole("alertdialog", { name: "無効化" })
    .getByRole("button", { name: "キャンセル" })
    .click();
  expect(disableVersions).toEqual([]);

  await moreButton.click();
  await page.getByRole("menuitem", { name: "無効化" }).click();
  await page
    .getByRole("alertdialog", { name: "無効化" })
    .getByRole("button", { name: "実行" })
    .click();
  await expect(editActions.getByRole("button", { name: "有効化" })).toBeVisible();
  await expect(editActions.getByRole("button", { name: "保存", exact: true })).toHaveCount(0);
  await expect(
    editActions.getByRole("button", { name: "パスワードをリセット" })
  ).toHaveCount(0);
  await expect(displayName).toBeDisabled();
  await expect(temporaryPassword).toBeDisabled();
  await expect(copyTemporaryPassword).toBeDisabled();
  await expect(page.getByRole("radio")).toHaveCount(2);
  await expect(assignedRole).toBeVisible();
  await expect(assignedRole).toBeChecked();
  await expect(assignedRole).toBeDisabled();
  await expect(editActions.getByRole("button", { name: "その他の操作" })).toBeVisible();
  await page.getByRole("button", { name: "一覧に戻る" }).focus();
  await page.keyboard.press("Tab");
  await expect(editActions.getByRole("button", { name: "有効化" })).toBeFocused();
  await editPanel.locator("form").evaluate((form) =>
    (form as HTMLFormElement).requestSubmit()
  );
  expect(updateRequestCount).toBe(0);
  expect(resetRequestCount).toBe(2);
  await editActions.getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "削除" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(displayName).toHaveValue("未保存の営業ユーザー");
  await expect(page.getByText("ユーザーを無効化しました。既存セッションは失効しました。", { exact: true }).last()).toBeVisible();
  expect(disableVersions).toEqual([1]);

  await editActions.getByRole("button", { name: "有効化" }).click();
  await expect(editActions.getByRole("button", { name: "その他の操作" })).toBeVisible();
  await expect(editActions.getByRole("button", { name: "有効化" })).toHaveCount(0);
  await expect(editActions.getByRole("button", { name: "保存", exact: true })).toBeVisible();
  await expect(
    editActions.getByRole("button", { name: "パスワードをリセット" })
  ).toBeVisible();
  await expect(displayName).toBeEnabled();
  await expect(temporaryPassword).toBeEnabled();
  await expect(temporaryPassword).toHaveAttribute("readonly", "");
  await expect(copyTemporaryPassword).toBeEnabled();
  await expect(assignedRole).toBeEnabled();
  await expect(assignedRole).toBeChecked();
  await expect(page.getByText("ユーザーを有効化しました。次回ログインから利用できます。", { exact: true }).last()).toBeVisible();
  await expect(displayName).toHaveValue("未保存の営業ユーザー");
  expect(enableVersions).toEqual([2]);

  failNextDisable = true;
  await editActions.getByRole("button", { name: "その他の操作" }).click();
  await page.getByRole("menuitem", { name: "無効化" }).click();
  await page
    .getByRole("alertdialog", { name: "無効化" })
    .getByRole("button", { name: "実行" })
    .click();
  await expect(editActions).toContainText("最後のシステム管理者は無効化または権限解除できません。");
  await expect(displayName).toHaveValue("未保存の営業ユーザー");
  expect(disableVersions).toEqual([1, 3]);

  await page.setViewportSize({ width: 375, height: 812 });
  await expect(editActions.getByRole("button", { name: "保存", exact: true })).toBeVisible();
  await expect(editActions.getByRole("button", { name: "パスワードをリセット" })).toBeVisible();
  await expectNoPageHorizontalScroll(page);
});

test("無効ユーザーの削除は確認・フォーカス復帰・選択移動を保つ", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const viewerRole = {
    ...systemRole,
    role_id: "role-delete-viewer",
    role_code: "DELETE_VIEWER",
    display_name: "削除確認ロール",
    is_built_in: false,
    permissions: ["menu.query"],
  };
  const activeUser = {
    user_uuid: "active-delete-user",
    login_user_id: "active.user",
    display_name: "有効ユーザー",
    status: "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 4,
    role_ids: [viewerRole.role_id],
    assigned_roles: [],
    is_bootstrap_admin: false,
  };
  const disabledUser = {
    ...activeUser,
    user_uuid: "disabled-delete-user",
    login_user_id: "disabled.user",
    display_name: "削除対象ユーザー",
    status: "DISABLED",
    version: 7,
  };
  let users = [activeUser, disabledUser];
  let deleteCount = 0;
  let observedIfMatch = "";

  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [viewerRole])
  );
  await page.route("**/api/security/users", (route) => fulfill(route, users));
  await page.route("**/api/security/users/disabled-delete-user", async (route) => {
    deleteCount += 1;
    observedIfMatch = route.request().headers()["if-match"] ?? "";
    users = users.filter((user) => user.user_uuid !== disabledUser.user_uuid);
    await fulfill(route, {
      deleted: true,
      user_uuid: disabledUser.user_uuid,
      login_user_id: disabledUser.login_user_id,
    });
  });

  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/settings/security/users");

  const activeTrigger = page.getByTestId(
    "security-users-row-actions-active-delete-user-trigger"
  );
  await activeTrigger.click();
  await expect(page.getByRole("menuitem", { name: "削除" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  const deleteTrigger = page.getByTestId(
    "security-users-row-actions-disabled-delete-user-trigger"
  );
  await deleteTrigger.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("End");
  const deleteMenuItem = page.getByRole("menuitem", { name: "削除" });
  await expect(deleteMenuItem).toBeFocused();
  await page.keyboard.press("Enter");

  const deleteDialog = page.getByRole("alertdialog", { name: "削除" });
  await expect(deleteDialog).toContainText(
    "「削除対象ユーザー」（ログインユーザーID: disabled.user）を完全に削除します。"
  );
  await expect(deleteDialog).toContainText("割り当てロール、既存セッションは削除され");
  await page.mouse.click(4, 4);
  await expect(deleteDialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(deleteDialog).toHaveCount(0);
  await expect(deleteTrigger).toBeFocused();
  expect(deleteCount).toBe(0);

  await deleteTrigger.click();
  await page.getByRole("menuitem", { name: "削除" }).click();
  await deleteDialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(deleteTrigger).toBeFocused();
  expect(deleteCount).toBe(0);

  await deleteTrigger.click();
  await page.getByRole("menuitem", { name: "削除" }).click();
  await deleteDialog.getByRole("button", { name: "削除", exact: true }).click();

  await expect(page.getByText("削除対象ユーザー", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "有効ユーザー" })).toBeVisible();
  await expect(page.getByText("削除対象ユーザー を完全に削除しました。", { exact: true }).last()).toBeVisible();
  expect(observedIfMatch).toBe('"7"');
  expect(deleteCount).toBe(1);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
});

test("行アクションメニューを閉じた直後に別要素へフォーカスしても奪い返さない", async ({
  page,
}) => {
  await mockDatabaseGateReady(page);
  const viewerRole = {
    ...systemRole,
    role_id: "role-focus-viewer",
    role_code: "FOCUS_VIEWER",
    display_name: "フォーカス確認ロール",
    is_built_in: false,
    permissions: ["menu.query"],
  };
  const firstUser = {
    user_uuid: "focus-first-user",
    login_user_id: "focus.first",
    display_name: "先のユーザー",
    status: "ACTIVE",
    force_password_change: false,
    locked_until: null,
    version: 1,
    role_ids: [viewerRole.role_id],
    assigned_roles: [],
    is_bootstrap_admin: false,
  };
  const secondUser = {
    ...firstUser,
    user_uuid: "focus-second-user",
    login_user_id: "focus.second",
    display_name: "後のユーザー",
    version: 2,
  };
  await page.route("**/api/security/roles?include_archived=false", (route) =>
    fulfill(route, [viewerRole])
  );
  await page.route("**/api/security/users", (route) => fulfill(route, [firstUser, secondUser]));

  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/settings/security/users");

  const firstTrigger = page.getByTestId("security-users-row-actions-focus-first-user-trigger");
  const secondTrigger = page.getByTestId("security-users-row-actions-focus-second-user-trigger");
  await firstTrigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitem", { name: "編集" })).toBeFocused();

  // Escape で閉じた直後（フォーカス復帰の rAF が走る前）に別の行へフォーカスを移す。
  await page.evaluate(() => {
    document.activeElement?.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
    );
    document
      .querySelector<HTMLButtonElement>(
        '[data-testid="security-users-row-actions-focus-second-user-trigger"]'
      )
      ?.focus();
  });

  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(secondTrigger).toBeFocused();
  await expect(firstTrigger).not.toBeFocused();

  // 移した先の行でそのままメニューを開ける（開くのは後のユーザーの行）。
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitem", { name: "編集" })).toBeFocused();
  await expect(secondTrigger).toHaveAttribute("aria-expanded", "true");
  await expect(firstTrigger).toHaveAttribute("aria-expanded", "false");
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
  await expect(page.getByRole("menuitem", { name: "パスワードをリセット" })).toHaveCount(0);
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "無効化" })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("menuitem", { name: "無効化" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(adminUserRowAction).toBeFocused();
  await expect(page.getByTestId("security-users-detail-actions").getByRole("button", { name: "編集" })).toBeVisible();
  await expect(page.getByTestId("security-users-detail-actions").getByRole("button", { name: "パスワードをリセット" })).toHaveCount(0);
  await expect(page.getByTestId("security-users-detail-actions").getByRole("button", { name: "その他の操作" })).toBeVisible();
  await salesUserRow.locator("td").nth(1).click();
  await expect(salesUserRow).toHaveAttribute("data-selected", "true");
  await expect(page.locator("dl").getByText("ロック中", { exact: true })).toBeVisible();
  await expect(page.getByText("ロック期限", { exact: true })).toHaveCount(0);
  const salesUserDetailActions = page.getByTestId("security-users-detail-actions");
  const salesUserRowAction = page.getByTestId("security-users-row-actions-sales-user-trigger");
  await salesUserRowAction.click();
  await expect(page.getByRole("menuitem", { name: "パスワードをリセット" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(
    salesUserDetailActions.getByRole("button", { name: "パスワードをリセット" })
  ).toHaveCount(0);
  await expect(salesUserDetailActions.getByRole("button", { name: "ロック解除" })).toBeVisible();
  await salesUserDetailActions.getByRole("button", { name: "その他の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "有効化" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "削除" })).toBeVisible();
  await page.keyboard.press("Escape");
  await salesUserDetailActions.getByRole("button", { name: "編集" }).click();
  const disabledUserEditActions = page.getByRole("group", { name: "ユーザー編集操作" });
  await expect(
    disabledUserEditActions.getByRole("button", { name: "保存", exact: true })
  ).toHaveCount(0);
  await expect(
    disabledUserEditActions.getByRole("button", { name: "パスワードをリセット" })
  ).toHaveCount(0);
  await expect(disabledUserEditActions.getByRole("button", { name: "有効化" })).toBeVisible();
  await expect(page.getByLabel("表示名")).toBeDisabled();
  await expect(page.getByLabel("一時パスワード", { exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "一時パスワードをコピー" })).toBeDisabled();
  await expect(page.getByRole("radio")).toHaveCount(2);
  await expect(page.getByLabel("検索閲覧")).toBeVisible();
  await expect(page.getByLabel("検索閲覧")).toBeChecked();
  await expect(page.getByLabel("検索閲覧")).toBeDisabled();
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
  const mobileAdminUserRowAction = page.getByTestId("security-users-row-actions-admin-user-trigger");
  await expect(mobileAdminUserRowAction).toBeVisible();
  await mobileAdminUserRowAction.click();
  await expect(page.getByRole("menuitem", { name: "パスワードをリセット" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("security-users-detail-actions").getByRole("button", { name: "パスワードをリセット" })).toHaveCount(0);
  await expectNoPageHorizontalScroll(page);
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
  // ロールバッジは共有 StatusBadge(token 化)へ移行。variant で判定する。
  await expect(inactiveRoleBadge).toHaveAttribute("data-status-variant", "neutral");
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
  await expect(page.getByText("データ管理者", { exact: true }).last()).toHaveAttribute(
    "data-status-variant",
    "info"
  );
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
  await expect(permissionBulkActions.getByRole("button", { name: "選択をすべて解除" })).toBeDisabled();
  await permissionBulkActions.getByRole("button", { name: "すべて選択" }).click();
  await expect(page.getByRole("checkbox", { name: /外観/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /ユーザー管理/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /ロール・権限管理/ })).toBeChecked();
  const securityGroupBulkActions = page.getByTestId("security-roles-セキュリティ管理-permission-selection-actions");
  const securityGroupHeading = securityGroupBulkActions
    .locator("..")
    .getByRole("heading", { name: "セキュリティ管理" });
  await expect
    .poll(async () => {
      const [actionsBox, headingBox] = await Promise.all([
        securityGroupBulkActions.boundingBox(),
        securityGroupHeading.boundingBox(),
      ]);
      if (!actionsBox || !headingBox) return Number.POSITIVE_INFINITY;
      return Math.abs(actionsBox.x - headingBox.x);
    })
    .toBeLessThanOrEqual(1);
  await expect(securityGroupBulkActions.getByRole("button", { name: "セキュリティ管理 の選択をすべて解除" })).toBeEnabled();
  await securityGroupBulkActions.getByRole("button", { name: "セキュリティ管理 の選択をすべて解除" }).click();
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

test("ロールコード競合はコード欄へ結び付き、403 は安全なページ通知と request ID で示す", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole])
  );
  let submitCount = 0;
  await page.route("**/api/security/roles", async (route) => {
    if (route.request().method() === "GET") {
      await fulfill(route, [systemRole]);
      return;
    }
    submitCount += 1;
    const response =
      submitCount === 1
        ? problemEnvelope({
          status: 409,
          code: "SECURITY_ROLE_CODE_CONFLICT",
          title: "ロールを作成できません",
          detail: "このロールコードは既に使用されています。別のコードを入力してください。",
          requestId: "role-conflict-request",
          fieldErrors: [
            {
              pointer: "/role_code",
              code: "already_exists",
              message: "別のロールコードを入力してください。",
            },
          ],
          })
        : submitCount === 2
          ? problemEnvelope({
              status: 500,
              code: "INTERNAL_SERVER_ERROR",
              title: "サーバー内部でエラーが発生しました",
              detail: "サーバー内部でエラーが発生しました。時間をおいて再試行してください。",
              requestId: "role-internal-request",
            })
          : problemEnvelope({
              status: 403,
              code: "SECURITY_PERMISSION_DENIED",
              title: "この操作を実行する権限がありません",
              detail: "ロールを作成する権限がありません。システム管理者に確認してください。",
              requestId: "role-forbidden-request",
            });
    await route.fulfill({
      status: response.problem.status,
      contentType: "application/json",
      headers: { "X-Request-ID": response.problem.request_id },
      body: JSON.stringify(response),
    });
  });

  await page.goto("/settings/security/roles");
  await page.getByTestId("security-roles-actions").getByRole("button", { name: "新規作成" }).click();
  const roleCode = page.getByLabel("ロールコード");
  await roleCode.fill("DUPLICATE_ROLE");
  await page.getByLabel("ロール名").fill("重複ロール");
  const actionBar = page.getByRole("group", { name: "ロール編集操作" });
  await actionBar.getByRole("button", { name: "新規作成" }).click();

  const conflictMessage = "このロールコードは既に使用されています。別のコードを入力してください。";
  await expect(page.getByText(conflictMessage, { exact: true })).toHaveCount(1);
  await expect(roleCode).toHaveAttribute("aria-invalid", "true");
  await expect(roleCode).toHaveAttribute("aria-describedby", "security-role-code-error");
  await expect(roleCode).toBeFocused();
  await expect(actionBar.getByText(conflictMessage, { exact: true })).toHaveCount(0);

  await roleCode.fill("AVAILABLE_ROLE");
  await expect(roleCode).not.toHaveAttribute("aria-invalid", "true");
  await expect(page.getByText(conflictMessage, { exact: true })).toHaveCount(0);
  await actionBar.getByRole("button", { name: "新規作成" }).click();
  await expect(actionBar).toContainText("サーバー内部でエラーが発生しました。時間をおいて再試行してください。");
  await expect(actionBar).toContainText("リクエストID: role-internal-request");
  await expect(actionBar).not.toContainText("ORA-");
  await page.getByLabel("ロール名").fill("再試行ロール");
  await actionBar.getByRole("button", { name: "新規作成" }).click();
  await expect(page.getByRole("heading", { name: "この機能を利用する権限がありません" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("必要なロールが付与されているか、システム管理者に確認してください。");
  await expect(page.getByRole("status")).toContainText("リクエストID: role-forbidden-request");
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
  expect(submitCount).toBe(3);
});

test("SYSTEM_ADMIN はロール管理で業務プロファイル利用権限を設定できる", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.unroute("**/api/security/profile-access/profiles**");
  await page.route("**/api/security/profile-access/profiles**", (route) =>
    fulfill(route, [
      {
        id: "default",
        name: "標準プロファイル",
        category: "共通",
        description: "標準の業務プロファイル",
        archived: false,
        allowed_role_ids: ["role-query-default"],
      },
      {
        id: "finance",
        name: "財務プロファイル",
        category: "会計",
        description: "財務部門向け",
        archived: false,
        allowed_role_ids: [],
      },
      ...Array.from({ length: 18 }, (_, index) => ({
        id: `verification-${String(index + 1).padStart(2, "0")}`,
        name: `検証プロファイル ${String(index + 1).padStart(2, "0")}`,
        category: "検証",
        description: "スクロール領域の表示確認用プロファイル",
        archived: false,
        allowed_role_ids: [],
      })),
    ])
  );
  const permissionRows = [
    {
      code: "menu.query",
      group: "メニュー権限",
      label: "SQL 生成",
      description: "SQL 生成を表示し、関連操作を利用できます。",
      implies: ["nl2sql.profiles.read"],
    },
    {
      code: "nl2sql.profiles.read",
      group: "参照権限",
      label: "業務プロファイル参照",
      description: "業務プロファイルの利用コンテキストを参照できます。",
      implies: [],
    },
  ];
  const queryRole = {
    ...systemRole,
    role_id: "role-query-default",
    role_code: "QUERY_DEFAULT",
    display_name: "SQL 利用者",
    description: "標準 profile のみ",
    is_built_in: false,
    permissions: ["menu.query"],
    data_entitlements: [],
    allowed_profile_ids: ["default"],
  };
  let savedPayload: Record<string, unknown> | null = null;
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [systemRole, queryRole])
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, permissionRows));
  await page.route("**/api/security/roles/role-query-default", async (route) => {
    savedPayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfill(route, {
      ...queryRole,
      version: 2,
      allowed_profile_ids: savedPayload.allowed_profile_ids,
    });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/settings/security/roles");
  await page
    .getByTestId("security-roles-grid")
    .locator("tbody tr")
    .filter({ hasText: "SQL 利用者" })
    .locator("td")
    .first()
    .click();
  await expect(page.getByText("1 件", { exact: true })).toBeVisible();
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" }).click();
  const profileSearch = page.getByTestId("security-roles-profile-access-search");
  const profileAccessList = page.getByTestId("security-roles-profile-access-list");
  const readProfileAccessScrollState = () =>
    profileAccessList.evaluate((node) => {
      const computed = window.getComputedStyle(node);
      return {
        clientHeight: node.clientHeight,
        maxHeight: Number.parseFloat(computed.maxHeight),
        overflowX: computed.overflowX,
        overflowY: computed.overflowY,
        rootFontSize: Number.parseFloat(
          window.getComputedStyle(document.documentElement).fontSize
        ),
        scrollHeight: node.scrollHeight,
      };
    });
  await expect(profileSearch).toBeVisible();
  await expect(profileAccessList).toHaveAttribute("role", "region");
  await expect(profileAccessList).toHaveAccessibleName("使用可能な業務プロファイル");
  await expect(profileAccessList).toHaveAttribute("tabindex", "0");
  const desktopScrollState = await readProfileAccessScrollState();
  expect(desktopScrollState.maxHeight).toBeCloseTo(
    28 * desktopScrollState.rootFontSize,
    0
  );
  expect(desktopScrollState.clientHeight).toBeLessThanOrEqual(
    Math.ceil(desktopScrollState.maxHeight)
  );
  expect(desktopScrollState.scrollHeight).toBeGreaterThan(desktopScrollState.clientHeight);
  expect(desktopScrollState.overflowX).toBe("hidden");
  expect(desktopScrollState.overflowY).toBe("auto");
  await profileAccessList.focus();
  await expect(profileAccessList).toBeFocused();
  await profileAccessList.press("End");
  await expect
    .poll(() => profileAccessList.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);
  await profileSearch.focus();
  await expect(profileSearch).toBeFocused();
  await profileSearch.fill("財務");
  await expect(page.getByRole("checkbox", { name: /財務プロファイル/ })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /標準プロファイル/ })).toHaveCount(0);
  const filteredScrollState = await readProfileAccessScrollState();
  expect(filteredScrollState.clientHeight).toBeLessThan(filteredScrollState.maxHeight);
  expect(filteredScrollState.scrollHeight).toBeLessThanOrEqual(
    filteredScrollState.clientHeight + 1
  );

  const profileBulkActions = page.getByTestId("security-roles-profile-access-selection-actions");
  await expect
    .poll(async () => {
      const [actionsBox, listBox] = await Promise.all([
        profileBulkActions.boundingBox(),
        profileAccessList.boundingBox(),
      ]);
      if (!actionsBox || !listBox) return Number.POSITIVE_INFINITY;
      return Math.abs(actionsBox.x - listBox.x);
    })
    .toBeLessThanOrEqual(1);
  await profileBulkActions.getByRole("button", { name: "すべて選択" }).click();
  await expect(page.getByRole("checkbox", { name: /財務プロファイル/ })).toBeChecked();
  await profileBulkActions.getByRole("button", { name: "選択をすべて解除" }).click();
  await expect(page.getByRole("checkbox", { name: /財務プロファイル/ })).not.toBeChecked();
  await page.getByRole("checkbox", { name: /財務プロファイル/ }).check();
  await expect(page.getByRole("checkbox", { name: /財務プロファイル/ })).toBeChecked();
  await profileSearch.fill("");
  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
  const mobileScrollState = await readProfileAccessScrollState();
  expect(mobileScrollState.maxHeight).toBeCloseTo(
    17.5 * mobileScrollState.rootFontSize,
    0
  );
  expect(mobileScrollState.clientHeight).toBeLessThanOrEqual(
    Math.ceil(mobileScrollState.maxHeight)
  );
  expect(mobileScrollState.scrollHeight).toBeGreaterThan(mobileScrollState.clientHeight);
  expect(mobileScrollState.overflowX).toBe("hidden");
  expect(mobileScrollState.overflowY).toBe("auto");
  await expect(profileSearch).toBeVisible();
  await expect
    .poll(async () => {
      const [actionsBox, listBox] = await Promise.all([
        profileBulkActions.boundingBox(),
        profileAccessList.boundingBox(),
      ]);
      if (!actionsBox || !listBox) return Number.POSITIVE_INFINITY;
      return Math.abs(actionsBox.x - listBox.x);
    })
    .toBeLessThanOrEqual(1);
  await page.getByRole("group", { name: "ロール編集操作" }).getByRole("button", { name: "保存" }).click();

  await expect
    .poll(() => (savedPayload?.allowed_profile_ids as string[] | undefined)?.sort())
    .toEqual(["default", "finance"]);
});

test("ロール・権限管理は業務プロファイル候補の取得失敗でもロール一覧を表示する", async ({ page }) => {
  await mockDatabaseGateReady(page);
  await page.unroute("**/api/security/profile-access/profiles**");
  await page.route("**/api/security/profile-access/profiles**", (route) =>
    fulfill(route, "業務プロファイル候補の取得に失敗しました。", 500)
  );
  await page.route("**/api/security/permissions", (route) =>
    fulfill(route, [
      {
        code: "menu.query",
        group: "メニュー権限",
        label: "SQL 生成",
        description: "SQL 生成を表示し、関連操作を利用できます。",
        implies: [],
      },
    ])
  );
  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, [
      systemRole,
      {
        ...systemRole,
        role_id: "role-query-default",
        role_code: "QUERY_DEFAULT",
        display_name: "SQL 利用者",
        description: "標準 profile のみ",
        is_built_in: false,
        permissions: ["menu.query"],
        data_entitlements: [],
        allowed_profile_ids: ["default"],
      },
    ])
  );

  await page.goto("/settings/security/roles");

  await expect(
    page.getByText("業務プロファイル利用権限の候補を読み込めませんでした。", {
      exact: false,
    })
  ).toBeVisible();
  await expect(page.getByTestId("security-roles-grid").getByText("SQL 利用者")).toBeVisible();
  await expect(page.getByText("対象データはありません。")).toHaveCount(0);
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
  await page.unroute("**/api/security/profile-access/profiles**");
  await page.route("**/api/security/profile-access/profiles**", (route) =>
    fulfill(route, [
      {
        id: "default",
        name: "標準プロファイル",
        category: "共通",
        description: "標準の業務プロファイル",
        archived: false,
        allowed_role_ids: ["role-archived-data"],
      },
      {
        id: "finance",
        name: "財務プロファイル",
        category: "会計",
        description: "財務部門向け",
        archived: false,
        allowed_role_ids: [],
      },
    ])
  );
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
    allowed_profile_ids: ["default"],
  };
  let roles: unknown[] = [systemRole, activeRole, archivedRole];
  let updateRoleRequestCount = 0;
  await page.route("**/api/security/roles?include_archived=true", (route) => fulfill(route, roles));
  await page.route("**/api/security/roles/role-archived-data", async (route) => {
    updateRoleRequestCount += 1;
    await fulfill(route, archivedRole);
  });
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
  const archivedEditPanel = page.locator("#security-roles-panel-edit");
  const archivedEditActions = page.getByRole("group", { name: "ロール編集操作" });
  const roleName = page.getByLabel("ロール名");
  const roleDescription = page.getByLabel("説明");
  const queryPermission = page.getByRole("checkbox", { name: /SQL 生成/u });
  const directSqlPermission = page.getByRole("checkbox", { name: /SELECT SQL を実行/u });
  const defaultProfile = page.getByRole("checkbox", { name: /標準プロファイル/u });
  const financeProfile = page.getByRole("checkbox", { name: /財務プロファイル/u });
  const profileSearch = page.getByLabel("業務プロファイルを検索");
  await expect(roleName).toBeDisabled();
  await expect(roleDescription).toBeDisabled();
  await expect(queryPermission).toBeVisible();
  await expect(queryPermission).toBeChecked();
  await expect(queryPermission).toBeDisabled();
  await expect(directSqlPermission).toBeVisible();
  await expect(directSqlPermission).toBeChecked();
  await expect(directSqlPermission).toBeDisabled();
  await expect(defaultProfile).toBeVisible();
  await expect(defaultProfile).toBeChecked();
  await expect(defaultProfile).toBeDisabled();
  await expect(financeProfile).toBeVisible();
  await expect(financeProfile).not.toBeChecked();
  await expect(financeProfile).toBeDisabled();
  await expect(profileSearch).toBeVisible();
  await expect(profileSearch).toBeDisabled();
  await expect(
    page
      .getByTestId("security-roles-permission-selection-actions")
      .getByRole("button", { name: "すべて選択" })
  ).toBeDisabled();
  await expect(
    page
      .getByTestId("security-roles-profile-access-selection-actions")
      .getByRole("button", { name: "すべて選択" })
  ).toBeDisabled();
  await page.getByRole("button", { name: "一覧に戻る" }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByTestId("security-roles-profile-access-list")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(archivedEditActions.getByRole("button", { name: "復元" })).toBeFocused();
  await archivedEditPanel.locator("form").evaluate((form) =>
    (form as HTMLFormElement).requestSubmit()
  );
  expect(updateRoleRequestCount).toBe(0);
  await expect(archivedEditActions.getByRole("button", { name: "保存" })).toHaveCount(0);
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
  await page.getByTestId("security-roles-detail-actions").getByRole("button", { name: "編集" }).click();
  await expect(roleName).toBeEnabled();
  await expect(roleDescription).toBeEnabled();
  await expect(queryPermission).toBeEnabled();
  await expect(queryPermission).toBeChecked();
  await expect(defaultProfile).toBeEnabled();
  await expect(defaultProfile).toBeChecked();
  await expect(financeProfile).toBeEnabled();
  await expect(profileSearch).toBeEnabled();
  await expect(page.getByRole("group", { name: "ロール編集操作" }).getByRole("button", { name: "保存" })).toBeVisible();
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expectNoPageHorizontalScroll(page);

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

test("アーカイブ済みカスタムロールの削除は409を保持し再試行できる", async ({ page }) => {
  await mockDatabaseGateReady(page);
  const activeRole = {
    ...systemRole,
    role_id: "role-active-delete",
    role_code: "ACTIVE_DELETE",
    display_name: "有効カスタムロール",
    is_built_in: false,
  };
  const archivedRole = {
    ...activeRole,
    role_id: "role-archived-delete",
    role_code: "ARCHIVED_DELETE",
    display_name: "削除対象ロール",
    archived: true,
    version: 9,
  };
  let roles = [systemRole, activeRole, archivedRole];
  let deleteCount = 0;
  const observedIfMatch: string[] = [];

  await page.route("**/api/security/roles?include_archived=true", (route) =>
    fulfill(route, roles)
  );
  await page.route("**/api/security/permissions", (route) => fulfill(route, []));
  await page.route("**/api/security/roles/role-archived-delete", async (route) => {
    deleteCount += 1;
    observedIfMatch.push(route.request().headers()["if-match"] ?? "");
    if (deleteCount === 1) {
      const response = problemEnvelope({
        status: 409,
        code: "SECURITY_ROLE_DELETE_ENTITLEMENTS_PRESENT",
        title: "現在の状態では削除できません",
        detail:
          "このロールにはデータ権限が残っています。Deep Data Security で空の Data Grant を適用してから削除してください。",
        requestId: "role-delete-conflict",
      });
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify(response),
      });
      return;
    }
    roles = roles.filter((role) => role.role_id !== archivedRole.role_id);
    await fulfill(route, {
      deleted: true,
      role_id: archivedRole.role_id,
      role_code: archivedRole.role_code,
    });
  });

  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/settings/security/roles");

  await page.getByTestId("security-roles-row-actions-role-active-delete-trigger").click();
  await expect(page.getByRole("menuitem", { name: "削除" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  const archivedTrigger = page.getByTestId(
    "security-roles-row-actions-role-archived-delete-trigger"
  );
  await archivedTrigger.click();
  await page.getByRole("menuitem", { name: "削除" }).click();
  const deleteDialog = page.getByRole("alertdialog", { name: "削除" });
  await expect(deleteDialog).toContainText(
    "「削除対象ロール」（ロールコード: ARCHIVED_DELETE）を完全に削除します。"
  );
  await expect(deleteDialog).toContainText("機能権限と業務プロファイルの関連は削除され");
  await deleteDialog.getByRole("button", { name: "削除", exact: true }).click();

  await expect(page.getByText("削除対象ロール", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("main", { name: "メイン領域" })).toContainText(
    "Deep Data Security で空の Data Grant を適用してから削除してください。"
  );

  await page
    .getByTestId("security-roles-detail-actions")
    .getByRole("button", { name: "編集" })
    .click();
  const editActions = page.getByRole("group", { name: "ロール編集操作" });
  await editActions.getByRole("button", { name: "その他の操作" }).click();
  await page.getByRole("menuitem", { name: "削除" }).click();
  await deleteDialog.getByRole("button", { name: "削除", exact: true }).click();

  await expect(page.getByText("削除対象ロール", { exact: true })).toHaveCount(0);
  await expect(page.getByText("削除対象ロール を完全に削除しました。", { exact: true }).last()).toBeVisible();
  expect(observedIfMatch).toEqual(['"9"', '"9"']);
  expect(deleteCount).toBe(2);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoPageHorizontalScroll(page);
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
  await expect(page.getByText("構成済み", { exact: true }).first()).toBeVisible();
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
  await expect(page.getByText("構成状態を確認中", { exact: true }).first()).toBeVisible();
  await page.getByRole("tab", { name: "基盤構成" }).click();
  await expect(page.getByRole("heading", { name: "V001.1 共有 DATA USER とロール" })).toBeVisible();
  await expect(page.locator("pre:visible")).toHaveCount(0);
  await page.getByText("SQL とチェックサムを表示", { exact: true }).first().click();
  await expect(page.locator("pre:visible")).toHaveCount(1);
  await expect(page.getByText("CREATE END USER DEEPSEC_DATA_USER", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "データ権限" }).click();
  await expect(page.getByText("Data Grant を適用する前に", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "基盤構成へ" })).toBeVisible();
  const verificationCard = page.getByTestId("security-deepsec-verification-card");
  await expect(verificationCard).toBeVisible();
  await expect(verificationCard.getByText("検証を実行すると", { exact: false })).toBeVisible();
  const verifyButton = verificationCard.getByRole("button", { name: "Data Grant を検証" });
  await expect(verifyButton).toBeDisabled();
  await expectNoElementHorizontalOverflow(verifyButton);

  releaseStatus();
  await expect(page.getByText("未構成", { exact: true }).first()).toBeVisible();
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

  await expect(page.getByText("状態取得失敗", { exact: true }).first()).toBeVisible();
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
    await expect(page.getByText("未構成", { exact: true }).first()).toBeVisible();
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
  type DataEntitlementPayload = {
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
  };
  let previewPayload: {
    version: number;
    data_entitlements: DataEntitlementPayload[];
  } | null = null;
  const applyPayloads: Array<{
    version: number;
    confirmation: string;
    data_entitlements: DataEntitlementPayload[];
  }> = [];
  let failNextApply = false;
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
  const scrollableSalesObjects = [
    salesObject,
    ...Array.from({ length: 6 }, (_, index) => ({
      ...salesObject,
      name: `ORDER_HISTORY_${index + 1}`,
      qualified_name: `SALES.ORDER_HISTORY_${index + 1}`,
      comment: `受注履歴${index + 1}`,
    })),
  ];
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
      ...Array.from({ length: 12 }, (_, index) => ({
        column_name: `AUDIT_COLUMN_${String(index + 1).padStart(2, "0")}`,
        logical_name: `監査列${index + 1}`,
        data_type: "VARCHAR2(64)",
        nullable: true,
        comment: "",
        sample_values: [],
      })),
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
    ownerPrefix: string | null;
    queryScope: string | null;
    q: string | null;
  }> = [];
  await page.route("**/api/nl2sql/db-admin/objects**", async (route) => {
    const url = new URL(route.request().url());
    const cursor = url.searchParams.get("cursor");
    const ownerPrefix = url.searchParams.get("owner_prefix");
    const q = url.searchParams.get("q");
    objectRequests.push({
      limit: url.searchParams.get("limit"),
      cursor,
      ownerPrefix,
      queryScope: url.searchParams.get("query_scope"),
      q,
    });
    const items =
      ownerPrefix === "SAL" || q === "ORDERS"
        ? scrollableSalesObjects
        : cursor === "deepsec-page-2"
          ? scrollableSalesObjects
          : [deepSecTargetObject];
    await fulfill(route, {
      runtime: "oracle",
      owner: ownerPrefix ?? "",
      items,
      total: scrollableSalesObjects.length + 1,
      table_count: scrollableSalesObjects.length + 1,
      view_count: 0,
      counts_included: false,
      next_cursor: ownerPrefix || q || cursor ? null : "deepsec-page-2",
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
      version: number;
      data_entitlements: DataEntitlementPayload[];
    };
    previewPayload = payload;
    const cleanupSql = payload.data_entitlements.length
      ? []
      : [
          "SET USE DATA GRANTS ONLY ON SALES.ORDERS DISABLED",
          "DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DG_PREVIEW",
        ];
    await fulfill(route, {
      role_id: "role-query",
      version: payload.version,
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
      cleanup_sql: cleanupSql,
      checksum: "a".repeat(64),
    });
  });
  await page.route("**/api/security/deepsec/data-entitlements/role-query/apply", async (route) => {
    const payload = route.request().postDataJSON() as {
      version: number;
      confirmation: string;
      data_entitlements: DataEntitlementPayload[];
    };
    applyPayloads.push(payload);
    if (failNextApply) {
      failNextApply = false;
      await fulfill(route, "Oracle で Data Grant の適用に失敗しました。", 500);
      return;
    }
    const currentRole = entitlementRoles.find(
      (role) => (role as { role_id?: string }).role_id === "role-query"
    ) as Record<string, unknown> & { data_entitlements: Array<Record<string, unknown>> };
    const updated = {
      ...currentRole,
      version: payload.version + 1,
      data_entitlements: payload.data_entitlements.map((item, index) => ({
        entitlement_id: item.entitlement_id ?? `saved-${index}`,
        data_grant_name: item.entitlement_id ? "NL2SQL_DG_PREVIEW" : "NL2SQL_DG_SAVED",
        sql_checksum: "e".repeat(64),
        apply_status: "APPLIED",
        apply_error_message: "",
        applied_at: "2026-08-28T00:00:00Z",
        sql: [],
        checksum: "e".repeat(64),
        ...item,
      })),
    };
    entitlementRoles = [
      systemRole,
      updated,
      archivedRole,
    ];
    await fulfill(route, {
      role: updated,
      status: "APPLIED",
      checksum: "b".repeat(64),
      cleanup_count: 0,
      applied_count: updated.data_entitlements.length,
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
    ownerPrefix: null,
    queryScope: "name_comment",
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
  const firstRuleTab = entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-0");
  const firstRule = entitlementForm.getByTestId("security-deepsec-entitlement-rule-0");
  await expect(firstRuleTab).toHaveAttribute("aria-pressed", "true");
  await expect(firstRule.getByText("Data Grant", { exact: true })).toBeVisible();
  await expect(firstRule.getByText("Data Grant 1", { exact: true })).toHaveCount(0);
  const objectPicker = firstRule.getByTestId("security-deepsec-object-picker-0");
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
  const objectSearch = objectPicker.getByRole("searchbox", { name: "検索" });
  const objectOwner = objectPicker.getByRole("searchbox", { name: "所有者" });
  await expect(objectSearch).toHaveAttribute("placeholder", "名前・コメントを入力");
  await expect(objectOwner).toHaveAttribute("placeholder", "所有者の先頭を入力（例：ADM）");
  await expectEqualFilterWidths(objectSearch, objectOwner);
  await objectSearch.focus();
  await page.keyboard.press("Tab");
  await expect(objectOwner).toBeFocused();
  await objectOwner.fill("sal");
  await expect(objectOwner).toHaveValue("SAL");
  await expect
    .poll(() => objectRequests.some((request) => request.ownerPrefix === "SAL"))
    .toBeTruthy();
  await expect(objectPicker.getByRole("option", { name: /SALES\.ORDERS/ })).toBeVisible();
  await objectSearch.fill("ORDERS");
  await expect.poll(() => objectRequests.some((request) => request.q === "ORDERS")).toBeTruthy();
  await objectPicker.getByRole("option", { name: /SALES\.ORDERS/ }).click();
  await expect(entitlementForm.getByText("CUSTOMER_NAME", { exact: true })).toBeVisible();
  const scopeModeSelect = entitlementForm.getByLabel("行 scope");
  const columnsFieldset = entitlementForm.getByRole("group", { name: /許可列/ });
  const columnsLegend = columnsFieldset.locator("legend");
  const columnActions = firstRule.getByTestId("security-deepsec-entitlement-column-selection-actions-0");
  const columnsGrid = firstRule.getByTestId("security-deepsec-entitlement-columns-grid-0");
  const objectPickerList = objectPicker.getByTestId("security-deepsec-object-picker-list-0");
  const main = page.getByRole("main", { name: "メイン領域" });
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
  await setElementScrollTop(firstRule, 64);
  await selectAllColumnsButton.scrollIntoViewIfNeeded();
  if ((await main.evaluate((node) => node.scrollTop)) === 0) {
    await setMainScrollTop(page, 32);
  }
  await selectAllColumnsButton.scrollIntoViewIfNeeded();
  await setElementScrollTop(objectPickerList, 72);
  await setElementScrollTop(columnsGrid, 72);
  await expect
    .poll(() =>
      Promise.all(
        [main, firstRule, objectPickerList, columnsGrid].map((container) =>
          container.evaluate((node) => node.scrollTop)
        )
      ).then((positions) => positions.every((position) => position > 0))
    )
    .toBeTruthy();
  const preservedScrollContainers = [main, firstRule, objectPickerList, columnsGrid];
  await expectScrollPositionsPreserved(page, preservedScrollContainers, () =>
    selectAllColumnsButton.click()
  );
  await expect(selectAllColumnsButton).toBeDisabled();
  await expect(clearAllColumnsButton).toBeEnabled();
  await expect(firstRule.getByRole("checkbox", { name: /ORDER_ID/ })).toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /CUSTOMER_NAME/ })).toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /REGION_CODE/ })).toBeChecked();
  await expectScrollPositionsPreserved(page, preservedScrollContainers, () =>
    clearAllColumnsButton.click()
  );
  await expect(selectAllColumnsButton).toBeEnabled();
  await expect(clearAllColumnsButton).toBeDisabled();
  await expect(firstRule.getByRole("checkbox", { name: /ORDER_ID/ })).not.toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /CUSTOMER_NAME/ })).not.toBeChecked();
  await expect(firstRule.getByRole("checkbox", { name: /REGION_CODE/ })).not.toBeChecked();
  await selectAllColumnsButton.focus();
  await expectScrollPositionsPreserved(page, preservedScrollContainers, () =>
    selectAllColumnsButton.press("Enter")
  );
  await expect(firstRule.getByRole("checkbox", { name: /ORDER_ID/ })).toBeChecked();
  await clearAllColumnsButton.focus();
  await expectScrollPositionsPreserved(page, preservedScrollContainers, () =>
    clearAllColumnsButton.press("Space")
  );
  await expect(firstRule.getByRole("checkbox", { name: /ORDER_ID/ })).not.toBeChecked();
  await expect(objectPicker.locator("#deepsec-entitlement-resource-0 [aria-hidden='true']")).toHaveText("*");
  await expect(firstRule.getByTestId("security-deepsec-entitlement-editor-title-0")).toHaveText("SALES.ORDERS");
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
        columnActionsStartWithGrid: Boolean(
          columnActionsBox &&
            columnsGridBox &&
            Math.abs(columnActionsBox.x - columnsGridBox.x) <= 1
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
      columnActionsStartWithGrid: true,
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
  await entitlementForm.getByText("ロール全体の SQL プレビュー", { exact: true }).click();
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
    version: 3,
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
  expect(applyPayloads).toHaveLength(0);
  await expect(page.getByText("SQL プレビューを生成しました。", { exact: true })).toBeVisible();
  await expect(entitlementForm.getByText("GRANT SELECT ON SALES.ORDERS TO NL2SQL_APP_DB_ROLE")).toBeVisible();
  await expect(entitlementForm.getByRole("button", { name: "ポリシーを保存" })).toHaveCount(0);
  const applyField = entitlementForm.getByTestId("execution-confirmation-field");
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN");
  await expect(applyField.getByRole("button", { name: "Data Grant を適用" })).toBeDisabled();
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await expect(applyField.getByRole("button", { name: "Data Grant を適用" })).toBeEnabled();
  await applyField.getByRole("button", { name: "Data Grant を適用" }).click();
  await expect.poll(() => applyPayloads.at(-1)).toEqual({
    version: 3,
    confirmation: "ADMIN_EXECUTE",
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
  await expect(page.getByText("Data Grant を適用しました。", { exact: true }).last()).toBeVisible();

  await entitlementForm.getByRole("button", { name: "データ権限を削除" }).click();
  await expect(entitlementForm.getByText("Data Grant 1", { exact: true })).toHaveCount(0);
  await entitlementForm.getByText("ロール全体の SQL プレビュー", { exact: true }).click();
  await expect(sqlPreviewButton).toBeEnabled();
  await sqlPreviewButton.click();
  await expect.poll(() => previewPayload).toEqual({ version: 4, data_entitlements: [] });
  await expect(
    entitlementForm.getByText("DROP DATA GRANT IF EXISTS APP_OWNER.NL2SQL_DG_PREVIEW")
  ).toBeVisible();
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await expect(applyField.getByRole("button", { name: "Data Grant を適用" })).toBeEnabled();
  await applyField.getByRole("button", { name: "Data Grant を適用" }).click();
  await expect.poll(() => applyPayloads.at(-1)).toEqual({
    version: 4,
    confirmation: "ADMIN_EXECUTE",
    data_entitlements: [],
  });
  await expect(page.getByText("Data Grant を適用しました。", { exact: true }).last()).toBeVisible();

  await entitlementForm.getByRole("button", { name: "データ権限を追加" }).click();
  const replacementRule = entitlementForm.getByTestId("security-deepsec-entitlement-rule-0");
  const replacementPicker = replacementRule.getByTestId("security-deepsec-object-picker-0");
  await expect(replacementRule.getByText("Data Grant", { exact: true })).toBeVisible();
  await replacementPicker.getByRole("option", { name: /SALES\.ORDERS/ }).click();
  await replacementRule.getByRole("checkbox", { name: /ORDER_ID/ }).check();
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  failNextApply = true;
  await applyField.getByRole("button", { name: "Data Grant を適用" }).click();
  await expect(
    entitlementForm.getByText("Oracle で Data Grant の適用に失敗しました。", { exact: true })
  ).toBeVisible();
  await expect(replacementRule.getByRole("checkbox", { name: /ORDER_ID/ })).toBeChecked();
  await expect(applyField.getByRole("textbox", { name: "実行確認語" })).toHaveValue(
    "ADMIN_EXECUTE"
  );
  await applyField.getByRole("button", { name: "Data Grant を適用" }).click();
  await expect.poll(() => applyPayloads.at(-1)).toEqual({
    version: 5,
    confirmation: "ADMIN_EXECUTE",
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

test("DeepSec Data Grant は3件追加しても下部操作と選択編集を維持する", async ({ page }, testInfo) => {
  if (testInfo.project.name === "desktop") {
    await page.setViewportSize({ width: 1600, height: 948 });
  }
  await mockDatabaseGateReady(page);
  const dataRole = {
    role_id: "role-data",
    role_code: "DATA_USER",
    display_name: "データユーザー",
    description: "構造化データ参照",
    is_built_in: false,
    archived: false,
    version: 1,
    data_entitlements: [
      {
        entitlement_id: "entitlement-existing-1",
        data_grant_name: "NL2SQL_DG_EMPLOYEES_READ",
        resource_code: "HR.EMPLOYEES",
        scope_code: "*",
        capability: "SELECT",
        target_owner: "HR",
        target_object: "EMPLOYEES",
        target_type: "TABLE",
        column_names: ["EMPLOYEE_ID"],
        scope_mode: "ALL",
        scope_column: "",
        scope_filters: [],
        apply_status: "PENDING",
        apply_error_message: "",
        applied_at: null,
        sql: [],
        checksum: "a".repeat(64),
      },
      {
        entitlement_id: "entitlement-existing-2",
        data_grant_name: "NL2SQL_DG_EMPLOYEES_PROFILE",
        resource_code: "HR.EMPLOYEES",
        scope_code: "*",
        capability: "SELECT",
        target_owner: "HR",
        target_object: "EMPLOYEES",
        target_type: "TABLE",
        column_names: ["DISPLAY_NAME"],
        scope_mode: "ALL",
        scope_column: "",
        scope_filters: [],
        apply_status: "PENDING",
        apply_error_message: "",
        applied_at: null,
        sql: [],
        checksum: "b".repeat(64),
      },
    ],
  };
  const auditRole = {
    ...dataRole,
    role_id: "role-audit",
    role_code: "AUDIT_USER",
    display_name: "監査ユーザー",
    data_entitlements: [
      {
        entitlement_id: "entitlement-audit-1",
        data_grant_name: "NL2SQL_DG_AUDIT_EMPLOYEES",
        resource_code: "HR.EMPLOYEES",
        scope_code: "*",
        capability: "SELECT",
        target_owner: "HR",
        target_object: "EMPLOYEES",
        target_type: "TABLE",
        column_names: ["EMPLOYEE_ID", "DISPLAY_NAME"],
        scope_mode: "ALL",
        scope_column: "",
        scope_filters: [],
        apply_status: "PENDING",
        apply_error_message: "",
        applied_at: null,
        sql: [],
        checksum: "c".repeat(64),
      },
    ],
  };
  let roleWidePreviewPayload: {
    version: number;
    data_entitlements: Array<Record<string, unknown>>;
  } | null = null;
  await page.route("**/api/nl2sql/db-admin/objects**", (route) => {
    const cursor = new URL(route.request().url()).searchParams.get("cursor");
    fulfill(route, {
      runtime: "oracle",
      owner: "",
      items: [deepSecTargetObject],
      total: 2,
      table_count: 2,
      view_count: 0,
      counts_included: false,
      next_cursor: cursor ? null : "deepsec-page-2",
      refreshed_at: "2026-07-19T00:00:00Z",
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/EMPLOYEES**", (route) =>
    fulfill(route, deepSecTargetObjectDetail)
  );
  await page.route("**/api/security/deepsec/status", (route) =>
    fulfill(route, {
      configured: true,
      driver_mode: "thin",
      connection_security: "wallet_mtls",
      deepsec_enabled: true,
      data_user: "DEEPSEC_DATA_USER",
      has_data_user_password: true,
      objects: { data_grants: 0 },
      message: "構成済みです。",
    })
  );
  await page.route("**/api/security/deepsec/plan", (route) => fulfill(route, deepSecPlan(true)));
  await page.route("**/api/security/deepsec/data-entitlements", (route) =>
    fulfill(route, [dataRole, auditRole])
  );
  await page.route("**/api/security/deepsec/data-entitlements/role-data/preview", async (route) => {
    roleWidePreviewPayload = route.request().postDataJSON() as {
      version: number;
      data_entitlements: Array<Record<string, unknown>>;
    };
    await fulfill(route, {
      role_id: dataRole.role_id,
      version: dataRole.version,
      data_entitlements: dataRole.data_entitlements.map((item) => ({
        ...item,
        sql: [
          `-- ${item.data_grant_name}`,
          `GRANT SELECT ON ${item.resource_code} TO NL2SQL_APP_DB_ROLE`,
          `DROP DATA GRANT IF EXISTS APP_OWNER.${item.data_grant_name}`,
          `SET USE DATA GRANTS ONLY ON ${item.resource_code} ENABLED`,
        ],
        checksum: item.checksum,
      })),
      cleanup_sql: [],
      checksum: "d".repeat(64),
    });
  });

  await page.goto("/settings/security/deepsec");
  await page.getByRole("tab", { name: "データ権限" }).click();
  const entitlementForm = page.getByTestId("security-deepsec-entitlement-form");
  const addGrantButton = entitlementForm.getByRole("button", { name: "データ権限を追加" });
  const dataRoleCard = page.getByTestId("security-deepsec-entitlement-role-role-data");
  const auditRoleCard = page.getByTestId("security-deepsec-entitlement-role-role-audit");

  await expect(dataRoleCard).toBeVisible();
  await expect(auditRoleCard).toBeVisible();
  await setMainScrollTop(page, 0);
  await expectMainScrollPreservedAfterClick(page, dataRoleCard);
  await expect(dataRoleCard).toHaveAttribute("aria-pressed", "true");
  await expectElementScrollTop(entitlementForm.getByTestId("security-deepsec-entitlement-rule-0"), 0);
  await setElementScrollTop(entitlementForm.getByTestId("security-deepsec-entitlement-rule-0"), 96);
  await setMainScrollTop(page, 0);
  await expectMainScrollPreservedAfterClick(page, auditRoleCard);
  await expect(auditRoleCard).toHaveAttribute("aria-pressed", "true");
  await expectElementScrollTop(entitlementForm.getByTestId("security-deepsec-entitlement-rule-0"), 0);
  await setMainScrollTop(page, 0);
  await expectMainScrollPreservedAfterClick(page, dataRoleCard);
  await expect(dataRoleCard).toHaveAttribute("aria-pressed", "true");
  await expect(entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-0")).toBeVisible();
  await expect(entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-1")).toBeVisible();
  const secondSavedRuleTab = entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-1");
  await secondSavedRuleTab.click();
  await expect(secondSavedRuleTab).toHaveAttribute("aria-pressed", "true");
  const workspaceFrame = entitlementForm.getByTestId(
    "security-deepsec-entitlement-workspace-frame"
  );
  const sqlPreview = entitlementForm.getByTestId("security-deepsec-sql-preview");
  const workspaceHeightBeforePreview = Math.round(
    await workspaceFrame.evaluate((node) => node.getBoundingClientRect().height)
  );
  await sqlPreview.locator("summary").focus();
  await sqlPreview.locator("summary").press("Enter");
  await expect(sqlPreview).toHaveAttribute("open", "");
  await expect
    .poll(() =>
      workspaceFrame.evaluate((node) => Math.round(node.getBoundingClientRect().height))
    )
    .toBe(workspaceHeightBeforePreview);
  await entitlementForm.getByTestId("security-deepsec-sql-preview-generate").click();
  await expect.poll(() => roleWidePreviewPayload).toMatchObject({
    version: 1,
    data_entitlements: [
      { entitlement_id: "entitlement-existing-1" },
      { entitlement_id: "entitlement-existing-2" },
    ],
  });
  await expect(entitlementForm.getByText("-- NL2SQL_DG_EMPLOYEES_READ")).toBeVisible();
  await expect(entitlementForm.getByText("-- NL2SQL_DG_EMPLOYEES_PROFILE")).toBeVisible();
  await expect
    .poll(() =>
      workspaceFrame.evaluate((node) => Math.round(node.getBoundingClientRect().height))
    )
    .toBe(workspaceHeightBeforePreview);
  await expect
    .poll(() =>
      sqlPreview.evaluate((node) => ({
        scrolls: node.scrollHeight > node.clientHeight + 1,
        overflowY: window.getComputedStyle(node).overflowY,
      }))
    )
    .toEqual({ scrolls: true, overflowY: "auto" });
  await entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-0").click();
  await setElementScrollTop(entitlementForm.getByTestId("security-deepsec-entitlement-rule-0"), 96);
  await setMainScrollTop(page, 0);
  await expectMainScrollPreservedAfterClick(page, addGrantButton);

  const rulesList = entitlementForm.getByTestId("security-deepsec-entitlement-rules-list");
  const firstRuleTab = entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-0");
  const secondRuleTab = entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-1");
  const thirdRuleTab = entitlementForm.getByTestId("security-deepsec-entitlement-rule-tab-2");
  const actionRegion = entitlementForm.getByTestId("security-deepsec-entitlement-action-region");

  await expect(rulesList).toBeVisible();
  await expect(firstRuleTab).toHaveAttribute("aria-pressed", "false");
  await expect(secondRuleTab).toHaveAttribute("aria-pressed", "false");
  await expect(thirdRuleTab).toHaveAttribute("aria-pressed", "true");
  await expectElementScrollTop(entitlementForm.getByTestId("security-deepsec-entitlement-rule-2"), 0);
  if (testInfo.project.name === "desktop") {
    await expect
      .poll(() =>
        entitlementForm.evaluate((node) => {
          const workspace = node.querySelector(
            '[data-testid="security-deepsec-entitlement-workspace"]'
          );
          const editor = node.querySelector('[data-testid="security-deepsec-entitlement-rule-2"]');
          const workspaceBox = workspace?.getBoundingClientRect();
          const editorBox = editor?.getBoundingClientRect();
          return {
            workspaceHeightIsExpected: Boolean(
              workspaceBox && workspaceBox.height >= 447 && workspaceBox.height <= 449
            ),
            editorMatchesWorkspace: Boolean(
              workspaceBox && editorBox && Math.abs(workspaceBox.height - editorBox.height) <= 1
            ),
            editorScrollable: Boolean(
              editor &&
                window.getComputedStyle(editor).overflowY === "auto" &&
                editor.scrollHeight > editor.clientHeight
            ),
          };
        })
      )
      .toEqual({
        workspaceHeightIsExpected: true,
        editorMatchesWorkspace: true,
        editorScrollable: true,
      });
  }
  await expect(entitlementForm.getByTestId("security-deepsec-entitlement-rule-2").getByTestId("security-deepsec-object-picker-2")).toBeVisible();
  await expectDeepSecDataGrantRegionsDoNotOverlap(page, "security-deepsec-entitlement-rule-2");
  const thirdObjectPicker = entitlementForm
    .getByTestId("security-deepsec-entitlement-rule-2")
    .getByTestId("security-deepsec-object-picker-2");
  await expectElementCenterUnobscured(thirdObjectPicker.getByRole("option", { name: /HR\.EMPLOYEES/u }));
  await expectElementCenterUnobscured(
    thirdObjectPicker.getByTestId("security-deepsec-object-picker-load-more-2")
  );
  await expectElementCenterUnobscured(entitlementForm.getByRole("button", { name: "データ権限を削除" }));
  await thirdObjectPicker.getByTestId("security-deepsec-object-picker-load-more-2").click();
  await setElementScrollTop(entitlementForm.getByTestId("security-deepsec-entitlement-rule-2"), 96);
  await setMainScrollTop(page, 0);
  await expectMainScrollPreservedAfterClick(page, firstRuleTab);
  await expect(firstRuleTab).toHaveAttribute("aria-pressed", "true");
  await expect(thirdRuleTab).toHaveAttribute("aria-pressed", "false");
  await expectElementScrollTop(entitlementForm.getByTestId("security-deepsec-entitlement-rule-0"), 0);
  await expect(entitlementForm.getByTestId("security-deepsec-entitlement-rule-0").getByTestId("security-deepsec-object-picker-0")).toBeVisible();
  await expect(entitlementForm.getByTestId("security-deepsec-entitlement-rule-2")).toHaveCount(0);
  await expectDeepSecDataGrantRegionsDoNotOverlap(page, "security-deepsec-entitlement-rule-0");

  await expect(actionRegion.getByTestId("security-deepsec-sql-preview")).toBeVisible();
  await expect(actionRegion.getByTestId("execution-confirmation-field")).toBeVisible();
  const applyField = actionRegion.getByTestId("execution-confirmation-field");
  const applyButton = actionRegion.getByRole("button", { name: "Data Grant を適用" });
  await expect(applyButton).toBeVisible();
  await applyField.getByRole("textbox", { name: "実行確認語" }).fill("ADMIN_EXECUTE");
  await expect(applyButton).toBeEnabled();
  await expectElementCenterUnobscured(applyButton);
  await expect
    .poll(() =>
      entitlementForm.evaluate((node) => {
        const list = node.querySelector('[data-testid="security-deepsec-entitlement-rules-list"]');
        const actions = node.querySelector('[data-testid="security-deepsec-entitlement-action-region"]');
        const formStyle = window.getComputedStyle(node);
        const listStyle = list ? window.getComputedStyle(list) : null;
        const formBox = node.getBoundingClientRect();
        const actionBox = actions?.getBoundingClientRect();
        return {
          formOverflow: formStyle.overflow,
          listOverflowY: listStyle?.overflowY,
          actionsInsideForm: Boolean(
            actionBox &&
              actionBox.y >= formBox.y - 1 &&
              actionBox.y + actionBox.height <= formBox.y + formBox.height + 1
          ),
        };
      })
    )
    .toEqual({
      formOverflow: "visible",
      listOverflowY: "auto",
      actionsInsideForm: true,
    });
  await expectNoElementHorizontalOverflow(entitlementForm);
  await expectNoPageHorizontalScroll(page);

  if (testInfo.project.name === "desktop") {
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect
      .poll(async () =>
        Math.round(await workspaceFrame.evaluate((node) => node.getBoundingClientRect().height))
      )
      .toBe(448);
    await expectNoPageHorizontalScroll(page);
    await expectNoElementHorizontalOverflow(entitlementForm);
    await expectDeepSecDataGrantRegionsDoNotOverlap(page, "security-deepsec-entitlement-rule-0");
    await expectElementCenterUnobscured(applyButton);
  }

  await page.setViewportSize({ width: 375, height: 812 });
  await expect
    .poll(async () =>
      Math.round(await workspaceFrame.evaluate((node) => node.getBoundingClientRect().height))
    )
    .toBe(504);
  await expectNoPageHorizontalScroll(page);
  await expectNoElementHorizontalOverflow(entitlementForm);
  await expectDeepSecDataGrantRegionsDoNotOverlap(page, "security-deepsec-entitlement-rule-0");
  await expectElementCenterUnobscured(applyButton);
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
        { key: "foundation", passed: true, detail: "Deep Data Security の基盤構成は適用済みです。" },
        {
          key: "predicate_table_grants",
          passed: true,
          detail: "Data Grant predicate 用 app table SELECT grants are applied.",
        },
        {
          key: "vpd_policy:department",
          passed: true,
          detail: "ADMIN.DEPARTMENT: enabled VPD/RLS policies are not present.",
        },
        {
          key: "data_grant:department",
          passed: true,
          detail: "NL2SQL_DG_DEPARTMENT -> ADMIN.DEPARTMENT: rows=4, data_role_rows=4",
        },
        {
          key: "data_grant:employee",
          passed: true,
          detail: "NL2SQL_DG_EMPLOYEE -> ADMIN.EMPLOYEE: rows=6, data_role_rows=6",
        },
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
  const dataPermissionsPanel = page.locator("#security-deepsec-panel-data-permissions");
  const dataPermissionsHeader = dataPermissionsPanel.locator("xpath=./div[1]");
  const verificationCard = page.getByTestId("security-deepsec-verification-card");
  await expect(dataPermissionsHeader.getByRole("button", { name: "Data Grant を検証" })).toHaveCount(0);
  await expect(verificationCard.getByText("検証を実行すると", { exact: false })).toBeVisible();
  const verifyButton = verificationCard.getByRole("button", { name: "Data Grant を検証" });
  await expect(verifyButton).toBeEnabled();
  await expectNoElementHorizontalOverflow(verifyButton);
  await verifyButton.click();
  await page.getByRole("alertdialog").getByRole("button", { name: "実行" }).click();
  await expect(verificationCard.getByText("no_context", { exact: true })).toBeVisible();
  await expect(verificationCard.getByText("sensitive_masked=true", { exact: true })).toBeVisible();
  await expect(verificationCard.getByText(/2026\/07\/19/u)).toHaveCount(0);
  const verificationResults = verificationCard.getByTestId(
    "security-deepsec-verification-results"
  );
  await expect(verificationResults).toHaveAttribute("role", "region");
  await expect(verificationResults).toHaveAttribute(
    "aria-label",
    "Data Grant 検証結果。必要に応じて縦方向にスクロールできます。"
  );
  await expect(verificationResults).toHaveAttribute("tabindex", "0");
  const verificationScrollState = await verificationResults.evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      clientHeight: node.clientHeight,
      maxHeight: Number.parseFloat(computed.maxHeight),
      overflowY: computed.overflowY,
      rootFontSize: Number.parseFloat(
        window.getComputedStyle(document.documentElement).fontSize
      ),
      scrollHeight: node.scrollHeight,
    };
  });
  expect(verificationScrollState.maxHeight).toBeCloseTo(
    23.25 * verificationScrollState.rootFontSize,
    0
  );
  expect(verificationScrollState.clientHeight).toBeLessThanOrEqual(
    Math.ceil(verificationScrollState.maxHeight)
  );
  expect(verificationScrollState.scrollHeight).toBeGreaterThan(
    verificationScrollState.clientHeight
  );
  expect(verificationScrollState.overflowY).toBe("auto");
  await verificationResults.focus();
  await expect(verificationResults).toBeFocused();
  await verificationResults.press("End");
  await expect
    .poll(() => verificationResults.evaluate((node) => node.scrollTop))
    .toBeGreaterThan(0);

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
