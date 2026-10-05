// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ChatResultTable,
  chatResultSummaryText,
  isNumericResultColumn,
  resultRowsToCsv,
  type ChatResultTableColumn,
} from "../src/components/data/chat-result-table";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const columns: ChatResultTableColumn[] = [
  { name: "ID" },
  { name: "NAME" },
  { name: "AMOUNT", type: "number" },
  { name: "NOTE" },
];

function rows(count: number) {
  return Array.from({ length: count }, (_, index) => [
    index + 1,
    `名前 ${index + 1}`,
    (index + 1) * 100,
    index === 0 ? null : "x".repeat(300),
  ]);
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

const $ = (selector: string) => document.querySelector<HTMLElement>(selector);

describe("chatResultSummaryText", () => {
  it("行数・列数・所要時間を 1 行にし、打ち切り・総件数・0 行を区別する", () => {
    expect(chatResultSummaryText({ rowCount: 12, columnCount: 5, elapsedMs: 800 })).toBe("12 行・5 列・0.8 秒");
    expect(chatResultSummaryText({ rowCount: 1000, columnCount: 8, truncated: true, elapsedMs: 1200 })).toBe(
      "先頭の 1,000 行を取得しました（さらに行があります）・8 列・1.2 秒"
    );
    expect(chatResultSummaryText({ rowCount: 1000, columnCount: 8, truncated: true, totalRowCount: 1234 })).toBe(
      "先頭の 1,000 行を取得しました（全 1,234 行）・8 列"
    );
    expect(chatResultSummaryText({ rowCount: 0, columnCount: 3, elapsedMs: 300 })).toBe(
      "該当する行はありません・3 列・0.3 秒"
    );
  });
});

describe("isNumericResultColumn / resultRowsToCsv", () => {
  it("型の指定、または NULL を除く値がすべて数値の列を右寄せの対象にする", () => {
    const data = [
      [1, "1", null],
      [null, "x", null],
    ];
    expect(isNumericResultColumn(data, 0)).toBe(true);
    expect(isNumericResultColumn(data, 1)).toBe(false);
    expect(isNumericResultColumn(data, 2)).toBe(false);
    expect(isNumericResultColumn(data, 1, "NUMBER")).toBe(true);
  });

  it("CSV は BOM・CRLF・RFC 4180 のエスケープ・式の無害化で、NULL は空の欄", () => {
    const csv = resultRowsToCsv(
      [{ name: "A" }, { name: "B" }, { name: "C" }],
      [
        ['x, "y"', "=1+1", null],
        ["改行\nあり", -5, { k: 1 }],
      ]
    );
    expect(csv.startsWith("\uFEFF")).toBe(true);
    expect(csv.slice(1)).toBe(['A,B,C', '"x, ""y""",\'=1+1,', '"改行\nあり",-5,"{""k"":1}"', ""].join("\r\n"));
  });
});

describe("ChatResultTable", () => {
  it("要約・先頭の行のプレビュー（表頭固定・表の中のスクロール）・NULL・右寄せ・省略を出す", () => {
    act(() =>
      root.render(<ChatResultTable columns={columns} rows={rows(60)} elapsedMs={800} testId="result" />)
    );
    expect($('[data-testid="result-summary"]')?.textContent).toBe("60 行・4 列・0.8 秒");
    const table = $('[data-testid="result-table"]')!;
    expect(table.querySelectorAll("tbody tr")).toHaveLength(50);
    expect(table.querySelector("thead")?.className).toContain("sticky");
    expect($('[data-testid="result-preview-note"]')?.textContent).toContain("先頭の 50 行");
    const firstRow = table.querySelector("tbody tr")!;
    expect(firstRow.querySelectorAll("td")[3].textContent).toBe("NULL");
    expect(firstRow.querySelectorAll("td")[3].querySelector("span")?.className).toContain("italic");
    expect(firstRow.querySelectorAll("td")[2].className).toContain("text-right");
    const longCell = table.querySelectorAll("tbody tr")[1].querySelectorAll("td")[3].querySelector("span")!;
    expect(longCell.className).toContain("truncate");
    expect(longCell.getAttribute("title")).toBe("x".repeat(300));
    // 打ち切っていないときは案内を出さない。
    expect($('[data-testid="result-truncated"]')).toBeNull();
  });

  it("打ち切ったら要約と案内で明示し、全件の導線を出す", () => {
    act(() =>
      root.render(
        <ChatResultTable
          columns={columns}
          rows={rows(1000)}
          truncated
          rowLimit={1000}
          cellsTruncated
          maxCellChars={2000}
          fullResult={{ href: "/direct-sql", label: "SELECT SQL を実行で開く", hint: "すべての行は別の画面で。" }}
          testId="result"
        />
      )
    );
    expect($('[data-testid="result-summary"]')?.textContent).toBe(
      "先頭の 1,000 行を取得しました（さらに行があります）・4 列"
    );
    const notice = $('[data-testid="result-truncated"]')!;
    expect(notice.textContent).toContain("1 回に取得するのは先頭の 1,000 行までです。表示と CSV は取得した行だけです。");
    expect(notice.textContent).toContain("すべての行は別の画面で。");
    expect(notice.querySelector('a[href="/direct-sql"]')?.textContent).toContain("SELECT SQL を実行で開く");
    expect($('[data-testid="result-cells-truncated"]')?.textContent).toContain("2,000 文字");
  });

  it("0 行は要約だけで、表・すべての行・CSV を出さない", () => {
    act(() => root.render(<ChatResultTable columns={columns} rows={[]} elapsedMs={300} testId="result" />));
    expect($('[data-testid="result-summary"]')?.textContent).toBe("該当する行はありません・4 列・0.3 秒");
    expect($('[data-testid="result-table"]')).toBeNull();
    expect($('[data-testid="result-view-all"]')).toBeNull();
    expect($('[data-testid="result-csv"]')).toBeNull();
  });

  it("「すべての行を見る」は広いシートでページを送り、CSV は取得したすべての行を渡す", () => {
    const onDownloadCsv = vi.fn();
    const onCsvDownloaded = vi.fn();
    act(() =>
      root.render(
        <ChatResultTable
          columns={columns}
          rows={rows(60)}
          csvFilename="out.csv"
          onDownloadCsv={onDownloadCsv}
          onCsvDownloaded={onCsvDownloaded}
          testId="result"
        />
      )
    );
    const sheet = $('[data-testid="result-sheet"]')!;
    expect(sheet.hasAttribute("inert")).toBe(true);
    // 閉じている間は全行の表を描かない。
    expect($('[data-testid="result-all-table"]')).toBeNull();
    act(() => $('[data-testid="result-view-all"]')!.click());
    expect(sheet.hasAttribute("inert")).toBe(false);
    expect(sheet.getAttribute("role")).toBe("dialog");
    expect(sheet.className).toContain("sm:w-[64rem]");
    expect($('[data-testid="result-all-table"]')!.querySelectorAll("tbody tr")).toHaveLength(10);
    expect($('[data-testid="result-all-pagination"]')?.textContent).toContain("1-10 / 60 件");
    // 全行の表は折り返して全文を出す。
    const fullCell = $('[data-testid="result-all-table"]')!.querySelectorAll("tbody tr")[1].querySelectorAll("td")[3];
    expect(fullCell.querySelector("span")?.className).toContain("whitespace-pre-wrap");
    expect(fullCell.querySelector("span")?.className).toContain("line-clamp-6");
    expect(fullCell.querySelector("span")?.getAttribute("title")).toBe("x".repeat(300));

    act(() => $('[data-testid="result-csv"]')!.click());
    expect(onDownloadCsv).toHaveBeenCalledTimes(1);
    const [csv, filename] = onDownloadCsv.mock.calls[0];
    expect(filename).toBe("out.csv");
    expect((csv as string).trimEnd().split("\r\n")).toHaveLength(61);
    expect(onCsvDownloaded).toHaveBeenCalledTimes(1);
  });

  it("文言は製品から上書きできる", () => {
    act(() =>
      root.render(
        <ChatResultTable
          columns={columns}
          rows={rows(2)}
          labels={{ rows: (count) => `${count} rows`, columns: (count) => `${count} cols`, separator: " / " }}
          testId="result"
        />
      )
    );
    expect($('[data-testid="result-summary"]')?.textContent).toBe("2 rows / 4 cols");
  });
});
