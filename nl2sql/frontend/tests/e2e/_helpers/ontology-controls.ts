import { expect, type Locator, type Page } from "@playwright/test";

export type OntologyViewTabName = "接地パス" | "全体" | "物理 ER";

/** 概念図の表示の切り替え（共有の Tabs。#5949）の tablist。 */
export function ontologyViewTabs(scope: Locator) {
  return scope.getByRole("tablist", { name: "オントロジーグラフ表示モード", exact: true });
}

/** 概念図の表示の切り替えのタブ（「接地パス」「全体」「物理 ER」）。 */
export function ontologyViewTab(scope: Locator, name: OntologyViewTabName) {
  return scope.getByRole("tab", { name, exact: true });
}

/** 両画面で、表示の切り替え（共有の Tabs）のキーボード操作と、凡例・ズームの compact な外観を検証する。 */
export async function expectLegacyOntologyControls(page: Page, scope: Locator) {
  const mobile = (page.viewportSize()?.width ?? 0) < 640;
  const modes = ontologyViewTabs(scope);
  const all = ontologyViewTab(scope, "全体");
  const physical = ontologyViewTab(scope, "物理 ER");
  const grounding = ontologyViewTab(scope, "接地パス");
  const originalMode = (await modes.locator('[aria-selected="true"]').innerText()).trim() as OntologyViewTabName;
  await expect(modes.getByRole("tab")).toHaveText(["接地パス", "全体", "物理 ER"]);
  await all.click();
  await expect(all).toBeFocused();
  await expect(all).toHaveAttribute("aria-selected", "true");
  // 選んだタブに対応する tabpanel が画布を包む（aria-controls の先がある）。
  const panel = scope.getByRole("tabpanel", { name: "全体", exact: true });
  await expect(panel.getByTestId("ontology-graph-canvas")).toBeVisible();
  await expect(all).toHaveAttribute("aria-controls", (await panel.getAttribute("id"))!);
  // SQL 結果の入れ子領域は 375px 時に約 180px。ページ幅だけでは内部の裁切を検出できない。
  // 入りきらないときはタブの列の中だけで横に送り（Tabs の標準）、親の領域を広げず、選んだタブは列の中に見える。
  const layout = await modes.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const graph = element.parentElement!.parentElement!;
    const selected = element.querySelector('[aria-selected="true"]')!.getBoundingClientRect();
    return {
      width: graph.clientWidth,
      scrollWidth: graph.scrollWidth,
      selectedVisible: selected.left >= bounds.left - 1 && selected.right <= bounds.right + 1,
    };
  });
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width + 1);
  expect(layout.selectedVisible).toBe(true);
  // アイコンは 14 / 16 / 20 / 24px に統一（奇数 px はボケる。README §3）。
  await expect(all.locator("svg")).toHaveCSS("width", "16px");

  // WAI-ARIA Tabs: Tab の停止位置は選んだタブ 1 つで、← → / Home / End で移る。
  await expect(all).toHaveAttribute("tabindex", "0");
  await expect(physical).toHaveAttribute("tabindex", "-1");
  await page.keyboard.press("ArrowRight");
  await expect(physical).toBeFocused();
  await expect(physical).toHaveAttribute("aria-selected", "true");
  await expect(all).toHaveAttribute("aria-selected", "false");
  await expect(scope.getByRole("tabpanel", { name: "物理 ER", exact: true })).toBeVisible();
  // フォーカスの表示はグローバルの :focus-visible の outline 1 つ（ring と二重にしない。#355）。
  const focus = await physical.evaluate(element => {
    const style = getComputedStyle(element);
    return { visible: element.matches(":focus-visible"), outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth, shadow: style.boxShadow };
  });
  expect(focus.visible).toBe(true);
  expect(focus.outlineStyle).toBe("solid");
  expect(focus.outlineWidth).toBe("2px");
  expect(focus.shadow).toBe("none");
  await page.keyboard.press("ArrowRight");
  await expect(grounding).toBeFocused();
  await page.keyboard.press("End");
  await expect(physical).toHaveAttribute("aria-selected", "true");
  // 種類の絞り込みの凡例はカードの表示（React Flow）にある。全体は Cytoscape の画布の凡例（#5922）。
  await page.keyboard.press("Home");
  await expect(grounding).toHaveAttribute("aria-selected", "true");

  const legend = scope.getByTestId("ontology-graph-legend").getByRole("button").first();
  // 補助テキストの下限は 12px（README §3）。
  await expect(legend).toHaveCSS("font-size", "12px");
  await expect(legend).toHaveCSS("border-top-width", "1px");
  // 選択した共通 Button はデザインシステムの accent を使う。
  const legendTokens = await legend.evaluate(element => {
    const probe = document.createElement("span");
    element.append(probe);
    probe.style.color = "var(--color-accent-fg)";
    const border = getComputedStyle(probe).color;
    probe.style.color = "var(--color-accent-muted)";
    const background = getComputedStyle(probe).color;
    probe.remove();
    return { border, background };
  });
  await expect(legend).toHaveCSS("border-top-color", legendTokens.border);
  await expect(legend).toHaveCSS("background-color", legendTokens.background);
  await legend.focus();
  await page.keyboard.press("Space");
  await expect(legend).toHaveAttribute("aria-pressed", "false");
  await expect(legend).toHaveCSS("text-decoration-line", "line-through");
  await page.mouse.move(0, 0);
  await expect(legend).toHaveCSS("opacity", "0.4");
  await page.keyboard.press("Enter");
  await expect(legend).toHaveAttribute("aria-pressed", "true");
  await expect(legend).toHaveCSS("opacity", "1");
  await expect(legend).toHaveCSS("text-decoration-line", "none");

  const zoom = scope.getByRole("button", { name: "グラフを拡大", exact: true });
  // 共有 Button sm の iconOnly（32px の正方形、タッチ端末は 44px。#5522）。
  const side = mobile ? "44px" : "32px";
  await expect(zoom).toHaveCSS("height", side);
  await expect(zoom).toHaveCSS("width", side);
  await expect(zoom).toHaveCSS("padding-left", "0px");
  await expect(zoom.locator("svg")).toHaveCSS("width", "16px");
  await ontologyViewTab(scope, originalMode).click();
}

