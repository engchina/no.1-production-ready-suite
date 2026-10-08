import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  CursorPagination,
  DEFAULT_PAGE_SIZE,
  DEFAULT_PAGINATION_LABELS,
  OffsetPagination,
  Pagination,
  cursorPagination,
  lastPageOffset,
  offsetForPage,
  offsetPagination,
  outOfRangeOffset,
  paginationRange,
  useCursorPages,
  usePagination,
  type UsePaginationOptions,
} from "../src/components/data/pagination";
import { PageSizeSelect } from "../src/components/data/page-size-select";

const items = Array.from({ length: 25 }, (_, index) => `item-${index + 1}`);

/** フックの初回描画の結果を文字列にして確かめる（effect は SSR では走らない）。 */
function probe(list: readonly string[], pageSize?: number, options?: UsePaginationOptions) {
  function Probe() {
    const { page, totalPages, pageItems, range } = usePagination(list, pageSize, options);
    return <output>{JSON.stringify({ page, totalPages, pageItems, range })}</output>;
  }
  const html = renderToStaticMarkup(<Probe />);
  return JSON.parse(html.replace(/^<output>|<\/output>$/g, "").replace(/&quot;/g, '"')) as {
    page: number;
    totalPages: number;
    pageItems: string[];
    range: { start: number; end: number; total: number };
  };
}

describe("usePagination", () => {
  it("既定は 10 件/ページで 1 ページ目から", () => {
    expect(DEFAULT_PAGE_SIZE).toBe(10);
    const result = probe(items);
    expect(result.page).toBe(1);
    expect(result.totalPages).toBe(3);
    expect(result.pageItems).toHaveLength(10);
    expect(result.range).toEqual({ start: 1, end: 10, total: 25 });
  });

  it("制御式の page で保持したページを最初の描画から出す（作業状態の復元）", () => {
    const result = probe(items, undefined, { page: 3, onPageChange: () => undefined });
    expect(result.page).toBe(3);
    expect(result.pageItems).toEqual(["item-21", "item-22", "item-23", "item-24", "item-25"]);
    expect(result.range).toEqual({ start: 21, end: 25, total: 25 });
  });

  it("保持したページが範囲外なら表示だけ末尾のページに寄せる", () => {
    const result = probe(items.slice(0, 12), undefined, { page: 5, onPageChange: () => undefined });
    expect(result.page).toBe(2);
    expect(result.pageItems).toEqual(["item-11", "item-12"]);
  });

  it("空の一覧は 1 ページ・範囲 0 件", () => {
    const result = probe([], undefined, { page: 2, onPageChange: () => undefined });
    expect(result).toMatchObject({ page: 1, totalPages: 1, pageItems: [], range: { start: 0, end: 0, total: 0 } });
  });
});

describe("Pagination", () => {
  it("1 ページしかないときは出さない", () => {
    const html = renderToStaticMarkup(
      <Pagination page={1} totalPages={1} onPageChange={() => undefined} summary="1–3 / 3 件" prevLabel="前へ" nextLabel="次へ" />
    );
    expect(html).toBe("");
  });

  it("2 ページ以上で件数・前へ・次へを出し、端では押せない", () => {
    const html = renderToStaticMarkup(
      <Pagination page={1} totalPages={3} onPageChange={() => undefined} summary="1–10 / 25 件" prevLabel="前へ" nextLabel="次へ" pageIndicator="1 / 3 ページ" testId="p" />
    );
    expect(html).toContain('data-testid="p"');
    expect(html).toContain("1–10 / 25 件");
    expect(html).toContain("1 / 3 ページ");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*前へ/);
  });
});

describe("offsetPagination（サーバー側のページング）", () => {
  it("offset / limit / total を page / totalPages / range に直す", () => {
    expect(offsetPagination({ offset: 20, limit: 10, total: 25, count: 5 })).toEqual({
      page: 3,
      totalPages: 3,
      range: { start: 21, end: 25, total: 25 },
    });
    expect(offsetPagination({ offset: 0, limit: 10, total: 0, count: 0 })).toEqual({
      page: 1,
      totalPages: 1,
      range: { start: 0, end: 0, total: 0 },
    });
  });

  it("offsetForPage はページ番号を offset に戻す", () => {
    expect(offsetForPage(1, 10)).toBe(0);
    expect(offsetForPage(3, 10)).toBe(20);
    expect(offsetForPage(0, 10)).toBe(0);
  });
});

