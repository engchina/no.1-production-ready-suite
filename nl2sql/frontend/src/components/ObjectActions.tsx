import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { ChevronDown, MoreHorizontal } from "lucide-react";

import { FloatingActionMenu } from "@/components/FloatingMenu";
import { Button } from "@/components/ui/button";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { splitObjectActions, visibleEntityActions, type EntityAction } from "./ObjectActionsCore";

export {
  splitObjectActions,
  visibleEntityActions,
  type EntityAction,
  type EntityActionTone,
} from "./ObjectActionsCore";

function useActionMenu() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const closeOnOutsideClick = (event: globalThis.MouseEvent) => {
      const target = event.target as Node;
      if (containerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    window.requestAnimationFrame(() => {
      const firstEnabled = menuRef.current?.querySelector<HTMLButtonElement>(
        '[role="menuitem"]:not(:disabled)'
      );
      firstEnabled?.focus({ preventScroll: true });
    });
  }, [open]);

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus({ preventScroll: true }));
  };

  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
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

  return { open, setOpen, close, containerRef, triggerRef, menuRef, handleMenuKeyDown };
}

function MenuItems({
  actions,
  onClose,
  onActionClick,
}: {
  actions: readonly EntityAction[];
  onClose: (restoreFocus?: boolean) => void;
  onActionClick?: (event: MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <>
      {actions.map((action, index) => {
        const Icon = action.icon;
        const danger = action.tone === "danger";
        return (
          <div
            key={action.id}
            role="none"
            className={cn(
              danger &&
                (index === 0 || actions[index - 1]?.tone !== "danger") &&
                "mt-1 border-t border-border pt-1"
            )}
          >
            <Button
              type="button"
              role="menuitem"
              size="sm"
              variant="ghost"
              loading={action.loading}
              disabled={action.disabled}
              aria-label={action.ariaLabel}
              data-testid={action.testId}
              data-entity-action-id={action.id}
              data-entity-action-tone={action.tone ?? "default"}
              className={cn(
                "h-[44px] w-full justify-start whitespace-nowrap px-3 text-left sm:h-8",
                danger && "text-danger hover:bg-danger-bg hover:text-danger"
              )}
              onClick={(event) => {
                event.stopPropagation();
                onActionClick?.(event);
                onClose(false);
                void action.onSelect();
              }}
            >
              {Icon ? <Icon size={15} aria-hidden="true" /> : null}
              <span>{action.label}</span>
            </Button>
          </div>
        );
      })}
    </>
  );
}

export function RowActionMenu({
  actions,
  ariaLabel,
  disabled = false,
  loading = false,
  testId,
}: {
  actions: readonly EntityAction[];
  ariaLabel: string;
  disabled?: boolean;
  loading?: boolean;
  testId?: string;
}) {
  const visible = useMemo(() => visibleEntityActions(actions), [actions]);
  const menuId = useId();
  const { open, setOpen, close, containerRef, triggerRef, menuRef, handleMenuKeyDown } = useActionMenu();

  if (visible.length === 0) return null;

  return (
    <div
      ref={containerRef}
      className="relative inline-flex"
      role="group"
      aria-label={ariaLabel}
      data-testid={testId}
      onClick={(event) => event.stopPropagation()}
    >
      <Button
        ref={triggerRef}
        type="button"
        size="sm"
        variant="ghost"
        className="h-9 w-9 px-0"
        loading={loading}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={menuId}
        aria-haspopup="menu"
        data-testid={testId ? `${testId}-trigger` : undefined}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((current) => !current);
        }}
      >
        <MoreHorizontal size={16} aria-hidden="true" />
      </Button>
      {open ? (
        <FloatingActionMenu
          id={menuId}
          open={open}
          triggerRef={triggerRef}
          menuRef={menuRef}
          className="min-w-44"
          onKeyDown={handleMenuKeyDown}
        >
          <MenuItems actions={visible} onClose={close} />
        </FloatingActionMenu>
      ) : null}
    </div>
  );
}

export function ObjectActionBar({
  actions,
  ariaLabel,
  testId,
}: {
  actions: readonly EntityAction[];
  ariaLabel: string;
  testId?: string;
}) {
  const { inline, overflow } = useMemo(() => splitObjectActions(actions), [actions]);
  const menuId = useId();
  const { open, setOpen, close, containerRef, triggerRef, menuRef, handleMenuKeyDown } = useActionMenu();

  if (inline.length === 0 && overflow.length === 0) return null;

  return (
    <div
      ref={containerRef}
      className="relative flex flex-col gap-2 sm:flex-row sm:flex-wrap xl:justify-end"
      role="group"
      aria-label={ariaLabel}
      data-testid={testId}
    >
      {inline.map((action) => {
        const Icon = action.icon;
        return (
          <Button
            key={action.id}
            type="button"
            variant="secondary"
            size="sm"
            loading={action.loading}
            disabled={action.disabled}
            aria-label={action.ariaLabel}
            data-testid={action.testId}
            data-entity-action-id={action.id}
            data-entity-action-tone={action.tone ?? "default"}
            onClick={() => void action.onSelect()}
          >
            {Icon ? <Icon size={15} aria-hidden="true" /> : null}
            <span>{action.label}</span>
          </Button>
        );
      })}
      {overflow.length > 0 ? (
        <>
          <Button
            ref={triggerRef}
            type="button"
            variant="secondary"
            size="sm"
            aria-expanded={open}
            aria-controls={menuId}
            aria-haspopup="menu"
            data-testid={testId ? `${testId}-more` : undefined}
            onClick={() => setOpen((current) => !current)}
          >
            <span>{t("common.actions.more")}</span>
            <ChevronDown
              size={15}
              className={cn("transition-transform", open && "rotate-180")}
              aria-hidden="true"
            />
          </Button>
          {open ? (
            <FloatingActionMenu
              id={menuId}
              open={open}
              triggerRef={triggerRef}
              menuRef={menuRef}
              className="min-w-52"
              onKeyDown={handleMenuKeyDown}
            >
              <MenuItems actions={overflow} onClose={close} />
            </FloatingActionMenu>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
