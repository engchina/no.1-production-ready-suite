import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { type LucideIcon } from "lucide-react";

import { FloatingActionMenu } from "@/components/FloatingMenu";
import { Button } from "@/components/ui/button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { StatusBadge, type StatusVariant } from "@/components/ui/status-badge";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type PageActionKind = "primary" | "secondary" | "utility" | "danger";

export interface PageAction {
  id: string;
  kind: PageActionKind;
  label: string;
  icon?: LucideIcon;
  onClick: () => void | Promise<void>;
  loading?: boolean;
  disabled?: boolean;
  ariaLabel?: string;
  testId?: string;
}

const ACTION_KIND_ORDER: Record<PageActionKind, number> = {
  primary: 0,
  secondary: 1,
  utility: 2,
  danger: 3,
};

type PageActionGroup = "task" | "utility" | "danger";

function actionGroup(kind: PageActionKind): PageActionGroup {
  if (kind === "utility") return "utility";
  if (kind === "danger") return "danger";
  return "task";
}

function orderedActions(actions: readonly PageAction[]) {
  return actions
    .map((action, index) => ({ action, index }))
    .sort(
      (left, right) =>
        ACTION_KIND_ORDER[left.action.kind] - ACTION_KIND_ORDER[right.action.kind] ||
        left.index - right.index
    )
    .map(({ action }) => action);
}

function actionVariant(kind: PageActionKind) {
  if (kind === "primary") return "primary" as const;
  if (kind === "danger") return "danger" as const;
  return "secondary" as const;
}

function PageActionButton({
  action,
  mobile = false,
  menuItem = false,
  onInvoked,
}: {
  action: PageAction;
  mobile?: boolean;
  menuItem?: boolean;
  onInvoked?: () => void;
}) {
  const Icon = action.icon;
  const handleClick = (event: ReactMouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    onInvoked?.();
    void action.onClick();
  };

  return (
    <Button
      type="button"
      size="sm"
      variant={menuItem ? "ghost" : actionVariant(action.kind)}
      loading={action.loading}
      disabled={action.disabled}
      aria-label={action.ariaLabel}
      role={menuItem ? "menuitem" : undefined}
      data-testid={action.testId}
      data-page-action-id={action.id}
      data-page-action-kind={action.kind}
      className={cn(
        mobile && "h-[44px] min-w-0 flex-1 whitespace-nowrap px-3",
        menuItem && "h-[44px] w-full justify-start whitespace-nowrap px-3"
      )}
      onClick={handleClick}
    >
      {Icon ? <Icon size={15} aria-hidden="true" /> : null}
      <span>{action.label}</span>
    </Button>
  );
}

