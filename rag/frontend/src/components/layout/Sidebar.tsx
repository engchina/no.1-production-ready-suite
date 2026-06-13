"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { NAV_SECTIONS } from "./nav-config";

/**
 * 折りたたみ可能なサイドナビ（参照実装の sideTabBar 構造を踏襲）。
 */
export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="flex h-screen w-60 flex-col bg-sidebar text-sidebar-foreground"
      aria-label="サイドナビゲーション"
    >
      <div className="flex h-14 items-center px-5 text-base font-bold text-white">
        {t("app.title")}
      </div>
      <nav className="flex-1 overflow-y-auto px-3 py-2">
        {NAV_SECTIONS.map((section) => (
          <div key={section.titleKey} className="mb-4">
            <div className="px-3 py-1 text-xs font-semibold uppercase tracking-wide opacity-60">
              {t(section.titleKey)}
            </div>
            <ul className="mt-1 space-y-0.5">
              {section.items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(item.href + "/");
                const Icon = item.icon;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                        active
                          ? "bg-sidebar-active text-white"
                          : "hover:bg-white/10"
                      )}
                    >
                      <Icon size={16} aria-hidden />
                      <span>{t(item.labelKey)}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
