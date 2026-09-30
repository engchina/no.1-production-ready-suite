import type { DocumentExtractionExportFormat } from "@/lib/api";

/** 抽出エクスポートでファイルとコピーを出す形式（Chunk は「Chunk / Citation」タブで扱う。#561）。 */
export const EXTRACTION_EXPORT_FORMATS = [
  { format: "markdown", extension: ".md" },
  { format: "html", extension: ".html" },
  { format: "json", extension: ".json" },
] as const satisfies ReadonlyArray<{
  format: Exclude<DocumentExtractionExportFormat, "chunks">;
  extension: string;
}>;

export type ExtractionExportFileFormat = (typeof EXTRACTION_EXPORT_FORMATS)[number]["format"];

type ClipboardLike = Pick<Clipboard, "writeText"> & Partial<Pick<Clipboard, "write">>;
type ClipboardItemConstructor = new (items: Record<string, Promise<Blob> | Blob>) => ClipboardItem;

/**
 * 取得を待つ内容をクリップボードへ書く。
 *
 * Safari はクリックの処理の中で clipboard を呼ばないと書き込みを拒否するため、`ClipboardItem` が
 * 使えるときは取得を待つ前に Promise のまま渡す。使えないときは取得を待ってから `writeText` する。
 * 取得の失敗も書き込みの失敗も、返す Promise の reject になる（呼び出し側で失敗を表示する）。
 */
export async function copyTextFromPromise(
  text: Promise<string>,
  clipboard: ClipboardLike = navigator.clipboard,
  ClipboardItemCtor: ClipboardItemConstructor | undefined = globalThis.ClipboardItem,
): Promise<void> {
  if (ClipboardItemCtor && typeof clipboard.write === "function") {
    const blob = text.then((value) => new Blob([value], { type: "text/plain" }));
    await clipboard.write([new ClipboardItemCtor({ "text/plain": blob })]);
    return;
  }
  await clipboard.writeText(await text);
}