export function PageActionBar({
  actions,
  ariaLabel = t("common.pageActions"),
  testId,
}: {
  actions: readonly PageAction[];
  ariaLabel?: string;
  testId?: string;
}) {
  const ordered = useMemo(() => orderedActions(actions), [actions]);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuId = useId();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const collapseInCompactHeader = ordered.length > 1;
  const compactPrimaryAction = ordered[0];
  const compactOverflowActions = collapseInCompactHeader ? ordered.slice(1) : [];

  useEffect(() => {
    if (!menuOpen) return;

    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (containerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen) return;
    window.requestAnimationFrame(() => {
      const firstEnabled = menuRef.current?.querySelector<HTMLButtonElement>(
        '[role="menuitem"]:not(:disabled)'
      );
      firstEnabled?.focus({ preventScroll: true });
    });
  }, [menuOpen]);

  const closeMenu = (restoreFocus = false) => {
    setMenuOpen(false);
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus({ preventScroll: true }));
  };

  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
      return;
    }

    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []
    );
    if (items.length === 0) return;

    const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
    let nextIndex: number | null = null;
    if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % items.length;
    if (event.key === "ArrowUp") nextIndex = (currentIndex - 1 + items.length) % items.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = items.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    items[nextIndex]?.focus({ preventScroll: true });
  };

  if (ordered.length === 0) return null;

  return (
    <div
      ref={containerRef}
      className="relative min-w-0"
      role="group"
      aria-label={ariaLabel}
      data-testid={testId}
    >
      <div className="hidden flex-wrap items-center justify-end gap-2 lg:flex">
        {ordered.map((action, index) => {
          const group = actionGroup(action.kind);
          const previousKind = ordered[index - 1]?.kind;
          const startsGroup = Boolean(previousKind && actionGroup(previousKind) !== group);
          return (
            <div
              key={action.id}
              data-page-action-group={group}
              data-page-action-group-start={startsGroup ? "true" : undefined}
              className={cn(startsGroup && "ml-2 border-l border-border pl-4")}
            >
              <PageActionButton action={action} />
            </div>
          );
        })}
      </div>

      <div className="flex w-full min-w-0 items-center justify-end gap-2 lg:hidden">
        {collapseInCompactHeader ? (
          <>
            <PageActionButton action={compactPrimaryAction} mobile />
            <Button
              ref={triggerRef}
              type="button"
              size="sm"
              variant="secondary"
              className="h-[44px] min-w-0 flex-1 whitespace-nowrap px-3"
              aria-expanded={menuOpen}
              aria-controls={menuId}
              aria-haspopup="menu"
              data-testid="page-actions-more"
              onClick={() => setMenuOpen((current) => !current)}
            >
              <span>{t("common.actions.more")}</span>
              <DisclosureChevron expanded={menuOpen} size={15} />
            </Button>
          </>
        ) : (
          ordered.map((action) => (
            <PageActionButton key={action.id} action={action} mobile />
          ))
        )}
      </div>

      {collapseInCompactHeader && menuOpen ? (
        <FloatingActionMenu
          id={menuId}
          open={menuOpen}
          triggerRef={triggerRef}
          menuRef={menuRef}
          className="min-w-56 lg:hidden"
          onKeyDown={handleMenuKeyDown}
        >
          {compactOverflowActions.map((action, index) => {
            const group = actionGroup(action.kind);
            const previousKind = compactOverflowActions[index - 1]?.kind;
            const startsGroup = Boolean(previousKind && actionGroup(previousKind) !== group);
            return (
              <div
                key={action.id}
                role="none"
                data-page-action-group={group}
                data-page-action-group-start={startsGroup ? "true" : undefined}
                className={cn(startsGroup && "mt-1 border-t border-border pt-1")}
              >
                <PageActionButton
                  action={action}
                  menuItem
                  onInvoked={() => closeMenu(false)}
                />
              </div>
            );
          })}
        </FloatingActionMenu>
      ) : null}
    </div>
  );
}

export function PageHeaderStatusBadge({
  variant,
  label,
  announcementLabel = label,
  testId,
}: {
  variant: StatusVariant;
  label: string;
  /** 頻繁に変わる件数を除外し、screen reader へ通知する安定した状態文言。 */
  announcementLabel?: string;
  testId?: string;
}) {
  return (
    <span
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-page-header-status="true"
      data-testid={testId}
    >
      <span aria-hidden="true">
        <StatusBadge variant={variant} label={label} />
      </span>
      <span className="sr-only">{announcementLabel}</span>
    </span>
  );
}

/** NL2SQL 固有のレスポンシブ画面ヘッダー。 */
export function PageHeader({
  title,
  subtitle,
  actions = [],
  status,
  meta,
  className,
  actionsAriaLabel,
  actionsTestId,
}: {
  title: string;
  subtitle?: string;
  actions?: readonly PageAction[];
  status?: ReactNode;
  meta?: ReactNode;
  className?: string;
  actionsAriaLabel?: string;
  actionsTestId?: string;
}) {
  return (
    <header
      className={cn(
        "flex min-w-0 flex-col gap-4 border-b border-border bg-card px-4 py-5 sm:px-8 lg:flex-row lg:items-start lg:justify-between",
        className
      )}
    >
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h1 className="min-w-0 break-words text-xl font-bold text-foreground">{title}</h1>
          {status}
        </div>
        {subtitle ? <p className="mt-1 text-sm leading-6 text-muted">{subtitle}</p> : null}
        {meta ? <div className="mt-1 text-xs leading-5 text-muted">{meta}</div> : null}
      </div>
      {actions.length > 0 ? (
        <div className="w-full min-w-0 lg:w-auto">
          <PageActionBar
            actions={actions}
            ariaLabel={actionsAriaLabel}
            testId={actionsTestId}
          />
        </div>
      ) : null}
    </header>
  );
}
