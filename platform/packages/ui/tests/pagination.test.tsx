import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_PAGE_SIZE,
  Pagination,
  offsetForPage,
  offsetPagination,
  usePagination,
  type UsePaginationOptions,
} from "../src/components/data/pagination";

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
