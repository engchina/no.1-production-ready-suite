import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { cn } from "../../lib/utils";
import { useToastStore, type ToastItem } from "../../store/toast-store";

import { Button } from "./button";
import { toneIcon, toneRole, toneText } from "./feedback-tone";
import { MessageText } from "./message-text";

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
 * 重なり順はモーダルの下（`--z-toast` < `--z-scrim` < `--z-dialog`）。モーダル中はモーダル外を操作できないため、
 * 通知を上に重ねても押せず、確認ダイアログのボタンを覆うだけになる。
 *
 * 閉じるボタンの aria ラベルは `dismissLabel` で注入（既定「閉じる」）。
 */
export function Toaster({
  dismissLabel = "閉じる",
  regionLabel = "通知",
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
      className="pointer-events-none fixed z-[var(--z-toast)] flex max-h-[calc(100dvh-2rem)] w-[min(92vw,22rem)] flex-col gap-2 overflow-y-auto"
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
      className="animate-toast-in pointer-events-auto flex items-start gap-2.5 rounded-lg border border-border bg-surface-raised px-3.5 py-3 shadow-[var(--shadow-toast)]"
    >
      <Icon size={16} className={cn("mt-0.5 shrink-0", toneText[item.tone])} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium leading-relaxed text-fg">
          <MessageText text={item.message} />
        </p>
        {item.description ? (
          <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">
            <MessageText text={item.description} />
          </p>
        ) : null}
        {item.action ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
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
        type="button"
        variant="ghost"
        size="sm"
        iconOnly
        touchTarget
        icon={X}
        onClick={() => dismiss(item.id)}
        aria-label={dismissLabel}
        className="-mr-2 -mt-2 text-fg-muted hover:enabled:text-fg"
      />
    </div>
  );
}
