// @vitest-environment happy-dom
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DocumentPreview, documentPreviewLabels, evidencePreviewFocus, type PreviewViewerPage } from "../src";

// #2389: 原文のプレビュー（RAG の文書の詳細・根拠のパネルと Agent の根拠のパネルで同じ部品）。
// データの取り方（URL・ページの一覧・本文）は製品が props で渡す。

function html(node: React.ReactNode): HTMLElement {
  const host = document.createElement("div");
  host.innerHTML = renderToStaticMarkup(node);
  return host;
}

const PAGES: PreviewViewerPage[] = [
  { pageNumber: 1, points: { width: 600, height: 800 }, imageUrl: (dpi) => `/p/1?dpi=${dpi}` },
  { pageNumber: 2, points: { width: 600, height: 800 }, imageUrl: (dpi) => `/p/2?dpi=${dpi}` },
];

describe("DocumentPreview", () => {
  it("PDF はページの一覧を読み込む間は Skeleton と経過、届いたらページの画像のビューアとツールバー", () => {
    const loading = html(<DocumentPreview kind="pdf" fileName="a.pdf" pages={{ status: "loading" }} />);
    expect(loading.querySelector('[data-testid="preview-loading"]')?.getAttribute("aria-label")).toBe(
      "プレビューを読み込んでいます",
    );

    const ready = html(
      <DocumentPreview
        kind="pdf"
        fileName="a.pdf"
        pages={{ status: "ready", pages: PAGES }}
        focusPage={1}
        focusBbox={[0.1, 0.1, 0.5, 0.2]}
      />,
    );
    const toolbar = ready.querySelector('[role="toolbar"]')!;
    expect(toolbar.getAttribute("aria-label")).toBe("プレビューの操作");
    const names = Array.from(toolbar.querySelectorAll("button")).map((button) => button.getAttribute("aria-label"));
    expect(names).toEqual([
      "前のページ",
      "次のページ",
      "縮小",
      "拡大",
      "横幅に合わせる",
      "全体を表示",
      "左に回転",
      "右に回転",
      "強調した位置へ移動",
    ]);
    expect(ready.querySelector('[data-testid="preview-page-status"]')?.textContent).toBe("1 / 2");
    expect(ready.querySelector('[data-testid="preview-highlight-status"]')).not.toBeNull();
  });

  it("理由のある失敗は理由と再試行、ページを描けないときは iframe、URL も無ければ取得できない案内", () => {
    const failed = html(
      <DocumentPreview
        kind="pdf"
        fileName="a.pdf"
        pages={{ status: "error", error: new Error("原文のファイルが保存先にありません。"), onRetry: () => undefined }}
      />,
    );
    expect(failed.textContent).toContain("原文のファイルが保存先にありません。");
    expect(failed.textContent).toContain("再試行");

    const iframe = html(<DocumentPreview kind="pdf" fileName="a.pdf" pages={{ status: "fallback" }} iframeUrl="/a.pdf" />);
    expect(iframe.querySelector("iframe")?.getAttribute("src")).toBe("/a.pdf");

    const none = html(<DocumentPreview kind="pdf" fileName="a.pdf" pages={{ status: "ready", pages: [] }} />);
    expect(none.textContent).toContain("プレビューを取得できませんでした。");
  });

  it("画像は 1 枚のビューア、本文は読み込みの表示、表示できない形式は案内とダウンロード", () => {
    const image = html(<DocumentPreview kind="image" fileName="a.png" imageUrl="/a.png" />);
    expect(image.querySelector('[data-testid="preview-viewer"]')).not.toBeNull();
    expect(image.querySelector('[data-testid="preview-page-status"]')).toBeNull();

    const text = html(
      <DocumentPreview kind="text" fileName="a.json" loadText={() => Promise.resolve("{}")} textKey="a" />,
    );
    expect(text.querySelector('[data-testid="preview-text-loading"]')).not.toBeNull();

    const unsupported = html(
      <DocumentPreview kind="unsupported" fileName="a.zip" downloadUrl="/a.zip?disposition=attachment" unsupportedMessage="表示できません。" />,
    );
    expect(unsupported.textContent).toContain("表示できません。");
    expect(unsupported.querySelector("a")?.getAttribute("href")).toBe("/a.zip?disposition=attachment");
    expect(unsupported.querySelector("a")?.textContent).toBe("ファイルをダウンロード");
    const noDownload = html(<DocumentPreview kind="unsupported" fileName="a.zip" />);
    expect(noDownload.querySelector("a")).toBeNull();
    expect(noDownload.textContent).toContain("このファイル形式はプレビューに対応していません。");
  });

  it("文言は上書きできる", () => {
    const host = html(
      <DocumentPreview
        kind="pdf"
        fileName="a.pdf"
        pages={{ status: "loading" }}
        labels={documentPreviewLabels({ loading: "loading" })}
      />,
    );
    expect(host.querySelector('[data-testid="preview-loading"]')?.getAttribute("aria-label")).toBe("loading");
  });
});

describe("evidencePreviewFocus", () => {
  it("根拠の metadata から最初のページと包含 bbox 1 つの強調を作る", () => {
    const focus = evidencePreviewFocus({
      page_start: "3",
      page_width: 600,
      page_height: 800,
      bbox: "[60, 80, 300, 200]",
      bbox_unit: "absolute",
    });
    expect(focus.focusPage).toBe(3);
    expect(focus.focusBbox).toEqual([60, 80, 300, 200]);
    expect(focus.focusBboxUnit).toBe("absolute");
    expect(focus.focusPageSize).toMatchObject({ width: 600, height: 800 });
    expect(focus.highlights).toHaveLength(1);
    expect(focus.highlights[0]).toMatchObject({ page: 3, tone: "primary" });
  });

  it("要素の表示領域があれば要素ごとに強調し、metadata が無ければ強調しない", () => {
    const focus = evidencePreviewFocus({
      page_start: 1,
      page_width: 1224,
      page_height: 1584,
      bbox: "[122.4, 158.4, 1101.6, 792]",
      display_regions: [
        {
          page: 1,
          boxes: [
            { record_id: "r1", seq_no: 1, bbox: [122.4, 158.4, 612, 316.8] },
            { record_id: "r2", seq_no: 2, bbox: [612, 633.6, 1101.6, 792] },
          ],
        },
      ],
    });
    expect(focus.highlights.map((highlight) => highlight.page)).toEqual([1, 1]);
    expect(evidencePreviewFocus(null)).toMatchObject({ focusPage: null, focusBbox: null, highlights: [] });
  });
});
