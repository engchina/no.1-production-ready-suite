import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`評価の基準は品質評価の基準を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockEvaluation(page, "standard");

    await page.goto("/settings/evaluation");

    await expect(page.getByRole("heading", { name: "評価の基準", exact: true, level: 1 })).toBeVisible();
    // 基準は標準・厳格の 2 つだけ(#591)。
    await expect(page.getByRole("radio")).toHaveCount(2);
    await expect(page.getByRole("radio", { name: /標準/ })).toBeChecked();
    await expect(page.getByRole("radio", { name: /厳格/ })).toBeVisible();
    // 選んだ基準の閾値を、検索・根拠・回答の観点ごとに指標の意味と一緒に出す。
    const metrics = page.getByTestId("settings-evaluation-metrics");
    await expect(metrics.getByRole("heading", { name: "標準の指標と閾値" })).toBeVisible();
    for (const perspective of ["検索", "根拠", "回答"]) {
      await expect(metrics.getByRole("heading", { name: perspective, exact: true })).toBeVisible();
    }
    await expect(metrics.locator("[data-testid^='settings-evaluation-metric-']")).toHaveCount(9);
    await expect(page.getByTestId("settings-evaluation-metric-context_recall")).toContainText(
      "閾値 80%"
    );
    await expect(
      page.getByTestId("settings-evaluation-metric-answer_pass_rate")
    ).toContainText("標準回答が必要");
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect(
      (await openSidebarNav(page))
        .locator("#nav-section-nav-section-pipeline")
        .getByRole("link", { name: "評価の基準" })
    ).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page);
  });
}

test("評価の基準は厳格を選んで閾値を表示し保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let saved: unknown = null;
  await page.route("**/api/settings/evaluation-suite", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: evaluationEnvelope("strict") });
      return;
    }
    await route.fulfill({ json: evaluationEnvelope("standard") });
  });

  await page.goto("/settings/evaluation");

  const strict = page.getByRole("radio", { name: /厳格/ });
  await strict.click();
  await expect(strict).toBeChecked();
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();
  await expect(page.getByTestId("settings-evaluation-metric-claim_support_rate")).toContainText(
    "閾値 100%"
  );

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("品質評価設定を保存しました。")).toBeVisible();
  expect(saved).toEqual({ suite: "strict" });
  await expectNoHorizontalOverflow(page);
});

test("品質評価設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/evaluation-suite", async (route) => {
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["品質評価設定を取得できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/settings/evaluation");

  await expect(page.getByRole("alert")).toContainText("品質評価設定を取得できませんでした。");
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

function evaluationEnvelope(suite: string) {
  const thresholds = (strict: boolean) => ({
    context_recall: strict ? 0.9 : 0.8,
    mrr: strict ? 0.8 : 0.6,
    faithfulness: strict ? 0.8 : 0.7,
    citation_traceability_coverage: strict ? 0.95 : 0.9,
    claim_support_rate: strict ? 1 : 0.9,
    answer_keyword_hit_rate: strict ? 0.9 : 0.8,
    refusal_accuracy: strict ? 1 : 0.9,
    requirement_coverage: strict ? 0.9 : 0.8,
    answer_pass_rate: strict ? 0.8 : 0.7,
  });
  const specs = [
    { name: "standard", thresholds: thresholds(false) },
    { name: "strict", thresholds: thresholds(true) },
  ];
  const selected = specs.find((s) => s.name === suite) ?? specs[0];
  return {
    data: {
      suite,
      thresholds: selected.thresholds,
      suites: specs.map((s) => ({
        ...s,
        origin: "x",
        recommended_for: ["general"],
        selected: s.name === suite,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockEvaluation(page: Page, suite: string) {
  await page.route("**/api/settings/evaluation-suite", async (route) => {
    await route.fulfill({ json: evaluationEnvelope(suite) });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
