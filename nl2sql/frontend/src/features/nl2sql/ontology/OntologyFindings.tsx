import { StatusBadge } from "@engchina/production-ready-ui";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { t } from "@/lib/i18n";
import type { OntologyFinding } from "./types";

/** 表示だけを集約し、公開版に保存した対象・コード・原文は変更しない。 */
export function OntologyFindings({ findings, label }: {
  findings: OntologyFinding[];
  label: string;
}) {
  const groups = new Map<string, { message: string; severity: string; items: OntologyFinding[] }>();
  for (const finding of findings) {
    const message = (finding.message_ja || finding.message || "").trim();
    if (!message) continue;
    const key = `${finding.severity}:${message.replace(/\s+/g, " ")}`;
    const group = groups.get(key);
    if (group) group.items.push(finding);
    else groups.set(key, { message, severity: finding.severity, items: [finding] });
  }
  if (!groups.size) return null;
  const ordered = [...groups.entries()].sort(([, a], [, b]) =>
    Number(b.severity === "error") - Number(a.severity === "error")
  );
  return <div role="region" aria-label={label} tabIndex={0}
    className="max-h-72 min-w-0 overflow-y-auto overscroll-contain rounded-md border border-border bg-surface p-4">
    <ul className="grid min-w-0 gap-4">
      {ordered.map(([key, group]) => <li key={key} className="grid min-w-0 gap-2 border-b border-border pb-4 last:border-b-0 last:pb-0">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge variant={group.severity === "error" ? "danger" : "warning"}
            label={t(group.severity === "error" ? "markdownOntology.blockingError" : "markdownOntology.warning")} />
          <span className="text-xs text-fg-muted">{t("markdownOntology.occurrences", { count: group.items.length })}</span>
        </div>
        <p className="whitespace-pre-wrap break-words text-sm">{group.message}</p>
        <details className="group/disclosure min-w-0 text-xs">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring [&::-webkit-details-marker]:hidden">
            <span>{t("markdownOntology.findingTargets")}</span>
            <DisclosureChevron expanded="group" size={16} className="text-fg-muted" />
          </summary>
          <ul className="grid min-w-0 gap-1 pt-2">
            {[...new Set(group.items.map(item => [item.definition_id, item.field, item.code].filter(Boolean).join(" / ") || t("markdownOntology.generalFinding")))].map(target =>
              <li key={target} className="break-all text-fg-muted">{target}</li>
            )}
          </ul>
        </details>
      </li>)}
    </ul>
  </div>;
}
