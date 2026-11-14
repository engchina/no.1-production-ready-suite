import { expect, test } from "./fixtures/mock-api";

// 安全チェックは 3 製品で共通の設定（platform の共通画面。#5062）。
test("安全チェックの入力と出力の検査を独立に保存する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await page.goto("/settings/guardrail");
  await expect(page.getByRole("heading", { name: "安全チェック", level: 1 })).toBeVisible();
  const input = page.getByRole("checkbox", { name: "入力を検査する", exact: true });
  const output = page.getByRole("checkbox", { name: "出力を検査する", exact: true });
  await expect(input).not.toBeChecked();
  await expect(output).not.toBeChecked();
  await output.check();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
  await page.reload();
  await expect(input).not.toBeChecked();
  await expect(output).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("guardrail-settings.png"), fullPage: true });
});
