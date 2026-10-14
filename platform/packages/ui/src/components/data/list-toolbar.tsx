import type { ReactNode } from "react";

import { cn } from "../../lib/utils";
import { ListCount, type ListCountProps } from "./list-count";

export interface ListToolbarProps {
  /**
   * 一覧の絞り込みの検索欄（`SearchField`）。ツールバーの**左端（先頭）**に置く。
   * 幅は部品が決める（`lg` 28rem で固定し、行を埋めない。置き場所が狭いと全幅）ので、SearchField に `width` を渡さない。
   */
  search?: ReactNode;
  /** 検索欄のすぐ右に並べる絞り込み（`width` を渡した `SelectField` / `ToggleChip` の group など）。 */
  filters?: ReactNode;
  /**
   * 一覧の件数（`ListCount` の props）。右側の一覧への操作より前（左）に「全 N 件」「N 件（全 M 件）」を出す（#1891）。
   * 件数はここ 1 か所に出し、カードの見出しのバッジと二重にしない。
   */
  count?: ListCountProps;
  /** 件数のほかに右側に出す状態の文言（例:「選択中 3 件」）。件数には `count` を使う。 */
  summary?: ReactNode;
  /** 右端に置く一覧全体への操作（追加・一括操作など）。主操作は最後（右端）に置く。 */
  actions?: ReactNode;
  className?: string;
  testId?: string;
}

/**
 * 一覧のツールバー（#600 / #1891）。3 製品の一覧の上の「検索・絞り込み・件数・操作」の並びを 1 つにする。
 *
 * ```text
 * [🔍 検索欄（lg 28rem）] [絞り込み] [絞り込み]  ……空き……  [全 123 件] [一覧への操作（主操作は右端）]
 * ```
 *
 * - **左**: 検索欄（先頭・`lg` 28rem）→ 絞り込み（それぞれの `width`）。読む順と Tab の順の先頭に置く。
 *   検索欄で行を埋めない（隣の部品とつり合う幅。Visma・Spiris のツールバーの検索欄は 200〜510px）。
 * - **右**: 件数（`count` = `ListCount`）→ 一覧全体への操作。右端に寄せ、主操作は右端。
 * - 幅はツールバー自身の幅（container query）で決める。42rem 以上は 1 行に並べ、入らない絞り込みは次の行へ折り返す。
 *   それより狭い（375px・横に並べたパネル）と縦に積み、検索欄を先頭に全幅で置く。
 *
 * 規則と根拠は UX 契約 page-archetypes.md「一覧のツールバー」。
 */
export function ListToolbar({ search, filters, count, summary, actions, className, testId }: ListToolbarProps) {
  const hasStart = Boolean(search) || Boolean(filters);
  const hasEnd = Boolean(count) || Boolean(summary) || Boolean(actions);
  if (!hasStart && !hasEnd) return null;
  return (
    <div className={cn("@container min-w-0", className)} data-list-toolbar="" data-testid={testId}>
      <div className="flex min-w-0 flex-col gap-2 @2xl:flex-row @2xl:flex-wrap @2xl:items-end @2xl:gap-x-6">
        {hasStart ? (
          <div
            className="flex min-w-0 flex-col gap-2 @md:flex-row @md:flex-wrap @md:items-end @md:gap-x-3"
            data-list-toolbar-start=""
          >
            {search ? (
              <div
                className="min-w-0 @md:w-[var(--field-width-lg)] @md:max-w-full @md:shrink-0"
                data-list-toolbar-search=""
              >
                {search}
              </div>
            ) : null}
            {filters}
          </div>
        ) : null}
        {hasEnd ? (
          <div
            className="flex min-w-0 flex-wrap items-center gap-2 @2xl:ml-auto @2xl:min-h-[var(--field-height)] @2xl:justify-end"
            data-list-toolbar-end=""
          >
            {count ? <ListCount {...count} /> : null}
            {summary ? <div className="min-w-0 text-xs text-fg-muted tnum">{summary}</div> : null}
            {actions}
          </div>
        ) : null}
      </div>
    </div>
  );
}
