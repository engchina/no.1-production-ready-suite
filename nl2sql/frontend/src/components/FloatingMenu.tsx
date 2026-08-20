import {
  useCallback,
  useLayoutEffect,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

const MENU_GAP = 4;
const MENU_VIEWPORT_PADDING = 8;

export type FloatingMenuPlacement = "top" | "bottom";

type FloatingMenuPosition = {
  constrained: boolean;
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
  align = "end",
  menuRef,
  open,
  triggerRef,
}: {
  align?: "start" | "end";
  menuRef: RefObject<HTMLDivElement | null>;
  open: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const [position, setPosition] = useState<FloatingMenuPosition | undefined>();

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const menu = menuRef.current;
    if (!open || !trigger || !menu) return;

    const triggerRect = trigger.getBoundingClientRect();
    const menuWidth = Math.ceil(menu.offsetWidth);
    const menuRect = menu.getBoundingClientRect();
    const menuStyle = window.getComputedStyle(menu);
    const menuBorderHeight =
      (Number.parseFloat(menuStyle.borderTopWidth) || 0) +
      (Number.parseFloat(menuStyle.borderBottomWidth) || 0);
    const menuHeight = Math.ceil(
      Math.max(menuRect.height, menu.scrollHeight + menuBorderHeight)
    );
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
    const renderedHeight = Math.max(
      0,
      Math.min(menuHeight, availableInDirection, maxViewportHeight)
    );
    const constrained = renderedHeight + 1 < menuHeight;

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
      constrained,
      placement,
      style: {
        left: Math.round(left),
        ...(constrained ? { maxHeight: Math.floor(renderedHeight) } : {}),
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

export function FloatingActionMenu({
  align,
  children,
  className,
  id,
  menuRef,
  onKeyDown,
  open,
  triggerRef,
}: {
  align?: "start" | "end";
  children: ReactNode;
  className?: string;
  id: string;
  menuRef: RefObject<HTMLDivElement | null>;
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void;
  open: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const position = useFloatingMenuPosition({ align, open, triggerRef, menuRef });

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={menuRef}
      id={id}
      role="menu"
      data-floating-menu-constrained={position?.constrained ? "true" : undefined}
      data-floating-menu-placement={position?.placement}
      className={cn(
        "fixed z-50 grid gap-1 rounded-md border border-border bg-card p-1 text-sm shadow-lg",
        position?.constrained && "overflow-y-auto overscroll-contain",
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
