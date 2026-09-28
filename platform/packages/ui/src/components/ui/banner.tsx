import { X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

import { Button } from "./button";
import { toneIcon, toneRole, toneSurface, type FeedbackTone } from "./feedback-tone";
import { MessageText } from "./message-text";

/**
 * Banner（ページ/セクション常設の状況提示）。
 * 「設定未完了」「縮退モード」等の状況に使う。一時的な成功は Toast を使う。
 *
 * i18n はパッケージに持ち込まないため、閉じるボタンの aria ラベルは `dismissLabel` で注入する
 * （未指定時は日本語の既定値「閉じる」）。
 * 閉じるボタンは Toast と同じ共有 `Button`（ghost・iconOnly・44px）。色はトーンの文字色に合わせ、
 * ホバーの面はトーンの淡い地の上で見える `fg` の薄い重ねにする。
 */
export function Banner({
  severity,
  title,
  children,
  action,
  onDismiss,
  dismissLabel = "閉じる",
  className,
}: {
  severity: FeedbackTone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  /** 指定すると閉じる × を表示する。 */
  onDismiss?: () => void;
  /** 閉じるボタンの aria-label（i18n 文字列を注入。既定「閉じる」）。 */
  dismissLabel?: string;
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
        {title ? (
          <p className="font-medium leading-relaxed">
            <MessageText text={title} />
          </p>
        ) : null}
        {children ? (
          <div className={cn("leading-relaxed text-fg/90", title && "mt-0.5")}>
            {typeof children === "string" ? <MessageText text={children} /> : children}
          </div>
        ) : null}
        {action ? <div className="mt-2 flex flex-wrap gap-2">{action}</div> : null}
      </div>
      {onDismiss ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          iconOnly
          touchTarget
          icon={X}
          onClick={onDismiss}
          aria-label={dismissLabel}
          className="-mr-2 -mt-2 shrink-0 text-current hover:enabled:bg-fg/5 active:enabled:bg-fg/10"
        />
      ) : null}
    </div>
  );
}
