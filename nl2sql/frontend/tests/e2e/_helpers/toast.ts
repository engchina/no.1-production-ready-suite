import type { Page } from "@playwright/test";

/**
 * 画面下端の通知（Toast）を閉じる。375px では通知が本文の下端のボタンに重なることがあり、
 * ポインタを重ねると通知の自動の消去が止まる（#351）ため、押す前に閉じる。
 */
export async function dismissToasts(page: Page) {
  const close = page.getByRole("region", { name: "通知" }).getByRole("button", { name: "閉じる" });
  while ((await close.count()) > 0) {
    await close.first().click();
  }
}
