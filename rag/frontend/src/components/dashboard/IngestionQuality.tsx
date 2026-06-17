import {
  BookOpen,
  CircleGauge,
  FileCheck2,
  FileImage,
  FileWarning,
  Layers3,
  ListChecks,
  RefreshCcw,
  ShieldAlert,
  Sigma,
  Table2,
  TriangleAlert,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { DashboardIngestionQuality } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";

/** 構造化取込のカバレッジと chunk metadata 分布。 */
export function IngestionQuality({ quality }: { quality: DashboardIngestionQuality }) {
  const coverage =
    quality.document_count > 0
      ? Math.round((quality.structured_document_count / quality.document_count) * 100)
      : 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Layers3 size={16} className="text-primary" aria-hidden />
          {t("dashboard.ingestionQuality.title")}
        </CardTitle>
        <CardDescription>{t("dashboard.ingestionQuality.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="font-medium text-foreground">
              {t("dashboard.ingestionQuality.structuredCoverage")}
            </span>
            <span className="tnum text-muted">{coverage}%</span>
          </div>
          <div
            className="mt-2 h-2 rounded-full bg-background"
            role="meter"
            aria-label={t("dashboard.ingestionQuality.structuredCoverage")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={coverage}
          >
            <div
              className="h-full rounded-full bg-primary"
              style={{ width: `${coverage}%` }}
            />
          </div>
          <p className="tnum mt-2 text-xs text-muted">
            {t("dashboard.ingestionQuality.structuredDocuments", {
              structured: quality.structured_document_count,
              total: quality.document_count,
            })}
          </p>
        </div>

        <MetricGroup title={t("dashboard.ingestionQuality.structureMetrics")}>
          <Metric
            icon={Layers3}
            label={t("dashboard.ingestionQuality.elements")}
            value={quality.element_count}
          />
          <Metric
            icon={BookOpen}
            label={t("dashboard.ingestionQuality.pages")}
            value={quality.page_count}
          />
          <Metric
            icon={Table2}
            label={t("dashboard.ingestionQuality.tables")}
            value={quality.table_count}
          />
          <Metric
            icon={ListChecks}
            label={t("dashboard.ingestionQuality.lists")}
            value={quality.list_count}
          />
          <Metric
            icon={FileImage}
            label={t("dashboard.ingestionQuality.figures")}
            value={quality.figure_count ?? 0}
          />
          <Metric
            icon={Sigma}
            label={t("dashboard.ingestionQuality.formulas")}
            value={quality.formula_count ?? 0}
          />
        </MetricGroup>

        <MetricGroup title={t("dashboard.ingestionQuality.healthMetrics")}>
          <Metric
            icon={CircleGauge}
            label={t("dashboard.ingestionQuality.pageCoverage")}
            value={formatPercent(quality.average_page_coverage ?? 0)}
          />
          <Metric
            icon={TriangleAlert}
            label={t("dashboard.ingestionQuality.lowConfidence")}
            value={quality.low_confidence_count ?? 0}
          />
          <Metric
            icon={ShieldAlert}
            label={t("dashboard.ingestionQuality.fallbacks")}
            value={quality.fallback_document_count ?? 0}
          />
          <Metric
            icon={FileWarning}
            label={t("dashboard.ingestionQuality.failedSegments")}
            value={quality.failed_segment_document_count ?? 0}
          />
          <Metric
            icon={RefreshCcw}
            label={t("dashboard.ingestionQuality.segmentArtifactMisses")}
            value={quality.segment_artifact_cache_miss_document_count ?? 0}
          />
          <Metric
            icon={BookOpen}
            label={t("dashboard.ingestionQuality.longDocuments")}
            value={quality.long_document_count ?? 0}
          />
        </MetricGroup>

        <Distribution
          title={t("dashboard.ingestionQuality.chunkProfiles")}
          values={quality.chunk_profile_counts}
          empty={t("dashboard.ingestionQuality.emptyDistribution")}
        />
        <Distribution
          title={t("dashboard.ingestionQuality.parserBackends")}
          values={quality.parser_backend_counts ?? {}}
          empty={t("dashboard.ingestionQuality.emptyDistribution")}
        />
        <Distribution
          title={t("dashboard.ingestionQuality.contentKinds")}
          values={quality.content_kind_counts}
          empty={t("dashboard.ingestionQuality.emptyDistribution")}
        />
      </CardContent>
    </Card>
  );
}

function MetricGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold text-foreground">{title}</h3>
      <div className="grid grid-cols-2 gap-3">{children}</div>
    </section>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: number | string;
}) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="flex items-start gap-2 text-xs leading-5 text-muted">
        <Icon size={14} className="mt-0.5 shrink-0 text-primary" aria-hidden />
        <span className="min-w-0">{label}</span>
      </div>
      <p className="tnum mt-2 text-lg font-semibold text-foreground">
        {typeof value === "number" ? formatNumber(value) : value}
      </p>
    </div>
  );
}

function Distribution({
  title,
  values,
  empty,
}: {
  title: string;
  values: Record<string, number>;
  empty: string;
}) {
  const entries = Object.entries(values).sort((left, right) => right[1] - left[1]);
  const max = Math.max(1, ...entries.map(([, count]) => count));

  return (
    <section className="space-y-2 border-t border-border pt-4">
      <h3 className="flex items-center gap-2 text-xs font-semibold text-foreground">
        <FileCheck2 size={14} className="text-primary" aria-hidden />
        {title}
      </h3>
      {entries.length === 0 ? (
        <p className="text-xs text-muted">{empty}</p>
      ) : (
        <ul className="space-y-2">
          {entries.map(([label, count]) => (
            <li key={label} className="space-y-1">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="min-w-0 truncate text-muted" title={label}>
                  {distributionLabel(label)}
                </span>
                <span className="tnum font-medium text-foreground">{formatNumber(count)}</span>
              </div>
              <div className="h-1.5 rounded-full bg-background" aria-hidden>
                <div
                  className="h-full rounded-full bg-primary/70"
                  style={{ width: `${Math.max(8, (count / max) * 100)}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function distributionLabel(value: string): string {
  switch (value) {
    case "text":
      return t("search.filters.contentKind.text");
    case "list":
      return t("search.filters.contentKind.list");
    case "table":
      return t("search.filters.contentKind.table");
    case "figure":
      return t("search.filters.contentKind.figure");
    case "equation":
      return t("search.filters.contentKind.equation");
    case "code":
      return t("search.filters.contentKind.code");
    case "email":
      return t("search.filters.contentKind.email");
    case "slide":
      return t("search.filters.contentKind.slide");
    case "sheet":
      return t("search.filters.contentKind.sheet");
    case "unknown":
      return t("dashboard.ingestionQuality.unknown");
    case "enterprise_ai":
      return t("dashboard.ingestionQuality.parserBackend.enterpriseAi");
    case "local_partition":
      return t("dashboard.ingestionQuality.parserBackend.localPartition");
    case "unsupported":
      return t("dashboard.ingestionQuality.parserBackend.unsupported");
    default:
      return value;
  }
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
