import type { DocumentExtractionExportFormat } from "@/lib/api";

/** 選択中のレシピの抽出結果と索引済み Chunk を書き出す形式。 */
export const EXTRACTION_EXPORT_FORMATS = [
  { format: "markdown", extension: ".md" },
  { format: "html", extension: ".html" },
  { format: "json", extension: ".json" },
  { format: "chunks", extension: ".json" },
] as const satisfies ReadonlyArray<{
  format: DocumentExtractionExportFormat;
  extension: string;
}>;

export type ExtractionExportFileFormat = (typeof EXTRACTION_EXPORT_FORMATS)[number]["format"];
