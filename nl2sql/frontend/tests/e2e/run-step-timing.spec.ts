import { expect, test } from "./_helpers/test";
import { FIXTURE_BASE_URL } from "./_helpers/web-server";

test.use({baseURL: FIXTURE_BASE_URL});
for (const theme of ["light", "dark"] as const) {
  test(`経過と軌跡が同じ実測座標と所要時間を使う (${theme})`, {tag: "@responsive"}, async ({page}, testInfo) => {
    const frozenAt = new Date("2026-01-01T00:00:01Z");
    await page.clock.install({time: frozenAt});
    await page.clock.pauseAt(frozenAt);
    await page.route("**/__run-step-timing", route => route.fulfill({contentType: "text/html", body: `<html data-theme="${theme}" lang="ja"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module">import RefreshRuntime from "/@react-refresh"; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;</script><script type="module" src="/tests/fixtures/run-step-timing.tsx"></script></body></html>`}));
    await page.goto("/__run-step-timing");
    const before = await page.locator("[data-run-timeline-step]").evaluateAll(rows => rows.map(row => ({
      id: row.getAttribute("data-run-timeline-step"), label: row.querySelector("[data-run-timeline-label]")?.textContent,
      timing: row.querySelector("[data-run-step-status-duration]")?.textContent,
      bar: row.querySelector("[data-run-step-bar]")?.getAttribute("style"),
      width: row.querySelector("[data-run-step-bar]")?.getAttribute("data-measured-width"),
    })));
    expect(before).toHaveLength(4);
    expect(before.find(row => row.id === "duration-only")).toMatchObject({timing: "完了 · 3.0 秒", bar: undefined});
    expect(before.find(row => row.id === "zero")?.width).toBe("0");
    await page.screenshot({path: testInfo.outputPath(`timing-process-${theme}.png`), fullPage: true});
    await page.getByRole("button", {name: "軌跡を調べる", exact: true}).click();
    const after = await page.locator("[data-trace-record]").evaluateAll(rows => rows.map(row => ({
      id: row.getAttribute("data-trace-record"), label: row.querySelector("[data-trace-record-label]")?.textContent,
      timing: row.querySelector("[data-trace-step-status-duration]")?.textContent,
      bar: row.querySelector("[data-run-step-bar]")?.getAttribute("style"),
      width: row.querySelector("[data-run-step-bar]")?.getAttribute("data-measured-width"),
    })));
    expect(after).toEqual(before);
    await expect(page.getByText("4 / 4 件を表示", {exact: true})).toBeVisible();
    await page.screenshot({path: testInfo.outputPath(`timing-trace-${theme}.png`), fullPage: true});
  });
}
