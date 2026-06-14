import { X } from "lucide-react";
import type { ReactNode } from "react";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import { toneIcon, toneRole, toneSurface, type FeedbackTone } from "./feedback-tone";

/**
 * Banner（ページ/セクション常設の状況提示）。docs/frontend-messaging-spec.md §3.4。
 * 「設定未完了」「縮退モード」等の状況に使う。一時的な成功は Toast を使う。
 */
export function Banner({
  severity,
  title,
  children,
  action,
  onDismiss,
  className,
}: {
  severity: FeedbackTone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  /** 指定すると閉じる × を表示する。 */
  onDismiss?: () => void;
  className?: string;
}) {
  const Icon = toneIcon[severity];

  return (
    <div
      role={toneRole(severity)}
      className={cn(
        "flex items-start gap-2.5 rounded-lg border px-3.5 py-3 text-sm",
        toneSurface[severity],
        className
      )}
    >
      <Icon size={16} className="mt-0.5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? (
          <div className={cn("text-foreground/90", title && "mt-0.5")}>{children}</div>
        ) : null}
        {action ? <div className="mt-2 flex flex-wrap gap-2">{action}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={t("common.dismiss")}
          className="-mr-1 -mt-0.5 inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-current/70 transition-colors hover:bg-foreground/5"
        >
          <X size={14} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
