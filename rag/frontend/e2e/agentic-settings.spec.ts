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

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`高度な検索設定は検索方式を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockAgentic(page, "off");

    await page.goto("/settings/agentic");

    await expect(page.getByRole("heading", { name: "高度な検索" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /計画なし/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /スマートルーティング/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /検索向けに 1 回 LLM で書き換え/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /HyDE/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /RRF 融合へ注入/ })).toBeVisible();
    // hyde カードは label と専用チップで "HyDE" を 2 回描画する。
    await expect(page.getByText("HyDE", { exact: true })).toHaveCount(2);
    await expect(page.getByRole("link", { name: "高度な検索" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("高度な検索設定は off 以外で LLM 追加呼び出し警告を出して保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let saved: unknown = null;
  await page.route("**/api/settings/agentic", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: agenticEnvelope("decompose") });
      return;
    }
    await route.fulfill({ json: agenticEnvelope("off") });
  });

  await page.goto("/settings/agentic");

  // off では警告を出さない。
  await expect(page.getByText(/追加の LLM 呼び出し/)).toHaveCount(0);

  const decompose = page.getByRole("radio", { name: /RRF 融合へ注入/ });
  await decompose.click();
  await expect(decompose).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText(/追加の LLM 呼び出し/).first()).toBeVisible();

  // max_subqueries も編集して保存 payload に含める。
  await page.getByLabel("最大 sub-question 数").fill("5");
  await expect(page.getByLabel("最大 sub-question 数")).toHaveValue("5");
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("高度な検索設定を保存しました。")).toBeVisible();
  expect(saved).toEqual({ profile: "decompose", max_subqueries: 5 });
  await expectNoHorizontalOverflow(page);
});

test("高度な検索設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/agentic", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["高度な検索設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/agentic");

  await expect(page.getByRole("alert")).toContainText("高度な検索設定を取得できませんでした。");
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

function agenticEnvelope(profile: string) {
  const specs = [
    { name: "off", enabled: false, rewrite: false, decompose: false, multi_hop: false, hyde: false },
    {
      name: "smart_routing",
      enabled: true,
      rewrite: true,
      decompose: false,
      multi_hop: false,
      hyde: false,
    },
    {
      name: "query_rewrite",
      enabled: true,
      rewrite: true,
      decompose: false,
      multi_hop: false,
      hyde: false,
    },
    { name: "hyde", enabled: true, rewrite: true, decompose: false, multi_hop: false, hyde: true },
    {
      name: "decompose",
      enabled: true,
      rewrite: false,
      decompose: true,
      multi_hop: false,
      hyde: false,
    },
    {
      name: "multi_hop",
      enabled: true,
      rewrite: false,
      decompose: true,
      multi_hop: true,
      hyde: false,
    },
  ];
  const selected = specs.find((s) => s.name === profile) ?? specs[0];
  return {
    data: {
      profile,
      enabled: selected.enabled,
      rewrite: selected.rewrite,
      decompose: selected.decompose,
      multi_hop: selected.multi_hop,
      max_subqueries: 3,
      profiles: specs.map((s) => ({
        ...s,
        origin: "x",
        recommended_for: ["general"],
        selected: s.name === profile,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockAgentic(page: Page, profile: string) {
  await page.route("**/api/settings/agentic", async (route) => {
    await route.fulfill({ json: agenticEnvelope(profile) });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
