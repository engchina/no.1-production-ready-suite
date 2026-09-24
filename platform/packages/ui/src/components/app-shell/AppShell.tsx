import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

/**
 * アプリ全体のシェル。左に Sidebar、右にメイン領域（スクロール）を配置する。
 * Sidebar は `sidebar` スロットで注入する（ルーター/auth/i18n を持ち込まないため）。
 */
export function AppShell({
  sidebar,
  children,
  className,
  mainClassName,
  skipLinkLabel = "本文へスキップ",
}: {
  sidebar: ReactNode;
  children: ReactNode;
  className?: string;
  mainClassName?: string;
  /** キーボード利用者が nav を飛ばして本文へ移るリンクのラベル（翻訳済み）。 */
  skipLinkLabel?: string;
}) {
  return (
    <div className={cn("relative flex h-screen w-full overflow-hidden bg-canvas text-fg", className)}>
      <a className="pr-skip-link" href="#pr-main">
        {skipLinkLabel}
      </a>
      {sidebar}
      <main
        id="pr-main"
        tabIndex={-1}
        className={cn("flex min-w-0 flex-1 flex-col overflow-y-auto focus:outline-none", mainClassName)}
      >
        {children}
      </main>
    </div>
  );
}
