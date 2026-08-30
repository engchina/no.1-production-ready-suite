import type { StatusVariant } from "@engchina/production-ready-ui";

import { cn } from "@/lib/utils";

export type { StatusVariant };

const VARIANT_STYLES: Record<StatusVariant, string> = {
  neutral: "border-border bg-card text-muted",
  info: "border-info/30 bg-info-bg text-info",
  pending: "border-warning/30 bg-warning-bg text-warning",
  success: "border-success/30 bg-success-bg text-success",
  warning: "border-warning/30 bg-warning-bg text-warning",
  danger: "border-danger/30 bg-danger-bg text-danger",
};

/** NL2SQL の light/dark semantic token に追従するステータスバッジ。 */
export function StatusBadge({
  variant,
  label,
  className,
}: {
  variant: StatusVariant;
  label: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xs font-medium",
        VARIANT_STYLES[variant],
        className
      )}
      data-status-variant={variant}
    >
      {label}
    </span>
  );
}
