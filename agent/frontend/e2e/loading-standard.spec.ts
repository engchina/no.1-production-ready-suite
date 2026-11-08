import type { Page, Route } from "@playwright/test";

import { findLoadingStandardViolations } from "../../../platform/packages/ui/e2e/loading-checks";
import { expect, test } from "./fixtures/mock-api";

/**
 * 読み込み中の表示の基準（#4649。docs/agents/ui.md「読み込み中」）を、Agent の代表の画面で確かめる。
 * 領域の先頭に灰色の枠・処理の名前・経過時間を 1 つだけ出し、領域の全幅を使い、内容の形の Skeleton で覆う。
 * 同じ時に読み込む複数の要求は 1 つの表示にまとめ、欄の読み込みは欄の形の Skeleton で場所を確保する。
 * desktop と mobile-375 の 2 project で実行する（幅で折り返しとあふれが変わる）。
 */

const AGENTS_URL = /\/api\/agents(\?.*)?$/;

/** 応答を、戻り値の関数を呼ぶまで止める。 */
async function hold(page: Page, pattern: string | RegExp) {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(pattern, async (route: Route) => {
    await gate;
    await route.fallback();
  });
  return () => release();
}

test.describe("読み込み中の表示の基準", { tag: "@responsive" }, () => {
  test("品質評価: 業務 Agent の読み込み中は評価セットの空の表示を出さず、経過時間を 1 か所だけ出す", async ({
    page,
  }, testInfo) => {
    const release = await hold(page, AGENTS_URL);
    await page.goto("/evaluation");

    const loading = page.getByTestId("evaluation-sets-loading");
    await expect(loading).toContainText("業務 Agent を読み込んでいます");
    await expect(loading.locator('[data-skeleton="field"]')).toHaveCount(3);
    await expect(loading.locator('[data-skeleton="table"]')).toBeVisible();
    await expect(page.getByText("評価セットはまだありません")).toHaveCount(0);
    expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("evaluation-agents-loading.png"), fullPage: true });

    release();
    await expect(loading).toHaveCount(0);
    await expect(page.locator("#evaluation-agent")).toBeVisible();
  });

  test("品質評価: 最近の評価の読み込み中は、比べる評価の候補を形だけにして経過時間を重ねない", async ({
    page,
  }, testInfo) => {
    const release = await hold(page, "**/api/evaluations?**");
    await page.goto("/evaluation");

    const loading = page.getByTestId("evaluations-loading");
    await expect(loading).toContainText("最近の評価を読み込んでいます");
    const compareOptions = page.getByTestId("evaluation-compare-options-loading");
    await expect(compareOptions).toHaveAttribute("aria-label", "比べる評価の候補を読み込んでいます");
    await expect(compareOptions.locator('[data-skeleton="field"]')).toHaveCount(2);
    await expect(compareOptions.getByRole("timer")).toHaveCount(0);
    await expect(page.getByRole("timer")).toHaveCount(1);
    expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("evaluation-jobs-loading.png"), fullPage: true });

    release();
    await expect(loading).toHaveCount(0);
    await expect(compareOptions).toHaveCount(0);
  });

  test("実行環境: 実行環境と保存先を 1 つの表示で読み込み、2 つのカードの形で寸法を予約する", async ({
    page,
  }, testInfo) => {
    const release = await hold(page, "**/api/runtime/**");
    await page.goto("/runtimes");

    const loading = page.getByTestId("runtimes-loading");
    await expect(loading).toContainText("実行環境を読み込んでいます");
    await expect(loading.getByTestId("builtin-runtime-card-skeleton")).toBeVisible();
    await expect(loading.getByTestId("runtime-storage-card-skeleton")).toBeVisible();
    expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("runtimes-loading.png"), fullPage: true });

    release();
    await expect(loading).toHaveCount(0);
    await expect(page.getByTestId("builtin-runtime-card")).toBeVisible();
    await expect(page.getByTestId("runtime-storage-card")).toBeVisible();
  });

  test("実行の作成: 業務 Agent と実行環境の読み込みを 1 つの表示で続けて数え、途中で文言を替えない", async ({
    page,
  }, testInfo) => {
    const releaseAgents = await hold(page, AGENTS_URL);
    const releaseRuntime = await hold(page, "**/api/runtime/status");
    await page.goto("/runs?id=new");

    const loading = page.getByTestId("query-loading");
    await expect(loading).toContainText("業務 Agent と実行環境を読み込んでいます");
    await expect(loading.locator('[data-skeleton="form"]')).toBeVisible();
    expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("run-form-loading.png"), fullPage: true });

    // 業務 Agent だけ読めても、同じ表示のまま（経過時間を 00:00 から数え直さない）。
    releaseAgents();
    await expect(page.locator("#run-agent")).toHaveCount(0);
    await expect(loading).toContainText("業務 Agent と実行環境を読み込んでいます");
    expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);

    releaseRuntime();
    await expect(loading).toHaveCount(0);
    await expect(page.locator("#run-agent")).toBeVisible();
  });

  test("業務 Agent の編集: モデルの候補の読み込み中は、欄の形の Skeleton で場所を確保する", async ({
    page,
  }, testInfo) => {
    const release = await hold(page, "**/api/runtime/status");
    await page.goto("/agents?id=default");

    const field = page.getByTestId("agent-model-loading");
    await expect(field).toHaveAttribute("aria-label", "モデルを読み込んでいます");
    await expect(field).toHaveAttribute("data-skeleton", "field");
    await field.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("agent-model-loading.png") });
    const reserved = await field.boundingBox();

    release();
    await expect(field).toHaveCount(0);
    const select = page.locator('[id$="-agent-model"]');
    await expect(select).toBeVisible();
    // 欄の幅と高さは読み込み後の欄（ラベル + 欄 + 補足）とおおむね同じ（読み込み後に下を押し下げない）。
    const loaded = await select.evaluate((node) => {
      const box = node.closest(".space-y-1\\.5")?.getBoundingClientRect();
      return box ? { width: box.width, height: box.height } : null;
    });
    expect(reserved).not.toBeNull();
    expect(loaded).not.toBeNull();
    expect(Math.abs((reserved?.height ?? 0) - (loaded?.height ?? 0))).toBeLessThanOrEqual(1);
  });

  test("アカウント: 使える業務 Agent の候補の読み込み中は、欄の形で場所を確保し、絞れない案内を出さない", async ({
    page,
  }, testInfo) => {
    const release = await hold(page, AGENTS_URL);
    await page.goto("/account");

    const field = page.getByTestId("api-key-agent-scope-loading");
    await expect(field).toHaveAttribute("aria-label", "使える業務 Agent を読み込んでいます");
    await expect(page.getByTestId("api-key-agent-scope-elsewhere")).toHaveCount(0);
    await field.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("account-agent-scope-loading.png") });
    const reserved = await field.boundingBox();

    release();
    await expect(field).toHaveCount(0);
    const loaded = await page.getByRole("radiogroup", { name: "使える業務 Agent" }).boundingBox();
    expect(Math.abs((reserved?.height ?? 0) - (loaded?.height ?? 0))).toBeLessThanOrEqual(1);
  });
});
