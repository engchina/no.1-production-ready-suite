/** 3 製品の実画面で、共有の軌跡の調査を確かめる。 */
// 製品ごとに独立した Playwright を使うため、実行環境と型は呼び出し元から渡す。
interface ExplorerLocator {
  getByRole(role: "button" | "heading" | "searchbox" | "tab" | "combobox" | "option", options?: {name: string; exact?: boolean}): ExplorerLocator;
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
  toEqual(value: unknown): void;
}

export async function confirmTraceExplorer(page: ExplorerPage, testInfo: {outputPath(...paths: string[]): string}, expect: (value: unknown) => unknown, probeLayout = false) {
  const check = (value: unknown) => expect(value) as ExplorerAssertions;
  const processDetails = page.locator("[data-run-process-details]").first();
  const wasOpen = await page.evaluate(() => document.querySelector("[data-run-process-details]")?.hasAttribute("open") ?? false, undefined);
  if (!wasOpen) await processDetails.locator("summary").first().click();
  check(await page.evaluate(() => {
    const detail = document.querySelector("[data-run-process-details]");
    const rows = Array.from(detail?.querySelectorAll("[data-process-identifier]") ?? []);
    const gap = parseFloat(getComputedStyle(document.documentElement).fontSize) / 4;
    const models = detail?.querySelector("[data-run-used-models]");
    const target = matchMedia("(pointer: coarse)").matches ? 44 : 24;
    const copies = Array.from(detail?.querySelectorAll("[data-copy-inline]") ?? []);
    if (!copies.length || copies.some(copy => Math.abs(copy.getBoundingClientRect().height - target) > 1)) return false;
    const groups = [...rows, ...(models ? [models] : [])];
    return rows.length === 3 && models !== null && groups.slice(1).every((row, index) =>
      row.getBoundingClientRect().top - groups[index].getBoundingClientRect().bottom <= gap + 1
    );
  }, undefined)).toBe(true);
  await processDetails.screenshot({path: testInfo.outputPath("process-details-compact.png")});
  if (!wasOpen) await processDetails.locator("summary").first().click();
  check(await page.evaluate(() => {
    const summary = document.querySelector("[data-run-progress-summary]");
    return summary?.getAttribute("data-run-status") === "succeeded"
      && summary.classList.contains("text-success-fg")
      && !summary.querySelector(".lucide-circle-x")
      && document.querySelector('[data-run-timeline-step][data-status="failed"]') !== null;
  }, undefined)).toBe(true);
  await page.locator("[data-run-timeline-section]").first().screenshot({path: testInfo.outputPath("recovered-run-summary.png")});
  const normalRows = await page.evaluate(() => Array.from(document.querySelectorAll("[data-run-timeline-step]")).map(row => ({
    id: row.getAttribute("data-run-timeline-step"), name: row.querySelector("[data-run-timeline-label]")?.textContent,
    level: row.getAttribute("data-level") ?? "0", parent: row.getAttribute("data-parent-id"),
    status: row.getAttribute("data-status"), duration: row.querySelector("[data-run-step-status-duration]")?.textContent, summary: row.querySelector("[data-run-result-summary]")?.textContent ?? "",
  })), undefined);
  check(normalRows.some(row => row.status === "skipped" && row.name === "会話の添付から根拠を選ぶ" && row.duration?.includes("スキップ"))).toBe(true);
  const normalStyle = await page.evaluate(() => {
    const label = document.querySelector("[data-run-timeline-label]");
    const bar = document.querySelector("[data-run-timeline-bar]");
    const style = label ? getComputedStyle(label) : null;
    return { font: style?.fontSize ?? null, family: style?.fontFamily, weight: style?.fontWeight, line: style?.lineHeight, color: style?.color, height: bar ? getComputedStyle(bar).height : null, radius: bar ? getComputedStyle(bar).borderRadius : null };
  }, undefined);
  await page.getByRole("button", {name: "軌跡を調べる", exact: true}).first().click();
  const explorer = page.locator("[data-trace-explorer]");
  await check(explorer).toBeVisible();
  check(await page.evaluate(expected => {
    const labels = Array.from(document.querySelectorAll("[data-trace-record-label]"));
    const bars = Array.from(document.querySelectorAll("[data-trace-time-bar]"));
    return labels.length > 0 && bars.length > 0 && labels.every(label => { const style = getComputedStyle(label); return !expected.font || (style.fontSize === expected.font && style.fontFamily === expected.family && style.fontWeight === expected.weight && style.lineHeight === expected.line && style.color === expected.color); }) && bars.every(bar => (!expected.height || getComputedStyle(bar).height === expected.height) && (!expected.radius || getComputedStyle(bar).borderRadius === expected.radius));
  }, normalStyle)).toBe(true);
  check(await page.evaluate(expected => {
    const rows = Array.from(document.querySelectorAll("[data-trace-record]"));
    const ids = new Set(rows.map(row => row.getAttribute("data-trace-record")));
    const source = expected.filter(row => ids.has(row.id));
    return source.length === rows.length && rows.every((row, i) => row.getAttribute("data-trace-record") === source[i].id
      && row.querySelector("[data-trace-record-label]")?.textContent === source[i].name
      && (row.getAttribute("data-level") ?? "0") === source[i].level && row.getAttribute("data-parent-id") === source[i].parent
      && row.getAttribute("data-status") === source[i].status
      && row.querySelector("[data-trace-step-status-duration]")?.textContent === source[i].duration
      && (!source[i].summary || (row.querySelector("[data-trace-result-summary]")?.textContent ?? "") === source[i].summary));
  }, normalRows)).toBe(true);
  await check(explorer.getByRole("heading", {name: "軌跡の調査"})).toBeFocused();
  const search = explorer.getByRole("searchbox", {name: "呼び出しを検索"});
  await explorer.getByRole("combobox", {name: "種類"}).click();
  await page.getByRole("option", {name: "モデル", exact: true}).click();
  await check(explorer.locator('[data-trace-record]:not([data-trace-category="model"])')).toHaveCount(0);
  await explorer.getByRole("button", {name: "条件をクリア", exact: true}).last().click();
  await explorer.getByRole("combobox", {name: "状態"}).click();
  await page.getByRole("option", {name: "失敗", exact: true}).click();
  await check(explorer.locator("[data-trace-record]")).toHaveCount(1);
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
  await check(inspector.getByRole("tab", {name: "概要", exact: true})).toHaveAttribute("aria-selected", "true");
  await check(inspector.getByRole("tab")).toHaveCount(5);
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
  await inspector.getByRole("tab", {name: "入出力仕様", exact: true}).click();
  await check(inspector).toContainText("入力・出力の項目、型、必須条件を確認します。");
  await check(inspector).toContainText("synthetic-call-schema");
  await check(inspector.locator("[data-trace-timing]")).toContainText("開始時刻");
  check(await page.evaluate(() => {
    const fields = Array.from(document.querySelectorAll("[data-trace-timing] dt"));
    return fields.length === 2 && fields.every(label => {
      const value = label.nextElementSibling;
      if (!value) return false;
      const a = label.getBoundingClientRect(), b = value.getBoundingClientRect();
      return a.right <= b.left && Math.abs((a.top + a.height / 2) - (b.top + b.height / 2)) <= 1;
    });
  }, undefined)).toBe(true);
  await check(inspector.locator("[data-trace-technical]")).toContainText("Span name");
  check(await page.evaluate(() => {
    const fields = Array.from(document.querySelectorAll("[data-trace-technical] dd"))
      .filter(field => !field.querySelector("button"));
    return fields.length > 0 && fields.every(field => {
      const value = field.querySelector("span");
      return value !== null && field.getBoundingClientRect().height <= value.getBoundingClientRect().height + 1;
    });
  }, undefined)).toBe(true);
  await check(inspector.locator("[data-trace-timing] button")).toHaveCount(0);
  check(await page.evaluate(() => {
    const content = document.querySelector("[data-trace-inspector-content]");
    const text = content?.querySelector("[data-trace-timing] time")?.textContent ?? "";
    const groups = Array.from(content?.children ?? []).filter(element => ["data-trace-processing-content", "data-trace-timing", "data-trace-technical"].some(attribute => element.hasAttribute(attribute)));
    return text.includes("(") && !text.includes("T00:") && content?.querySelector("[data-trace-timing]")?.previousElementSibling?.textContent?.includes("Span ID") === true
      && groups[0]?.hasAttribute("data-trace-processing-content") === true
      && groups[1]?.hasAttribute("data-trace-timing") === true && groups[2]?.hasAttribute("data-trace-technical") === true;
  }, undefined)).toBe(true);
  await inspector.getByRole("tab", {name: "入力", exact: true}).click();
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth, undefined)).toBe(true);
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => { document.documentElement.classList.remove("light", "dark"); document.documentElement.dataset.theme = value; }, theme);
    await explorer.screenshot({path: testInfo.outputPath(`trace-explorer-detail-${theme}.png`)});
    await inspector.screenshot({path: testInfo.outputPath(`trace-inspector-${theme}.png`)});
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
  await explorer.getByRole("button", {name: "条件をクリア", exact: true}).last().click();
  await search.fill("synthetic-stage-detail");
  await explorer.locator('[data-trace-record][data-trace-category="stage"] button').first().click();
  await check(inspector.getByRole("tab")).toHaveCount(5);
  await check(inspector.getByRole("tab", {name: "入力", exact: true})).toHaveAttribute("aria-selected", "true");
  await check(inspector).toContainText("記録されていません");
  await inspector.getByRole("tab", {name: "概要", exact: true}).click();
  await check(inspector).toContainText("synthetic-stage-detail");
  check(await page.evaluate(() => {
    const content = document.querySelector("[data-trace-inspector-content]");
    const timing = content?.querySelector(":scope > [data-trace-timing]");
    const identifier = timing?.previousElementSibling;
    if (!content || !timing || !identifier?.textContent?.includes("Span ID")) return false;
    const gap = parseFloat(getComputedStyle(content).rowGap);
    const actual = timing.getBoundingClientRect().top - identifier.getBoundingClientRect().bottom;
    return gap > 0 && Math.abs(actual - gap) < 1 && timing.textContent?.includes("開始時刻") === true;
  }, undefined)).toBe(true);
  check(await page.evaluate(() => {
    const content = document.querySelector("[data-trace-processing-content]");
    const fields = Array.from(content?.querySelectorAll("dt") ?? []).map(node => node.textContent);
    const result = content?.querySelector("dl");
    return fields[fields.length - 1] === "結果" && result?.nextElementSibling?.tagName === "UL";
  }, undefined)).toBe(true);
  for (const theme of ["light", "dark"]) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await inspector.screenshot({path: testInfo.outputPath(`trace-stage-timing-${theme}.png`)});
  }
  await explorer.getByRole("button", {name: "経過に戻る", exact: true}).click();
  await check(page.getByRole("button", {name: "軌跡を調べる", exact: true}).first()).toBeFocused();
}

/** 経過の技術属性を調べる受入も、利用者と同じ軌跡の導線を通す。 */
export async function inspectTraceStep(page: ExplorerPage, timeline: ExplorerLocator, stepId: string) {
  await timeline.getByRole("button", {name: "軌跡を調べる", exact: true}).first().click();
  const explorer = page.locator("[data-trace-explorer]");
  await explorer.getByRole("searchbox", {name: "呼び出しを検索"}).fill(stepId);
  await explorer.locator("[data-trace-record] button").first().click();
}
