// @vitest-environment happy-dom
import { Layers } from "lucide-react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AnswerEvidence,
  EvidenceCard,
  EvidenceList,
  evidenceCountLabel,
  evidenceLabels,
  evidenceScoreMeterPercent,
  formatEvidenceScore,
  type EvidenceListItem,
} from "../src";

// #2328: 回答の根拠の共有の部品（RAG のチャット・RAG 検索・Agent のチャットで同じ見出しとカード）。

function item(overrides: Partial<EvidenceListItem> = {}): EvidenceListItem {
  return {
    key: "doc-a:set:1",
    fileName: "文書 A.pdf",
    text: "合成の本文の抜粋です。",
    usedInAnswer: false,
    answerUse: null,
    score: 0.5,
    rerankScore: null,
    ...overrides,
  };
}

function html(node: React.ReactNode): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(node);
  return host;
}

describe("evidenceCountLabel", () => {
  it("AI に渡した件数と、回答で引用・該当確認済み・参考の内訳を 0 件を省いて並べる", () => {
    const items = [
      item({ usedInAnswer: true, answerUse: "confirmed" }),
      item({ usedInAnswer: true, answerUse: "reference" }),
      item({ usedInAnswer: true, answerUse: null }),
      item(),
    ];
    expect(evidenceCountLabel(items)).toBe("AI に渡した 4 件 ／ 回答で引用 3 件 ／ うち該当確認済み 1 件 ／ 参考 1 件");
  });

  it("引用したかが分からない（検索だけ）なら件数だけ、引用が 0 件なら内訳を出さない", () => {
    expect(evidenceCountLabel([item({ usedInAnswer: null })])).toBe("AI に渡した 1 件");
    expect(evidenceCountLabel([item(), item()])).toBe("AI に渡した 2 件");
  });

  it("文言は上書きできる", () => {
    expect(evidenceCountLabel([item()], evidenceLabels({ count: (n) => `${n} items` }))).toBe("1 items");
  });
});

describe("EvidenceCard", () => {
  it("番号・文書名・引用のバッジ・経路・抜粋・チップ・補足・原典・スコアを出す", () => {
    const host = html(
      <ul>
        <EvidenceCard
          index={1}
          item={item({
            usedInAnswer: true,
            answerUse: "confirmed",
            superseded: true,
            routeBadges: ["Vector #1", "Evidence"],
            rerankScore: 0.42,
            chips: [{ id: "page", label: "ページ", value: "p.3" }],
            tags: [
              { id: "recipe", value: "レシピ 1", icon: Layers },
              { id: "category", value: "分類 A", tone: "info" },
            ],
            sourceUrl: "https://example.com/a",
          })}
          actions={<button type="button">根拠を表示</button>}
        />
      </ul>,
    );
    const main = host.querySelector('[data-testid="citation-main"]')!;
    expect(main.textContent).toContain("2");
    expect(main.textContent).toContain("文書 A.pdf");
    expect(main.textContent).toContain("回答で引用");
    expect(main.textContent).toContain("旧版");
    expect(host.querySelector('[data-testid="evidence-route-badges"]')?.textContent).toBe("Vector #1Evidence");
    expect(host.querySelector('[data-testid="citation-text"]')?.textContent).toBe("合成の本文の抜粋です。");
    expect(host.querySelector("dt")?.textContent).toBe("ページ");
    expect(host.querySelector("dd")?.textContent).toBe("p.3");
    expect(host.textContent).toContain("レシピ 1");
    expect(host.querySelector('[data-surface-tint="info"]')?.textContent).toBe("分類 A");
    expect(host.querySelector("a")?.getAttribute("href")).toBe("https://example.com/a");
    expect(host.querySelector('[role="meter"]')?.getAttribute("aria-valuetext")).toBe("0.420");
    expect(host.querySelector("button")?.textContent).toBe("根拠を表示");
  });

  it("参考の記載は「参考」、引用していなければバッジを出さず、Rerank が無ければ未実行と出す", () => {
    const reference = html(<ul><EvidenceCard index={0} item={item({ usedInAnswer: true, answerUse: "reference" })} /></ul>);
    expect(reference.textContent).toContain("参考");
    expect(reference.textContent).not.toContain("回答で引用");
    const plain = html(<ul><EvidenceCard index={0} item={item({ score: null })} /></ul>);
    expect(plain.textContent).not.toContain("回答で引用");
    expect(plain.textContent).toContain("Rerank 未実行");
    expect(plain.textContent).toContain("—");
    expect(plain.querySelector('[role="meter"]')).toBeNull();
  });
});

describe("AnswerEvidence", () => {
  it("根拠が無ければ何も出さず、あれば件数の見出しの折りたたみ（既定は閉じる）にカードを入れる", () => {
    expect(renderToStaticMarkup(<AnswerEvidence items={[]} />)).toBe("");
    const host = html(<AnswerEvidence items={[item({ usedInAnswer: true }), item({ key: "b" })]} />);
    const details = host.querySelector("details")!;
    expect(details.getAttribute("data-testid")).toBe("answer-citations");
    expect(details.hasAttribute("open")).toBe(false);
    expect(host.querySelector("summary")?.textContent).toContain("AI に渡した 2 件 ／ 回答で引用 1 件");
    expect(host.querySelectorAll('[data-testid="evidence-card"]')).toHaveLength(2);
  });

  it("スコアは小数 3 桁、メーターは 0〜100%", () => {
    expect(formatEvidenceScore(0.12345)).toBe("0.123");
    expect(formatEvidenceScore(null)).toBe("—");
    expect(evidenceScoreMeterPercent(0.5)).toBe(50);
    expect(evidenceScoreMeterPercent(2)).toBe(100);
    expect(evidenceScoreMeterPercent(-1)).toBe(0);
  });
});

describe("EvidenceList", () => {
  it("見方の切り替え（タブ）を持たず、一覧は一定の高さでスクロールする（#2388）", () => {
    const host = html(<EvidenceList items={[item(), item({ key: "b" })]} />);
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(host.querySelector("ul")?.className).toContain("bounded-scroll-area-lg");
    expect(host.querySelectorAll('[data-testid="evidence-card"]')).toHaveLength(2);
  });

  it("onOpenSource を渡すと、各カードの操作の行の先頭に同じ名前の「根拠を表示」を出す（3 製品で同じ）", () => {
    const host = html(
      <EvidenceList
        items={[item(), item({ key: "b", fileName: "文書 B.pdf" })]}
        idPrefix="t"
        onOpenSource={() => undefined}
        renderActions={() => <span data-testid="extra">評価</span>}
      />,
    );
    const buttons = Array.from(host.querySelectorAll("button"));
    expect(buttons.map((button) => button.textContent)).toEqual(["根拠を表示", "根拠を表示"]);
    expect(buttons[1].getAttribute("aria-label")).toBe("文書 B.pdf の根拠を表示");
    expect(buttons[1].getAttribute("aria-haspopup")).toBe("dialog");
    expect(buttons[0].getAttribute("data-testid")).toBe("t-source-0");
    expect(host.querySelectorAll('[data-testid="extra"]')).toHaveLength(2);
  });

  it("開けない画面（onOpenSource なし）ではボタンを出さない", () => {
    const host = html(<EvidenceList items={[item()]} />);
    expect(host.querySelector("button")).toBeNull();
  });
});
