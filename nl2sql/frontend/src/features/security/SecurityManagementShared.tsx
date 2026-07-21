import { Children, type ReactNode } from "react";
import { Search, type LucideIcon } from "lucide-react";

import { FixedSplitPane } from "@/components/layout/FixedSplitPane";
import { t } from "@/lib/i18n";
import type { FixedSplitWidePane } from "@/lib/fixed-split-pane";
import { cn } from "@/lib/utils";

export interface SecurityManagementMetric {
  label: string;
  value: string;
  emphasis?: boolean;
  testId?: string;
}

export function SecurityManagementPanelShell({
  id,
  labelledBy,
  ariaLabel,
  idPrefix,
  className = "",
  splitId,
  preferredWidePane = "right",
  children,
}: {
  id: string;
  labelledBy?: string;
  ariaLabel?: string;
  idPrefix: string;
  className?: string;
  splitId?: string;
  preferredWidePane?: FixedSplitWidePane;
  children: ReactNode;
}) {
  const panelChildren = Children.toArray(children);
  const splitPaneId = splitId && panelChildren.length === 2 ? splitId : null;

  return (
    <section
      id={id}
      role="region"
      aria-labelledby={labelledBy}
      aria-label={ariaLabel}
      className={cn("grid gap-4 rounded-md border border-border bg-card p-4 shadow-sm", className)}
      data-testid="security-management-panel-shell"
      data-management-id={idPrefix}
    >
      {splitPaneId ? (
        <FixedSplitPane
          splitId={splitPaneId}
          preferredWidePane={preferredWidePane}
          left={panelChildren[0]}
          right={panelChildren[1]}
        />
      ) : (
        children
      )}
    </section>
  );
}

export function SecurityPanelHeader({
  title,
  description,
  icon: Icon,
  headingId,
  action,
}: {
  title: string;
  description?: string;
  icon: LucideIcon;
  headingId?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h2 id={headingId} className="flex items-center gap-2 text-base font-semibold text-foreground">
          <Icon size={18} aria-hidden="true" />
          <span className="min-w-0 break-words">{title}</span>
        </h2>
        {description ? <p className="mt-1 text-sm leading-6 text-muted">{description}</p> : null}
      </div>
      {action ? <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">{action}</div> : null}
    </div>
  );
}

export function SecurityManagementStatusBar({
  ariaLabel,
  metrics,
  actions,
}: {
  ariaLabel: string;
  metrics: SecurityManagementMetric[];
  actions?: ReactNode;
}) {
  return (
    <section className="rounded-md border border-border bg-card px-4 py-3 shadow-sm" aria-label={ariaLabel}>
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <dl className="grid gap-3 sm:grid-cols-3 xl:flex xl:flex-wrap xl:items-center">
          {metrics.map((metric) => (
            <div key={`${metric.label}-${metric.value}`} className="rounded-md border border-border bg-background px-3 py-2">
              <dt className="text-xs font-medium text-muted">{metric.label}</dt>
              <dd
                className={cn("mt-1 font-semibold tabular-nums text-foreground", metric.emphasis && "text-lg")}
                data-testid={metric.testId}
              >
                {metric.value}
              </dd>
            </div>
          ))}
        </dl>
        {actions ? <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">{actions}</div> : null}
      </div>
    </section>
  );
}

export function SecuritySearchField({
  label,
  placeholder,
  value,
  testId,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string;
  testId?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
      <span>{label}</span>
      <span className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden="true" />
        <input
          type="search"
          value={value}
          data-testid={testId}
          onChange={(event) => onChange(event.currentTarget.value)}
          className="min-h-11 w-full rounded-md border border-border bg-card py-2 pl-9 pr-3 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
          placeholder={placeholder}
        />
      </span>
    </label>
  );
}

export function SecurityDetailField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 min-w-0 break-words text-sm font-medium text-foreground">{children}</dd>
    </div>
  );
}

export function SecurityEmptySelection({ title, hint }: { title: string; hint: string }) {
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4">
      <div className="py-10 text-center">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        <p className="mt-1 text-sm leading-6 text-muted">{hint}</p>
      </div>
    </section>
  );
}

export function securityFilteredCount(filtered: number, total: number) {
  return t("security.common.filteredCount", { filtered, total });
}
