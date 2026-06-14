import type { DocumentElement } from "./api";

export interface ParsedStructuredExtraction {
  rawText: string;
  documentType: string;
  confidence: number | null;
  warnings: string[];
  elements: DocumentElement[];
}

export interface ExtractionElementStats {
  elementCount: number;
  titleCount: number;
  textCount: number;
  tableCount: number;
  listCount: number;
  pageCount: number;
}

type JsonScalar = string | number | boolean | null;

/** unknown な extraction JSON を画面表示用の安全な形へ正規化する。 */
export function parseStructuredExtraction(input: unknown): ParsedStructuredExtraction {
  const source = recordValue(input);
  const rawText = stringValue(source.raw_text);
  const documentType = stringValue(source.document_type);
  const confidence = numberValue(source.confidence, 0, 1);
  const warnings = arrayValue(source.warnings).map(String).filter(Boolean);
  const elements = parseElements(source.elements);

  return {
    rawText,
    documentType,
    confidence,
    warnings,
    elements,
  };
}

/** extraction elements の低 cardinality 統計。 */
export function summarizeDocumentElements(elements: DocumentElement[]): ExtractionElementStats {
  const pages = new Set<number>();
  const stats: ExtractionElementStats = {
    elementCount: elements.length,
    titleCount: 0,
    textCount: 0,
    tableCount: 0,
    listCount: 0,
    pageCount: 0,
  };

  for (const element of elements) {
    if (element.kind === "title") stats.titleCount += 1;
    else if (element.kind === "table") stats.tableCount += 1;
    else if (element.kind === "list") stats.listCount += 1;
    else if (element.kind === "text") stats.textCount += 1;
    if (typeof element.page_number === "number") pages.add(element.page_number);
  }

  stats.pageCount = pages.size;
  return stats;
}

function parseElements(value: unknown): DocumentElement[] {
  return arrayValue(value)
    .map((item, index) => parseElement(item, index))
    .filter((item): item is DocumentElement => item != null)
    .sort((left, right) => left.order - right.order);
}

function parseElement(value: unknown, fallbackOrder: number): DocumentElement | null {
  const source = recordValue(value);
  const text = stringValue(source.text);
  if (!text) return null;

  return {
    kind: stringValue(source.kind) || "text",
    text,
    order: integerValue(source.order) ?? fallbackOrder,
    page_number: integerValue(source.page_number, 1) ?? null,
    bbox: numberArrayValue(source.bbox),
    section_path: arrayValue(source.section_path).map(String).filter(Boolean),
    confidence: numberValue(source.confidence, 0, 1),
    metadata: metadataValue(source.metadata),
  };
}

function recordValue(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown, min?: number, max?: number): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (min != null && value < min) return null;
  if (max != null && value > max) return null;
  return value;
}

function integerValue(value: unknown, min = 0): number | null {
  const parsed = numberValue(value, min);
  return parsed == null ? null : Math.trunc(parsed);
}

function numberArrayValue(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length !== 4) return null;
  const numbers = value.filter((item): item is number => typeof item === "number");
  return numbers.length === 4 && numbers.every(Number.isFinite) ? numbers : null;
}

function metadataValue(value: unknown): Record<string, JsonScalar> {
  const source = recordValue(value);
  const metadata: Record<string, JsonScalar> = {};
  for (const [key, item] of Object.entries(source)) {
    if (item == null || ["string", "number", "boolean"].includes(typeof item)) {
      metadata[key] = item as JsonScalar;
    }
  }
  return metadata;
}
