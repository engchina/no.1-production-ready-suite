import type {
  DocumentElement,
  ExtractionAsset,
  ExtractionPage,
  ExtractionTable,
  ExtractionTableCell,
} from "./api";

export interface ParsedStructuredExtraction {
  rawText: string;
  documentType: string;
  confidence: number | null;
  warnings: string[];
  elements: DocumentElement[];
  pages: ExtractionPage[];
  tables: ExtractionTable[];
  assets: ExtractionAsset[];
  parserArtifacts: Record<string, JsonScalar>;
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
  const pages = parsePages(source.pages);
  const tables = parseTables(source.tables);
  const assets = parseAssets(source.assets);

  return {
    rawText,
    documentType,
    confidence,
    warnings,
    elements,
    pages,
    tables,
    assets,
    parserArtifacts: metadataValue(source.parser_artifacts),
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
    element_id: stringValue(source.element_id) || null,
    parent_id: stringValue(source.parent_id) || null,
    content_kind: stringValue(source.content_kind) || null,
    source_parser: stringValue(source.source_parser) || null,
    page_number: integerValue(source.page_number, 1) ?? null,
    bbox: numberArrayValue(source.bbox),
    section_path: arrayValue(source.section_path).map(String).filter(Boolean),
    confidence: numberValue(source.confidence, 0, 1),
    metadata: metadataValue(source.metadata),
  };
}

function parsePages(value: unknown): ExtractionPage[] {
  return arrayValue(value)
    .map((item): ExtractionPage | null => {
      const source = recordValue(item);
      const pageNumber = integerValue(source.page_number, 1);
      if (pageNumber == null) return null;
      return {
        page_number: pageNumber,
        label: stringValue(source.label) || null,
        width: numberValue(source.width, 0),
        height: numberValue(source.height, 0),
        rotation: integerValue(source.rotation),
        element_ids: arrayValue(source.element_ids).map(String).filter(Boolean),
        metadata: metadataValue(source.metadata),
      };
    })
    .filter((item): item is ExtractionPage => item != null);
}

function parseTables(value: unknown): ExtractionTable[] {
  return arrayValue(value)
    .map((item): ExtractionTable | null => {
      const source = recordValue(item);
      const tableId = stringValue(source.table_id);
      if (!tableId) return null;
      return {
        table_id: tableId,
        element_id: stringValue(source.element_id) || null,
        page_number: integerValue(source.page_number, 1),
        caption: stringValue(source.caption) || null,
        cells: parseTableCells(source.cells),
        metadata: metadataValue(source.metadata),
      };
    })
    .filter((item): item is ExtractionTable => item != null);
}

function parseTableCells(value: unknown): ExtractionTableCell[] {
  return arrayValue(value)
    .map((item): ExtractionTableCell | null => {
      const source = recordValue(item);
      const row = integerValue(source.row);
      const col = integerValue(source.col);
      if (row == null || col == null) return null;
      return {
        row,
        col,
        text: stringValue(source.text),
        row_span: integerValue(source.row_span, 1) ?? 1,
        col_span: integerValue(source.col_span, 1) ?? 1,
        page_number: integerValue(source.page_number, 1),
        bbox: numberArrayValue(source.bbox),
        confidence: numberValue(source.confidence, 0, 1),
        metadata: metadataValue(source.metadata),
      };
    })
    .filter((item): item is ExtractionTableCell => item != null);
}

function parseAssets(value: unknown): ExtractionAsset[] {
  return arrayValue(value)
    .map((item): ExtractionAsset | null => {
      const source = recordValue(item);
      const assetId = stringValue(source.asset_id);
      if (!assetId) return null;
      return {
        asset_id: assetId,
        kind: stringValue(source.kind) || "figure",
        object_path: stringValue(source.object_path) || null,
        page_number: integerValue(source.page_number, 1),
        bbox: numberArrayValue(source.bbox),
        alt_text: stringValue(source.alt_text) || null,
        metadata: metadataValue(source.metadata),
      };
    })
    .filter((item): item is ExtractionAsset => item != null);
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
  if (!Array.isArray(value) || ![4, 8].includes(value.length)) return null;
  const numbers = value.filter((item): item is number => typeof item === "number");
  return numbers.length === value.length && numbers.every(Number.isFinite) ? numbers : null;
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
