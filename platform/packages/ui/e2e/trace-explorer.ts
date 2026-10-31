/** 3 製品の実画面で、共有の軌跡の調査を確かめる。 */
// 製品ごとに独立した Playwright を使うため、実行環境と型は呼び出し元から渡す。
interface ExplorerLocator {
  getByRole(role: "button" | "heading" | "searchbox" | "tab" | "combobox" | "option", options: {name: string; exact?: boolean}): ExplorerLocator;
  locator(selector: string): ExplorerLocator;
  first(): ExplorerLocator;
  last(): ExplorerLocator;
  click(): Promise<void>;
  fill(value: string): Promise<void>;
  focus(): Promise<void>;
  press(key: string): Promise<void>;
  screenshot(options: {path: string}): Promise<unknown>;
  isVisible(): Promise<boolean>;
}
interface ExplorerPage extends Pick<ExplorerLocator, "getByRole" | "locator"> {
  screenshot(options: {path: string; fullPage: boolean}): Promise<unknown>;
  viewportSize(): {width: number; height: number} | null;
  setViewportSize(size: {width: number; height: number}): Promise<void>;
  emulateMedia(options: {reducedMotion: "reduce" | "no-preference"}): Promise<void>;
  evaluate<R, A>(fn: (arg: A) => R | Promise<R>, arg: A): Promise<R>;
}
interface ExplorerAssertions {
  toBeVisible(): Promise<void>;
  toBeFocused(): Promise<void>;
  toContainText(value: string): Promise<void>;
  toHaveValue(value: string): Promise<void>;
  toHaveCount(value: number): Promise<void>;
  toHaveAttribute(name: string, value: string): Promise<void>;
  toBe(value: boolean): void;
}

