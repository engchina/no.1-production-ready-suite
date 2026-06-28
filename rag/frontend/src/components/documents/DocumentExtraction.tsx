import {
  BookOpen,
  Check,
  CircleAlert,
  Clipboard,
  FileText,
  Hash,
  Layers3,
  ListChecks,
  Table2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type Ref } from "react";

import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import type { DocumentElement, ExtractionTable, ExtractionTableCell } from "@/lib/api";
import {
  parseStructuredExtraction,
  summarizeDocumentElements,
} from "@/lib/extraction";
import { scrollFocusedControlIntoView } from "@/lib/focus-scroll";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { tableCellKey, tableCellRef } from "@/lib/table-cell-focus";

import { ExtractedText, InfoChip } from "./extraction-bits";

const KIND_LABELS: Record<string, Parameters<typeof t>[0]> = {
  title: "flow.extraction.kind.title",
  text: "flow.extraction.kind.text",
  list: "flow.extraction.kind.list",
  table: "flow.extraction.kind.table",
  figure: "flow.extraction.kind.figure",
  figure_caption: "flow.extraction.kind.figureCaption",
  table_caption: "flow.extraction.kind.tableCaption",
  header: "flow.extraction.kind.header",
  footer: "flow.extraction.kind.footer",
  code: "flow.extraction.kind.code",
  equation: "flow.extraction.kind.equation",
  other: "flow.extraction.kind.other",
};

