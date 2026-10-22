import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth } from "./_helpers";

test.beforeEach(async ({ page }) => { await mockLocalAuth(page); });

const envelope = (enabled = false, configured = true) => ({
  data: { enabled, oci_configured: configured, oci_warning_code: configured ? null : "oci_guardrails_compartment_missing", config_source: "runtime" },
  error_messages: [], warning_messages: [],
});

for (const width of [1280, 375]) {
  test(`OCI Guardrails は説明付きの二択カードを表示する (${width}px)`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/settings/guardrail", (route) => route.fulfill({ json: envelope() }));
    await page.goto("/settings/guardrail");
    const toggle = page.getByRole("radio", { name: /^検査する/ });
    await expect(toggle).not.toBeChecked();
    const off = page.getByRole("radio", { name: /^検査しない/ });
    await expect(off).toBeChecked();
    await expect(off).toHaveAccessibleName(/既定/);
    const offBox = await off.boundingBox();
    const onBox = await toggle.boundingBox();
    expect(offBox).not.toBeNull();
    expect(onBox).not.toBeNull();
    if (width < 768) expect(onBox!.y).toBeGreaterThan(offBox!.y);
    else expect(onBox!.y).toBe(offBox!.y);
    await expect(page.getByRole("switch")).toHaveCount(0);
    await expect(page.getByRole("radio")).toHaveCount(2);
    const main = page.getByRole("main");
    for (const removed of ["Local", "根拠チェック方針", "根拠一致数", "根拠一致率", "読み取り専用"])
      await expect(main).not.toContainText(removed);
    await expect(main).toContainText("無効なときは検査しません");
    await expect(main).toContainText("検査を完了できないとき");
    await expect(page.getByRole("link", { name: /公式ドキュメント/ })).toHaveAttribute("href", "https://docs.oracle.com/en-us/iaas/Content/generative-ai/guardrails.htm");
    await expectNoPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("oci-guardrails-choice-cards.png"), fullPage: true });
  });

  test(`OCI Guardrails の on/off を保存できる (${width}px)`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let enabled = false;
    const bodies: unknown[] = [];
    await page.route("**/api/settings/guardrail", async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON(); bodies.push(body); enabled = body.enabled;
      }
      await route.fulfill({ json: envelope(enabled) });
    });
    await page.goto("/settings/guardrail");
    const toggle = page.getByRole("radio", { name: /^検査する/ });
    const save = page.getByRole("button", { name: "保存", exact: true });
    for (const value of [true, false]) {
      await page.getByRole("radio", { name: value ? /^検査する/ : /^検査しない/ }).check();
      await expect(toggle).toBeChecked({ checked: value });
      await save.click();
      await expect(save).toBeDisabled();
    }
    expect(bodies).toEqual([{ enabled: true }, { enabled: false }]);
    await expectNoPageOverflow(page);
  });

  test(`保存失敗は選択を保持し、変更を破棄すると元に戻る (${width}px)`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/settings/guardrail", (route) => route.fulfill(route.request().method() === "PATCH"
      ? { status: 500, json: { data: null, error_messages: ["保存できませんでした"], warning_messages: [] } }
      : { json: envelope() }));
    await page.goto("/settings/guardrail");
    const toggle = page.getByRole("radio", { name: /^検査する/ });
    await toggle.click(); await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("保存できませんでした", { exact: true })).toBeVisible();
    await expect(toggle).toBeChecked();
    await page.getByRole("button", { name: "変更を破棄" }).click();
    await expect(toggle).not.toBeChecked();
    await expect(page.getByText("保存できませんでした", { exact: true })).toHaveCount(0);
  });
}

test("未設定の案内は on を選んだときだけ表示する", async ({ page }) => {
  await page.route("**/api/settings/guardrail", (route) => route.fulfill({ json: envelope(false, false) }));
  await page.goto("/settings/guardrail");
  const link = page.getByRole("link", { name: "OCI 認証設定を開く" });
  await expect(link).toHaveCount(0);
  await page.getByRole("radio", { name: /^検査する/ }).check();
  await expect(link).toBeVisible();
  await expectNoPageOverflow(page);
});

test("保存済みの有効設定を保持し、キーボードで選択して破棄できる", async ({ page }) => {
  await page.route("**/api/settings/guardrail", (route) => route.fulfill({ json: envelope(true) }));
  await page.goto("/settings/guardrail");
  const on = page.getByRole("radio", { name: /^検査する/ });
  const off = page.getByRole("radio", { name: /^検査しない/ });
  await expect(on).toBeChecked();
  await on.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(off).toBeFocused();
  await expect(off).toBeChecked();
  await page.getByRole("button", { name: "変更を破棄" }).click();
  await expect(on).toBeChecked();
});
