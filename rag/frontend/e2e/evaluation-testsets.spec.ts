import { readFile } from "node:fs/promises";
import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow } from "./_helpers";
import { mockTestSetPage } from "./_testsets";
import { chooseEvaluationProfile, mockEvaluationImport } from "./_evaluation-jobs";

// ファイル自体の六列互換と位置の復元は backend の reader/writer 往復で検証する。
// ここでは明示選択・編集・保存再開・取得したファイルのアップロードを実画面で検証する。
test("本人の評価から一人で下書きを保存し、明示確認してダウンロードと再取り込みへ進む", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const state = await mockTestSetPage(page);
  await page.goto("/evaluation?tab=calibration");
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.getByLabel("下書きの名前", { exact: true }).fill("合成テストセット");
  await page.getByTestId("testset-sources").getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "評価の項目を表示する" }).click();
  const candidates = page.getByTestId("testset-candidates");
  await expect(candidates.getByRole("checkbox")).toHaveCount(2);
  await expect(candidates.getByRole("checkbox").first()).not.toBeChecked();
  await page.getByRole("button", { name: "すべて選択", exact: true }).click();
  await page.getByRole("button", { name: "選んだ項目で下書きを保存" }).click();
  const editor = page.getByTestId("testset-editor");
  await expect(page.getByLabel("標準回答", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "この項目の収録を確認する" })).toBeDisabled();
  await page.getByText("参考：生成された回答と根拠", { exact: true }).click();
  await page.getByRole("button", { name: "標準回答に使う", exact: true }).click();
  await page.getByLabel("標準回答", { exact: true }).fill("確認した合成の標準回答");
  await page.getByRole("button", { name: "下書きを保存", exact: true }).click();
  await expect.poll(() => state.getSaved()?.items[0].standard_answer).toBe("確認した合成の標準回答");
  await page.reload();
  await page.getByRole("button", { name: "合成テストセット", exact: true }).click();
  await expect(page.getByLabel("標準回答", { exact: true })).toHaveValue("確認した合成の標準回答");
  await page.getByRole("button", { name: "この項目の収録を確認する" }).click();
  await expect.poll(() => state.getSaved()?.items[0].confirmed).toBe(true);
  expect(state.getSaved()?.items[1].confirmed).toBe(false);
  await expectNoPageOverflow(page);
  await editor.screenshot({ path: testInfo.outputPath("testset-editor.png"), animations: "disabled" });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "テストデータをダウンロード", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("evaluation_testset.xlsx");
  const path = await download.path();
  expect(path).toBeTruthy();
  const bytes = await readFile(path!);
  await page.getByRole("tab", { name: "評価の実行", exact: true }).click();
  await mockEvaluationImport(page);
  await chooseEvaluationProfile(page);
  await page.getByTestId("evaluation-request-file").setInputFiles({ name: download.suggestedFilename(), mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: bytes });
  await expect(page.getByTestId("evaluation-import-ready")).toBeVisible();
});

test("編集後は確認が解除され、保存前の離脱に確認が出る", { tag: "@responsive" }, async ({ page }) => {
  await mockTestSetPage(page);
  await page.goto("/evaluation?tab=calibration");
  await page.getByRole("button", { name: "新しい下書きを作る" }).click();
  await page.getByLabel("下書きの名前", { exact: true }).fill("未保存の合成下書き");
  await page.getByRole("button", { name: "キャンセル", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
});
