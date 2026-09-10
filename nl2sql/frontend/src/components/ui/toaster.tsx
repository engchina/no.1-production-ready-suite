import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { cn, useToastStore, toneIcon, toneRole, toneText, MessageText, type ToastItem } from "@engchina/production-ready-ui";
import { Button } from "./button";
import { t } from "@/lib/i18n";

export interface ToasterProps {
  dismissLabel?: string;
  regionLabel?: string;
  placement?: "bottom-left" | "bottom-right";
}

/**
 * Toast 表示領域（既定は画面右下スタック）。
 * フォーカスを奪わず aria-live で読み上げる（toast-accessibility）。
 * アプリ最上位で一度だけ描画する。
 *
 * 閉じるボタンの aria ラベルは `dismissLabel` で注入（既定「閉じる」）。
 */
export function Toaster({
  dismissLabel = t("common.dismiss"),
  regionLabel = t("common.notifications"),
  placement = "bottom-right",
}: ToasterProps = {}) {
  const toasts = useToastStore((state) => state.toasts);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);
  if (!mounted) return null;

  return createPortal(
    <div
      role="region"
      aria-label={regionLabel}
      aria-live="polite"
      aria-relevant="additions"
      className="pointer-events-none fixed z-[1000] flex max-h-[calc(100dvh-2rem)] w-[min(92vw,22rem)] flex-col gap-2 overflow-y-auto"
      style={{
        bottom: "max(1rem, env(safe-area-inset-bottom))",
        ...(placement === "bottom-left"
          ? { left: "max(1rem, env(safe-area-inset-left))" }
          : { right: "max(1rem, env(safe-area-inset-right))" }),
      }}
    >
      {toasts.map((item) => (
        <ToastCard key={item.id} item={item} dismissLabel={dismissLabel} />
      ))}
    </div>,
    document.body
  );
}

function ToastCard({ item, dismissLabel }: { item: ToastItem; dismissLabel: string }) {
  const dismiss = useToastStore((state) => state.dismiss);
  const Icon = toneIcon[item.tone];

  return (
    <div
      role={toneRole(item.tone)}
      className="animate-toast-in pointer-events-auto flex items-start gap-2.5 rounded-lg border border-border bg-card px-3.5 py-3 shadow-lg"
    >
      <Icon size={16} className={cn("mt-0.5 shrink-0", toneText[item.tone])} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-relaxed text-foreground">
          <MessageText text={item.message} />
        </p>
        {item.description ? (
          <p className="mt-0.5 text-xs leading-relaxed text-muted">
            <MessageText text={item.description} />
          </p>
        ) : null}
        {item.action ? (
          <Button
            variant="ghost"
            size="sm"
            type="button"
            onClick={() => {
              item.action?.onClick();
              dismiss(item.id);
            }}
            className="mt-1.5"
          >
            {item.action.label}
          </Button>
        ) : null}
      </div>
      <Button
        variant="ghost"
        size="sm"
        type="button"
        onClick={() => dismiss(item.id)}
        aria-label={dismissLabel}
        iconOnly touchTarget
      >
        <X size={14} aria-hidden />
      </Button>
    </div>
  );
}
