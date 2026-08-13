import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type RefObject,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { ChevronDown, MoreHorizontal } from "lucide-react";

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

const MENU_GAP = 4;
const MENU_VIEWPORT_PADDING = 8;

type FloatingMenuPlacement = "top" | "bottom";
type FloatingMenuPosition = {
  placement: FloatingMenuPlacement;
  style: CSSProperties;
};

function isVerticalScrollable(element: HTMLElement) {
  const overflowY = window.getComputedStyle(element).overflowY;
  return (
    /(auto|scroll|overlay)/u.test(overflowY) && element.scrollHeight > element.clientHeight + 1
  );
}

function getScrollableAncestor(element: HTMLElement) {
  let current = element.parentElement;
  while (current && current !== document.body && current !== document.documentElement) {
    if (isVerticalScrollable(current)) return current;
    current = current.parentElement;
  }
  return null;
}

function useFloatingMenuPosition({
  open,
  triggerRef,
  menuRef,
  align = "end",
}: {
  open: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
  menuRef: RefObject<HTMLDivElement | null>;
  align?: "start" | "end";
}) {
  const [position, setPosition] = useState<FloatingMenuPosition | undefined>();

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const menu = menuRef.current;
    if (!open || !trigger || !menu) return;

    const triggerRect = trigger.getBoundingClientRect();
    const menuWidth = Math.ceil(menu.offsetWidth);
    const menuHeight = Math.ceil(menu.scrollHeight);
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const maxViewportHeight = Math.max(0, viewportHeight - MENU_VIEWPORT_PADDING * 2);
    const scrollableAncestor = getScrollableAncestor(trigger);
    const boundaryRect = scrollableAncestor?.getBoundingClientRect();
    const boundaryTop = boundaryRect
      ? Math.max(MENU_VIEWPORT_PADDING, boundaryRect.top)
      : MENU_VIEWPORT_PADDING;
    const boundaryBottom = boundaryRect
      ? Math.min(viewportHeight - MENU_VIEWPORT_PADDING, boundaryRect.bottom)
      : viewportHeight - MENU_VIEWPORT_PADDING;
    const availableBelow = Math.max(0, boundaryBottom - triggerRect.bottom - MENU_GAP);
    const availableAbove = Math.max(0, triggerRect.top - boundaryTop - MENU_GAP);
    const fitsBelow = menuHeight <= availableBelow;
    const fitsAbove = menuHeight <= availableAbove;
    const placement: FloatingMenuPlacement =
      fitsBelow || (!fitsAbove && availableBelow >= availableAbove) ? "bottom" : "top";
    const availableInDirection = placement === "top" ? availableAbove : availableBelow;
    const maxHeight = Math.max(0, Math.min(menuHeight, availableInDirection, maxViewportHeight));
    const renderedHeight = maxHeight;

    const unclampedTop =
      placement === "top"
        ? triggerRect.top - MENU_GAP - renderedHeight
        : triggerRect.bottom + MENU_GAP;
    const top = Math.min(
      Math.max(MENU_VIEWPORT_PADDING, unclampedTop),
      Math.max(MENU_VIEWPORT_PADDING, viewportHeight - MENU_VIEWPORT_PADDING - renderedHeight)
    );
    const unclampedLeft =
      align === "end" ? triggerRect.right - menuWidth : triggerRect.left;
    const left = Math.min(
      Math.max(MENU_VIEWPORT_PADDING, unclampedLeft),
      Math.max(MENU_VIEWPORT_PADDING, viewportWidth - MENU_VIEWPORT_PADDING - menuWidth)
    );

    setPosition({
      placement,
      style: {
        left: Math.round(left),
        maxHeight: Math.floor(maxHeight),
        maxWidth: `calc(100vw - ${MENU_VIEWPORT_PADDING * 2}px)`,
        top: Math.round(top),
        transformOrigin:
          placement === "top"
            ? align === "end"
              ? "bottom right"
              : "bottom left"
            : align === "end"
              ? "top right"
              : "top left",
      },
    });
  }, [align, menuRef, open, triggerRef]);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(undefined);
      return undefined;
    }

    updatePosition();
    const animationFrame = window.requestAnimationFrame(updatePosition);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, updatePosition]);

  return position;
}

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
      firstEnabled?.focus();
    });
  }, [open]);

  const close = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus());
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
    items[nextIndex]?.focus();
  };

  return { open, setOpen, close, containerRef, triggerRef, menuRef, handleMenuKeyDown };
}

function FloatingActionMenu({
  children,
  className,
  id,
  menuRef,
  onKeyDown,
  open,
  triggerRef,
}: {
  children: ReactNode;
  className?: string;
  id: string;
  menuRef: RefObject<HTMLDivElement | null>;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  open: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const position = useFloatingMenuPosition({ open, triggerRef, menuRef });

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={menuRef}
      id={id}
      role="menu"
      data-floating-menu-placement={position?.placement}
      className={cn(
        "fixed z-50 grid gap-1 overflow-y-auto overscroll-contain rounded-md border border-border bg-card p-1 text-sm shadow-lg",
        !position && "opacity-0",
        className
      )}
      style={position?.style ?? { left: -9999, top: -9999 }}
      onKeyDown={onKeyDown}
    >
      {children}
    </div>,
    document.body
  );
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
