import { describe, expect, it, vi } from "vitest";

import { copyTextFromPromise, EXTRACTION_EXPORT_FORMATS } from "./DocumentExtractionExport.logic";

class FakeClipboardItem {
  constructor(readonly items: Record<string, Promise<Blob> | Blob>) {}
}

describe("EXTRACTION_EXPORT_FORMATS", () => {
  it("Markdown / HTML / JSON の 3 形式だけを出し、Chunks を含めない", () => {
    expect(EXTRACTION_EXPORT_FORMATS.map((item) => item.format)).toEqual([
      "markdown",
      "html",
      "json",
    ]);
    expect(EXTRACTION_EXPORT_FORMATS.map((item) => item.extension)).toEqual([
      ".md",
      ".html",
      ".json",
    ]);
  });
});

describe("copyTextFromPromise", () => {
  it("ClipboardItem が使えるときは、取得を待つ前に Promise のまま clipboard.write へ渡す", async () => {
    let resolveText: (value: string) => void = () => undefined;
    const text = new Promise<string>((resolve) => {
      resolveText = resolve;
    });
    const written: FakeClipboardItem[] = [];
    const clipboard = {
      writeText: vi.fn(),
      write: vi.fn(async (items: FakeClipboardItem[]) => {
        written.push(...items);
        await items[0].items["text/plain"];
      }),
    };

    const copying = copyTextFromPromise(
      text,
      clipboard as unknown as Clipboard,
      FakeClipboardItem as unknown as typeof ClipboardItem,
    );
    // 取得が終わる前に write が呼ばれている（Safari のユーザー操作の制約）。
    expect(clipboard.write).toHaveBeenCalledTimes(1);
    resolveText("# 見出し");
    await copying;

    const blob = await written[0].items["text/plain"];
    expect(blob.type).toBe("text/plain");
    await expect(blob.text()).resolves.toBe("# 見出し");
    expect(clipboard.writeText).not.toHaveBeenCalled();
  });

  it("ClipboardItem が無いときは、取得を待ってから writeText する", async () => {
    const clipboard = { writeText: vi.fn(async () => undefined) };

    await copyTextFromPromise(Promise.resolve("<article />"), clipboard, undefined);

    expect(clipboard.writeText).toHaveBeenCalledWith("<article />");
  });

  it("取得に失敗したら reject する（コピーの失敗として表示する）", async () => {
    const clipboard = {
      writeText: vi.fn(async () => undefined),
      write: vi.fn(async (items: FakeClipboardItem[]) => {
        await items[0].items["text/plain"];
      }),
    };

    await expect(
      copyTextFromPromise(
        Promise.reject(new Error("404")),
        clipboard as unknown as Clipboard,
        FakeClipboardItem as unknown as typeof ClipboardItem,
      ),
    ).rejects.toThrow("404");
    await expect(
      copyTextFromPromise(
        Promise.reject(new Error("404")),
        { writeText: clipboard.writeText },
        undefined,
      ),
    ).rejects.toThrow("404");
    expect(clipboard.writeText).not.toHaveBeenCalled();
  });
});
