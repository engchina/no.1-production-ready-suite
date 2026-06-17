import { expect, test, type Page } from "@playwright/test";

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

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`Parser adapter 設定は runtime readiness を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    await mockParserAdapters(page);

    await page.goto("/settings/parser-adapters");

    await expect(page.getByRole("heading", { name: "Parser アダプター" })).toBeVisible();
    await expect(page.getByText("Docling -> Marker")).toBeVisible();
    await expect(page.getByText("Active")).toBeVisible();
    await expect(page.getByText("Missing")).toBeVisible();
    await expect(page.getByText("package 未導入")).toBeVisible();
    await expect(page.getByText("backend 選択外")).toBeVisible();

    const navLink = page.getByRole("link", { name: "Parser アダプター" });
    await expect(navLink).toHaveAttribute("aria-current", "page");
    await navLink.focus();
    await expect(navLink).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/settings\/parser-adapters$/);
    await expectNoHorizontalOverflow(page);
  });
}

test("Parser adapter 設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["Parser adapter 設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/parser-adapters");

  await expect(page.getByRole("alert")).toContainText(
    "Parser adapter 設定を取得できませんでした。"
  );
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

async function mockParserAdapters(page: Page) {
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      json: {
        data: {
          adapter_backend: "auto",
          effective_order: ["docling", "marker"],
          config_source: "runtime",
          adapters: [
            {
              backend: "docling",
              package_name: "docling",
              enabled: true,
              selected: true,
              installed: true,
              status: "active",
              version: "1.2.3",
              warning_code: null,
            },
            {
              backend: "marker",
              package_name: "marker",
              enabled: true,
              selected: true,
              installed: false,
              status: "missing",
              version: null,
              warning_code: "adapter_package_missing",
            },
            {
              backend: "unstructured",
              package_name: "unstructured",
              enabled: true,
              selected: false,
              installed: false,
              status: "ignored",
              version: null,
              warning_code: "adapter_flag_ignored_by_backend",
            },
          ],
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
}
