import { toast } from "@engchina/production-ready-ui";

/** 固定の承載面がない danger 通知は、利用者が閉じるまで保持する。 */
export function toastError(
  message: string,
  options?: Parameters<typeof toast.error>[1]
): void {
  toast.error(message, { ...options, duration: options?.duration ?? 0 });
}
