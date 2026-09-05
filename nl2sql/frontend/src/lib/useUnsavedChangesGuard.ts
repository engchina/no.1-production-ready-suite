import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

/**
 * 未保存の編集があるとき、画面離脱の前に確認を挟む。
 *
 * react-router の `useBlocker` は data router 専用で、本アプリの `<BrowserRouter>`
 * では使えない。そのため内部リンクの click を capture 段階で受けて確認ダイアログを
 * 挟み、承認された場合のみ `navigate` する。タブを閉じる・再読込は `beforeunload`
 * が担当する。
 *
 * 制約: ブラウザの戻る/進む(popstate)は data router なしでは安全に差し戻せないため
 * 対象外。編集内容の保護は上記 2 経路で行う。
 */
export function useUnsavedChangesGuard(
  enabled: boolean,
  confirmLeave: () => Promise<boolean>
): void {
  const navigate = useNavigate();
  const confirmLeaveRef = useRef(confirmLeave);
  confirmLeaveRef.current = confirmLeave;

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
