import { create } from "zustand";

import type { FeedbackTone } from "../components/ui/feedback-tone";

/**
 * Toast（一時通知）ストア。
 * コンポーネント外からも `toast.success(...)` 等で呼べるよう Zustand store + 純関数 API で提供する。
 */

export interface ToastAction {
  /** 表示ラベル（i18n 済み文字列）。 */
  label: string;
  onClick: () => void;
}

export interface ToastOptions {
  /** 補足説明（i18n 済み文字列）。 */
  description?: string;
  /** 「元に戻す」等のアクション（undo-support）。 */
  action?: ToastAction;
  /** 自動消滅までの ms。0 で自動消滅しない。未指定はトーン既定値。 */
  duration?: number;
}

export interface ToastItem extends ToastOptions {
  id: string;
  tone: FeedbackTone;
  message: string;
}

interface ToastStore {
  toasts: ToastItem[];
  push: (item: Omit<ToastItem, "id">) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

/** トーン別の既定表示時間（ms）。danger は長め（toast-dismiss: 3–5s, 重要度で延長可）。 */
const DEFAULT_DURATION: Record<FeedbackTone, number> = {
  success: 4000,
  info: 4000,
  warning: 6000,
  danger: 8000,
};

const timers = new Map<string, ReturnType<typeof setTimeout>>();

function clearTimer(id: string) {
  const handle = timers.get(id);
  if (handle) {
    clearTimeout(handle);
    timers.delete(id);
  }
}

function nextId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `toast-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (item) => {
    const id = nextId();
    set((state) => ({ toasts: [...state.toasts, { ...item, id }] }));

    // action 付き、または duration 明示 0 のときは自動消滅させない。
    const duration =
      item.duration ?? (item.action ? 0 : DEFAULT_DURATION[item.tone]);
    if (duration > 0) {
      const handle = setTimeout(() => {
        useToastStore.getState().dismiss(id);
      }, duration);
      timers.set(id, handle);
    }
    return id;
  },
  dismiss: (id) => {
    clearTimer(id);
    set((state) => ({ toasts: state.toasts.filter((toast) => toast.id !== id) }));
  },
  clear: () => {
    timers.forEach((handle) => clearTimeout(handle));
    timers.clear();
    set({ toasts: [] });
  },
}));

function show(tone: FeedbackTone, message: string, options?: ToastOptions): string {
  return useToastStore.getState().push({ tone, message, ...options });
}

/**
 * 一時通知 API。message / description / action.label には **i18n 済み文字列**を渡す。
 * 生のリテラル直書きは避ける（アプリ側の i18n 経由の文字列を渡す）。
 */
export const toast = {
  success: (message: string, options?: ToastOptions) => show("success", message, options),
  info: (message: string, options?: ToastOptions) => show("info", message, options),
  warning: (message: string, options?: ToastOptions) => show("warning", message, options),
  /** 失敗通知（danger トーン）。 */
  error: (message: string, options?: ToastOptions) => show("danger", message, options),
  dismiss: (id: string) => useToastStore.getState().dismiss(id),
};