/** RAG 取込で得た構造化要素・本文・軽量メタデータを表示する。 */
export function DocumentExtraction({
  extraction,
  selectedElementId = null,
  selectedTableCellKey = null,
  focusRequestKey = null,
  focusSelectedElement = false,
  focusSelectedTableCell = false,
  onElementSelect,
  onTableCellSelect,
}: {
  extraction: Record<string, unknown>;
  selectedElementId?: string | null;
  selectedTableCellKey?: string | null;
  focusRequestKey?: string | null;
  focusSelectedElement?: boolean;
  focusSelectedTableCell?: boolean;
  onElementSelect?: (elementId: string) => void;
  onTableCellSelect?: (table: ExtractionTable, cell: ExtractionTableCell) => void;
}) {
  const parsed = parseStructuredExtraction(extraction);
  const stats = summarizeDocumentElements(parsed.elements);
  const selectedElementRef = useRef<HTMLButtonElement | null>(null);
  const selectedTableCellRef = useRef<HTMLButtonElement | null>(null);
  const hasSummary =
    parsed.rawText ||
    parsed.documentType ||
    parsed.confidence != null ||
    parsed.warnings.length > 0 ||
    parsed.elements.length > 0 ||
    parsed.tables.length > 0;

  useEffect(() => {
    if (!focusRequestKey || !selectedElementId || !selectedElementRef.current) return;
    scrollFocusedControlIntoView(selectedElementRef.current, {
      focus: focusSelectedElement,
    });
  }, [focusRequestKey, focusSelectedElement, selectedElementId]);

  useEffect(() => {
    if (!focusRequestKey || !selectedTableCellKey || !selectedTableCellRef.current) return;
    scrollFocusedControlIntoView(selectedTableCellRef.current, {
      focus: focusSelectedTableCell,
    });
  }, [focusRequestKey, focusSelectedTableCell, selectedTableCellKey]);

  if (!hasSummary) {
    return <p className="text-sm text-muted">{t("flow.extraction.empty")}</p>;
  }

  return (
    <div className="space-y-4 rounded-lg border border-border bg-background p-4">
      <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <MetadataItem
          label={t("flow.extraction.documentType")}
          value={parsed.documentType || "—"}
        />
        <MetadataItem
          label={t("flow.extraction.confidence")}
          value={confidenceText(parsed.confidence)}
        />
      </dl>

      {parsed.warnings.length > 0 ? (
        <Banner severity="warning">
          <ul className="space-y-1">
            {parsed.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Banner>
      ) : null}

      {parsed.elements.length > 0 ? (
        <>
          <div
            className="grid grid-cols-2 gap-3 lg:grid-cols-4"
            aria-label={t("flow.extraction.structureStats")}
          >
            <StatTile
              icon={Layers3}
              label={t("flow.extraction.stats.elements")}
              value={formatNumber(stats.elementCount)}
            />
            <StatTile
              icon={BookOpen}
              label={t("flow.extraction.stats.pages")}
              value={formatNumber(stats.pageCount)}
            />
            <StatTile
              icon={Table2}
              label={t("flow.extraction.stats.tables")}
              value={formatNumber(stats.tableCount)}
            />
            <StatTile
              icon={ListChecks}
              label={t("flow.extraction.stats.lists")}
              value={formatNumber(stats.listCount)}
            />
          </div>

          <section>
            <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
              {t("flow.extraction.elements")}
              <span className="tnum text-xs font-normal text-muted">
                {formatNumber(stats.elementCount)}
              </span>
            </h4>
            <ol className="space-y-3 pr-1">
              {parsed.elements.map((element) => (
                <ElementItem
                  key={elementKey(element)}
                  element={element}
                  selected={elementKey(element) === selectedElementId}
                  buttonRef={
                    elementKey(element) === selectedElementId ? selectedElementRef : undefined
                  }
                  onSelect={onElementSelect}
                />
              ))}
            </ol>
          </section>
        </>
      ) : null}

      {parsed.tables.some((table) => table.cells.length > 0) ? (
        <TableCellsPanel
          tables={parsed.tables}
          selectedTableCellKey={selectedTableCellKey}
          selectedTableCellRef={selectedTableCellRef}
          onTableCellSelect={onTableCellSelect}
        />
      ) : null}

      <RawTextBlock rawText={parsed.rawText} compact={parsed.elements.length > 0} />
    </div>
  );
}

function MetadataItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="tnum mt-0.5 font-medium text-foreground">{value}</dd>
    </div>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="flex items-center gap-2 text-xs font-medium text-muted">
        <Icon size={14} className="text-primary" aria-hidden />
        <span>{label}</span>
      </div>
      <p className="tnum mt-2 text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

function ElementItem({
  element,
  selected,
  buttonRef,
  onSelect,
}: {
  element: DocumentElement;
  selected: boolean;
  buttonRef?: Ref<HTMLButtonElement>;
  onSelect?: (elementId: string) => void;
}) {
  const id = elementKey(element);
  const lowConfidence = typeof element.confidence === "number" && element.confidence < 0.65;
  return (
    <li>
      <button
        ref={buttonRef}
        type="button"
        className={`w-full rounded-md border p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
          selected ? "border-primary bg-primary/5" : "border-border bg-card hover:bg-background"
        }`}
        aria-pressed={selected}
        onClick={() => onSelect?.(id)}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {elementKindLabel(element.kind)}
          </span>
          {element.content_kind ? (
            <span className="rounded-full bg-background px-2 py-0.5 text-xs text-muted">
              {element.content_kind}
            </span>
          ) : null}
          {typeof element.page_number === "number" ? (
            <span className="tnum rounded-full bg-background px-2 py-0.5 text-xs text-muted">
              {t("flow.extraction.page", { page: element.page_number })}
            </span>
          ) : null}
          {element.section_path?.length ? (
            <span className="min-w-0 max-w-full rounded-full bg-info-bg px-2 py-0.5 text-xs text-info">
              <span className="break-words">{element.section_path.join(" > ")}</span>
            </span>
          ) : null}
          {typeof element.confidence === "number" ? (
            <span
              className={`tnum inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${
                lowConfidence ? "bg-warning-bg text-warning" : "bg-success-bg text-success"
              }`}
            >
              {lowConfidence ? <CircleAlert size={12} aria-hidden /> : null}
              {confidenceText(element.confidence)}
            </span>
          ) : null}
        </div>
        <div className="mt-2">
          <ExtractedText text={element.text} clamp />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <InfoChip
            icon={Hash}
            label={element.source_parser ? `${id} / ${element.source_parser}` : id}
          />
        </div>
      </button>
    </li>
  );
}

function TableCellsPanel({
  tables,
  selectedTableCellKey,
  selectedTableCellRef,
  onTableCellSelect,
}: {
  tables: ExtractionTable[];
  selectedTableCellKey: string | null;
  selectedTableCellRef: Ref<HTMLButtonElement>;
  onTableCellSelect?: (table: ExtractionTable, cell: ExtractionTableCell) => void;
}) {
  return (
    <section>
      <h4 className="mb-2 text-sm font-semibold text-foreground">
        {t("flow.extraction.tableCells")}
      </h4>
      <div className="max-h-[420px] space-y-3 overflow-auto pr-1">
        {tables
          .filter((table) => table.cells.length > 0)
          .map((table) => (
            <div
              key={table.table_id}
              className="rounded-md border border-border bg-card p-3"
              data-testid="extraction-table-cells"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="break-all text-xs font-medium text-foreground">
                  {table.caption || table.table_id}
                </span>
                {typeof table.page_number === "number" ? (
                  <span className="tnum rounded-full bg-background px-2 py-0.5 text-xs text-muted">
                    {t("flow.extraction.page", { page: table.page_number })}
                  </span>
                ) : null}
              </div>
              <div className="mt-2 overflow-auto">
                <div className="grid min-w-full gap-1" style={tableGridStyle(table)}>
                  {table.cells.map((cell) => {
                    const key = tableCellKey(table.table_id, cell);
                    const selected = key === selectedTableCellKey;
                    const ref = tableCellRef(cell);
                    return (
                      <button
                        key={key}
                        ref={selected ? selectedTableCellRef : undefined}
                        type="button"
                        className={`min-h-11 rounded border px-2 py-1.5 text-left text-xs transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
                          selected
                            ? "border-primary bg-primary/10 text-foreground"
                            : "border-border bg-background text-foreground hover:bg-card"
                        }`}
                        aria-pressed={selected}
                        aria-label={tableCellAriaLabel(table, cell)}
                        data-testid="extraction-table-cell"
                        onClick={() => onTableCellSelect?.(table, cell)}
                      >
                        <span className="flex flex-wrap items-center gap-1">
                          <span className="tnum rounded bg-card px-1.5 py-0.5 text-[11px] text-muted">
                            {ref || t("flow.extraction.tableCellPosition", {
                              row: cell.row + 1,
                              col: cell.col + 1,
                            })}
                          </span>
                          {cell.bbox ? (
                            <span className="rounded bg-success-bg px-1.5 py-0.5 text-[11px] text-success">
                              bbox
                            </span>
                          ) : null}
                        </span>
                        <span className="mt-1 block">
                          <ExtractedText text={cell.text} />
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          ))}
      </div>
    </section>
  );
}

function tableGridStyle(table: ExtractionTable) {
  const columnCount = Math.max(
    1,
    ...table.cells.map((cell) => cell.col + Math.max(1, cell.col_span))
  );
  return {
    gridTemplateColumns: `repeat(${columnCount}, minmax(7rem, 1fr))`,
  };
}

function tableCellAriaLabel(table: ExtractionTable, cell: ExtractionTableCell): string {
  const ref = tableCellRef(cell);
  return t("flow.extraction.tableCellAria", {
    table: table.caption || table.table_id,
    cell: ref || t("flow.extraction.tableCellPosition", {
      row: cell.row + 1,
      col: cell.col + 1,
    }),
    text: cell.text || "—",
  });
}

function RawTextBlock({ rawText, compact }: { rawText: string; compact: boolean }) {
  if (!rawText) {
    return <p className="text-sm text-muted">{t("flow.extraction.noRawText")}</p>;
  }

  const content = (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="mb-2 flex justify-end">
        <CopyRawTextButton text={rawText} />
      </div>
      <div className="max-h-[420px] overflow-auto">
        <ExtractedText text={rawText} />
      </div>
    </div>
  );

  if (!compact) {
    return (
      <section>
        <h4 className="mb-2 text-sm font-semibold text-foreground">
          {t("flow.extraction.rawText")}
        </h4>
        {content}
      </section>
    );
  }

  return (
    <details className="rounded-md border border-border bg-card px-3 py-2">
      <summary className="min-h-10 cursor-pointer text-sm font-semibold text-foreground">
        <span className="inline-flex items-center gap-2 py-2">
          <FileText size={15} className="text-primary" aria-hidden />
          {t("flow.extraction.rawText")}
        </span>
      </summary>
      <div className="pb-2">{content}</div>
    </details>
  );
}

function CopyRawTextButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "success" | "error">("idle");

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("success");
    } catch {
      setState("error");
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      {state === "error" ? (
        <FormStatus
          tone="danger"
          className="text-xs"
          message={t("flow.extraction.copyFailed")}
        />
      ) : null}
      <Button variant="ghost" size="sm" onClick={() => void handleCopy()}>
        {state === "success" ? (
          <Check size={14} aria-hidden />
        ) : (
          <Clipboard size={14} aria-hidden />
        )}
        {state === "success" ? t("flow.extraction.copied") : t("flow.extraction.copyRawText")}
      </Button>
    </span>
  );
}

function elementKindLabel(kind: string): string {
  return t(KIND_LABELS[kind] ?? "flow.extraction.kind.other");
}

function elementKey(element: DocumentElement): string {
  return element.element_id || `el-${String(element.order).padStart(4, "0")}`;
}

function confidenceText(value: number | null): string {
  return value == null ? "—" : `${formatNumber(Math.round(value * 100))}%`;
}
