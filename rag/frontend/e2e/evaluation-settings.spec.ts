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
  test(`品質評価設定は品質評価設定を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockEvaluation(page, "request_only");

    await page.goto("/settings/evaluation");

    await expect(page.getByRole("heading", { name: "品質評価設定" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /リクエスト準拠/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /厳格 CI/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Ragas 観点/ })).toBeVisible();
    await expect(
      page.getByText("プリセット閾値なし(request の thresholds を使用)")
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "品質評価設定" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("品質評価設定は strict_ci を選んで閾値表示し保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let saved: unknown = null;
  await page.route("**/api/settings/evaluation-suite", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: evaluationEnvelope("strict_ci") });
      return;
    }
    await route.fulfill({ json: evaluationEnvelope("request_only") });
  });

  await page.goto("/settings/evaluation");

  const strict = page.getByRole("radio", { name: /厳格 CI/ });
  await strict.click();
  await expect(strict).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("groundedness_pass_rate", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("品質評価設定を保存しました。")).toBeVisible();
  expect(saved).toEqual({ suite: "strict_ci" });
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
  const specs: { name: string; thresholds: Record<string, number>; focus_metrics: string[] }[] = [
    { name: "request_only", thresholds: {}, focus_metrics: [] },
    {
      name: "retrieval_focused",
      thresholds: { precision_at_k: 0.6, recall_at_k: 0.8, mrr: 0.7 },
      focus_metrics: ["precision_at_k", "recall_at_k", "mrr"],
    },
    {
      name: "balanced",
      thresholds: { precision_at_k: 0.6, recall_at_k: 0.8, mrr: 0.7, groundedness_pass_rate: 0.9 },
      focus_metrics: ["groundedness_pass_rate"],
    },
    {
      name: "strict_ci",
      thresholds: { groundedness_pass_rate: 0.95, citation_traceability_coverage: 0.9 },
      focus_metrics: ["groundedness_pass_rate", "citation_traceability_coverage"],
    },
    {
      name: "ragas_like",
      thresholds: { faithfulness: 0.8, context_recall: 0.8 },
      focus_metrics: ["faithfulness", "context_recall"],
    },
  ];
  const selected = specs.find((s) => s.name === suite) ?? specs[0];
  return {
    data: {
      suite,
      thresholds: selected.thresholds,
      focus_metrics: selected.focus_metrics,
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
