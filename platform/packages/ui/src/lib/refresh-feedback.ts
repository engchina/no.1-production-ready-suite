import { toast } from "../store/toast-store";

/** 手動更新の対象がすべて成功したときだけ、完了を一度知らせる。 */
export async function notifyRefreshSuccess(
  message: string,
  ...requests: Promise<{ isSuccess: boolean }>[]
): Promise<void> {
  const results = await Promise.allSettled(requests);
  if (results.length && results.every((result) => result.status === "fulfilled" && result.value.isSuccess)) {
    toast.success(message);
  }
}
