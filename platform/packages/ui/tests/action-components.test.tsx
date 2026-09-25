import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  ActionResultRegion,
  BulkSelectionActions,
  ClearActionButton,
  ContentActionBar,
} from "../src";

const noop = () => {};

describe("ContentActionBar", () => {
  it("操作は aria-label 付きの group にまとめ、情報がなければ右寄せ全幅にする", () => {
    const html = renderToStaticMarkup(
      <ContentActionBar ariaLabel="SQL の操作">
        <button type="button">コピー</button>
      </ContentActionBar>
    );
    expect(html).toContain('role="group"');
    expect(html).toContain('aria-label="SQL の操作"');
    expect(html).toContain("justify-end");
    expect(html).toContain("w-full");
  });

  it("title / description / meta を左の情報欄に描き、操作は ml-auto で右に寄せる", () => {
    const html = renderToStaticMarkup(
      <ContentActionBar ariaLabel="結果の操作" title="結果" description="説明" meta="3 件">
        <button type="button">出力</button>
      </ContentActionBar>
    );
    expect(html).toContain(">結果<");
    expect(html).toContain(">説明<");
    expect(html).toContain(">3 件<");
    expect(html).toContain("ml-auto");
  });
});

describe("BulkSelectionActions", () => {
  it("全選択は secondary、全解除は ghost で、スコープ付きの aria-label と test id を持つ", () => {
    const html = renderToStaticMarkup(
      <BulkSelectionActions
        selectLabel="すべて選択"
        clearLabel="選択をすべて解除"
        selectAriaLabel="表 をすべて選択"
        clearAriaLabel="表 の選択をすべて解除"
        onSelectAll={noop}
        onClearAll={noop}
        clearDisabled
        dataTestId="bulk"
      />
    );
    expect(html).toContain('aria-label="表 をすべて選択"');
    expect(html).toContain('aria-label="表 の選択をすべて解除"');
    expect(html).toContain('data-testid="bulk-select"');
    expect(html).toContain('data-testid="bulk-clear"');
    // 全解除だけ disabled
    expect(html.match(/disabled=""/g)).toHaveLength(1);
  });

  it("busy のときは両方 disabled にして aria-busy を立てる", () => {
    const html = renderToStaticMarkup(
      <BulkSelectionActions selectLabel="a" clearLabel="b" onSelectAll={noop} onClearAll={noop} busy />
    );
    expect(html).toContain('aria-busy="true"');
    expect(html.match(/disabled=""/g)).toHaveLength(2);
  });
});

describe("ClearActionButton", () => {
  it("label を必須の表示名にし、ariaLabel がなければ label を aria-label に使う", () => {
    const html = renderToStaticMarkup(<ClearActionButton label="SQL 入力・結果をリセット" onClick={noop} />);
    expect(html).toContain('aria-label="SQL 入力・結果をリセット"');
    expect(html).toContain('type="button"');
    expect(html).toContain("whitespace-nowrap");
  });
});

describe("ActionResultRegion", () => {
  it("結果もエラーもなければ何も描かない", () => {
    expect(renderToStaticMarkup(<ActionResultRegion loading={false} operationKey="a" />)).toBe("");
  });

  it("エラーは danger の Banner で描き、結果より優先する", () => {
    const html = renderToStaticMarkup(
      <ActionResultRegion loading={false} operationKey="a" errorMessage="失敗しました。" testId="run">
        <p>結果</p>
      </ActionResultRegion>
    );
    expect(html).toContain('data-testid="run-error"');
    expect(html).toContain("失敗しました。");
    expect(html).not.toContain("<p>結果</p>");
  });

  it("結果を描き、読込中の aria-busy は立てない", () => {
    const html = renderToStaticMarkup(
      <ActionResultRegion loading={false} operationKey="a" testId="run">
        <p>結果</p>
      </ActionResultRegion>
    );
    expect(html).toContain('data-testid="run-region"');
    expect(html).toContain("<p>結果</p>");
    expect(html).not.toContain("aria-busy");
  });
});
