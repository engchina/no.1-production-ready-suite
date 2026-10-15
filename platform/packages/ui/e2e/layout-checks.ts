/**
 * 3 製品の e2e が共通に使う、一覧・入力欄の見た目の検査（#1891）。
 *
 * - `measureHorizontalOverflow`: ページ（documentElement と main）の横あふれ（px）
 * - `measureShellHiddenOverflow`: `AppShell` の外枠の隠れたあふれ（px。本文の下端が画面の外に切れる原因）
 * - `findClippedPlaceholders`: placeholder の文字の幅が欄の内側の幅を超える欄（切れて読めない placeholder）
 * - `findFieldsOffWidthScale`: 幅の段（xs 8rem / sm 12rem / md 20rem / lg 28rem）にも親の幅いっぱいにも当たらない欄と、
 *   `FormGrid` のセルからはみ出した欄（任意）
 * - `findNarrowFieldTexts`: 補足・エラーの文が置き場所の幅（横に並べる行では min(20rem, 置き場所の幅)）より狭く折り返す欄
 * - `jumpToPage`: ページ送り（共通の `Pagination`）のページ番号の入力で移る
 * - `setFormGridMaxWidth`: `FormGrid` の置き場所の幅を狭めて確かめる（`FORM_GRID_PROBE_WIDTHS_REM`）
 * - `findToolbarMisalignment`: 一覧のツールバー（`ListToolbar`）の同じ行の操作部品の高さ・縦の中心のずれ（#1933）
 * - `findToolbarFieldWidthMismatch`: 縦に積んだ（狭い）ツールバーで、検索欄と幅のそろわない絞り込みの欄（#1933）
 *
 * 製品の spec は相対パスで import し（RAG・Agent `../../platform/packages/ui/e2e/layout-checks`、NL2SQL
 * `../../../../platform/packages/ui/e2e/layout-checks`）、結果を製品の `expect` で確かめる:
 *
 * ```ts
 * expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
 * expect(await findClippedPlaceholders(page)).toEqual([]);
 * await jumpToPage(page.getByTestId("file-list-pagination"), 3);
 * expect(await findToolbarMisalignment(page)).toEqual([]);
 * ```
 *
 * `@playwright/test` の型は import しない（この file は platform にあり、製品の node_modules から解決できないため。
 * `platform/scripts/e2e-impact/fixture.ts` と同じ）。必要な形だけを下に書く。
 * page.evaluate に渡す関数は、ブラウザの中で実行されるので外の変数を参照しない（引数で渡す）。
 */

/** `page.evaluate` を持つもの（Playwright の `Page`）。 */
export interface EvaluatePage {
  evaluate<R, A>(pageFunction: (arg: A) => R | Promise<R>, arg: A): Promise<R>;
}

/** ページ番号の入力欄とボタンを探せるもの（Playwright の `Locator`。ページ送りの `nav`）。 */
export interface PaginationLocator {
  getByRole(
    role: "textbox",
    options: { name: string; exact?: boolean }
  ): { fill(value: string): Promise<void>; press(key: string): Promise<void> };
}

