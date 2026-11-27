import { useId } from "react";
import { Table2 } from "lucide-react";
import {
  Banner, DataTable, INFORMATION_TABLE_ROW_CLASS, INFORMATION_TABLE_VISIBLE_ROWS,
  ListScrollRegion, ListToolbar, StatusBadge,
} from "@production-ready/ui";
import { t } from "@/lib/i18n";
import { listCountLabels, listScrollLabel } from "@/lib/pagination-labels";
import { DbObjectName } from "../components/DbObjectName";
import type { OntologyErDetails, OntologyErKeyRole } from "./erDetails";

function erKeyRoleLabel(role: OntologyErKeyRole): string {
  switch (role) {
    case "pk":
      return t("ontologyPlayground.erKeyRolePk");
    case "fk":
      return t("ontologyPlayground.erKeyRoleFk");
    case "pk_fk":
      return t("ontologyPlayground.erKeyRolePkFk");
    case "none":
    default:
      return t("ontologyPlayground.erKeyRoleNone");
  }
}

function erObjectTypeLabel(type: OntologyErDetails["objectType"]): string {
  if (type === "table") return t("nl2sql.ontology.nodeKind.table");
  if (type === "view") return t("nl2sql.ontology.nodeKind.view");
  return t("nl2sql.ontology.nodeKind.unknown");
}

export function OntologyErDetailsPanel({ details }: { details: OntologyErDetails }) {
  const titleId = useId();
  return (
    <section
      className="grid gap-4 rounded-md border border-border bg-surface p-3"
      aria-labelledby={titleId}
      data-testid="ontology-er-details-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3
            id={titleId}
            className="flex items-center gap-2 text-sm font-semibold text-fg"
          >
            <Table2 size={16} className="text-accent-fg" aria-hidden="true" />
            {t("ontologyPlayground.erDetailsTitle")}
          </h3>
          <p className="mt-1 leading-5">
            <DbObjectName value={details.objectName} size="xs" data-testid="ontology-er-detail-object-name" />
          </p>
        </div>
        <div className="flex flex-wrap gap-2" aria-label={t("ontologyPlayground.erSummary")}>
          <StatusBadge icon={false} variant="neutral" label={erObjectTypeLabel(details.objectType)} />
        </div>
      </div>

      {details.columns.length > 0 ? (
        // 列の件数は表の上のツールバーの ListCount 1 か所（見出しのバッジにしない。#4636）。
        <div className="grid min-w-0 gap-2">
        <ListToolbar count={{ total: details.columns.length, labels: listCountLabels() }} testId="ontology-er-columns-toolbar" />
        <DataTable
          columns={[
            {
              key: "columnName",
              header: t("ontologyPlayground.erColumnName"),
              headerClassName: "w-[24%]",
              className: "break-all py-3 font-mono leading-5",
              render: (column) => column.columnName,
            },
            {
              key: "dataType",
              header: t("ontologyPlayground.erDataType"),
              headerClassName: "w-[16%]",
              className: "break-all py-3 font-mono leading-5 text-fg-muted",
              render: (column) => column.dataType,
            },
            {
              key: "keyRole",
              header: t("ontologyPlayground.erKeyRole"),
              headerClassName: "w-[12%]",
              className: "py-3",
              render: (column) => erKeyRoleLabel(column.keyRole),
            },
            {
              key: "businessNameJa",
              header: t("ontologyPlayground.erBusinessName"),
              headerClassName: "w-[22%]",
              className: "break-words py-3",
              render: (column) => column.businessNameJa,
            },
            {
              key: "descriptionJa",
              header: t("ontologyPlayground.erDescription"),
              headerClassName: "w-[26%]",
              className: "break-words py-3 text-fg-muted",
              render: (column) => column.descriptionJa || "-",
            },
          ]}
          rows={details.columns}
          getRowKey={(column) => column.id}
          rowProps={(column) => ({ className: INFORMATION_TABLE_ROW_CLASS, "data-testid": `ontology-er-column-${column.id}` })}
          tableClassName="w-full min-w-[48rem] table-fixed"
          scrollTestId="ontology-er-columns"
          stickyHeader
          visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
        />
        </div>
      ) : (
        <Banner severity="info">{t("ontologyPlayground.erColumnsEmpty")}</Banner>
      )}

      <div className="grid gap-2" data-testid="ontology-er-joins">
        <h4 className="text-xs font-semibold text-fg-muted">
          {t("ontologyPlayground.erJoinConditions")}
        </h4>
        {details.joins.length > 0 ? (
          <ListScrollRegion label={listScrollLabel(t("ontologyPlayground.erJoinConditions"))} className="grid gap-2">
            {details.joins.map((join) => (
              <div
                key={join.id}
                className="grid gap-1 rounded-md border border-border bg-surface-sunken px-3 py-2"
                data-testid={`ontology-er-join-${join.id}`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-fg">
                    {join.relationshipNameJa}
                  </span>
                  <span className="text-xs text-fg-muted">{join.cardinality}</span>
                </div>
                <p className="text-xs leading-5 text-fg-muted">
                  {join.sourceLabel} → {join.targetLabel}
                </p>
                <code data-surface-tint="neutral" className="break-all rounded bg-surface-hover px-2 py-1 font-mono text-xs leading-5 text-fg">
                  {join.joinCondition}
                </code>
              </div>
            ))}
          </ListScrollRegion>
        ) : (
          <p className="text-sm leading-6 text-fg-muted">{t("ontologyPlayground.erJoinsEmpty")}</p>
        )}
      </div>
    </section>
  );
}
