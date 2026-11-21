import { afterEach, expect, it, vi } from "vitest";
import { notifyRefreshSuccess } from "../src/lib/refresh-feedback";
import { toast } from "../src/store/toast-store";

afterEach(() => vi.restoreAllMocks());

it("最後の取得が成功してから、一度だけ通知する", async () => {
  const success = vi.spyOn(toast, "success").mockReturnValue("id");
  let finish!: (value: { isSuccess: boolean }) => void;
  const last = new Promise<{ isSuccess: boolean }>((resolve) => { finish = resolve; });
  const refresh = notifyRefreshSuccess("更新", Promise.resolve({ isSuccess: true }), last);
  expect(success).not.toHaveBeenCalled();
  finish({ isSuccess: true });
  await refresh;
  expect(success).toHaveBeenCalledExactlyOnceWith("更新");
});

it("一部の取得が失敗したときは成功を通知しない", async () => {
  const success = vi.spyOn(toast, "success").mockReturnValue("id");
  for (const failed of [() => Promise.resolve({ isSuccess: false }), () => Promise.reject(new Error())]) {
    await notifyRefreshSuccess("更新", Promise.resolve({ isSuccess: true }), failed());
  }
  expect(success).not.toHaveBeenCalled();
});
