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
import { ChevronDown, type LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
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
        mobile && "h-[44px] min-w-0 flex-1 whitespace-nowrap px-3 sm:h-8",
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
  const collapseOnMobile = ordered.length > 1;
  const mobilePrimaryAction = ordered[0];
  const mobileOverflowActions = collapseOnMobile ? ordered.slice(1) : [];

  useEffect(() => {
    if (!menuOpen) return;

    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setMenuOpen(false);
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
      firstEnabled?.focus();
    });
  }, [menuOpen]);

  const closeMenu = (restoreFocus = false) => {
    setMenuOpen(false);
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus());
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
    items[nextIndex]?.focus();
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
      <div className="hidden flex-wrap items-center justify-end gap-2 sm:flex">
        {ordered.map((action, index) => (
          <div
            key={action.id}
            className={cn(
              action.kind === "danger" &&
                index > 0 &&
                ordered[index - 1]?.kind !== "danger" &&
                "ml-1 border-l border-border pl-3"
            )}
          >
            <PageActionButton action={action} />
          </div>
        ))}
      </div>

      <div className="flex w-full min-w-0 items-center justify-end gap-2 sm:hidden">
        {collapseOnMobile ? (
          <>
            <PageActionButton action={mobilePrimaryAction} mobile />
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
              <ChevronDown
                size={15}
                className={cn("transition-transform", menuOpen && "rotate-180")}
                aria-hidden="true"
              />
            </Button>
          </>
        ) : (
          ordered.map((action) => (
            <PageActionButton key={action.id} action={action} mobile />
          ))
        )}
      </div>

      {collapseOnMobile && menuOpen ? (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          className="absolute right-0 top-full z-50 mt-2 grid min-w-56 gap-1 rounded-md border border-border bg-card p-1 shadow-lg sm:hidden"
          onKeyDown={handleMenuKeyDown}
        >
          {mobileOverflowActions.map((action, index) => (
            <div
              key={action.id}
              role="none"
              className={cn(
                action.kind === "danger" &&
                  (index === 0 || mobileOverflowActions[index - 1]?.kind !== "danger") &&
                  "mt-1 border-t border-border pt-1"
              )}
            >
              <PageActionButton
                action={action}
                menuItem
                onInvoked={() => closeMenu(false)}
              />
            </div>
          ))}
        </div>
      ) : null}
    </div>
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
        "flex min-w-0 flex-col gap-4 border-b border-border bg-card px-4 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-8",
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
        <div className="w-full min-w-0 sm:w-auto sm:shrink-0">
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
