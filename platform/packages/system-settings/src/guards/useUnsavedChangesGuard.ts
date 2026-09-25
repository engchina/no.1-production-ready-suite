import { useContext, useEffect, useRef } from "react";
import { UNSAFE_DataRouterContext, useBlocker, useNavigate } from "react-router-dom";

/**
 * 未保存の編集があるとき、画面離脱の前に確認を挟む。
 *
 * - 内部リンクの click を capture 段階で受けて確認ダイアログを挟み、承認された場合のみ `navigate` する。
 * - タブを閉じる・再読込は `beforeunload` が担当する。
 * - ブラウザの戻る/進む（popstate）は、data router（`createBrowserRouter` + `RouterProvider`）の中でだけ
 *   `useBlocker` で確認する（#138）。`<BrowserRouter>` では `useBlocker` を使えないため対象外。
 *   画面内のボタンが自分で確認してから `navigate` する流れ（PUSH / REPLACE）は二重に確認しないよう止めない。
 */
export function useUnsavedChangesGuard(
  enabled: boolean,
  confirmLeave: () => Promise<boolean>
): void {
  const navigate = useNavigate();
  const confirmLeaveRef = useRef(confirmLeave);
  confirmLeaveRef.current = confirmLeave;
  // ponytail: ルーターの種類はアプリの生存中に変わらないため、条件付きの hook 呼び出しでも順序は安定する。
  const inDataRouter = useContext(UNSAFE_DataRouterContext) !== null;
  if (inDataRouter) useHistoryPopGuard(enabled, confirmLeaveRef);

  useEffect(() => {
    if (!enabled) return;

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Safari 等の旧仕様向け。文言はブラウザ側が決めるため i18n 対象外。
      event.returnValue = "";
    };

    const handleClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest("a");
      if (!anchor || anchor.hasAttribute("download") || anchor.target === "_blank") return;
      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#")) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      const destination = `${url.pathname}${url.search}${url.hash}`;
      const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      if (destination === current) return;

      event.preventDefault();
      event.stopPropagation();
      void confirmLeaveRef.current().then((confirmed) => {
        if (confirmed) navigate(destination);
      });
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    document.addEventListener("click", handleClick, true);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      document.removeEventListener("click", handleClick, true);
    };
  }, [enabled, navigate]);
}

/** data router の中で、戻る/進むによる別 URL への移動を確認する。キャンセルしたら URL を元に戻す。 */
function useHistoryPopGuard(
  enabled: boolean,
  confirmLeaveRef: { current: () => Promise<boolean> }
) {
  const blocker = useBlocker(
    ({ historyAction, currentLocation, nextLocation }) =>
      enabled &&
      historyAction === "POP" &&
      (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search)
  );

  // blocker は router の状態に保持され、止めるたびに新しいオブジェクトになる。同じ履歴の項目へ
  // 続けて戻る/進むしたとき（state が blocked のまま）も確認し直せるよう、オブジェクトごとに扱う。
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    let settled = false;
    void confirmLeaveRef.current().then((confirmed) => {
      if (settled || blocker.state !== "blocked") return;
      settled = true;
      if (confirmed) blocker.proceed();
      else blocker.reset();
    });
    return () => {
      settled = true;
    };
  }, [blocker, confirmLeaveRef]);
}
