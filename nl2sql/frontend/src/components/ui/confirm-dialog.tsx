import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { MessageText, toneIcon, type FeedbackTone } from "@engchina/production-ready-ui";

import { Button } from "@/components/ui/button";
import { DialogOverlayPortal } from "@/components/ui/dialog-overlay";
import { t } from "@/lib/i18n";

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

export interface ConfirmDefaultLabels {
  confirm: string;
  cancel: string;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

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
  returnFocus: HTMLElement | null;
}

/**
 * 確認ダイアログ Provider。
 * NL2SQL では確認面を中立背景に統一し、danger はアイコン・タイトル・確定ボタンに限定する。
 */
export function ConfirmProvider({
  children,
  labels = { confirm: t("common.confirm"), cancel: t("common.cancel") },
}: {
  children: ReactNode;
  labels?: ConfirmDefaultLabels;
}) {
  const [state, setState] = useState<DialogState | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      const activeElement =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const activeMenu = activeElement?.closest<HTMLElement>('[role="menu"]');
      const menuTrigger = activeMenu?.id
        ? Array.from(document.querySelectorAll<HTMLElement>("[aria-controls]")).find(
            (element) => element.getAttribute("aria-controls") === activeMenu.id
          ) ?? null
        : null;
      setState({ options, resolve, returnFocus: menuTrigger ?? activeElement });
    });
  }, []);

  const settle = useCallback((value: boolean) => {
    setState((current) => {
      current?.resolve(value);
      return null;
    });
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state ? (
        <ConfirmDialog
          options={state.options}
          returnFocus={state.returnFocus}
          labels={labels}
          onCancel={() => settle(false)}
          onConfirm={() => settle(true)}
        />
      ) : null}
    </ConfirmContext.Provider>
  );
}

function ConfirmDialog({
  options,
  returnFocus,
  labels,
  onCancel,
  onConfirm,
}: {
  options: ConfirmOptions;
  returnFocus: HTMLElement | null;
  labels: ConfirmDefaultLabels;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { title, description, tone = "danger", dismissOnOverlay = true } = options;
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(returnFocus);
  const Icon = toneIcon[tone];

  useEffect(() => {
    confirmRef.current?.focus({ preventScroll: true });
    return () => {
      if (previouslyFocused.current) {
        previouslyFocused.current.focus({ preventScroll: true });
      }
    };
  }, []);

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
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  const confirmVariant = tone === "danger" ? "danger" : "primary";
  const toneClass =
    tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-info";
  const iconClass =
    tone === "danger"
      ? "border-danger/30 text-danger"
      : tone === "warning"
        ? "border-warning/30 text-warning"
        : "border-info/30 text-info";
  const panelClass =
    "animate-dialog-in max-h-[90dvh] w-full max-w-md overflow-auto rounded-md border border-border bg-card shadow-xl";

  return (
    <DialogOverlayPortal
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && dismissOnOverlay) onCancel();
      }}
    >
      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className={panelClass}
      >
        <div className="flex items-start gap-3 bg-card px-5 pt-5">
          <span
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border bg-background ${iconClass}`}
          >
            <Icon size={18} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className={`text-base font-semibold ${toneClass}`}>
              <MessageText text={title} />
            </h2>
            {description ? (
              <p id={descriptionId} className="mt-1 text-sm leading-relaxed text-muted">
                <MessageText text={description} />
              </p>
            ) : null}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2 border-t border-border bg-background px-5 py-4">
          <Button variant="secondary" size="sm" onClick={onCancel}>
            {options.cancelLabel ?? labels.cancel}
          </Button>
          <Button ref={confirmRef} variant={confirmVariant} size="sm" onClick={onConfirm}>
            {options.confirmLabel ?? labels.confirm}
          </Button>
        </div>
      </div>
    </DialogOverlayPortal>
  );
}
