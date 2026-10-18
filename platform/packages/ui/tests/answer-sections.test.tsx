// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerSections, MarkdownInlineText } from "../src";

// #2328: 回答の本文の節と根拠の行（RAG のチャット・RAG 検索・Agent のチャットで共通）。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("AnswerSections", () => {
  it("段落・見出しつきの箇条書き・補足の行・根拠の行（押せる行と文字だけの行）を出す", () => {
    const onOpen = vi.fn();
    act(() =>
      root.render(
        <AnswerSections
          live
          blocks={[
            { kind: "paragraph", key: "p", content: "要約の段落。" },
            {
              kind: "section",
              key: "s",
              title: "資料により確認できた事項",
              entries: [
                { key: "e0", content: "項目 1", citations: [{ key: "c1", label: "根拠：文書 A p.3", ariaLabel: "根拠：文書 A p.3 を表示", onOpen }] },
                { key: "e1", content: "項目 2", citations: [{ key: "c2", label: "根拠：文書 B" }] },
                { key: "e2", content: "補足", muted: true },
              ],
            },
            { kind: "content", key: "c", content: <div data-testid="table">表</div>, citations: [{ key: "c3", label: "根拠：文書 C" }] },
          ]}
        />
      )
    );
    const container = host.querySelector('[data-testid="answer-text"]')!;
    expect(container.getAttribute("aria-live")).toBe("polite");
    expect(container.querySelector("p")?.textContent).toBe("要約の段落。");
    expect(container.querySelector("h4")?.textContent).toBe("資料により確認できた事項");
    const items = container.querySelectorAll("li");
    expect(items).toHaveLength(3);
    expect(items[2].className).toContain("list-none");
    const link = container.querySelector('[data-testid="answer-citation-link"]') as HTMLButtonElement;
    expect(link.getAttribute("aria-label")).toBe("根拠：文書 A p.3 を表示");
    act(() => link.click());
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(Array.from(container.querySelectorAll('[data-testid="answer-citation-text"]')).map((node) => node.textContent)).toEqual([
      "根拠：文書 B",
      "根拠：文書 C",
    ]);
    expect(container.querySelector('[data-testid="table"]')).not.toBeNull();
  });

  it("MarkdownInlineText は段落の中の太字とリンクだけを描き、改行を保つ", () => {
    act(() => root.render(<p><MarkdownInlineText text={"**太字** と [文書](https://example.com)\n2 行目"} /></p>));
    const paragraph = host.querySelector("p")!;
    expect(paragraph.querySelector("strong")?.textContent).toBe("太字");
    expect(paragraph.querySelector("a")?.getAttribute("href")).toBe("https://example.com");
    expect(paragraph.querySelectorAll("br")).toHaveLength(1);
  });
});
