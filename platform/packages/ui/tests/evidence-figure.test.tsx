// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EvidenceFigureAction,
  EvidenceCard,
  EvidenceList,
  evidenceFigureLabels,
  type EvidenceFigureImage,
  type EvidenceListItem,
} from "../src";

// #2428: 図の根拠の「図を開く」（RAG と Agent で同じ部品。画像の取り方は製品が `load` で渡す）。

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

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function openButton(): HTMLButtonElement {
  return document.querySelector('[data-testid="figure-open"]') as HTMLButtonElement;
}

describe("EvidenceFigureAction", () => {
  it("ボタンの文言と読み上げ、AI が読んだ説明の注記を出す", () => {
    const load = vi.fn();
    act(() =>
      root.render(<EvidenceFigureAction title="文書 A.pdf" location="p.2" aiDescription load={load} testId="figure-open" />)
    );
    expect(openButton().textContent).toBe("図を開く");
    expect(openButton().getAttribute("aria-label")).toBe("図を開く: 文書 A.pdf");
    expect(openButton().getAttribute("aria-haspopup")).toBe("dialog");
    expect(document.querySelector('[data-testid="evidence-figure-description"]')?.textContent).toBe(
      "AI が図を読んだ説明です。元の図で確かめてください。"
    );
    // 開くまでは画像を取らない。
    expect(load).not.toHaveBeenCalled();
  });

  it("開くと画像の URL を取って出し、閉じると中断して解放する", async () => {
    const release = vi.fn();
    const signals: AbortSignal[] = [];
    const load = vi.fn((signal: AbortSignal): Promise<EvidenceFigureImage> => {
      signals.push(signal);
      return Promise.resolve({ url: "https://example.test/figure.png", release });
    });
    act(() => root.render(<EvidenceFigureAction title="文書 A.pdf" location="p.2" load={load} testId="figure-open" />));
    expect(document.querySelector('[data-testid="evidence-figure-description"]')).toBeNull();
    act(() => openButton().click());
    expect(document.querySelector('[data-testid="evidence-figure-loading"]')).not.toBeNull();
    await flush();
    expect(load).toHaveBeenCalledTimes(1);
    const image = document.querySelector('[data-testid="evidence-figure-image"]') as HTMLImageElement;
    expect(image.getAttribute("src")).toBe("https://example.test/figure.png");
    expect(image.getAttribute("alt")).toBe("文書 A.pdf の図（p.2）");
    expect(image.getAttribute("referrerpolicy")).toBe("no-referrer");
    act(() => image.dispatchEvent(new Event("load")));
    expect(document.querySelector('[data-testid="evidence-figure-loading"]')).toBeNull();
    const close = Array.from(document.querySelectorAll("button")).find(
      (button) => button.getAttribute("aria-label") === "図を閉じる"
    ) as HTMLButtonElement;
    act(() => close.click());
    expect(signals[0].aborted).toBe(true);
    expect(release).toHaveBeenCalledTimes(1);
  });

  it("取れなければ理由と取り直しを出し、取り直すともう一度取る", async () => {
    const load = vi
      .fn<(signal: AbortSignal) => Promise<EvidenceFigureImage>>()
      .mockRejectedValueOnce(new Error("資料が更新されたため、この図は開けません。"))
      .mockResolvedValueOnce({ url: "https://example.test/figure.png" });
    act(() => root.render(<EvidenceFigureAction title="文書 A.pdf" location={null} load={load} testId="figure-open" />));
    act(() => openButton().click());
    await flush();
    const error = document.querySelector('[data-testid="evidence-figure-error"]')!;
    expect(error.textContent).toContain("図を開けませんでした");
    expect(error.textContent).toContain("資料が更新されたため、この図は開けません。");
    const reload = Array.from(error.querySelectorAll("button")).find((button) => button.textContent === "もう一度読み込む")!;
    act(() => reload.click());
    await flush();
    expect(load).toHaveBeenCalledTimes(2);
    const image = document.querySelector('[data-testid="evidence-figure-image"]') as HTMLImageElement;
    expect(image.getAttribute("alt")).toBe("文書 A.pdf の図");
    // 画像を読めなければ理由を出す。
    act(() => image.dispatchEvent(new Event("error")));
    expect(document.querySelector('[data-testid="evidence-figure-error"]')?.textContent).toContain(
      evidenceFigureLabels().imageFailed
    );
  });
});

describe("EvidenceList の図を開く", () => {
  const item = (key: string): EvidenceListItem => ({
    key,
    fileName: `文書 ${key}.pdf`,
    text: "合成の本文。",
    usedInAnswer: null,
    answerUse: null,
    score: null,
    rerankScore: null,
  });

  it("「根拠を表示」のすぐ後ろ（操作の行の左の組）に置き、製品の操作は右の組に置く", () => {
    act(() =>
      root.render(
        <EvidenceList
          items={[item("A"), item("B")]}
          onOpenSource={() => undefined}
          renderFigure={(entry) =>
            entry.key === "A" ? (
              <EvidenceFigureAction title={entry.fileName} location={null} load={vi.fn()} testId="figure-A" />
            ) : null
          }
          renderActions={() => <span data-testid="product-action">評価</span>}
        />
      )
    );
    const cards = host.querySelectorAll('[data-testid="evidence-card"]');
    const source = cards[0].querySelector('[data-testid="evidence-source-0"]')!;
    const figure = cards[0].querySelector('[data-testid="figure-A"]')!;
    const leading = source.closest("div")!.parentElement!;
    expect(leading.contains(figure)).toBe(true);
    expect(leading.contains(cards[0].querySelector('[data-testid="product-action"]'))).toBe(false);
    // 図でない根拠には出さない。
    expect(cards[1].querySelector('[aria-label^="図を開く"]')).toBeNull();
  });
});

describe("EvidenceCard の図を開く", () => {
  it("1 件のカードも「根拠を表示」のすぐ後ろ（左の組）に置き、製品の操作は右の組に置く（#2433）", () => {
    act(() =>
      root.render(
        <ul>
          <EvidenceCard
            index={0}
            item={{
              key: "A",
              fileName: "文書 A.pdf",
              text: "合成の本文。",
              usedInAnswer: null,
              answerUse: null,
              score: null,
              rerankScore: null,
            }}
            leadingActions={
              <>
                <button type="button" data-testid="source">根拠を表示</button>
                <EvidenceFigureAction title="文書 A.pdf" location={null} load={vi.fn()} testId="figure-A" />
              </>
            }
            actions={<span data-testid="product-action">評価</span>}
          />
        </ul>
      )
    );
    const source = host.querySelector('[data-testid="source"]')!;
    const leading = source.parentElement!;
    const buttons = Array.from(leading.querySelectorAll("button")).map((button) => button.textContent);
    expect(buttons).toEqual(["根拠を表示", "図を開く"]);
    expect(leading.contains(host.querySelector('[data-testid="product-action"]'))).toBe(false);
  });
});
