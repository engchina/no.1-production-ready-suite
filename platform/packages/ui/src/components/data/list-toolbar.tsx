import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

export interface ListToolbarProps {
  /**
   * 一覧の絞り込みの検索欄（`SearchField`）。ツールバーの**左端（先頭）**に置く。
   * 幅は部品が決める（左の列の残りを埋める。狭い幅では全幅）ので、SearchField に幅の class を付けない。
   */
  search?: ReactNode;
  /** 検索欄のすぐ右に並べる絞り込み（`SelectField` / `ToggleChip` の group など）。 */
  filters?: ReactNode;
  /** 右側に置く件数・状態の文言（例:「12 件」）。一覧への操作より前（左）に並ぶ。 */
  summary?: ReactNode;
  /** 右端に置く一覧全体への操作（追加・一括操作など）。主操作は最後（右端）に置く。 */
  actions?: ReactNode;
  className?: string;
  testId?: string;
}

/**
 * 一覧のツールバー（#600）。3 製品の一覧の上の「検索・絞り込み・件数・操作」の並びを 1 つにする。
 *
 * - **左（2）**: 検索欄（先頭）→ 絞り込み。一覧の「見る範囲を決める」操作は、読む順（左上）の先頭に置き、Tab の順も最初にする。
 * - **右（1）**: 件数 → 一覧全体への操作（追加・一括操作）。右端に寄せ、主操作は右端。
 * - ツールバーの幅が 48rem 以上なら左右を 2:1 で同じ行に置く（design-system README §4「wide 画面の 100% 充填」の
 *   toolbar の比率配分）。画面幅ではなくツールバー自身の幅（container query）で決めるので、横に並べたパネルの中でも崩れない。
 *   それより狭いと縦に積み、検索欄を先頭に全幅で置く。右に何も無ければ、左の列が行全体を使う。
 *
 * 規則と根拠（Atlassian・GitHub Primer・Shopify Polaris・Carbon・Material の比較）は UX 契約 page-archetypes.md
 * 「一覧のツールバー（検索欄の位置）」。
 */
export function ListToolbar({ search, filters, summary, actions, className, testId }: ListToolbarProps) {
  const hasStart = Boolean(search) || Boolean(filters);
  const hasEnd = Boolean(summary) || Boolean(actions);
  if (!hasStart && !hasEnd) return null;
  return (
    <div className={cn("@container min-w-0", className)} data-list-toolbar="" data-testid={testId}>
      <div
        className={cn(
          "grid min-w-0 gap-2",
          hasStart && hasEnd && "@3xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] @3xl:items-end @3xl:gap-x-6"
        )}
      >
        {hasStart ? (
          <div
            className="flex min-w-0 flex-col gap-2 @md:flex-row @md:flex-wrap @md:items-end"
            data-list-toolbar-start=""
          >
            {search ? (
              <div className="min-w-0 @md:min-w-[14rem] @md:flex-1" data-list-toolbar-search="">
                {search}
              </div>
            ) : null}
            {filters}
          </div>
        ) : null}
        {hasEnd ? (
          <div
            className="flex min-w-0 flex-wrap items-center gap-2 @3xl:min-h-[var(--field-height)] @3xl:justify-end"
            data-list-toolbar-end=""
          >
            {summary ? <div className="min-w-0 text-xs text-fg-muted tnum">{summary}</div> : null}
            {actions}
          </div>
        ) : null}
      </div>
    </div>
  );
}
