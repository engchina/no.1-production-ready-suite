import { expect, test } from "@playwright/test";

for (const theme of ["light", "dark"]) {
  for (const width of [1280, 1920, 375]) {
    test(`${theme} ${width}px: 更新操作の枠・状態・キーボードを維持する`, async ({ page, isMobile }, testInfo) => {
      test.skip(isMobile !== (width === 375), "viewport に対応する project で検証する");
      await page.setViewportSize({ width, height: 900 });
      await page.route("**/__header-utilities", route => route.fulfill({ contentType: "text/html", body:
        `<html data-theme="${theme}" lang="ja"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module">import RefreshRuntime from "/@react-refresh"; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script><script type="module" src="/tests/fixtures/header-utilities.tsx"></script></body></html>` }));
      await page.goto("/__header-utilities");
      const header = page.locator("header").first();
      const standalone = page.getByRole("button", { name: "最新情報を取得" });
      const reference = page.getByRole("button", { name: "更新を完了" });
      for (const property of ["border-top-color", "background-color", "border-radius"]) {
        await expect(standalone).toHaveCSS(property, await reference.evaluate((el, prop) => getComputedStyle(el).getPropertyValue(prop), property));
      }
      await expect(standalone).toHaveCSS("border-top-width", "1px");
      await expect(standalone).not.toHaveCSS("border-top-color", "rgba(0, 0, 0, 0)");
      await page.keyboard.press("Tab");
      await expect(page.getByRole("link", { name: "本文へスキップ" })).toBeFocused();
      if (isMobile) {
        await header.getByRole("button", { name: "その他の操作" }).click();
        await expect(page.getByRole("menuitem").first()).toBeFocused();
        await page.keyboard.press("End");
        await expect(page.getByRole("menuitem", { name: "DB 構造を再取得" })).toBeFocused();
        await page.keyboard.press("Home");
        await page.keyboard.press("ArrowDown");
      } else {
        const importButton = header.getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" });
        for (const label of ["表示を更新", "DB 構造を再取得"]) {
          const utility = header.getByRole("button", { name: label, exact: true });
          for (const property of ["border-top-color", "background-color", "height", "border-radius"]) {
            await expect(utility).toHaveCSS(property, await importButton.evaluate((el, prop) => getComputedStyle(el).getPropertyValue(prop), property));
          }
        }
        await expect(header.getByRole("group").getByRole("button")).toHaveText([
          "表示を更新", "DB 構造を再取得", "Excel/CSV 取込(新規テーブル)", "テーブル作成",
        ]);
        await header.getByRole("button", { name: "表示を更新", exact: true }).focus();
      }
      const refresh = page.getByRole(isMobile ? "menuitem" : "button", { name: "表示を更新", exact: true });
      await expect(refresh).toBeFocused();
      await expect(refresh).toHaveCSS("outline-style", "solid");
      await page.keyboard.press("Enter");
      await expect(page.locator("output")).toHaveText("更新回数: 1");
      if (isMobile) await header.getByRole("button", { name: "その他の操作" }).click();
      await expect(refresh).toBeDisabled();
      await expect(refresh).toHaveAttribute("aria-busy", "true");
      await expect(refresh.locator("svg:visible")).toHaveCount(1);
      await expect(page.getByRole(isMobile ? "menuitem" : "button", { name: "DB 構造を再取得" })).toBeDisabled();
      if (isMobile) await page.keyboard.press("Escape");
      await reference.click();
      const tabs = header.getByRole("tab");
      await tabs.first().focus();
      await page.keyboard.press("ArrowRight");
      await expect(tabs.last()).toBeFocused();
      await page.keyboard.press("Home");
      await expect(tabs.first()).toBeFocused();
      await page.keyboard.press("End");
      await expect(tabs.last()).toBeFocused();
      const left = await page.getByRole("heading", { name: "テーブルの管理" }).evaluate(el => el.getBoundingClientRect().left);
      const bodyLeft = await page.getByTestId("header-body").evaluate(el => el.getBoundingClientRect().left + parseFloat(getComputedStyle(el).paddingLeft));
      expect(left).toBe(bodyLeft);
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      await page.screenshot({ path: testInfo.outputPath(`header-${theme}-${width}.png`), fullPage: true });
    });
  }
}
