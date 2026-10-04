import { expect, test, type Locator, type Page } from "./_helpers/test";

import { expectToastStackAtTop } from "./_helpers/toast";

// #411: 通知は主操作を覆わない。以前は画面の右下に出ていたため、ページの末尾（スクロールしきると画面の下端）の
// 「SQL 生成」を覆い、ポインタが通知に乗ると自動の消去が止まって（#351）押せなくなっていた。
// 置き場所は上端の見出しの面（desktop は PageHeader に重ねてページの操作のすぐ左、375px は上端のバー）。一時停止はそのまま残す。
async function openFixture(page: Page, theme: "light" | "dark") {
  await page.route("**/__toast-placement", (route) => route.fulfill({ contentType: "text/html", body:
    `<html class="${theme === "dark" ? "dark" : ""}" lang="ja"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module">import RefreshRuntime from "/@react-refresh"; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script><script type="module" src="/tests/fixtures/toast-placement.tsx"></script></body></html>` }));
  // HMR timestamp 付きの自己 import で fixture が二重に実行されないようにする（toast-pause.spec.ts と同じ）。
  await page.route(/\/tests\/fixtures\/toast-placement\.tsx\?t=\d+/, (route) => route.fulfill({
    contentType: "text/javascript", body: 'export * from "/tests/fixtures/toast-placement.tsx";' }));
  // 時刻を止め、runFor で進めた分だけ経過させる（通知が操作の途中で消えて、覆っていないように見えることを防ぐ）。
  await page.clock.install({ time: new Date("2026-09-28T09:00:00+09:00") });
  await page.clock.pauseAt(new Date("2026-09-28T09:00:01+09:00"));
  await page.goto("/__toast-placement");
}

/** 要素の中心を押すと、その要素に届く（通知などに覆われていない）。 */
async function expectHitsItself(target: Locator) {
  await expect(target).toBeInViewport();
  const hit = await target.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return top !== null && element.contains(top);
  });
  expect(hit).toBe(true);
}

function overlaps(a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

for (const theme of ["light", "dark"] as const) {
  test(`${theme}: 通知を出したまま、ページの操作と末尾の主操作を押せる（一時停止は維持）`, async ({ page }, testInfo) => {
    const mobile = testInfo.project.name === "mobile-375";
    await openFixture(page, theme);
    const region = page.getByRole("region", { name: "通知", exact: true });
    const counts = page.getByTestId("counts");
    const pageActions = page.getByRole("group", { name: "ページ操作" });
    const save = pageActions.getByRole("button", { name: "保存", exact: true });

    // ページの操作（PageHeader の右端）: 押した直後に出た通知が、押したボタンと操作の並びを覆わない。
    // 375px では通知が上端のバーから下へ積まれるため、スクロールしていない PageHeader の操作に届かない 2 件で確かめる
    // （3 件目は PageHeader の操作の高さに届く。UX 契約 messaging.md §3.1）。
    const stacked = mobile ? 2 : 3;
    for (let index = 0; index < stacked; index += 1) {
      await expectHitsItself(save);
      await save.click({ timeout: 3_000 });
    }
    await expect(counts).toContainText(`保存 ${stacked} 回`);
    await expect(region.getByRole("status")).toHaveCount(stacked);
    const regionBox = (await region.boundingBox())!;
    expect(overlaps(regionBox, (await pageActions.boundingBox())!)).toBe(false);
    if (mobile) {
      // 上端のバーに重ねるが、メニューのボタンは覆わない。
      await expectHitsItself(page.getByTestId("nav-drawer-trigger"));
    }
    // 本文の先頭の右の操作（ContentActionBar / ObjectActionBar の位置）も覆わない。
    const copy = page.getByRole("group", { name: "構造情報の操作" }).getByRole("button", { name: "コピー" });
    await expectHitsItself(copy);
    await copy.click({ timeout: 3_000 });
    await expect(counts).toContainText("コピー 1 回");
    await page.screenshot({ path: testInfo.outputPath(`toast-placement-header-${theme}.png`) });

    // ページの末尾の主操作: スクロールしきると画面の下端に来る（以前の通知の位置）。
    await page.locator("main").evaluate((main) => {
      main.scrollTop = main.scrollHeight;
    });
    const generate = page.getByRole("group", { name: "SQL 生成の操作" }).getByRole("button", { name: "SQL 生成" });
    const viewport = page.viewportSize()!;
    const generateBox = (await generate.boundingBox())!;
    expect(viewport.height - (generateBox.y + generateBox.height)).toBeLessThan(120);

    // 通知にポインタを乗せると止まる（desktop）。止めたままでも、覆われていない主操作を押せる。
    const first = region.getByRole("status").first();
    if (!mobile) {
      await first.hover();
      await page.clock.runFor(10_000);
      await expect(region.getByRole("status")).toHaveCount(stacked);
    }
    await expectHitsItself(generate);
    await generate.click({ timeout: 3_000 });
    await expect(counts).toContainText("生成 1 回");
    await expect(region.getByRole("status")).toHaveCount(stacked + 1);
    await expectToastStackAtTop(page);
    await page.screenshot({ path: testInfo.outputPath(`toast-placement-end-${theme}.png`) });

    // 離れたら残り時間から再開し、4 秒で消える。
    await page.mouse.move(0, 0);
    await page.clock.runFor(4_100);
    await expect(region.getByRole("status")).toHaveCount(0);

    if (!mobile) {
      // 1920px でも PageHeader に重ね、ページの操作を覆わない。
      await page.setViewportSize({ width: 1920, height: 1080 });
      await page.locator("main").evaluate((main) => {
        main.scrollTop = 0;
      });
      await save.click();
      await expect(region.getByRole("status")).toHaveCount(1);
      await expectToastStackAtTop(page);
      expect(overlaps((await region.boundingBox())!, (await pageActions.boundingBox())!)).toBe(false);
      await page.screenshot({ path: testInfo.outputPath(`toast-placement-1920-${theme}.png`) });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  });
}
