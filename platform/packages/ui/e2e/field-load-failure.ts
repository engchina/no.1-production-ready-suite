/**
 * 3 製品の e2e が共通に使う、欄の直下の読み取りの失敗（`DataLoadFailureState` の `layout="field"`）の検査（#4817）。
 * 基準は UX 契約 messaging.md §3.6.1「欄の候補・欄の値」:
 *
 * - 見出しは欄ごとの文（`role="alert"`）。同じ画面で複数の欄が失敗しても、同じ文を 2 つ出さない。
 * - 1 行の小さい表示（danger の面・枠で囲まない）。幅が足りるときは、文と操作（「詳細」・導線・再試行）が同じ行に並ぶ。
 * - 「詳細」は畳んで出す。
 * - 再試行はキーボードで届くボタンで、タッチ端末（pointer: coarse）では 44px 以上。
 * - 文の色は地に対して 4.5:1 以上。横にあふれない。
 *
 * 製品の spec は相対パスで import し（RAG・Agent `../../platform/packages/ui/e2e/field-load-failure`、NL2SQL
 * `../../../../platform/packages/ui/e2e/field-load-failure`）、結果を製品の `expect` で確かめる:
 *
 * ```ts
 * expect(await findFieldLoadFailureViolations(page)).toEqual([]);
 * ```
 *
 * `@playwright/test` の型は import しない（layout-checks.ts と同じ理由）。page.evaluate に渡す関数は外の変数を参照しない。
 */

import type { EvaluatePage } from "./layout-checks";

export interface FieldLoadFailureBox {
  /** data-testid（無ければ見出しの文）。 */
  name: string;
  /** 見出しの文（`role="alert"`）。 */
  message: string;
  /** 表示の高さ（px）。 */
  height: number;
  /** 文と操作の組が同じ行にあるか。 */
  singleRow: boolean;
  /** 文と操作の組が 1 行に入る幅があるか（入らないときは操作の組が文の下へ折り返してよい）。 */
  fitsSingleRow: boolean;
  /** danger の面・枠で囲んでいるか（囲まない）。 */
  boxed: boolean;
  /** 「詳細」が開いているか。 */
  detailsOpen: boolean;
  /** 再試行（最後のボタン）の高さ（px）と、キーボードで届くか。 */
  retryHeight: number;
  retryFocusable: boolean;
  /** 文の色と地のコントラスト比。 */
  contrast: number;
  /** 表示の中の横あふれ（px）と、画面の右端からのはみ出し（px）。 */
  overflowX: number;
  overflowViewport: number;
  /** タッチ端末（pointer: coarse）か。 */
  coarse: boolean;
}

