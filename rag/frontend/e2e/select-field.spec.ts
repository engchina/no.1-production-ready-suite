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

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: authStatus });
      return;
    }

    await route.fulfill({
      json: { data: null, error_messages: [], warning_messages: [] },
    });
  });
}

test.beforeEach(async ({ page }) => {
  if ((page.viewportSize()?.width ?? 0) <= 500) {
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "production-ready-rag.ui",
        JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
      );
    });
  }
  await mockApi(page);
});

test("OCI リージョンの候補を指定順で表示し、選択できる", async ({ page }) => {
  await page.goto("/settings/oci");

  const region = page.getByRole("combobox", { name: "リージョン", exact: true });
  await expect(region).toContainText("us-chicago-1");

  await region.click();
  const listbox = page.getByRole("listbox", { name: "リージョン", exact: true });
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option")).toHaveText([
    "ap-tokyo-1",
    "ap-osaka-1",
    "us-chicago-1",
  ]);
  await expect(listbox).toHaveClass(/shadow-lg/);

  await listbox.getByRole("option", { name: "ap-tokyo-1" }).click();
  await expect(region).toContainText("ap-tokyo-1");
  await expect(page.getByRole("listbox", { name: "リージョン", exact: true })).toBeHidden();
});

test("検索条件の内容種別も同じドロップダウン UI で選択できる", async ({ page }) => {
  await page.goto("/search");

  const contentKind = page.getByRole("combobox", { name: "内容種別" });
  await contentKind.click();

  const listbox = page.getByRole("listbox", { name: "内容種別" });
  await expect(listbox.getByRole("option")).toHaveText([
    "すべて",
    "本文",
    "箇条書き",
    "表",
    "図・画像",
  ]);

  await listbox.getByRole("option", { name: "表" }).click();
  await expect(contentKind).toContainText("表");
});

test("評価のランキング指標も同じドロップダウン UI で選択できる", async ({ page }) => {
  await page.goto("/evaluation");

  const rankingMetric = page.getByRole("combobox", { name: "ランキング指標" });
  await rankingMetric.click();

  const listbox = page.getByRole("listbox", { name: "ランキング指標" });
  await expect(listbox.getByRole("option")).toHaveText([
    "MRR",
    "Recall@K",
    "Precision@K",
    "回答キーワード",
    "Groundedness",
  ]);

  await listbox.getByRole("option", { name: "Recall@K" }).click();
  await expect(rankingMetric).toContainText("Recall@K");
});
