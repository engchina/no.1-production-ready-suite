import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function ContentActionBar({
  actionsClassName,
  ariaLabel,
  children,
  className,
  description,
  leading,
  meta,
  testId,
  title,
}: {
  actionsClassName?: string;
  ariaLabel: string;
  children: ReactNode;
  className?: string;
  description?: ReactNode;
  leading?: ReactNode;
  meta?: ReactNode;
  testId?: string;
  title?: ReactNode;
}) {
  const hasInfo = leading || title || description || meta;

  return (
    <div
      className={cn(
        "flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between",
        !hasInfo && "sm:items-center sm:justify-end",
        className
      )}
      data-testid={testId}
    >
      {leading ? (
        <div className="min-w-0">{leading}</div>
      ) : hasInfo ? (
        <div className="min-w-0 space-y-1">
          {title ? <div className="text-sm font-semibold text-foreground">{title}</div> : null}
          {description ? (
            <div className="text-sm leading-6 text-muted">{description}</div>
          ) : null}
          {meta ? <div className="text-xs leading-5 text-muted">{meta}</div> : null}
        </div>
      ) : null}
      <div
        role="group"
        aria-label={ariaLabel}
        className={cn(
          "flex min-w-0 flex-wrap items-center justify-end gap-2",
          !hasInfo && "w-full",
          actionsClassName
        )}
      >
        {children}
      </div>
    </div>
  );
}
