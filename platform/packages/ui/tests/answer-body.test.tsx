// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AnswerBody, AnswerSections } from "../src";

// #4734: 回答の本文の表を共通の結果の表で出す部品（Agent と RAG が同じものを使う）。内容はすべて合成のデータ。

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const TABLE = "| 列 A | 列 B |\n| --- | --- |\n| 値 1 | 値 2 |";

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

describe("AnswerBody", () => {
  it("表は結果の表、表以外は Markdown の本文で描く", () => {
    act(() => root.render(<AnswerBody text={`**太字**の文。\n\n${TABLE}`} testId="body" />));
    expect(host.querySelector("[data-markdown-text] strong")?.textContent).toBe("太字");
    expect(host.querySelector('[data-testid="body-table-1"] table')).not.toBeNull();
    expect(host.textContent).not.toContain("| ---");
  });

  it("表が 1 つなら番号の無い名前、複数なら番号つきの名前を付ける", () => {
    act(() => root.render(<AnswerBody text={TABLE} />));
    expect(host.querySelector("table")?.getAttribute("aria-label")).toBe("回答の表");
    act(() => root.render(<AnswerBody text={`${TABLE}\n\n文。\n\n${TABLE}`} />));
    expect([...host.querySelectorAll("table")].map((table) => table.getAttribute("aria-label"))).toEqual(["回答の表 1", "回答の表 2"]);
  });

  it("製品の文言と、表以外の描き方を渡せる", () => {
    act(() =>
      root.render(
        <AnswerBody
          text={`文。\n\n${TABLE}`}
          labels={{ tableName: () => "製品の表", table: (name) => ({ tableLabel: `${name}の名前` }) }}
          renderText={(text) => <p data-testid="plain">{text.trim()}</p>}
        />,
      ),
    );
    expect(host.querySelector('[data-testid="plain"]')?.textContent).toBe("文。");
    expect(host.querySelector("table")?.getAttribute("aria-label")).toBe("製品の表の名前");
  });

  it("splitOptions で区切りの行の無い行の並びも表にする", () => {
    act(() => root.render(<AnswerBody text={"| a | b |\n| c | d |"} splitOptions={{ headerlessRows: true }} />));
    expect(host.querySelectorAll("table tbody tr")).toHaveLength(2);
  });
});

describe("AnswerSections の項目の block（#4734）", () => {
  it("block の項目は lang の入れ物を div にし、それ以外は今までどおり span", () => {
    act(() =>
      root.render(
        <AnswerSections
          lang="zh-Hans"
          blocks={[
            {
              kind: "section",
              key: "s",
              entries: [
                { key: "a", content: <AnswerBody text={TABLE} />, block: true },
                { key: "b", content: "项目" },
              ],
            },
          ]}
        />,
      ),
    );
    const items = host.querySelectorAll("li");
    expect(items[0].querySelector(":scope > div")?.getAttribute("lang")).toBe("zh-Hans");
    expect(items[1].querySelector(":scope > span")?.getAttribute("lang")).toBe("zh-Hans");
  });
});
