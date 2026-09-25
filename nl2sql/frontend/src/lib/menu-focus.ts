import type { RefObject } from "react";

/**
 * メニューを閉じたあとトリガーへフォーカスを戻す。
 *
 * 閉じた直後に利用者が別の要素へフォーカスを移していた場合は奪い返さない。
 * (メニュー項目は unmount 済みのため、復帰時点の activeElement は body か移動先になる。
 *  移動先を上書きすると、キーボード操作で次の行へ移った直後に前の行へ戻される。)
 */
export function restoreMenuTriggerFocus(
  triggerRef: RefObject<HTMLElement | null>,
  ...scopeRefs: readonly RefObject<HTMLElement | null>[]
) {
  window.requestAnimationFrame(() => {
    const active = document.activeElement;
    const focusMovedAway =
      active !== null &&
      active !== document.body &&
      !scopeRefs.some((ref) => ref.current?.contains(active));
    if (focusMovedAway) return;
    triggerRef.current?.focus({ preventScroll: true });
  });
}
