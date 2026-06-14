import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import { Button } from "./button";
import { toneIcon, type FeedbackTone } from "./feedback-tone";

/**
 * ConfirmDialog（確認ダイアログ）。docs/frontend-messaging-spec.md §3.5。
 * 破壊的・不可逆操作の確認ゲート。`useConfirm()` で Promise<boolean> を await する。
 * フォーカストラップ / Esc キャンセル / トリガーへフォーカス復帰に対応。
 */

export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** confirm ボタンのトーン。破壊的操作は "danger"。 */
  tone?: Extract<FeedbackTone, "danger" | "warning" | "info">;
  /** オーバーレイクリックでキャンセルを許可（既定 true）。誤操作防止で false にできる。 */
  dismissOnOverlay?: boolean;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/** 確認ダイアログを開いて結果を待つ。Provider 配下でのみ利用可能。 */
export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm は <ConfirmProvider> の配下で使用してください。");
  }
  return ctx;
}

interface DialogState {
  options: ConfirmOptions;
  resolve: (value: boolean) => void;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DialogState | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      setState({ options, resolve });
    });
  }, []);

  const settle = useCallback(
    (value: boolean) => {
      setState((current) => {
        current?.resolve(value);
        return null;
      });
    },
    []
  );

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state ? (
        <ConfirmDialog
          options={state.options}
          onCancel={() => settle(false)}
          onConfirm={() => settle(true)}
        />
      ) : null}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({
  options,
  onCancel,
  onConfirm,
}: {
  options: ConfirmOptions;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { title, description, tone = "danger", dismissOnOverlay = true } = options;
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<Element | null>(null);
  const Icon = toneIcon[tone];

  // 開いたら確認ボタンへフォーカス、閉じたらトリガーへ復帰。
  useEffect(() => {
    previouslyFocused.current = document.activeElement;
    confirmRef.current?.focus();
    return () => {
      if (previouslyFocused.current instanceof HTMLElement) {
        previouslyFocused.current.focus();
      }
    };
  }, []);

  // Esc でキャンセル + 簡易フォーカストラップ。
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = panelRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  const confirmVariant = tone === "danger" ? "danger" : "primary";

  return createPortal(
    <div
      className="animate-overlay-in fixed inset-0 z-[1000] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && dismissOnOverlay) onCancel();
      }}
    >
      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={description ? "confirm-desc" : undefined}
        className="animate-dialog-in w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-xl"
      >
        <div className="flex items-start gap-3">
          <span
            className={cn(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-full",
              tone === "danger" && "bg-danger-bg text-danger",
              tone === "warning" && "bg-warning-bg text-warning",
              tone === "info" && "bg-info-bg text-info"
            )}
          >
            <Icon size={18} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="confirm-title" className="text-base font-semibold text-foreground">
              {title}
            </h2>
            {description ? (
              <p id="confirm-desc" className="mt-1 text-sm text-muted">
                {description}
              </p>
            ) : null}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel}>
            {options.cancelLabel ?? t("common.cancel")}
          </Button>
          <Button ref={confirmRef} variant={confirmVariant} size="sm" onClick={onConfirm}>
            {options.confirmLabel ?? t("common.confirm")}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  );
}
