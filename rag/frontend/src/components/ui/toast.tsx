import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { t } from "@/lib/i18n";
import { useToastStore, type ToastItem } from "@/lib/toast";
import { cn } from "@/lib/utils";

import { toneIcon, toneRole, toneText } from "./feedback-tone";

/**
 * Toast 表示領域（画面右下スタック）。docs/frontend-messaging-spec.md §3.1。
 * フォーカスを奪わず aria-live で読み上げる（toast-accessibility）。
 * アプリ最上位で一度だけ描画する。
 */
export function Toaster() {
  const toasts = useToastStore((state) => state.toasts);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);
  if (!mounted) return null;

  return createPortal(
    <div
      aria-live="polite"
      aria-relevant="additions"
      className="pointer-events-none fixed bottom-4 right-4 z-[1000] flex w-[min(92vw,22rem)] flex-col gap-2"
    >
      {toasts.map((item) => (
        <ToastCard key={item.id} item={item} />
      ))}
    </div>,
    document.body
  );
}

function ToastCard({ item }: { item: ToastItem }) {
  const dismiss = useToastStore((state) => state.dismiss);
  const Icon = toneIcon[item.tone];

  return (
    <div
      role={toneRole(item.tone)}
      className="animate-toast-in pointer-events-auto flex items-start gap-2.5 rounded-lg border border-border bg-card px-3.5 py-3 shadow-lg"
    >
      <Icon size={16} className={cn("mt-0.5 shrink-0", toneText[item.tone])} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{item.message}</p>
        {item.description ? (
          <p className="mt-0.5 text-xs text-muted">{item.description}</p>
        ) : null}
        {item.action ? (
          <button
            type="button"
            onClick={() => {
              item.action?.onClick();
              dismiss(item.id);
            }}
            className="mt-1.5 cursor-pointer text-xs font-medium text-primary hover:underline"
          >
            {item.action.label}
          </button>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => dismiss(item.id)}
        aria-label={t("common.dismiss")}
        className="-mr-1 -mt-0.5 inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted transition-colors hover:bg-background hover:text-foreground"
      >
        <X size={14} aria-hidden />
      </button>
    </div>
  );
}
