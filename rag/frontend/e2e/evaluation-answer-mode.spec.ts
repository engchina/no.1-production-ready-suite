/**
 * 品質評価の回答の方法（#5340）。合成の評価で、回答のモデルの真上のラジオ（既定はチャット・各選択肢の説明）、
 * 投入の body・作業状態・実行中の無効・job の表示と、desktop / 375px の並びを確かめる。
 */
import { expect, type Page, test } from "./fixtures/test";

import {
  evaluationImportResult,
  mockEvaluationJobList,
  mockEvaluationJobs,
  prepareEvaluationRun,
} from "./_evaluation-jobs";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

function metrics() {
  return {
    case_count: 2,
    error_count: 0,
    evaluation_suite: "standard",
    passed: true,
    required_evidence_recall: 1,
    mrr: 1,
    answer_correctness: 1,
    metric_case_counts: { required_evidence_recall: 2, mrr: 2, answer_correctness: 2 },
    threshold_failures: [],
    failure_reason_counts: {},
    category_breakdown: {},
    case_results: evaluationImportResult().cases.map((item, index) => ({
      case_id: item.id,
      trace_id: `trace-${index}`,
      status: "success",
      retrieved_document_ids: [],
      relevant_document_ids: [],
      hit_document_ids: [],
      guardrail_warnings: [],
      missing_evidence: [],
      failure_reasons: [],
      diagnostics: {},
      elapsed_ms: 1,
      error_type: null,
      error_message: null,
    })),
  };
}

async function mockScreen(page: Page) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockEvaluationJobList(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: envelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
  await page.route("**/api/settings/evaluation-suite", (route) =>
    route.fulfill({
      json: envelope({
        suite: "standard",
        thresholds: { answer_correctness: 0.9 },
        suites: [{ name: "standard", thresholds: { answer_correctness: 0.9 }, origin: "x", recommended_for: [], selected: true }],
        config_source: "runtime",
      }),
    })
  );
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(
    `回答の方法は回答のモデルの真上で選び、投入・作業状態・実行中の無効・job の表示に反映する (${viewport.name})`,
    { tag: "@responsive" },
    async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await mockScreen(page);
      const jobs = await mockEvaluationJobs(page, { runResult: () => metrics() });
      await page.goto("/evaluation");
      await prepareEvaluationRun(page);

      const group = page.getByRole("radiogroup", { name: "回答の方法" });
      const chat = group.getByRole("radio", { name: "チャット" });
      const search = group.getByRole("radio", { name: "RAG 検索" });
      // 既定はチャット。各選択肢に 1 行の説明（ラジオの説明として読み上げる）。
      await expect(chat).toBeChecked();
      await expect(chat).toHaveAccessibleDescription(/業務ガイド・承認済み FAQ・根拠の拡張/);
      await expect(search).toHaveAccessibleDescription(/拡張を使わず/);

      // 回答のモデルの真上（間にほかの欄を挟まない）。
      const groupBox = await group.boundingBox();
      const modelBox = await page.locator("#evaluation-answer-model").boundingBox();
      expect(groupBox && modelBox && groupBox.y + groupBox.height <= modelBox.y).toBe(true);
      expect(
        await group.evaluate((element) => {
          const next = element.nextElementSibling;
          return next?.contains(document.getElementById("evaluation-answer-model")) ?? false;
        })
      ).toBe(true);

      // 行はタッチの最小（44px）以上。desktop は横に、375px は縦に並ぶ。
      const rows = page.getByTestId("evaluation-answer-mode").locator("label");
      const [first, second] = [await rows.nth(0).boundingBox(), await rows.nth(1).boundingBox()];
      expect(first && second).toBeTruthy();
      expect(first!.height).toBeGreaterThanOrEqual(44);
      expect(second!.height).toBeGreaterThanOrEqual(44);
      if (viewport.name === "desktop") {
        expect(Math.abs(first!.y - second!.y)).toBeLessThan(1);
      } else {
        expect(second!.y).toBeGreaterThanOrEqual(first!.y + first!.height);
      }
      await expectNoPageOverflow(page);
      await group.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`answer-mode-conditions-${viewport.name}.png`) });

      // 行のどこを押しても選べ、再読込しても作業状態に残る。
      await rows.nth(1).click();
      await expect(search).toBeChecked();
      await page.reload();
      await expect(page.getByRole("radiogroup", { name: "回答の方法" }).getByRole("radio", { name: "RAG 検索" })).toBeChecked();
      await expect(page.getByTestId("evaluation-import-ready")).toBeVisible();

      await page.getByRole("button", { name: "評価実行" }).click();
      await expect.poll(() => jobs.runPayloads.length).toBe(1);
      expect(jobs.runPayloads[0]).toMatchObject({ answer_mode: "search" });
      // 実行中は変えられない（選んだ内容は読める）。
      const running = page.getByRole("radiogroup", { name: "回答の方法" });
      await expect(running.getByRole("radio", { name: "チャット" })).toBeDisabled();
      await expect(running.getByRole("radio", { name: "RAG 検索" })).toBeDisabled();
      await expect(running.getByRole("radio", { name: "RAG 検索" })).toBeChecked();
      const panel = page.getByTestId("evaluation-run-job");
      await expect(panel).toContainText("回答の方法: RAG 検索");

      jobs.complete();
      await expect(panel.locator("[data-status-variant]")).toHaveText("完了");
      await expect(running.getByRole("radio", { name: "チャット" })).toBeEnabled();
      // 過去の評価の一覧にも方法を出す（375px は日時の下にまとめる）。
      await expect(page.getByTestId("evaluation-history-row-jobrun1")).toContainText("RAG 検索");
      await expectNoPageOverflow(page);
      await panel.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`answer-mode-result-${viewport.name}.png`) });
    }
  );
}

test("キーボードの矢印で回答の方法を切り替えられる", async ({ page }) => {
  await mockScreen(page);
  await mockEvaluationJobs(page, { runResult: () => metrics() });
  await page.goto("/evaluation");
  await prepareEvaluationRun(page);
  const group = page.getByRole("radiogroup", { name: "回答の方法" });
  await group.getByRole("radio", { name: "チャット" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(group.getByRole("radio", { name: "RAG 検索" })).toBeChecked();
  await expect(group.getByRole("radio", { name: "RAG 検索" })).toBeFocused();
});
