import { ChevronDown, type LucideIcon } from "lucide-react";
import {
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { measureClass } from "./PageBody";

export interface PageHeaderAction {
  id: string;
  /** primary = ページの主操作 / secondary / utility = 枠付き secondary / danger = 破壊的操作。 */
  kind: "primary" | "secondary" | "utility" | "danger";
  /** 翻訳済みラベル。省略するとアイコンだけのボタンになる（`ariaLabel` 必須）。 */
  label?: string;
  /** 画面の文脈を含む読み上げ名。ラベルがあっても指定すれば優先する（アイコンだけの場合は必須）。 */
  ariaLabel?: string;
  icon?: LucideIcon;
  onClick?: () => void;
  loading?: boolean;
  disabled?: boolean;
  /** ボタンの data-testid。 */
  testId?: string;
}

const ORDER: Record<PageHeaderAction["kind"], number> = { danger: 0, utility: 1, secondary: 2, primary: 3 };
const VARIANT = { primary: "primary", secondary: "secondary", utility: "secondary", danger: "danger" } as const;

/**
 * アクションの並び順。右寄せグループなので右端（最も押しやすい位置）に primary、左端に danger。
 * 同じ kind の中は渡した順を保つ。
 */
export function orderActions(actions: PageHeaderAction[]): PageHeaderAction[] {
  return actions
    .map((action, index) => ({ action, index }))
    .sort((a, b) => ORDER[a.action.kind] - ORDER[b.action.kind] || a.index - b.index)
    .map(({ action }) => action);
}

/**
 * 狭い画面（lg 未満）で見せる 1 つ。primary → secondary → utility の優先で選び、danger は常にメニュー側に置く。
 * メニューは上から重要な順（secondary → utility → danger）。破壊的な操作を末尾に置き、誤タップを避ける。
 */
export function splitCompactActions(actions: PageHeaderAction[]) {
  // 同じ kind の中は渡した順を保つ（reverse すると同じ kind の順も逆になるため sort で並べる）。
  const byImportance = [...orderActions(actions)].sort((a, b) => ORDER[b.kind] - ORDER[a.kind]);
  if (byImportance.length <= 1) return { visible: byImportance, overflow: [] as PageHeaderAction[] };
  const visible = byImportance.find((action) => action.kind !== "danger");
  return { visible: visible ? [visible] : [], overflow: byImportance.filter((action) => action !== visible) };
}

/** メニュー内のキー操作（WAI-ARIA Menu Button）。移動先の index、対象外のキーは null。 */
export function nextMenuIndex(key: string, current: number, count: number): number | null {
  if (count === 0) return null;
  if (key === "ArrowDown") return (current + 1) % count;
  if (key === "ArrowUp") return (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

const COMPACT_QUERY = "(max-width: 1023px)";

function useCompact() {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(COMPACT_QUERY);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(COMPACT_QUERY).matches,
    () => false
  );
}

function ActionButton({ action, menuItem = false, onInvoked }: { action: PageHeaderAction; menuItem?: boolean; onInvoked?: () => void }) {
  return (
    <Button
      variant={menuItem ? "ghost" : VARIANT[action.kind]}
      tone={menuItem && action.kind === "danger" ? "danger" : "default"}
      role={menuItem ? "menuitem" : undefined}
      icon={action.icon}
      iconOnly={!menuItem && !action.label}
      aria-label={action.ariaLabel}
      data-testid={action.testId}
      loading={action.loading}
      disabled={action.disabled}
      className={menuItem ? "w-full justify-start" : undefined}
      onClick={() => {
        onInvoked?.();
        action.onClick?.();
      }}
    >
      {menuItem || action.label ? <span>{action.label ?? action.ariaLabel}</span> : null}
    </Button>
  );
}

/**
 * 「その他の操作」メニュー（WAI-ARIA Menu Button）。開くと先頭の項目へフォーカスし、
 * ↓ ↑ Home End で移動、Escape で閉じてトリガーへ戻る。外側のクリック・Tab でも閉じる。
 */
function OverflowMenu({ actions, label }: { actions: PageHeaderAction[]; label: string }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const items = () => Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);

  useEffect(() => {
    if (!open) return;
    items()[0]?.focus({ preventScroll: true });
    const closeOnOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutside);
    return () => document.removeEventListener("mousedown", closeOnOutside);
  }, [open]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
      return;
    }
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    const list = items();
    const next = nextMenuIndex(event.key, list.indexOf(document.activeElement as HTMLButtonElement), list.length);
    if (next === null) return;
    event.preventDefault();
    list[next]?.focus({ preventScroll: true });
  };

  // ponytail: ヘッダー直下・右端揃えで開くため viewport 反転は持たない。ヘッダー以外で使うなら DropdownMenu として切り出す。
  return (
    <div ref={rootRef} className="relative">
      <Button
        ref={triggerRef}
        type="button"
        variant="secondary"
        trailingIcon={ChevronDown}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        data-testid="page-actions-more"
        onClick={() => setOpen((current) => !current)}
      >
        <span>{label}</span>
      </Button>
      {open ? (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={onKeyDown}
          className="absolute right-0 top-full z-[var(--z-dropdown)] mt-1 grid min-w-56 gap-0.5 rounded-md border border-border bg-surface-raised p-1 shadow-[var(--shadow-popover)]"
        >
          {actions.map((action) => (
            <ActionButton key={action.id} action={action} menuItem onInvoked={() => setOpen(false)} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * 画面共通ヘッダー。lg 以上ではスクロールしても上端に貼り付き（sticky）、タイトルと主要操作に常に手が届く。
 * `<header>` は画面幅いっぱい（背景と罫線）、中身は PageBody と同じ計測コンテナに入れる。
 * `wide` は PageBody と必ず同じ値にする（ずらすと 1920px でタイトルと本文の左端がずれる）。
 */
export function PageHeader({
  title,
  subtitle,
  meta,
  status,
  breadcrumbs,
  actions,
  actionsLabel = "ページ操作",
  actionsTestId,
  moreActionsLabel = "その他の操作",
  tabs,
  wide = false,
  className,
}: {
  title: string;
  subtitle?: string;
  /** 副題の下の補足（最終更新・件数など）。 */
  meta?: ReactNode;
  /** タイトル横の状態表示（StatusBadge 等）。 */
  status?: ReactNode;
  /** タイトル上のパンくず（`<Breadcrumbs>`）。 */
  breadcrumbs?: ReactNode;
  /**
   * 配列で渡すと danger → utility → secondary → primary の順に並べ替えて描画する（推奨）。
   * ReactNode（ボタン等）も後方互換で受けるが、並び順は呼び出し側の責任になる。
   */
  actions?: PageHeaderAction[] | ReactNode;
  /** アクション群の aria-label（翻訳済み）。 */
  actionsLabel?: string;
  /** アクション群の data-testid。 */
  actionsTestId?: string;
  /** 狭い画面（lg 未満）で primary 以外をまとめるメニューのラベル（翻訳済み）。 */
  moreActionsLabel?: string;
  /** `<Tabs>` を渡すとヘッダー下端に吸い付く（ビュー切替の唯一の置き場所）。 */
  tabs?: ReactNode;
  wide?: boolean;
  className?: string;
}) {
  const compact = useCompact();
  let actionNodes: ReactNode = actions as ReactNode;
  if (Array.isArray(actions) && !actions.some(isValidElement)) {
    const list = actions as PageHeaderAction[];
    // 狭い画面では主操作 1 つ +「その他の操作」にまとめ、sticky ヘッダーが本文を覆わない高さに保つ。
    const { visible, overflow } = compact ? splitCompactActions(list) : { visible: orderActions(list), overflow: [] };
    actionNodes =
      list.length > 0 ? (
        <>
          {overflow.length > 0 ? <OverflowMenu actions={overflow} label={moreActionsLabel} /> : null}
          {visible.map((action) => (
            <ActionButton key={action.id} action={action} />
          ))}
        </>
      ) : null;
  }

  return (
    <header
      className={cn(
        // 貼り付くのは lg 以上だけ。狭い画面では文字の折り返しでヘッダーが高くなり、貼り付くと本文の表示領域を常に削るため。
        "flex flex-col border-b border-border bg-surface lg:sticky lg:top-0 lg:z-[var(--z-sticky)]",
        tabs ? "gap-4 pt-5" : "py-5",
        className
      )}
    >
      <div className={cn(measureClass(wide), "flex flex-wrap items-start justify-between gap-4")}>
        <div className="min-w-0 flex-auto">
          {breadcrumbs ? <div className="mb-1.5">{breadcrumbs}</div> : null}
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-xl font-bold text-fg">{title}</h1>
            {status}
          </div>
          {subtitle ? <p className="mt-1 text-sm text-fg-muted">{subtitle}</p> : null}
          {meta ? <div className="mt-1 text-xs text-fg-muted">{meta}</div> : null}
        </div>
        {actionNodes ? (
          <div role="group" aria-label={actionsLabel} data-testid={actionsTestId} className="flex min-w-0 flex-wrap items-center gap-2">
            {actionNodes}
          </div>
        ) : null}
      </div>
      {tabs ? <div className={measureClass(wide)}>{tabs}</div> : null}
    </header>
  );
}