/** ページ全体（documentElement と main）の横あふれ（px）。表などの意図的な横スクロールは各自の箱の中に閉じる前提。 */
export async function measureHorizontalOverflow(page: EvaluatePage): Promise<number> {
  return page.evaluate(() => {
    const root = document.documentElement;
    const main = document.querySelector("main");
    return Math.max(root.scrollWidth - root.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
  }, undefined);
}

/** 共通の `AppShell` の外枠の、隠れたスクロールのあふれ（px）。 */
export interface ShellHiddenOverflow {
  vertical: number;
  horizontal: number;
}

/**
 * 共通の `AppShell` の外枠（`main#pr-main` の親。`fixed inset-0` + `overflow: hidden`）の隠れたあふれ（px）を返す。
 * 本文の absolute の要素（`sr-only` など）が main の外を包含ブロックにすると、main をスクロールしても動かずに外枠の
 * 外に残り、外枠が隠れてスクロールできる。`scrollIntoView`・ページ内検索・支援技術で外枠ごとずれ、本文の下端が
 * 画面の外に切れる（RAG の品質評価。#2064。#1116 と同じ型）。どちらも 1px 以内であること。外枠が無ければ 0。
 */
export async function measureShellHiddenOverflow(page: EvaluatePage): Promise<ShellHiddenOverflow> {
  return page.evaluate(() => {
    const shell = document.getElementById("pr-main")?.parentElement;
    if (!shell) return { vertical: 0, horizontal: 0 };
    return {
      vertical: Math.max(0, shell.scrollHeight - shell.clientHeight, shell.scrollTop),
      horizontal: Math.max(0, shell.scrollWidth - shell.clientWidth, shell.scrollLeft),
    };
  }, undefined);
}

export interface ClippedPlaceholder {
  /** 欄の名前（label・aria-label・id）。 */
  name: string;
  placeholder: string;
  /** 文字の幅（px）。 */
  textWidth: number;
  /** 欄の内側の幅（px）。 */
  availableWidth: number;
  kind: "input" | "textarea" | "select";
}

export interface FieldScanOptions {
  /** 調べる範囲の CSS セレクタ（既定 `main`。無ければ body）。 */
  root?: string;
  /** 許容（px。既定 1）。 */
  tolerance?: number;
}

/**
 * placeholder が欄に収まらない入力欄・選択欄を返す（#1891）。
 *
 * - `input[placeholder]` / `textarea[placeholder]`（値が空で、見えているもの）: 欄の computed の font で
 *   canvas の measureText を測り、欄の内側の幅（padding・後置のボタンの分を除く）と比べる。textarea は折り返した行の
 *   高さで比べる。
 * - `SelectField` の未選択の表示（`[data-field-placeholder]`）: 1 行で切り詰めているので scrollWidth > clientWidth。
 * 収まらない placeholder は短い例にし、説明は欄の下の補足（helper）に移す（README §4「placeholder」）。
 */
export async function findClippedPlaceholders(
  page: EvaluatePage,
  options: FieldScanOptions = {}
): Promise<ClippedPlaceholder[]> {
  return page.evaluate(
    ({ root, tolerance }) => {
      const scope = document.querySelector(root) ?? document.body;
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      const px = (value: string) => {
        const parsed = Number.parseFloat(value);
        return Number.isFinite(parsed) ? parsed : 0;
      };
      const visible = (element: Element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
      };
      const nameOf = (element: HTMLElement) => {
        const labelled = element.getAttribute("aria-labelledby");
        const fromLabelledBy = labelled
          ? labelled
              .split(/\s+/)
              .map((id) => document.getElementById(id)?.textContent?.trim() ?? "")
              .join(" ")
              .trim()
          : "";
        const control = element as HTMLInputElement;
        const fromLabel = control.labels?.[0]?.textContent?.trim() ?? "";
        return element.getAttribute("aria-label") || fromLabelledBy || fromLabel || element.id || element.tagName.toLowerCase();
      };
      const results: Array<{
        name: string;
        placeholder: string;
        textWidth: number;
        availableWidth: number;
        kind: "input" | "textarea" | "select";
      }> = [];

      const fields = scope.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(
        "input[placeholder]:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea[placeholder]"
      );
      for (const field of fields) {
        if (!field.placeholder || field.value || !visible(field) || !context) continue;
        const style = getComputedStyle(field);
        context.font = style.font || `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const availableWidth = field.clientWidth - px(style.paddingLeft) - px(style.paddingRight);
        if (availableWidth <= 0) continue;
        const lines = field.placeholder.split("\n").map((line) => context.measureText(line).width);
        const textWidth = Math.max(...lines);
        if (field instanceof HTMLTextAreaElement) {
          const lineHeight = px(style.lineHeight) || px(style.fontSize) * 1.5;
          const needed = lines.reduce((sum, width) => sum + Math.max(1, Math.ceil((width - 1) / availableWidth)), 0);
          const availableHeight = field.clientHeight - px(style.paddingTop) - px(style.paddingBottom);
          if (needed * lineHeight > availableHeight + tolerance) {
            results.push({
              name: nameOf(field),
              placeholder: field.placeholder,
              textWidth: Math.round(textWidth),
              availableWidth: Math.round(availableWidth),
              kind: "textarea",
            });
          }
        } else if (textWidth > availableWidth + tolerance) {
          results.push({
            name: nameOf(field),
            placeholder: field.placeholder,
            textWidth: Math.round(textWidth),
            availableWidth: Math.round(availableWidth),
            kind: "input",
          });
        }
      }

      for (const span of scope.querySelectorAll<HTMLElement>("[data-field-placeholder]")) {
        if (!visible(span) || !span.textContent) continue;
        if (span.scrollWidth > span.clientWidth + tolerance) {
          const button = span.closest<HTMLElement>("button") ?? span;
          results.push({
            name: nameOf(button),
            placeholder: span.textContent,
            textWidth: span.scrollWidth,
            availableWidth: span.clientWidth,
            kind: "select",
          });
        }
      }
      return results;
    },
    { root: options.root ?? "main", tolerance: options.tolerance ?? 1 }
  );
}

export interface OffScaleField {
  name: string;
  /** 欄の幅（px）。 */
  width: number;
  /** 欄を入れている箱（欄より広い最初の祖先。セルからはみ出した欄はセル）の内側の幅（px）。 */
  containerWidth: number;
  /** `exceeds-cell`: `FormGrid` のセル（列の幅）からはみ出した欄（#1891 段 5）。省略は段の外の幅。 */
  reason?: "exceeds-cell";
}

/**
 * 幅の段（xs 8rem / sm 12rem / md 20rem / lg 28rem）にも、置き場所の幅いっぱい（`full`）にも当たらない入力欄・
 * 選択欄と、`FormGrid` のセルからはみ出した欄を返す（任意の検査。#1891）。段の幅は sm（640px）以上だけで効くので、
 * 375px では使わない。
 *
 * - 欄の幅 = 入力欄の枠（TextField は後置のボタンを含む枠、SelectField はボタン、textarea はそのもの）。
 * - 置き場所 = 欄より広い最初の祖先。欄の幅がその内側の幅と同じなら `full`、`FormGrid`（`[data-form-grid]`）の幅と
 *   同じなら `full`（フォームの最大幅まで）。
 * - 製品の画面固有の grid の列が `lg`（28rem）より狭く、欄がその列の幅いっぱいなら、段の幅が列で止められた
 *   （`max-w-full`）とみなして返さない（#1891 段 5）。`FormGrid` の列で止められた欄は返す（2 列は列が 28rem 以上の
 *   ときだけなので、止められたら `FormGrid` の誤り）。
 * - grid のセルいっぱいに伸びた欄（2 列のフォームの 770px の名前の欄など）は、段でも置き場所の幅でもないので返す。
 * - 段の幅でも、`FormGrid` のセル（列の幅）からはみ出した欄は `reason: "exceeds-cell"` で返す。
 * - 表の中・ページ送り・`[data-field-width-exempt]` の中は除く。
 */
export async function findFieldsOffWidthScale(
  page: EvaluatePage,
  options: FieldScanOptions = {}
): Promise<OffScaleField[]> {
  return page.evaluate(
    ({ root, tolerance }) => {
      const scope = document.querySelector(root) ?? document.body;
      const rootFont = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 14;
      const steps = [8, 12, 20, 28].map((rem) => rem * rootFont);
      const largestStep = steps[steps.length - 1];
      const near = (a: number, b: number) => Math.abs(a - b) <= tolerance;
      const contentWidth = (element: HTMLElement) => {
        const style = getComputedStyle(element);
        return element.clientWidth - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight);
      };
      const results: Array<{ name: string; width: number; containerWidth: number; reason?: "exceeds-cell" }> = [];
      const fields = scope.querySelectorAll<HTMLElement>(
        "input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]):not([type=color]), textarea, button[role=combobox]"
      );
      for (const field of fields) {
        if (field.closest("table, [data-pagination], [data-field-width-exempt], [role=listbox], [role=dialog] [data-searchable-panel]")) {
          continue;
        }
        const box =
          field instanceof HTMLInputElement && field.parentElement?.classList.contains("relative")
            ? field.parentElement
            : field;
        const rect = box.getBoundingClientRect();
        const width = rect.width;
        if (width <= 0) continue;
        const name =
          field.getAttribute("aria-label") ||
          (field as HTMLInputElement).labels?.[0]?.textContent?.trim() ||
          field.id ||
          field.tagName.toLowerCase();
        const formGrid = box.closest<HTMLElement>("[data-form-grid]");
        const tracksOf = (grid: HTMLElement) =>
          getComputedStyle(grid)
            .gridTemplateColumns.split(" ")
            .map((value) => Number.parseFloat(value))
            .filter((value) => Number.isFinite(value) && value > 0);
        // FormGrid のセル（grid の直下の要素）からはみ出した欄。2 列にまたがるセル（col-span-full）は grid の幅まで。
        const formGridInner = formGrid?.firstElementChild instanceof HTMLElement ? formGrid.firstElementChild : null;
        let cell: HTMLElement | null = box;
        while (formGridInner && cell && cell.parentElement !== formGridInner) cell = cell.parentElement;
        if (formGridInner && cell) {
          const cellRect = cell.getBoundingClientRect();
          const columnWidth = Math.max(...tracksOf(formGridInner), 0);
          const spansAll = cellRect.width > columnWidth + tolerance;
          const limit = spansAll ? contentWidth(formGridInner) : columnWidth;
          if (
            rect.right > cellRect.right + tolerance ||
            rect.left < cellRect.left - tolerance ||
            (limit > 0 && width > limit + tolerance)
          ) {
            results.push({ name, width: Math.round(width), containerWidth: Math.round(limit), reason: "exceeds-cell" });
            continue;
          }
        }
        // 製品の画面固有の grid で、段の幅が列で止められた欄（列が lg より狭く、欄が列の幅いっぱい。`max-w-full`）は
        // 段の内とみなす。`FormGrid` の列では止めない（2 列は列が lg 以上のときだけなので、止められたら FormGrid の誤り）。
        // 列が lg より広いのに列いっぱいの欄（2 列のフォームの 770px の名前の欄）は、下の検査で返す。
        let gridAncestor: HTMLElement | null = box.parentElement;
        while (gridAncestor && gridAncestor !== scope && !getComputedStyle(gridAncestor).display.includes("grid")) {
          gridAncestor = gridAncestor.parentElement;
        }
        if (
          gridAncestor &&
          gridAncestor !== formGridInner &&
          getComputedStyle(gridAncestor).display.includes("grid") &&
          tracksOf(gridAncestor).some((track) => track < largestStep - tolerance && near(width, track))
        ) {
          continue;
        }
        if (steps.some((step) => near(width, step))) continue;
        if (formGrid && near(width, contentWidth(formGrid))) continue;
        // 入力欄と操作の行（FieldActionRow）の `full` の欄は、行の幅から操作の幅を除いた残りに伸びる。
        // 行（の外枠）が置き場所の幅いっぱいなら `full` とみなす（#1891 段 4）。
        const actionRow = box.closest<HTMLElement>("[data-field-action-row]");
        const rowContainer = actionRow?.parentElement?.parentElement;
        if (actionRow && rowContainer && actionRow.getBoundingClientRect().width >= contentWidth(rowContainer) - tolerance) {
          continue;
        }
        let container: HTMLElement | null = box.parentElement;
        while (container && container.getBoundingClientRect().width <= width + tolerance) {
          container = container.parentElement;
        }
        if (!container) continue;
        const containerWidth = contentWidth(container);
        if (width >= containerWidth - tolerance) continue;
        results.push({ name, width: Math.round(width), containerWidth: Math.round(containerWidth) });
      }
      return results;
    },
    { root: options.root ?? "main", tolerance: options.tolerance ?? 1 }
  );
}

export interface NarrowFieldText {
  /** 欄の名前（label・aria-label・id）。 */
  name: string;
  /** 補足・エラーの文の id。 */
  textId: string;
  /** 文の幅（px）。 */
  width: number;
  /**
   * 期待する最小の幅（px）。置き場所が横に並べる行（flex）なら min(20rem, 外枠の親の内側の幅)、それ以外（block・grid の
   * セル）なら min(外枠の親の内側の幅, 60rem)。
   */
  expectedMin: number;
}

/**
 * 欄の補足（helper）・エラーの文が、置き場所の幅より狭く折り返している欄を返す（#1891 段 5・#2063）。`xs`（8rem）・
 * `sm`（12rem）の欄の補足が欄の幅や 20rem で折り返し、右に余白があるのに 2〜6 行になるのを検出する。
 *
 * - 対象の文 = 入力欄・選択欄の `aria-describedby` が指す要素のうち、欄と同じ外枠の中にあるもの（外枠の直下の行で測る）。
 *   欄の外の説明（`describedBy` で結んだ別の場所の文）は対象にしない。
 * - 置き場所の幅 = 外枠の親の内側の幅（`FormGrid` のセルなら列の幅）。文はその幅（1 行はフォームの最大幅 60rem まで）で
 *   折り返す。置き場所が横に並べる行（flex・inline-flex）なら、外枠は中身の幅なので、読みやすい最小の幅
 *   `min(20rem, 置き場所の幅)` より狭くないことだけを見る。
 * - 表の中・ページ送り・`[data-field-width-exempt]` の中は除く。sm（640px）未満は欄が全幅なので、どの幅でも使える。
 */
export async function findNarrowFieldTexts(
  page: EvaluatePage,
  options: FieldScanOptions = {}
): Promise<NarrowFieldText[]> {
  return page.evaluate(
    ({ root, tolerance }) => {
      const scope = document.querySelector(root) ?? document.body;
      const rootFont = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 14;
      const readable = 20 * rootFont;
      const formMax = 60 * rootFont;
      const contentWidth = (element: HTMLElement) => {
        const style = getComputedStyle(element);
        return element.clientWidth - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight);
      };
      // 外枠が grid の直下（外枠がセル）なら、そのセル（列。col-span なら複数の列と間）の幅。grid でなければ null。
      const gridAreaWidth = (box: HTMLElement, grid: HTMLElement): number | null => {
        const style = getComputedStyle(grid);
        if (!style.display.includes("grid")) return null;
        const tracks = style.gridTemplateColumns
          .split(" ")
          .map((value) => Number.parseFloat(value))
          .filter((value) => Number.isFinite(value) && value > 0);
        if (tracks.length === 0) return null;
        const gap = Number.parseFloat(style.columnGap) || 0;
        const gridLeft = grid.getBoundingClientRect().left + grid.clientLeft + Number.parseFloat(style.paddingLeft);
        const boxLeft = box.getBoundingClientRect().left;
        let start = 0;
        let position = gridLeft;
        while (start < tracks.length - 1 && boxLeft > position + tracks[start] + gap / 2) {
          position += tracks[start] + gap;
          start += 1;
        }
        const boxStyle = getComputedStyle(box);
        const end = boxStyle.gridColumnEnd;
        const spanMatch = /span (\d+)/.exec(end) ?? /span (\d+)/.exec(boxStyle.gridColumnStart);
        const span = end === "-1" ? tracks.length - start : spanMatch ? Number(spanMatch[1]) : 1;
        const used = tracks.slice(start, start + span);
        return used.reduce((sum, track) => sum + track, 0) + gap * Math.max(0, used.length - 1);
      };
      const results: Array<{ name: string; textId: string; width: number; expectedMin: number }> = [];
      const fields = scope.querySelectorAll<HTMLElement>("input[aria-describedby], textarea[aria-describedby], button[role=combobox][aria-describedby]");
      for (const field of fields) {
        if (field.closest("table, [data-pagination], [data-field-width-exempt], [role=listbox]")) continue;
        for (const textId of (field.getAttribute("aria-describedby") ?? "").split(/\s+/).filter(Boolean)) {
          // 文の行 = 外枠（欄を含む最初の祖先）の直下の要素（TextareaField の補足と文字数の行など、文を包む行ごと測る）。
          let block = document.getElementById(textId);
          for (let depth = 0; block?.parentElement && !block.parentElement.contains(field) && depth < 3; depth += 1) {
            block = block.parentElement;
          }
          const fieldBox = block?.parentElement;
          if (!block || !fieldBox || !fieldBox.contains(field) || !fieldBox.parentElement) continue;
          const width = block.getBoundingClientRect().width;
          if (width <= 0) continue;
          // 外枠の余白・枠線（ExecutionConfirmationField の枠など）の分は、文が使えない。
          const boxChrome = fieldBox.getBoundingClientRect().width - contentWidth(fieldBox);
          const placement = fieldBox.parentElement;
          const placementStyle = getComputedStyle(placement);
          const inRow = placementStyle.display.includes("flex");
          const available = (gridAreaWidth(fieldBox, placement) ?? contentWidth(placement)) - boxChrome;
          const expectedMin = inRow ? Math.min(readable, available) : Math.min(available, formMax);
          if (width >= expectedMin - tolerance) continue;
          const name =
            field.getAttribute("aria-label") ||
            (field as HTMLInputElement).labels?.[0]?.textContent?.trim() ||
            field.id ||
            field.tagName.toLowerCase();
          results.push({ name, textId, width: Math.round(width), expectedMin: Math.round(expectedMin) });
        }
      }
      return results;
    },
    { root: options.root ?? "main", tolerance: options.tolerance ?? 1 }
  );
}

/**
 * 共通のページ送りのページ番号の入力欄に `target` を入れて Enter で移る（#1891）。
 * 移ったことは呼び出し側が確かめる（例: `await expect(pagination).toContainText("3 / 5 ページ")`）。
 */
export async function jumpToPage(
  pagination: PaginationLocator,
  target: number,
  { inputName = "ページ番号", commit = "Enter" }: { inputName?: string; commit?: "Enter" | "Tab" } = {}
): Promise<void> {
  const input = pagination.getByRole("textbox", { name: inputName, exact: true });
  await input.fill(String(target));
  await input.press(commit);
}

/**
 * `FormGrid` を確かめる置き場所の幅（rem）。以前の 2 列の境（42rem）・その間・新しい境（57.5rem）の前後（#1891 段 5）。
 */
export const FORM_GRID_PROBE_WIDTHS_REM = [42, 50, 57, 58] as const;

/**
 * ページの `FormGrid`（`[data-form-grid]`）の最大幅を `rem` に狭め、置き場所が 42〜57rem の間の段組み・欄の幅・
 * 補足の折り返しを、画面の幅を変えずに確かめる（#1891 段 5）。`null` で元に戻す。
 */
export async function setFormGridMaxWidth(page: EvaluatePage, rem: number | null): Promise<void> {
  await page.evaluate((value) => {
    for (const grid of document.querySelectorAll<HTMLElement>("[data-form-grid]")) {
      grid.style.maxWidth = value === null ? "" : `${value}rem`;
    }
  }, rem);
}

export interface ToolbarControlBox {
  /** 部品の名前（aria-label・ラベル・文字・id）。 */
  name: string;
  /** 高さ（px）。 */
  height: number;
  /** 縦の中心（ページの上端からの px）。 */
  center: number;
}

export interface ToolbarMisalignment {
  /** ツールバーの `data-testid`（無ければ何番目か「#2」）。 */
  toolbar: string;
  /** 行の番号（上から 0 始まり）。 */
  row: number;
  /** 行の中の最も高い部品と最も低い部品の差（px）。 */
  heightDelta: number;
  /** 行の中の縦の中心の最大の差（px）。 */
  centerDelta: number;
  controls: ToolbarControlBox[];
}

/**
 * 一覧のツールバー（`[data-list-toolbar]`）の中で、同じ行に並ぶ操作部品（検索欄・選択欄・チップ・ボタン）の
 * 高さか縦の中心が `tolerance`（既定 1px）より大きくずれている行を返す（#1933）。
 *
 * - 部品: 入力欄（検索欄は枠の箱）、`button[role=combobox]`、ボタン（チップ・条件をクリア・操作）。検索欄の中の
 *   消去（×）・読み上げだけの要素・見えない要素は除く。
 * - 行: 縦の範囲が半分以上重なる部品をまとめる（折り返した 2 行目は別の行）。
 * - 規則: ツールバーの中の部品はすべて同じ高さ（md 36px・タッチ端末 44px）で、縦の中心がそろう
 *   （UX 契約 page-archetypes.md「一覧のツールバー」）。移行前の上のラベルの欄があると、中心がずれて返る。
 */
export async function findToolbarMisalignment(
  page: EvaluatePage,
  options: FieldScanOptions = {}
): Promise<ToolbarMisalignment[]> {
  return page.evaluate(
    ({ root, tolerance }) => {
      const scope = document.querySelector(root) ?? document.body;
      const results: Array<{
        toolbar: string;
        row: number;
        heightDelta: number;
        centerDelta: number;
        controls: Array<{ name: string; height: number; center: number }>;
      }> = [];
      const visible = (element: Element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width > 1 && rect.height > 1 && style.visibility !== "hidden" && style.display !== "none";
      };
      const nameOf = (element: HTMLElement) =>
        element.getAttribute("aria-label") ||
        (element as HTMLInputElement).labels?.[0]?.textContent?.trim() ||
        element.textContent?.trim().slice(0, 40) ||
        element.id ||
        element.tagName.toLowerCase();
      const toolbars = Array.from(scope.querySelectorAll<HTMLElement>("[data-list-toolbar]"));
      toolbars.forEach((toolbar, toolbarIndex) => {
        if (!visible(toolbar)) return;
        const controls: Array<{ element: HTMLElement; top: number; bottom: number }> = [];
        const candidates = toolbar.querySelectorAll<HTMLElement>(
          "input:not([type=hidden]):not([type=checkbox]):not([type=radio]), button, a[role=button]"
        );
        for (const element of candidates) {
          if (!visible(element)) continue;
          // 入力欄の中の消去（×）・後置のボタンは、入力欄の箱に含める（別の部品として数えない）。
          if (element.tagName === "BUTTON" && element.closest("[data-list-toolbar-search]")) continue;
          if (element.tagName !== "INPUT" && element.parentElement?.querySelector(":scope > input")) continue;
          let box: HTMLElement = element;
          if (element instanceof HTMLInputElement && element.parentElement?.classList.contains("relative")) {
            box = element.parentElement;
          }
          const rect = box.getBoundingClientRect();
          controls.push({ element: box, top: rect.top + window.scrollY, bottom: rect.bottom + window.scrollY });
        }
        controls.sort((a, b) => a.top - b.top);
        const rows: Array<typeof controls> = [];
        for (const control of controls) {
          const row = rows.find((candidate) =>
            candidate.some((other) => {
              const overlap = Math.min(other.bottom, control.bottom) - Math.max(other.top, control.top);
              const smaller = Math.min(other.bottom - other.top, control.bottom - control.top);
              return overlap >= smaller / 2;
            })
          );
          if (row) row.push(control);
          else rows.push([control]);
        }
        rows.forEach((row, rowIndex) => {
          if (row.length < 2) return;
          const heights = row.map((control) => control.bottom - control.top);
          const centers = row.map((control) => (control.top + control.bottom) / 2);
          const heightDelta = Math.max(...heights) - Math.min(...heights);
          const centerDelta = Math.max(...centers) - Math.min(...centers);
          if (heightDelta <= tolerance && centerDelta <= tolerance) return;
          results.push({
            toolbar: toolbar.dataset.testid || `#${toolbarIndex + 1}`,
            row: rowIndex,
            heightDelta: Math.round(heightDelta * 10) / 10,
            centerDelta: Math.round(centerDelta * 10) / 10,
            controls: row.map((control, index) => ({
              name: nameOf(control.element),
              height: Math.round(heights[index] * 10) / 10,
              center: Math.round(centers[index] * 10) / 10,
            })),
          });
        });
      });
      return results;
    },
    { root: options.root ?? "main", tolerance: options.tolerance ?? 1 }
  );
}

