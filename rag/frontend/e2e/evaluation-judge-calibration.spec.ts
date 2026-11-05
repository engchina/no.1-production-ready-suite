import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow } from "./_helpers";
import { mockJudgeCalibration } from "./_judge-calibration";
import { mockTestSetPage } from "./_testsets";

/** 旧校正データは削除・自動正解化せず、閲覧と保管用出力だけ残す（#4396）。 */
test("以前の校正データは読み取り専用で確認でき、未確定も保管用に書き出せる", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockTestSetPage(page);
  await mockJudgeCalibration(page, { withSet: true });
  let writes = 0;
  page.on("request", request => {
    if (request.url().includes("/api/evaluation/calibration/") && request.method() !== "GET") writes++;
  });
  await page.route("**/api/evaluation/calibration/label-sets/*/archive", route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ "形式": "以前の校正データ", "名前": "合成の旧集合", "項目": [] }),
    headers: { "content-disposition": 'attachment; filename="previous_calibration.json"' },
  }));
  await page.goto("/evaluation?tab=calibration");
  await expect(page.getByRole("button", { name: "記入の集合を作る", exact: true })).toHaveCount(0);
  await page.getByText("以前の校正データ", { exact: true }).click();
  await page.getByRole("button", { name: "校正の集合 A", exact: true }).click();
  const detail = page.getByTestId("previous-calibration-readonly");
  await expect(detail).toBeVisible();
  await expect(detail.getByRole("textbox")).toHaveCount(0);
  await expect(detail.getByRole("button", { name: /保存|裁定|校正を実行/ })).toHaveCount(0);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("previous-calibration.png"), animations: "disabled" });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "以前の校正データを書き出す" }).click();
  expect((await download).suggestedFilename()).toBe("previous_calibration.json");
  expect(writes).toBe(0);
});
