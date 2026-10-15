import { expect, type Locator } from "./test";
import { expectedControlHeight } from "./control-height";

/** 欄の幅の段（rem。共通の `FIELD_WIDTH_REM`）。ルートは 14px。 */
const FIELD_WIDTH_PX = { sm: 12 * 14, md: 20 * 14, lg: 28 * 14 } as const;
/** `sm:` の画面幅（640px）未満では、段の幅を持つ欄も全幅になる。 */
const SM_BREAKPOINT_PX = 640;

/**
 * DB オブジェクトの一覧のツールバー（共通の `ListToolbar`。#1891）の検索・所有者・種類の並びを確かめる。
 *
 * - 検索欄は lg（28rem）、所有者は md（20rem）、種類は sm（12rem）。置き場所（ツールバー）より広い段は置き場所の幅。
 *   画面幅が 640px 未満では所有者・種類も全幅（検索欄は置き場所が 28rem 未満なら全幅）。
 * - 並びは DOM の順（検索 → 所有者 → 種類）。次の欄は同じ行の右か、次の行の左端（検索欄の左端にそろう）。
 * - 3 つとも同じ高さ（md 36px。タッチ端末は 44px）。
 */
export async function expectDbObjectToolbarLayout(search: Locator, owner: Locator, kind?: Locator) {
  const fields = kind ? [search, owner, kind] : [search, owner];
  const steps = kind ? [FIELD_WIDTH_PX.lg, FIELD_WIDTH_PX.md, FIELD_WIDTH_PX.sm] : [FIELD_WIDTH_PX.lg, FIELD_WIDTH_PX.md];
  const page = search.page();
  const viewportWidth = page.viewportSize()!.width;
  const minHeight = await expectedControlHeight(page);
  await expect
    .poll(async () => {
      const measured = await Promise.all(
        fields.map((field) =>
          field.evaluate((node) => {
            // 入力欄は先頭のアイコンと消去のボタンを重ねる relative の箱が欄の外形（共通の layout-checks と同じ）。
            const box =
              node instanceof HTMLInputElement && node.parentElement?.classList.contains("relative")
                ? node.parentElement
                : node;
            const toolbar = node.closest("[data-list-toolbar]");
            const rect = box.getBoundingClientRect();
            return {
              x: rect.left,
              y: rect.top,
              width: rect.width,
              height: rect.height,
              toolbarWidth: toolbar?.getBoundingClientRect().width ?? 0,
            };
          })
        )
      );
      const problems: string[] = [];
      const toolbarWidth = measured[0].toolbarWidth;
      if (toolbarWidth <= 0) problems.push("ListToolbar の中にない");
      measured.forEach((box, index) => {
        if (box.toolbarWidth !== toolbarWidth) problems.push(`${index}: 別のツールバー`);
        if (box.height < minHeight - 1) problems.push(`${index}: 高さ ${box.height} < ${minHeight}`);
        if (Math.abs(box.height - measured[0].height) > 2) problems.push(`${index}: 高さが揃わない`);
        const fullWidth = index > 0 && viewportWidth < SM_BREAKPOINT_PX;
        const expected = fullWidth ? toolbarWidth : Math.min(steps[index], toolbarWidth);
        if (Math.abs(box.width - expected) > 2) problems.push(`${index}: 幅 ${box.width} != ${expected}`);
        if (index === 0) return;
        const previous = measured[index - 1];
        const sameRow = Math.abs(box.y - previous.y) <= 2;
        if (sameRow) {
          if (box.x < previous.x + previous.width - 1) problems.push(`${index}: 前の欄に重なる`);
        } else {
          if (box.y < previous.y + previous.height - 1) problems.push(`${index}: 前の欄より上`);
          if (Math.abs(box.x - measured[0].x) > 2) problems.push(`${index}: 折り返した行の左端が揃わない`);
        }
      });
      return problems;
    })
    .toEqual([]);
}
