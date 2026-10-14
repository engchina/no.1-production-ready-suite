/**
 * 3 製品の e2e が共通に使う、一覧・入力欄の見た目の検査（#1891）。
 *
 * - `measureHorizontalOverflow`: ページ（documentElement と main）の横あふれ（px）
 * - `findClippedPlaceholders`: placeholder の文字の幅が欄の内側の幅を超える欄（切れて読めない placeholder）
 * - `findFieldsOffWidthScale`: 幅の段（xs 8rem / sm 12rem / md 20rem / lg 28rem）にも親の幅いっぱいにも当たらない欄（任意）
 * - `jumpToPage`: ページ送り（共通の `Pagination`）のページ番号の入力で移る
 * - `findToolbarMisalignment`: 一覧のツールバー（`ListToolbar`）の同じ行の操作部品の高さ・縦の中心のずれ（#1933）
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
  /** 欄を入れている箱（欄より広い最初の祖先）の内側の幅（px）。 */
  containerWidth: number;
}

/**
 * 幅の段（xs 8rem / sm 12rem / md 20rem / lg 28rem）にも、置き場所の幅いっぱい（`full`）にも当たらない入力欄・
 * 選択欄を返す（任意の検査。#1891）。段の幅は sm（640px）以上だけで効くので、375px では使わない。
 *
 * - 欄の幅 = 入力欄の枠（TextField は後置のボタンを含む枠、SelectField はボタン、textarea はそのもの）。
 * - 置き場所 = 欄より広い最初の祖先。欄の幅がその内側の幅と同じなら `full`、`FormGrid`（`[data-form-grid]`）の幅と
 *   同じなら `full`（フォームの最大幅まで）。
 * - grid のセルいっぱいに伸びた欄（2 列のフォームの 770px の名前の欄など）は、段でも置き場所の幅でもないので返す。
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
      const near = (a: number, b: number) => Math.abs(a - b) <= tolerance;
      const contentWidth = (element: HTMLElement) => {
        const style = getComputedStyle(element);
        return element.clientWidth - Number.parseFloat(style.paddingLeft) - Number.parseFloat(style.paddingRight);
      };
      const results: Array<{ name: string; width: number; containerWidth: number }> = [];
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
        const width = box.getBoundingClientRect().width;
        if (width <= 0) continue;
        if (steps.some((step) => near(width, step))) continue;
        const formGrid = box.closest<HTMLElement>("[data-form-grid]");
        if (formGrid && near(width, contentWidth(formGrid))) continue;
        let container: HTMLElement | null = box.parentElement;
        while (container && container.getBoundingClientRect().width <= width + tolerance) {
          container = container.parentElement;
        }
        if (!container) continue;
        const containerWidth = contentWidth(container);
        if (width >= containerWidth - tolerance) continue;
        const name =
          field.getAttribute("aria-label") ||
          (field as HTMLInputElement).labels?.[0]?.textContent?.trim() ||
          field.id ||
          field.tagName.toLowerCase();
        results.push({ name, width: Math.round(width), containerWidth: Math.round(containerWidth) });
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
