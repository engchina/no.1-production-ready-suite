import type { ReactNode } from "react";
import { t } from "@/lib/i18n";
import { formatDateTimeWithYear } from "@/lib/format";
import { StatusBadge } from "@/components/ui/status-badge";
import { INFORMATION_TABLE_SCROLL_CLASS } from "@/lib/list-density";

export const ontologyInputClass =
  "h-[44px] min-w-0 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30";
export const ontologyTextareaClass =
  "min-h-28 min-w-0 w-full rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:bg-muted/30";
export function versionLabel(version?: number | null) {
  return version && Number.isInteger(version) && version > 0
    ? `v${version}`
    : t("ontologyUi.versionUnavailable");
}
export function versionOption(item: {
  display_version?: number | null;
  created_at?: string;
  published_at?: string;
  status?: "draft" | "published";
}) {
  return [
    versionLabel(item.display_version),
    item.status ? t(`ontologyResults.status.${item.status}`) : "",
    formatDateTimeWithYear(item.created_at ?? item.published_at),
  ]
    .filter(Boolean)
    .join(" · ");
}
export function TechnicalDetails({
  value,
  children,
}: {
  value?: unknown;
  children?: ReactNode;
}) {
  return (
    <details className="min-w-0 rounded-md border border-border bg-background p-3">
      <summary className="cursor-pointer text-sm font-medium">
        {t("ontologyUi.technicalDetails")}
      </summary>
      {children ?? (
        <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </details>
  );
}
export function ResultStatus({ status }: { status: string }) {
  const key = `ontologyWorkspace.job.${status}` as Parameters<typeof t>[0];
  const review = ["draft", "published", "reviewed", "unreviewed"].includes(
    status,
  );
  return (
    <span className="w-fit">
      <StatusBadge
        variant={
          ["succeeded", "published", "reviewed", "available"].includes(status)
            ? "success"
            : status === "failed"
              ? "danger"
              : ["running", "claimed"].includes(status)
                ? "info"
                : status === "unreviewed" || status === "configuration_required"
                  ? "warning"
                  : "neutral"
        }
        label={
          review
            ? t(`ontologyResults.status.${status}` as Parameters<typeof t>[0])
            : status === "available"
              ? t("ontologyCapability.available")
              : status === "configuration_required"
                ? t("ontologyCapability.configuration")
                : t(key) === key
                  ? status
                  : t(key)
        }
      />
    </span>
  );
}
export function fieldLabel(field: string) {
  const key = `ontologyResults.field.${field}` as Parameters<typeof t>[0];
  return t(key) === key ? field : t(key);
}
export function DefinitionValue({
  value,
  names = {},
}: {
  value: unknown;
  names?: Record<string, string>;
}) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length))
    return <span>{t("ontologyResults.unspecified")}</span>;
  if (typeof value === "boolean")
    return (
      <span>{t(value ? "ontologyResults.yes" : "ontologyResults.no")}</span>
    );
  if (Array.isArray(value))
    return (
      <ul className="grid gap-1">
        {value.map((item, index) => (
          <li key={index}>
            <DefinitionValue value={item} names={names} />
          </li>
        ))}
      </ul>
    );
  if (typeof value === "object")
    return (
      <dl className="grid gap-2">
        {Object.entries(value).map(([key, item]) => (
          <div key={key}>
            <dt className="font-medium">{fieldLabel(key)}</dt>
            <dd className="pl-3">
              <DefinitionValue value={item} names={names} />
            </dd>
          </div>
        ))}
      </dl>
    );
  return (
    <span className="break-words [overflow-wrap:anywhere]">
      {names[String(value)] ?? String(value)}
    </span>
  );
}
export function DataTable({
  headers,
  children,
  label,
}: {
  headers: string[];
  children: ReactNode;
  label: string;
}) {
  return (
    <div
      className={`${INFORMATION_TABLE_SCROLL_CLASS} rounded-md border border-border`}
      tabIndex={0}
      role="region"
      aria-label={label}
    >
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-background">
          <tr>
            {headers.map((header) => (
              <th
                key={header}
                scope="col"
                className="whitespace-nowrap border-b border-border px-3 py-2 text-xs font-semibold"
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="[&_td]:border-b [&_td]:border-border [&_td]:px-3 [&_td]:py-3 [&_td]:align-top [&_td]:[overflow-wrap:anywhere]">
          {children}
        </tbody>
      </table>
    </div>
  );
}
export function ChangesTable({
  before,
  after,
}: {
  before: Record<string, unknown>[];
  after: Record<string, unknown>[];
}) {
  const identity = (item: Record<string, unknown>) =>
    String(item.id ?? item.api_name);
  const previous = new Map(before.map((item) => [identity(item), item]));
  const next = new Map(after.map((item) => [identity(item), item]));
  const rows = [...new Set([...previous.keys(), ...next.keys()])].flatMap(
    (id) => {
      const a = previous.get(id) ?? {},
        b = next.get(id) ?? {};
      return [...new Set([...Object.keys(a), ...Object.keys(b)])]
        .filter(
          (key) =>
            key !== "id" && JSON.stringify(a[key]) !== JSON.stringify(b[key]),
        )
        .map((key) => ({
          id,
          key,
          name: String(b.name_ja ?? a.name_ja ?? b.api_name ?? a.api_name),
          before: a[key],
          after: b[key],
        }));
    },
  );
  return (
    <DataTable
      label={t("ontologyUi.changes")}
      headers={[
        t("ontologyResults.definition"),
        t("ontologyUi.field"),
        t("ontologyCapability.before"),
        t("ontologyCapability.after"),
      ]}
    >
      {rows.map((row) => (
        <tr key={`${row.id}:${row.key}`}>
          <td>{row.name}</td>
          <td>{fieldLabel(row.key)}</td>
          <td>
            <DefinitionValue value={row.before} />
          </td>
          <td>
            <DefinitionValue value={row.after} />
          </td>
        </tr>
      ))}
    </DataTable>
  );
}

export function EffectTable({
  before = {},
  after = {},
}: {
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
}) {
  return (
    <DataTable
      label={t("ontologyUi.changes")}
      headers={[
        t("ontologyCapability.property"),
        t("ontologyCapability.before"),
        t("ontologyCapability.after"),
      ]}
    >
      {[...new Set([...Object.keys(before), ...Object.keys(after)])].map(
        (key) => (
          <tr key={key}>
            <td>{key}</td>
            <td>
              <DefinitionValue value={before[key]} />
            </td>
            <td>
              <DefinitionValue value={after[key]} />
            </td>
          </tr>
        ),
      )}
    </DataTable>
  );
}
