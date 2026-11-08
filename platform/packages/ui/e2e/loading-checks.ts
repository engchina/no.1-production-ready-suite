/**
 * 3 製品の e2e が共通に使う、読み込み中・処理中の表示の検査（#4649）。基準は docs/agents/ui.md「読み込み中」:
 * 領域の先頭に 1 つだけ、灰色の枠（`bg-surface-sunken`）・処理の名前・経過時間を出し、置かれた領域の全幅を使い、
 * 375px でも横にあふれない。読み込み中の領域は内容の形の Skeleton で覆う。
 *
 * - `measureLoadingIndicators`: 見えている処理中の表示（`TimedLoadingState` と単独の `ProcessingIndicator`）の寸法と面
 * - `findLoadingStandardViolations`: 上の基準に合わない点を文で返す（空なら合っている）
 *
 * 製品の spec は相対パスで import し（RAG・Agent `../../platform/packages/ui/e2e/loading-checks`、NL2SQL
 * `../../../../platform/packages/ui/e2e/loading-checks`）、結果を製品の `expect` で確かめる:
 *
 * ```ts
 * expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
 * ```
 *
 * `@playwright/test` の型は import しない（layout-checks.ts と同じ理由）。page.evaluate に渡す関数は外の変数を参照しない。
 */

import type { EvaluatePage } from "./layout-checks";

export interface LoadingIndicatorBox {
  /** data-testid（無ければ処理の名前）。 */
  name: string;
  /** 背景が灰色の枠の色（`bg-surface-sunken`）か。 */
  sunken: boolean;
  /** 枠線があるか。 */
  bordered: boolean;
  /** 経過時間（role="timer"）が見えているか。 */
  hasTimer: boolean;
  /** 親の内容の幅に対する幅の比（1 で全幅）。 */
  widthRatio: number;
  /** 表示の中の横あふれ（px）と、画面の右端からのはみ出し（px）。 */
  overflowX: number;
  overflowViewport: number;
  /** 中の Skeleton の数（`TimedLoadingState` のときだけ意味を持つ）。 */
  skeletons: number;
  /** `TimedLoadingState`（領域の読み込み）か、単独の `ProcessingIndicator`（操作の進行）か。 */
  kind: "region" | "action";
}

export async function measureLoadingIndicators(page: EvaluatePage): Promise<LoadingIndicatorBox[]> {
  return page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "bg-surface-sunken";
    probe.style.position = "absolute";
    probe.style.visibility = "hidden";
    document.body.appendChild(probe);
    const sunkenColor = getComputedStyle(probe).backgroundColor;
    probe.remove();
    const selector = 'section[aria-busy="true"][data-processing-placement], [data-processing-surface="framed"]';
    const all = Array.from(document.querySelectorAll<HTMLElement>(selector));
    const visible = (el: HTMLElement) => {
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== "hidden";
    };
    // 外側のものだけ（TimedLoadingState の中の ProcessingIndicator は数えない）。
    const outer = all.filter((el) => visible(el) && !all.some((other) => other !== el && other.contains(el)));
    return outer.map((el) => {
      const style = getComputedStyle(el);
      const parent = el.parentElement;
      let widthRatio = 1;
      if (parent) {
        const ps = getComputedStyle(parent);
        const inner = parent.clientWidth - parseFloat(ps.paddingLeft) - parseFloat(ps.paddingRight);
        if (inner > 0) widthRatio = el.getBoundingClientRect().width / inner;
      }
      const timer = el.querySelector<HTMLElement>('[role="timer"]');
      const label = el.querySelector<HTMLElement>("[data-processing-label]")?.textContent?.trim() ?? "";
      return {
        name: el.getAttribute("data-testid") || label,
        sunken: style.backgroundColor === sunkenColor,
        bordered: parseFloat(style.borderTopWidth) > 0,
        hasTimer: Boolean(timer && timer.getBoundingClientRect().width > 0),
        widthRatio: Math.round(widthRatio * 1000) / 1000,
        overflowX: Math.max(0, el.scrollWidth - el.clientWidth),
        overflowViewport: Math.max(0, Math.round(el.getBoundingClientRect().right - document.documentElement.clientWidth)),
        skeletons: el.querySelectorAll('[data-skeleton], [class*="animate-pulse"]').length,
        kind: el.tagName === "SECTION" ? ("region" as const) : ("action" as const),
      };
    });
  }, null);
}

export interface LoadingStandardOptions {
  /** 見えている処理中の表示の数（1 つの領域に 1 つ）。省略すると数は確かめない。 */
  expectCount?: number;
  /** 領域の読み込み（`TimedLoadingState`）が内容の形の Skeleton を持つか。 */
  skeleton?: boolean;
  /** 全幅とみなす比の下限（既定 0.98）。 */
  minWidthRatio?: number;
}

export async function findLoadingStandardViolations(
  page: EvaluatePage,
  options: LoadingStandardOptions = {},
): Promise<string[]> {
  const boxes = await measureLoadingIndicators(page);
  const minWidthRatio = options.minWidthRatio ?? 0.98;
  const problems: string[] = [];
  if (options.expectCount !== undefined && boxes.length !== options.expectCount) {
    problems.push(`処理中の表示の数が ${boxes.length}（期待 ${options.expectCount}）: ${boxes.map((b) => b.name).join(", ")}`);
  }
  for (const box of boxes) {
    if (!box.sunken || !box.bordered) problems.push(`${box.name}: 灰色の枠が無い`);
    if (!box.hasTimer) problems.push(`${box.name}: 経過時間が無い`);
    if (box.widthRatio < minWidthRatio) problems.push(`${box.name}: 領域の全幅を使っていない（${box.widthRatio}）`);
    if (box.overflowX > 1 || box.overflowViewport > 1) {
      problems.push(`${box.name}: 横にあふれている（中 ${box.overflowX}px・画面 ${box.overflowViewport}px）`);
    }
    if (options.skeleton && box.kind === "region" && box.skeletons === 0) problems.push(`${box.name}: Skeleton が無い`);
  }
  return problems;
}
