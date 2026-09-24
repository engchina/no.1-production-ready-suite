import type { DocumentElement } from "@/lib/api";

/** DocRAG(docling) 要素の Vision 図説明の表示用情報。該当しなければ null。 */
export type ElementVision = {
  status: string;
  retrievalText: string;
  excluded: boolean;
};

export function elementVision(element: DocumentElement): ElementVision | null {
  const metadata = element.metadata ?? {};
  const status = typeof metadata.vision_status === "string" ? metadata.vision_status : "";
  const retrievalText =
    typeof metadata.vision_retrieval_text === "string" ? metadata.vision_retrieval_text : "";
  const excluded = metadata.rag_excluded === true || metadata.visual_role === "decorative";
  if (!status && !retrievalText && !excluded) return null;
  return { status, retrievalText, excluded };
}

const ENTITY: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&nbsp;": " ",
};

/**
 * Docling の表 HTML を読みやすいテキストへ落とす(行=改行、セル=" | ")。
 * 表でない text はそのまま返す。セル単位の確認は表セルパネルが担う。
 */
export function tableHtmlToText(text: string): string {
  if (!/^\s*<table[\s>]/i.test(text)) return text;
  const rows = text.match(/<tr[\s\S]*?<\/tr>/gi) ?? [];
  return rows
    .map((row) =>
      (row.match(/<t[hd][^>]*>[\s\S]*?<\/t[hd]>/gi) ?? [])
        .map((cell) =>
          cell
            .replace(/<br\s*\/?>/gi, " ")
            .replace(/<[^>]+>/g, "")
            .replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (entity) => ENTITY[entity] ?? entity)
            .trim()
        )
        .join(" | ")
    )
    .filter((line) => line.replace(/[|\s]/g, ""))
    .join("\n");
}
