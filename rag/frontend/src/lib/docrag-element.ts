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

/** Vision 説明の表示用 1 行(ラベルキーと値)。 */
export type VisionDetailLine = { field: string; value: string };

const VISION_DETAIL_FIELDS = [
  "visual_kind",
  "main_topic",
  "surrounding_context",
  "menu_route",
  "visible_screen_names",
  "visible_buttons",
  "visible_fields",
  "visible_values",
  "codes_and_errors",
  "table_headers",
  "table_rows",
  "diagram_nodes",
  "diagram_edges",
  "chart_series",
  "form_fields",
  "operation_steps",
  "condition_result_pairs",
  "exception_or_cautions",
  "correction_notes",
] as const;

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function formatVisionValue(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (Array.isArray(item)) return item.map(String).join(" | ");
        if (item && typeof item === "object") {
          return Object.values(item as Record<string, unknown>)
            .map((part) => String(part ?? "").trim())
            .filter(Boolean)
            .join(" → ");
        }
        return String(item ?? "").trim();
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

/**
 * 抽出 JSON の docrag_layout から要素(record)の Vision 説明を引き、値のある項目だけ返す。
 * 装飾・アイコンとして除外された図は reason に理由を返す。docrag 以外の抽出では null。
 */
export function docragVisionDetails(
  extraction: Record<string, unknown>,
  elementId: string
): { lines: VisionDetailLine[]; excludedReason: string } | null {
  const layout = record(record(extraction.parser_artifacts).docrag_layout);
  const records = Array.isArray(layout.records) ? layout.records : [];
  const found = records.map(record).find((item) => item.id === elementId);
  if (!found) return null;
  const raw = record(found.raw);
  const description = record(raw.vision_description);
  const lines = VISION_DETAIL_FIELDS.map((field) => ({
    field,
    value: formatVisionValue(description[field]),
  })).filter((line) => line.value);
  const excludedReason =
    raw.visual_role === "decorative" || raw.visual_role === "inline_icon"
      ? String(raw.visual_role_reason ?? raw.visual_role)
      : "";
  if (!lines.length && !excludedReason) return null;
  return { lines, excludedReason };
}
