import { expect, type Locator, type Page } from "@playwright/test";

/** 両画面で、統一前の compact な外観と keyboard による選択を検証する。 */
export async function expectLegacyOntologyControls(page: Page, scope: Locator) {
  const mobile = (page.viewportSize()?.width ?? 0) < 640;
  const modes = scope.getByTestId("ontology-graph-view-mode");
  const all = scope.getByTestId("ontology-graph-mode-all");
  const originalMode = await modes.locator('[aria-pressed="true"]').getAttribute("data-testid");
  await expect(modes).toHaveCSS("min-height", mobile ? "44px" : "40px");
  // SQL 結果の入れ子領域は 375px 時に約 180px。ページ幅だけでは内部の裁切を検出できない。
  const layout = await modes.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    const graph = element.parentElement!.parentElement!;
    return {
      width: graph.clientWidth,
      scrollWidth: graph.scrollWidth,
      buttonsFit: Array.from(element.querySelectorAll("button")).every(button => {
        const box = button.getBoundingClientRect();
        return box.left >= bounds.left && box.right <= bounds.right + 1
          && box.top >= bounds.top && box.bottom <= bounds.bottom + 1;
      }),
    };
  });
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width + 1);
  expect(layout.buttonsFit).toBe(true);
  await expect(modes).toHaveCSS("border-top-width", "1px");
  await expect(modes).not.toHaveCSS("box-shadow", "none");
  await all.focus();
  await page.keyboard.press("Enter");
  await expect(all).toBeFocused();
  await expect(all).toHaveAttribute("aria-pressed", "true");
  await expect(all).toHaveCSS("height", mobile ? "36px" : "32px");
  await expect(all.locator("svg")).toHaveCSS("width", "13px");
  const focus = await all.evaluate(element => {
    const style = getComputedStyle(element);
    return { visible: element.matches(":focus-visible"), shadow: style.boxShadow };
  });
  expect(focus.visible).toBe(true);
  expect(focus.shadow).not.toBe("none");
  // semantic primary と完全一致する塗り（統一後の薄い混色背景とは異なる）。
  const selectedColors = await all.evaluate(element => {
    const probe = document.createElement("span");
    probe.style.color = "var(--primary)";
    element.append(probe);
    const primary = getComputedStyle(probe).color;
    probe.remove();
    return { background: getComputedStyle(element).backgroundColor, primary };
  });
  expect(selectedColors.background).toBe(selectedColors.primary);

  const physical = scope.getByTestId("ontology-graph-mode-physical_er");
  await physical.focus();
  await page.keyboard.press("Space");
  await expect(physical).toHaveAttribute("aria-pressed", "true");
  await expect(all).toHaveAttribute("aria-pressed", "false");
  await all.click();

  const legend = scope.getByTestId("ontology-graph-legend").getByRole("button").first();
  await expect(legend).toHaveCSS("font-size", "10px");
  await expect(legend).toHaveCSS("border-top-width", "0px");
  await expect(legend).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
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
  const rootSize = await page.locator("html").evaluate(element => parseFloat(getComputedStyle(element).fontSize));
  await expect(zoom).toHaveCSS("height", `${rootSize * 2}px`);
  await expect(zoom).toHaveCSS("padding-left", `${rootSize * 0.75}px`);
  await expect(zoom.locator("svg")).toHaveCSS("width", "15px");
  if (originalMode) await scope.getByTestId(originalMode).click();
}
