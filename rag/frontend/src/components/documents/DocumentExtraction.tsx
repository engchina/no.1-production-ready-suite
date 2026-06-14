import {
  BookOpen,
  FileText,
  Layers3,
  ListChecks,
  Table2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Banner } from "@/components/ui/banner";
import type { DocumentElement } from "@/lib/api";
import {
  parseStructuredExtraction,
  summarizeDocumentElements,
} from "@/lib/extraction";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";

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
export function DocumentExtraction({ extraction }: { extraction: Record<string, unknown> }) {
  const parsed = parseStructuredExtraction(extraction);
  const stats = summarizeDocumentElements(parsed.elements);
  const hasSummary =
    parsed.rawText ||
    parsed.documentType ||
    parsed.confidence != null ||
    parsed.warnings.length > 0 ||
    parsed.elements.length > 0;

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
            <h4 className="mb-2 text-sm font-semibold text-foreground">
              {t("flow.extraction.elements")}
            </h4>
            <ol className="max-h-[520px] space-y-3 overflow-auto pr-1">
              {parsed.elements.map((element) => (
                <ElementItem key={`${element.order}-${element.kind}`} element={element} />
              ))}
            </ol>
          </section>
        </>
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

function ElementItem({ element }: { element: DocumentElement }) {
  return (
    <li className="rounded-md border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
          {elementKindLabel(element.kind)}
        </span>
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
          <span className="tnum rounded-full bg-success-bg px-2 py-0.5 text-xs text-success">
            {confidenceText(element.confidence)}
          </span>
        ) : null}
      </div>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/90">
        {element.text}
      </p>
    </li>
  );
}

function RawTextBlock({ rawText, compact }: { rawText: string; compact: boolean }) {
  if (!rawText) {
    return <p className="text-sm text-muted">{t("flow.extraction.noRawText")}</p>;
  }

  const content = (
    <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-card p-3 text-sm leading-6 text-foreground">
      {rawText}
    </pre>
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

function elementKindLabel(kind: string): string {
  return t(KIND_LABELS[kind] ?? "flow.extraction.kind.other");
}

function confidenceText(value: number | null): string {
  return value == null ? "—" : `${formatNumber(Math.round(value * 100))}%`;
}
