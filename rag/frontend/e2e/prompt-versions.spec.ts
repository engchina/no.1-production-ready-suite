import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

type Version = { version_id: string; name: string; active: boolean };

function promptsEnvelope(versions: Version[]) {
  return {
    data: {
      active_version_id: versions.find((v) => v.active)?.version_id ?? null,
      versions: versions.map((v) => ({
        version_id: v.version_id,
        name: v.name,
        system_prompt: "あなたは厳密なアシスタントです。",
        note: "",
        created_at: "2026-06-28T00:00:00Z",
        created_by: "",
        active: v.active,
      })),
    },
    error_messages: [],
    warning_messages: [],
  };
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`回答プロンプト画面は版一覧と作成フォームを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await page.route("**/api/settings/prompts", async (route) => {
      await route.fulfill({
        json: promptsEnvelope([
          { version_id: "v1", name: "標準版", active: true },
          { version_id: "v2", name: "監査版", active: false },
        ]),
      });
    });

    await page.goto("/settings/prompts");

    await expect(page.getByRole("heading", { name: "回答プロンプト版" })).toBeVisible();
    await expect(page.getByText("標準版")).toBeVisible();
    await expect(page.getByText("監査版")).toBeVisible();
    await expect(page.getByRole("button", { name: "版を作成" })).toBeVisible();
    await expect(page.getByRole("link", { name: "回答プロンプト" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoPageOverflow(page);
  });
}

test("回答プロンプト版を作成できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let created: unknown = null;
  await page.route("**/api/settings/prompts", async (route) => {
    if (route.request().method() === "POST") {
      created = route.request().postDataJSON();
      await route.fulfill({
        json: promptsEnvelope([{ version_id: "v1", name: "新規版", active: true }]),
      });
      return;
    }
    await route.fulfill({ json: promptsEnvelope([]) });
  });

  await page.goto("/settings/prompts");
  await page.getByPlaceholder(/例:/).fill("新規版");
  await page.getByPlaceholder(/社内ナレッジ検索アシスタント/).fill("厳密に回答してください。");
  await page.getByRole("button", { name: "版を作成" }).click();

  await expect(page.getByText("回答プロンプト版を作成しました。")).toBeVisible();
  expect(created).toEqual({
    name: "新規版",
    system_prompt: "厳密に回答してください。",
    activate: true,
  });
  await expectNoPageOverflow(page);
});

test("別の版を有効化できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let activatedUrl: string | null = null;
  await page.route("**/api/settings/prompts/*/activate", async (route) => {
    activatedUrl = route.request().url();
    await route.fulfill({
      json: promptsEnvelope([
        { version_id: "v1", name: "標準版", active: false },
        { version_id: "v2", name: "監査版", active: true },
      ]),
    });
  });
  await page.route("**/api/settings/prompts", async (route) => {
    await route.fulfill({
      json: promptsEnvelope([
        { version_id: "v1", name: "標準版", active: true },
        { version_id: "v2", name: "監査版", active: false },
      ]),
    });
  });

  await page.goto("/settings/prompts");
  await page.getByRole("button", { name: "有効化 監査版" }).click();

  await expect(page.getByText("回答プロンプト版を有効化しました。")).toBeVisible();
  expect(activatedUrl).toContain("/api/settings/prompts/v2/activate");
});

test("回答プロンプト取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/prompts", async (route) => {
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["回答プロンプト版を取得できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/settings/prompts");

  await expect(page.getByRole("alert")).toContainText("回答プロンプト版を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}
