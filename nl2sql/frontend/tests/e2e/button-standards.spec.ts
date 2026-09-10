import { expect, test } from "@playwright/test";
import { expectCompactSortHeaders } from "./_helpers/sort-header";

for (const theme of ["light", "dark"]) {
  test(`${theme}: 共通ボタンの実寸・状態・配置・キーボード操作`, async ({ page }, testInfo) => {
    await page.route("**/__button-standards", route => route.fulfill({ contentType: "text/html", body:
      `<html class="${theme === "dark" ? "dark" : ""}" lang="ja"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module">import RefreshRuntime from "/@react-refresh"; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script><script type="module" src="/tests/fixtures/button-standards.tsx"></script></body></html>` }));
    await page.goto("/__button-standards");
    const mobile = testInfo.project.name === "mobile-375";
    for (const [size, height] of [["sm", 32], ["md", 36], ["lg", 40]] as const) {
      for (const variant of ["primary", "secondary", "ghost", "danger", "disabled", "loading"]) {
        const button = page.getByTestId(`${size}-${variant}`);
        await expect(button).toBeVisible();
        const style = await button.evaluate(el => {
          const s = getComputedStyle(el);
          return { height: el.getBoundingClientRect().height, radius: s.borderRadius, border: s.borderTopWidth,
            font: s.fontSize, clipped: el.scrollWidth > el.clientWidth, color: s.color, bg: s.backgroundColor };
        });
        expect(style.height).toBe(mobile ? 44 : height);
        expect(style.radius).toBe("6px");
        expect(style.border).toBe("1px");
        expect(style.font).toBe("14px");
        expect(style.clipped).toBe(false);
        if (["disabled", "loading"].includes(variant)) await expect(button).toBeDisabled();
        if (variant === "loading") {
          await expect(button).toHaveAttribute("aria-busy", "true");
          await expect(button.locator("svg:visible")).toHaveCount(1);
        }
      }
    }
    const sortTable = page.getByTestId("sort-header-table");
    await expectCompactSortHeaders(sortTable);
    const sortButton = sortTable.getByRole("button", { name: "名称" });
    await sortButton.focus();
    await expect(sortButton).toBeFocused();
    await sortButton.press("Enter");
    await expect(sortTable.getByRole("columnheader", { name: "名称" })).toHaveAttribute("aria-sort", "descending");
    await expect(sortTable.locator("tbody tr").first()).toContainText("B");
    await sortTable.locator("thead").screenshot({ path: testInfo.outputPath(`column-font-${theme}.png`) });

    // 実際に合成された色を sRGB に変換して通常文字の 4.5:1 を検証する。
    const contrasts = await page.locator(".nl2sql-button:not(:disabled)").evaluateAll(buttons => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const ctx = canvas.getContext("2d")!;
      function luminance(color: string, background: string) {
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = background;
        ctx.fillRect(0, 0, 1, 1);
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, 1, 1);
        const rgb = Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3).map(value => {
          const c = value / 255;
          return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
        });
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      }
      return buttons.map(button => {
        const style = getComputedStyle(button);
        const surface = style.getPropertyValue("--card");
        const fg = luminance(style.color, surface);
        const bg = luminance(style.backgroundColor, surface);
        return { label: button.textContent, ratio: (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05) };
      });
    });
    for (const contrast of contrasts) expect(contrast.ratio, contrast.label ?? "icon").toBeGreaterThanOrEqual(4.5);
    await expect(page.getByRole("link", { name: "結果へ移動" })).toHaveCSS("height", mobile ? "44px" : "32px");
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.getByTestId("sm-loading").locator("svg:visible")).toHaveCSS("animation-name", "none");
    const primary = page.getByTestId("sm-primary");
    if (!mobile) {
      const initial = await primary.evaluate(el => getComputedStyle(el).backgroundColor);
      await primary.hover();
      const hover = await primary.evaluate(el => getComputedStyle(el).backgroundColor);
      expect(hover).not.toBe(initial);
      await page.mouse.down();
      const active = await primary.evaluate(el => getComputedStyle(el).backgroundColor);
      expect(active).not.toBe(hover);
      await page.mouse.move(0, 0);
      await page.mouse.up();
    }
    const icon = page.getByTestId("icon");
    const rect = await icon.boundingBox();
    expect(rect?.height).toBe(mobile ? 44 : 36);
    expect(rect?.width).toBe(rect?.height);
    await expect(page.getByTestId("field")).toHaveCSS("height", "44px");
    await page.getByTestId("sm-primary").focus();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("sm-secondary")).toBeFocused();
    await expect(page.getByTestId("sm-secondary")).toHaveCSS("outline-style", "solid");
    await page.keyboard.press("Enter");
    await expect(page.locator("output")).toHaveText("操作回数: 1");
    await page.getByRole("button", { name: "選択状態", exact: true }).click();
    await expect(page.getByRole("button", { name: "選択状態", exact: true })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "次へ", exact: true }).click();
    await expect(page.getByRole("navigation")).toContainText("2 / 3");
    await page.getByRole("button", { name: "再試行", exact: true }).click();
    await expect(page.locator("output")).toHaveText("操作回数: 2");
    await page.getByTestId("danger-trigger").click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "削除する" })).toHaveClass(/nl2sql-button--danger/);
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    const overflow = page.getByRole("button", { name: "対象の操作", exact: true });
    await overflow.click();
    await expect(page.getByRole("menuitem", { name: "削除", exact: true })).toHaveClass(/nl2sql-button--danger-tone/);
    await page.keyboard.press("Escape");
    await expect(overflow).toBeFocused();
    const form = page.getByTestId("form-actions");
    for (const action of await form.locator("button, a").all()) {
      await expect(action).toHaveCSS("height", mobile ? "44px" : "40px");
      expect(await action.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(false);
      if (mobile) expect((await action.boundingBox())?.width).toBe((await form.boundingBox())?.width);
    }
    await form.getByRole("button", { name: "その他の操作" }).click();
    const formDelete = page.getByRole("menuitem", { name: "保存済みの内容を削除" });
    await expect(formDelete).toHaveCSS("height", mobile ? "44px" : "32px");
    expect(await formDelete.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(false);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "保存通知", exact: true }).click();
    const notifications = page.getByRole("region", { name: "通知", exact: true });
    await expect(notifications).toContainText("保存しました");
    await expect(notifications.getByRole("button", { name: "閉じる" })).toHaveCSS("height", "44px");
    await notifications.getByRole("button", { name: "閉じる" }).click();
    await expect(notifications.getByRole("button")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`buttons-${theme}.png`), fullPage: true });
    await testInfo.attach(`buttons-${theme}`, { path: testInfo.outputPath(`buttons-${theme}.png`), contentType: "image/png" });
  });
}