/** 見えている欄の直下の失敗の表示を測る。 */
export async function measureFieldLoadFailures(page: EvaluatePage): Promise<FieldLoadFailureBox[]> {
  return page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true })!;
    // 色の書き方（oklch・color-mix など）によらず、塗った結果の RGBA を読む。
    const rgba = (color: string): [number, number, number, number] => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
      return [r, g, b, a / 255];
    };
    const luminance = ([r, g, b]: number[]) => {
      const channel = (value: number) => {
        const s = value / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
    };
    // 地の色: 祖先の背景を上から重ねる（半透明の面は下の面と合成する）。
    const backgroundOf = (el: Element): number[] => {
      const layers: [number, number, number, number][] = [];
      for (let node: Element | null = el; node; node = node.parentElement) {
        const color = rgba(getComputedStyle(node).backgroundColor);
        if (color[3] > 0) layers.push(color);
        if (color[3] >= 1) break;
      }
      let base = [255, 255, 255];
      for (const [r, g, b, a] of layers.reverse()) {
        base = [r * a + base[0] * (1 - a), g * a + base[1] * (1 - a), b * a + base[2] * (1 - a)];
      }
      return base;
    };
    const coarse = window.matchMedia("(pointer: coarse)").matches;
    // 見えているものだけ。閉じた Disclosure（`<details>`）の中身は箱の寸法を持つことがあるので、
    // `checkVisibility`（display: none と content-visibility: hidden の祖先を見る）でも確かめる。
    const roots = Array.from(document.querySelectorAll<HTMLElement>('[data-failure-layout="field"]')).filter((el) => {
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && el.checkVisibility({ visibilityProperty: true });
    });
    return roots.map((root) => {
      const rect = root.getBoundingClientRect();
      const alert = root.querySelector<HTMLElement>('[role="alert"]');
      const buttons = Array.from(root.querySelectorAll<HTMLElement>("button, a[href]"));
      const retry = Array.from(root.querySelectorAll<HTMLButtonElement>("button")).at(-1) ?? null;
      const toggle = root.querySelector<HTMLElement>("button[aria-expanded]");
      const alertRect = alert?.getBoundingClientRect();
      const retryRect = retry?.getBoundingClientRect();
      const actions = retry?.parentElement ?? null;
      const actionsRect = actions?.getBoundingClientRect();
      // 文を 1 行で出したときの幅（折り返す前の幅）を測る。
      let messageWidth = alertRect?.width ?? 0;
      if (alert) {
        const clone = alert.cloneNode(true) as HTMLElement;
        clone.style.position = "absolute";
        clone.style.visibility = "hidden";
        clone.style.whiteSpace = "nowrap";
        clone.style.width = "max-content";
        clone.removeAttribute("id");
        clone.removeAttribute("role");
        document.body.appendChild(clone);
        messageWidth = clone.getBoundingClientRect().width;
        clone.remove();
      }
      const row = alert?.parentElement;
      const gap = row ? Number.parseFloat(getComputedStyle(row).columnGap) || 0 : 0;
      const style = getComputedStyle(root);
      const textColor = alert ? rgba(getComputedStyle(alert).color) : [0, 0, 0, 1];
      const background = alert ? backgroundOf(alert) : [255, 255, 255];
      const [light, dark] = [luminance(textColor), luminance(background)].sort((a, b) => b - a);
      return {
        name: root.getAttribute("data-testid") ?? alert?.textContent ?? "",
        message: alert?.textContent?.trim() ?? "",
        height: rect.height,
        singleRow: Boolean(alertRect && actionsRect && actionsRect.top < alertRect.bottom && alertRect.top < actionsRect.bottom),
        fitsSingleRow: Boolean(actionsRect && messageWidth + gap + actionsRect.width <= rect.width + 0.5),
        boxed: rgba(style.backgroundColor)[3] > 0 || Number.parseFloat(style.borderTopWidth) > 0,
        detailsOpen: toggle?.getAttribute("aria-expanded") === "true",
        retryHeight: retryRect?.height ?? 0,
        retryFocusable: Boolean(retry && !retry.disabled && retry.tabIndex >= 0) && buttons.every((item) => item.tabIndex >= 0),
        contrast: (light + 0.05) / (dark + 0.05),
        overflowX: root.scrollWidth - root.clientWidth,
        overflowViewport: Math.max(0, rect.right - document.documentElement.clientWidth),
        coarse,
      };
    });
  }, undefined);
}

export interface FieldLoadFailureCheckOptions {
  /** 見えているはずの数（省くと 1 つ以上）。 */
  expectCount?: number;
}

/** 基準に合わない点を文で返す（空なら合っている）。 */
export async function findFieldLoadFailureViolations(
  page: EvaluatePage,
  { expectCount }: FieldLoadFailureCheckOptions = {}
): Promise<string[]> {
  const boxes = await measureFieldLoadFailures(page);
  const violations: string[] = [];
  if (expectCount === undefined ? boxes.length === 0 : boxes.length !== expectCount) {
    violations.push(`欄の直下の失敗の表示が ${boxes.length} 個（期待 ${expectCount ?? "1 個以上"}）`);
  }
  const seen = new Map<string, string>();
  for (const box of boxes) {
    if (!box.message) violations.push(`${box.name}: 見出しの文（role="alert"）が無い`);
    const other = seen.get(box.message);
    if (other !== undefined) violations.push(`${box.name}: 見出しが ${other} と同じ文（欄ごとの文にする）: ${box.message}`);
    seen.set(box.message, box.name);
    if (box.boxed) violations.push(`${box.name}: danger の面・枠で囲んでいる（1 行の小さい表示にする）`);
    if (box.detailsOpen) violations.push(`${box.name}: 「詳細」が開いている（畳んで出す）`);
    const controlHeight = box.coarse ? 44 : 32;
    if (box.fitsSingleRow && !box.singleRow) violations.push(`${box.name}: 幅が足りるのに文と操作が同じ行に無い`);
    if (box.singleRow && !box.detailsOpen && box.height > controlHeight + 4) {
      violations.push(`${box.name}: 1 行の高さ ${Math.round(box.height)}px が操作の高さ ${controlHeight}px を超える`);
    }
    if (box.retryHeight < controlHeight - 0.5) {
      violations.push(`${box.name}: 再試行の高さ ${Math.round(box.retryHeight)}px が ${controlHeight}px より小さい`);
    }
    if (!box.retryFocusable) violations.push(`${box.name}: 再試行・導線にキーボードで届かない`);
    if (box.contrast < 4.5) violations.push(`${box.name}: 文のコントラスト ${box.contrast.toFixed(2)} が 4.5 未満`);
    if (box.overflowX > 1) violations.push(`${box.name}: 表示の中で横に ${Math.round(box.overflowX)}px あふれる`);
    if (box.overflowViewport > 1) violations.push(`${box.name}: 画面の右端から ${Math.round(box.overflowViewport)}px はみ出す`);
  }
  return violations;
}
