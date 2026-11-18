import { useLayoutEffect, type RefObject } from "react";

/**
 * md 以上で右下の通知（`<Toaster/>`）が出ている間だけ、ページの末尾に空ける高さ（#5388。#5354 の常時の 14rem を置き換えた）。
 *
 * 通知の領域の実際の上端から画面の下端までの高さ + 1rem を `document.documentElement` の `--toast-reserve-bottom` に書き、
 * 共有の `PageBody` がそれを下の余白にする（スクロールしきったとき、表の行メニュー・末尾のボタンが通知の範囲の上に来る）。
 * 通知の件数・折り返し・画面の高さの変化に従い、通知の領域が空になったら消す（`PageBody` は 1.5rem に戻る）。
 * md 未満の上端の通知の高さを本文の前に確保する `useToastTopClearance`（#3751）と同じ型。
 *
 * 書くのは CSS 変数だけで、測る通知の領域の大きさは変えない（書き戻しの揺れは起きない）。ページは自動でスクロールしない。
 */
export const TOAST_RESERVE_BOTTOM_VAR = "--toast-reserve-bottom";

export function useToastBottomReserve(ref: RefObject<HTMLElement | null>, enabled: boolean) {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!enabled || !element) return;
    const root = document.documentElement;
    const update = () => {
      // 登場アニメーションの transform に影響されない外側の領域（通知を積んだ全体）を測る。
      const rect = element.getBoundingClientRect();
      if (rect.height > 0) {
        root.style.setProperty(TOAST_RESERVE_BOTTOM_VAR, `calc(${Math.ceil(window.innerHeight - rect.top)}px + var(--space-4))`);
      } else {
        root.style.removeProperty(TOAST_RESERVE_BOTTOM_VAR);
      }
    };
    update();
    // ResizeObserver の通知は描画の前に 1 回にまとまるので、そのまま測る（requestAnimationFrame を挟まない。
    // 時刻を止めたテストでも通知の追加・削除に従う）。
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    observer?.observe(element);
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
      root.style.removeProperty(TOAST_RESERVE_BOTTOM_VAR);
    };
  }, [ref, enabled]);
}