export interface ToolbarFieldWidthMismatch {
  /** ツールバーの `data-testid`（無ければ何番目か「#2」）。 */
  toolbar: string;
  /** 欄の名前。 */
  name: string;
  /** 欄の幅（px）。 */
  width: number;
  /** 縦に積んだ左の群れ（検索欄・絞り込み）の内側の幅（px）。 */
  expected: number;
}

/**
 * 縦に積んだ（置き場所が 28rem 未満の）一覧のツールバーで、左の群れの幅いっぱいでない欄（検索欄・選択・入力）を返す（#1933）。
 *
 * - 絞り込みの欄の幅の段は、画面ではなくツールバーの幅（container）で切り替える。狭い分割のペインでは、画面が広くても
 *   検索欄も絞り込みの欄も全幅にそろう（以前は検索欄だけ全幅で、隣の欄は画面の `sm:` で段の幅のままだった）。
 * - 1 行に並べている（広い）ツールバーは対象外（欄の幅は段。`findFieldsOffWidthScale`）。
 * - チップ・ボタンは対象外（文字の幅）。
 */
export async function findToolbarFieldWidthMismatch(
  page: EvaluatePage,
  options: FieldScanOptions = {}
): Promise<ToolbarFieldWidthMismatch[]> {
  return page.evaluate(
    ({ root, tolerance }) => {
      const scope = document.querySelector(root) ?? document.body;
      const results: Array<{ toolbar: string; name: string; width: number; expected: number }> = [];
      const toolbars = Array.from(scope.querySelectorAll<HTMLElement>("[data-list-toolbar]"));
      toolbars.forEach((toolbar, toolbarIndex) => {
        const start = toolbar.querySelector<HTMLElement>("[data-list-toolbar-start]");
        if (!start || start.getBoundingClientRect().width <= 1) return;
        if (getComputedStyle(start).flexDirection !== "column") return;
        const style = getComputedStyle(start);
        const expected =
          start.getBoundingClientRect().width -
          Number.parseFloat(style.paddingLeft) -
          Number.parseFloat(style.paddingRight);
        const fields = start.querySelectorAll<HTMLElement>(
          "input:not([type=hidden]):not([type=checkbox]):not([type=radio]), button[role=combobox], button[aria-haspopup=dialog][aria-labelledby]"
        );
        for (const field of fields) {
          const rect = field.getBoundingClientRect();
          if (rect.width <= 1 || rect.height <= 1) continue;
          const box =
            field instanceof HTMLInputElement && field.parentElement?.classList.contains("relative")
              ? field.parentElement
              : field;
          const width = box.getBoundingClientRect().width;
          if (Math.abs(width - expected) <= tolerance) continue;
          const name =
            field.getAttribute("aria-label") ||
            (field as HTMLInputElement).labels?.[0]?.textContent?.trim() ||
            field.id ||
            field.tagName.toLowerCase();
          results.push({
            toolbar: toolbar.dataset.testid || `#${toolbarIndex + 1}`,
            name,
            width: Math.round(width),
            expected: Math.round(expected),
          });
        }
      });
      return results;
    },
    { root: options.root ?? "main", tolerance: options.tolerance ?? 1 }
  );
}
