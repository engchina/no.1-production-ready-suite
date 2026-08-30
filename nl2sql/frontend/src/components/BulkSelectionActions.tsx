import { CheckSquare, X } from "lucide-react";

import { Button, type ButtonProps } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface BulkSelectionActionsProps {
  selectLabel: string;
  clearLabel: string;
  onSelectAll: () => void;
  onClearAll: () => void;
  selectAriaLabel?: string;
  clearAriaLabel?: string;
  selectDisabled?: boolean;
  clearDisabled?: boolean;
  busy?: boolean;
  size?: ButtonProps["size"];
  className?: string;
  dataTestId?: string;
}

export function BulkSelectionActions({
  selectLabel,
  clearLabel,
  onSelectAll,
  onClearAll,
  selectAriaLabel,
  clearAriaLabel,
  selectDisabled = false,
  clearDisabled = false,
  busy = false,
  size = "sm",
  className,
  dataTestId,
}: BulkSelectionActionsProps) {
  return (
    <div
      role="group"
      aria-busy={busy || undefined}
      className={cn("flex min-w-0 flex-wrap items-center justify-start gap-2", className)}
      data-testid={dataTestId}
    >
      <Button
        type="button"
        variant="secondary"
        size={size}
        className="whitespace-nowrap"
        aria-label={selectAriaLabel ?? selectLabel}
        disabled={busy || selectDisabled}
        data-testid={dataTestId ? `${dataTestId}-select` : undefined}
        onClick={onSelectAll}
      >
        <CheckSquare size={14} aria-hidden="true" />
        <span>{selectLabel}</span>
      </Button>
      <Button
        type="button"
        variant="ghost"
        size={size}
        className="whitespace-nowrap"
        aria-label={clearAriaLabel ?? clearLabel}
        disabled={busy || clearDisabled}
        data-testid={dataTestId ? `${dataTestId}-clear` : undefined}
        onClick={onClearAll}
      >
        <X size={14} aria-hidden="true" />
        <span>{clearLabel}</span>
      </Button>
    </div>
  );
}