export async function confirmTraceExplorer(page: ExplorerPage, testInfo: {outputPath(...paths: string[]): string}, expect: (value: unknown) => unknown, probeLayout = false) {
  const check = (value: unknown) => expect(value) as ExplorerAssertions;
  const normalStyle = await page.evaluate(() => {
    const label = document.querySelector("[data-run-timeline-label]");
    const bar = document.querySelector("[data-run-timeline-bar]");
    return { font: label ? getComputedStyle(label).fontSize : null, height: bar ? getComputedStyle(bar).height : null, radius: bar ? getComputedStyle(bar).borderRadius : null };
  }, undefined);
  await page.getByRole("button", {name: "軌跡を調べる", exact: true}).first().click();
  const explorer = page.locator("[data-trace-explorer]");
  await check(explorer).toBeVisible();
  check(await page.evaluate(expected => {
    const labels = Array.from(document.querySelectorAll("[data-trace-record-label]"));
    const bars = Array.from(document.querySelectorAll("[data-trace-time-bar]"));
    return labels.length > 0 && bars.length > 0 && labels.every(label => !expected.font || getComputedStyle(label).fontSize === expected.font) && bars.every(bar => (!expected.height || getComputedStyle(bar).height === expected.height) && (!expected.radius || getComputedStyle(bar).borderRadius === expected.radius));
  }, normalStyle)).toBe(true);
  await check(explorer.getByRole("heading", {name: "軌跡の調査"})).toBeFocused();
  const search = explorer.getByRole("searchbox", {name: "呼び出しを検索"});
  await explorer.getByRole("combobox", {name: "種類"}).click();
  await page.getByRole("option", {name: "モデル", exact: true}).click();
  await check(explorer.locator('[data-trace-record]:not([data-trace-category="model"])')).toHaveCount(0);
  await explorer.getByRole("button", {name: "条件をクリア", exact: true}).last().click();
  await explorer.getByRole("combobox", {name: "状態"}).click();
  await page.getByRole("option", {name: "失敗", exact: true}).click();
  await check(explorer).toContainText("条件に一致する呼び出しがありません");
  await explorer.getByRole("button", {name: "条件をクリア", exact: true}).last().click();
  await search.fill("no-synthetic-match");
  await check(explorer).toContainText("条件に一致する呼び出しがありません");
  await explorer.getByRole("button", {name: "条件をクリア", exact: true}).last().click();
  await check(search).toHaveValue("");
  await explorer.getByRole("tab", {name: "階層", exact: true}).focus();
  await explorer.getByRole("tab", {name: "階層", exact: true}).press("ArrowRight");
  await check(explorer.getByRole("tab", {name: "開始時刻順", exact: true})).toHaveAttribute("aria-selected", "true");
  await explorer.getByRole("tab", {name: "階層", exact: true}).click();
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => { document.documentElement.classList.remove("light", "dark"); document.documentElement.dataset.theme = value; }, theme);
    await explorer.screenshot({path: testInfo.outputPath(`trace-explorer-list-${theme}.png`)});
  }
  await search.fill("synthetic-call-input");
  await check(explorer.locator("[data-trace-record]").first()).toBeVisible();
  await explorer.locator("[data-trace-record] button").first().click();
  check(await page.evaluate(() => {
    const selected = document.querySelector("[data-trace-record] button[aria-pressed=true]")?.closest("[data-trace-record]");
    const marker = document.querySelector("[data-trace-overview-selected=true]");
    return Boolean(selected && marker && selected.getAttribute("data-trace-record") === marker.getAttribute("data-trace-overview-span") && getComputedStyle(marker).outlineStyle !== "none" && parseFloat(getComputedStyle(marker).outlineWidth) > 0);
  }, undefined)).toBe(true);
  const inspector = explorer.locator("[data-trace-inspector]");
  await check(inspector).toBeVisible();
  await inspector.getByRole("tab", {name: "入力", exact: true}).click();
  await check(inspector).toContainText("synthetic-call-input");
  await inspector.getByRole("tab", {name: "結果", exact: true}).click();
  await check(inspector).toContainText("synthetic-call-output");
  await inspector.getByRole("tab", {name: "思考", exact: true}).click();
  await check(inspector.locator("[data-call-readable] details[open] [data-markdown-text]")).toContainText("synthetic-call-thinking");
  await check(inspector.locator("[data-call-readable] details[open] [data-markdown-text]")).toBeVisible();
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => { document.documentElement.classList.remove("light", "dark"); document.documentElement.dataset.theme = value; }, theme);
    await inspector.screenshot({path: testInfo.outputPath(`trace-thinking-${theme}.png`)});
  }
  await inspector.getByRole("tab", {name: "Schema", exact: true}).click();
  await check(inspector).toContainText("synthetic-call-schema");
  await inspector.getByRole("tab", {name: "計時", exact: true}).click();
  await check(inspector).toContainText("開始時刻");
  await inspector.getByRole("tab", {name: "入力", exact: true}).click();
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth, undefined)).toBe(true);
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => { document.documentElement.classList.remove("light", "dark"); document.documentElement.dataset.theme = value; }, theme);
    await explorer.screenshot({path: testInfo.outputPath(`trace-explorer-detail-${theme}.png`)});
    await page.screenshot({path: testInfo.outputPath(`trace-explorer-page-${theme}.png`), fullPage: true});
  }
  if (probeLayout) {
    const original = page.viewportSize();
    await page.emulateMedia({reducedMotion: "reduce"});
    const sizes = original && original.width >= 1000 ? [{width: 1280, height: 900}, {width: 1920, height: 1080}] : [{width: 812, height: 375}];
    for (const size of sizes) {
      await page.setViewportSize(size);
      check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth, undefined)).toBe(true);
      await explorer.screenshot({path: testInfo.outputPath(`trace-explorer-${size.width}-reduced-motion.png`)});
    }
    if (original) await page.setViewportSize(original);
  }
  const back = inspector.getByRole("button", {name: "一覧に戻る", exact: true});
  if (await back.isVisible()) {
    await back.click();
    await check(explorer.locator('[data-trace-record] button[aria-pressed="true"]')).toBeFocused();
  }
  await explorer.getByRole("button", {name: "経過に戻る", exact: true}).click();
  await check(page.getByRole("button", {name: "軌跡を調べる", exact: true}).first()).toBeFocused();
}