/**
 * グラフの線（`.react-flow__edge`）をクリックする。SVG の <g> は Playwright の visibility の判定ができないため、
 * 線を画面に入れてから、線の中点を画面の座標でクリックする（#1788）。
 */
export async function clickOntologyEdge(page: Page, edge: Locator) {
  // 最初の範囲合わせ（読める縮尺。#5908）が終わって視点が止まってから線の位置を測る。
  const viewport = edge.locator("xpath=ancestor::*[contains(@class,'react-flow__viewport')]");
  let previous = "";
  await expect.poll(async () => {
    const current = await viewport.evaluate((el) => (el as HTMLElement).style.transform);
    const stable = current === previous;
    previous = current;
    return stable;
  }, { intervals: [150] }).toBe(true);
  const path = edge.locator("path.react-flow__edge-path");
  await path.evaluate((element) => element.scrollIntoView({ block: "center" }));
  const target = await path.evaluate((element) => {
    const path = element as SVGPathElement;
    const middle = path.getPointAtLength(path.getTotalLength() / 2);
    const matrix = path.getScreenCTM()!;
    const point = {
      x: middle.x * matrix.a + middle.y * matrix.c + matrix.e,
      y: middle.x * matrix.b + middle.y * matrix.d + matrix.f,
    };
    const frame = path.closest(".react-flow")!.getBoundingClientRect();
    const within = point.x > frame.left && point.x < frame.right && point.y > frame.top && point.y < frame.bottom;
    // 中ほどにノードが重なるときもクリックは線に届かない。
    const hit = within ? document.elementFromPoint(point.x, point.y) : null;
    return { ...point, inside: Boolean(hit && path.closest(".react-flow__edge")?.contains(hit)) };
  });
  // 読める縮尺の全体表示（#5908）で線の中ほどが画布の外にある・ノードに隠れるときは、キーボードで選ぶ（Enter で関係を選択）。
  if (target.inside) {
    await page.mouse.click(target.x, target.y);
    return;
  }
  await edge.focus();
  await edge.press("Enter");
}
