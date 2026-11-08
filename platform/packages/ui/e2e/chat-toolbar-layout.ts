/** 対象選択と新しい会話の隣接を、製品の assertion で確認する。 */
import type { EvaluatePage } from "./layout-checks";

type Assert = (actual: unknown) => {
  toBe(expected: unknown): unknown;
  toBeLessThan(expected: number): unknown;
  toBeLessThanOrEqual(expected: number): unknown;
};

export async function expectChatTargetNextToNewConversation(page: EvaluatePage, expect: Assert) {
  const measured = await page.evaluate(() => {
    const toolbar = document.querySelector<HTMLElement>("[data-chat-toolbar]");
    const target = toolbar?.querySelector<HTMLElement>("[data-chat-target-selector]");
    const button = toolbar?.lastElementChild;
    if (!toolbar || !target || !(button instanceof HTMLButtonElement)) {
      return { found: false, gapDifference: Infinity, fitsPage: false };
    }
    const targetBox = target.getBoundingClientRect();
    const buttonBox = button.getBoundingClientRect();
    const gap = parseFloat(getComputedStyle(toolbar).columnGap);
    return {
      found: true,
      gapDifference: Math.abs(buttonBox.left - targetBox.right - gap),
      fitsPage: document.documentElement.scrollWidth <= innerWidth,
    };
  }, undefined);
  expect(measured.found).toBe(true);
  expect(measured.gapDifference).toBeLessThan(2);
  expect(measured.fitsPage).toBe(true);
}

/** ツールバーの高さ。対象の読み込み中に測り、読み込み後に `expectChatToolbarHeightUnchanged` へ渡す。 */
export async function measureChatToolbarHeight(page: EvaluatePage) {
  return page.evaluate(() => document.querySelector("[data-chat-toolbar]")?.getBoundingClientRect().height ?? 0, undefined);
}

/** 対象の欄が読み込まれてもツールバーの高さが変わらず、会話の領域が動かない（1px 以内）。 */
export async function expectChatToolbarHeightUnchanged(page: EvaluatePage, expect: Assert, reservedHeight: number) {
  expect(reservedHeight > 0).toBe(true);
  expect(Math.abs(reservedHeight - (await measureChatToolbarHeight(page)))).toBeLessThanOrEqual(1);
}