describe("Pagination の文言（labels と range。#1266）", () => {
  it("既定は日本語で、range から件数とページを作り、桁区切りを付ける", () => {
    const html = renderToStaticMarkup(
      <Pagination page={2} totalPages={120} range={{ start: 11, end: 20, total: 1200 }} onPageChange={() => undefined} />
    );
    expect(html).toContain("11 - 20 / 1,200 件");
    expect(html).toContain("2 / 120 ページ");
    expect(html).toContain("前へ");
    expect(html).toContain("次へ");
    expect(html).toContain('aria-label="ページ送り"');
    expect(DEFAULT_PAGINATION_LABELS.summary({ start: 0, end: 0, total: 0 })).toBe("0 - 0 / 0 件");
  });

  it("製品の i18n の文言で部分だけ上書きできる", () => {
    const html = renderToStaticMarkup(
      <Pagination
        page={1}
        totalPages={3}
        range={{ start: 1, end: 10, total: 25 }}
        onPageChange={() => undefined}
        labels={{ summary: ({ total }) => `${total}件中`, prev: "Prev", ariaLabel: "一覧のページ" }}
      />
    );
    expect(html).toContain("25件中");
    expect(html).toContain("Prev");
    expect(html).toContain("次へ");
    expect(html).toContain('aria-label="一覧のページ"');
  });
});

describe("paginationRange / lastPageOffset / outOfRangeOffset", () => {
  it("件数の範囲は count と total から決め、0 件は 0 - 0", () => {
    expect(paginationRange(3, 10, 5, 25)).toEqual({ start: 21, end: 25, total: 25 });
    expect(paginationRange(1, 10, 0, 0)).toEqual({ start: 0, end: 0, total: 0 });
    expect(paginationRange(2, 10, 0, 25)).toEqual({ start: 0, end: 0, total: 25 });
  });

  it("最後のページの offset と、範囲外のときに移る offset", () => {
    expect(lastPageOffset(0, 10)).toBe(0);
    expect(lastPageOffset(25, 10)).toBe(20);
    expect(lastPageOffset(20, 10)).toBe(10);
    expect(outOfRangeOffset({ offset: 0, total: 0, limit: 10 })).toBeNull();
    expect(outOfRangeOffset({ offset: 10, total: 25, limit: 10 })).toBeNull();
    expect(outOfRangeOffset({ offset: 30, total: 25, limit: 10 })).toBe(20);
    expect(outOfRangeOffset({ offset: 20, total: 20, limit: 10 })).toBe(10);
    expect(outOfRangeOffset({ offset: 10, total: 0, limit: 10 })).toBe(0);
  });
});

describe("OffsetPagination（サーバー側の一覧の直下）", () => {
  it("offset / limit / total から件数とページを出し、1 ページなら出さない", () => {
    const html = renderToStaticMarkup(
      <OffsetPagination offset={10} limit={10} total={25} count={10} onPageChange={() => undefined} testId="pager" />
    );
    expect(html).toContain("11 - 20 / 25 件");
    expect(html).toContain("2 / 3 ページ");
    expect(
      renderToStaticMarkup(<OffsetPagination offset={0} limit={10} total={5} count={5} onPageChange={() => undefined} />)
    ).toBe("");
  });
});

describe("cursorPagination / CursorPagination / useCursorPages", () => {
  it("depth と next_cursor からページ数を決め、total が遅れていても次のページを数える", () => {
    expect(cursorPagination({ depth: 0, limit: 10, total: 25, count: 10, hasNext: true })).toEqual({
      page: 1,
      totalPages: 3,
      range: { start: 1, end: 10, total: 25 },
    });
    expect(cursorPagination({ depth: 2, limit: 10, total: 5, count: 3, hasNext: true })).toEqual({
      page: 3,
      totalPages: 4,
      range: { start: 21, end: 23, total: 23 },
    });
  });

  it("CursorPagination は隣のページだけに移り、空文字の next_cursor は続き無しとして扱う", () => {
    const html = renderToStaticMarkup(
      <CursorPagination depth={1} total={30} count={10} nextCursor="c3" onPrevious={() => undefined} onNext={() => undefined} />
    );
    expect(html).toContain("11 - 20 / 30 件");
    expect(html).toContain("2 / 3 ページ");
    const last = renderToStaticMarkup(
      <CursorPagination depth={0} total={10} count={10} nextCursor="" onPrevious={() => undefined} onNext={() => undefined} />
    );
    expect(last).toBe("");
  });

  it("useCursorPages は最初のページでカーソル無し・前へ戻れない", () => {
    function Probe() {
      const pages = useCursorPages();
      return <output>{JSON.stringify({ cursor: pages.cursor, depth: pages.depth, page: pages.page, canGoPrevious: pages.canGoPrevious })}</output>;
    }
    expect(renderToStaticMarkup(<Probe />)).toContain(
      JSON.stringify({ cursor: null, depth: 0, page: 1, canGoPrevious: false }).replace(/"/g, "&quot;")
    );
  });
});

describe("PageSizeSelect", () => {
  it("既定の選択肢と文言で、選択肢に無い値も出す", () => {
    const html = renderToStaticMarkup(<PageSizeSelect id="size" value={25} onValueChange={() => undefined} />);
    // 選択肢は開いたときだけ描くので、静的な出力ではラベルと選んだ値（選択肢に無い 25 も）を確かめる。
    expect(html).toContain("1 ページの件数");
    expect(html).toContain("25 件");
    expect(html).toContain('data-value="25"');
  });
});
