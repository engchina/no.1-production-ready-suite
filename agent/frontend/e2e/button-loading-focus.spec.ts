import { expect, test } from "./fixtures/mock-api";

// #355: 共有 Button は loading 中もフォーカスを保ち（ネイティブの disabled ではなく aria-disabled）、
// Enter / Space の連打で二重に保存しない。Agent の代表として Runtime Safety の保存で確かめる。
for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
]) {
  test(`${viewport.name}: 保存は loading 中もフォーカスを保ち、完了後もボタンに残る`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    let patches = 0;
    let release: () => void = () => {};
    const released = new Promise<void>((resolve) => {
      release = resolve;
    });
    // mock-api より後に登録した route が先に呼ばれる。PATCH を止めてから mock-api に渡す。
    await page.route("**/api/settings/runtime-safety", async (route) => {
      if (route.request().method() === "PATCH") {
        patches += 1;
        await released;
      }
      await route.fallback();
    });

    await page.goto("/settings/runtime-safety");
    await expect(page.getByRole("heading", { name: "Runtime Safety", level: 1 })).toBeVisible();
    await page.getByLabel("Run あたり最大ツール呼び出し").fill("20");

    const save = page.getByRole("button", { name: "保存" });
    await save.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => patches).toBe(1);
    await expect(save).toHaveAttribute("aria-busy", "true");
    await expect(save).toHaveAttribute("aria-disabled", "true");
    await expect(save).toBeFocused();
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");
    await expect(save).toBeFocused();

    release();
    await expect(page.getByText("設定を保存しました")).toBeVisible();
    await expect(save).not.toHaveAttribute("aria-busy", /.*/);
    await expect(save).toBeFocused();
    expect(patches).toBe(1);
  });
}
