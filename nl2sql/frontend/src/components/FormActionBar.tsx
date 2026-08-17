import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { ChevronDown, type LucideIcon } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export interface FormActionDescriptor {
  id: string;
  label: string;
  icon?: LucideIcon;
  onClick?: () => void | Promise<void>;
  href?: string;
  loading?: boolean;
  disabled?: boolean;
  ariaLabel?: string;
  testId?: string;
}

export interface FormActionBarProps {
  primaryActions?: readonly FormActionDescriptor[];
  secondaryActions?: readonly FormActionDescriptor[];
  dangerActions?: readonly FormActionDescriptor[];
  status?: ReactNode;
  ariaLabel: string;
  testId?: string;
}

function actionEnabled(action: FormActionDescriptor) {
  return !action.disabled && !action.loading;
}

function ActionContent({ action, iconSize = 16 }: { action: FormActionDescriptor; iconSize?: number }) {
  const Icon = action.icon;
  return (
    <>
      {Icon ? <Icon size={iconSize} aria-hidden="true" /> : null}
      <span>{action.label}</span>
    </>
  );
}

function VisibleAction({
  action,
  variant,
}: {
  action: FormActionDescriptor;
  variant: "primary" | "secondary";
}) {
  const className = "h-[44px] w-full whitespace-nowrap sm:h-10 sm:w-auto";

  if (action.href) {
    const enabled = actionEnabled(action);
    return (
      <a
        href={enabled ? action.href : undefined}
        aria-disabled={!enabled || undefined}
        aria-label={action.ariaLabel}
        data-testid={action.testId}
        data-form-action-id={action.id}
        data-form-action-kind={variant}
        className={cn(
          buttonVariants({ variant, size: "lg" }),
          className,
          !enabled && "pointer-events-none opacity-50"
        )}
        onClick={(event) => {
          if (!enabled) event.preventDefault();
        }}
      >
        <ActionContent action={action} />
      </a>
    );
  }

  return (
    <Button
      type="button"
      variant={variant}
      size="lg"
      className={className}
      loading={action.loading}
      disabled={action.disabled}
      aria-label={action.ariaLabel}
      data-testid={action.testId}
      data-form-action-id={action.id}
      data-form-action-kind={variant}
      onClick={() => void action.onClick?.()}
    >
      <ActionContent action={action} />
    </Button>
  );
}

function DangerMenuItem({
  action,
  onInvoked,
}: {
  action: FormActionDescriptor;
  onInvoked: () => void;
}) {
  const className =
    "h-[44px] w-full justify-start whitespace-nowrap px-3 text-left text-danger hover:bg-danger-bg hover:text-danger sm:h-8";

  if (action.href) {
    const enabled = actionEnabled(action);
    return (
      <a
        href={enabled ? action.href : undefined}
        role="menuitem"
        aria-disabled={!enabled || undefined}
        aria-label={action.ariaLabel}
        data-testid={action.testId}
        data-form-action-id={action.id}
        data-form-action-tone="danger"
        className={cn(buttonVariants({ variant: "ghost", size: "sm" }), className, !enabled && "pointer-events-none opacity-50")}
        onClick={(event) => {
          if (!enabled) {
            event.preventDefault();
            return;
          }
          onInvoked();
        }}
      >
        <ActionContent action={action} iconSize={15} />
      </a>
    );
  }

  return (
    <Button
      type="button"
      role="menuitem"
      variant="ghost"
      size="sm"
      className={className}
      loading={action.loading}
      disabled={action.disabled}
      aria-label={action.ariaLabel}
      data-testid={action.testId}
      data-form-action-id={action.id}
      data-form-action-tone="danger"
      onClick={(event: ReactMouseEvent<HTMLButtonElement>) => {
        event.preventDefault();
        onInvoked();
        void action.onClick?.();
      }}
    >
      <ActionContent action={action} iconSize={15} />
    </Button>
  );
}

function DangerActionsMenu({ actions }: { actions: readonly FormActionDescriptor[] }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    window.requestAnimationFrame(() => {
      const firstEnabled = menuRef.current?.querySelector<HTMLElement>(
        '[role="menuitem"]:not(:disabled):not([aria-disabled="true"])'
      );
      firstEnabled?.focus();
    });
  }, [open]);

  const closeMenu = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) window.requestAnimationFrame(() => triggerRef.current?.focus());
  };

  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu(true);
      return;
    }

    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>(
        '[role="menuitem"]:not(:disabled):not([aria-disabled="true"])'
      ) ?? []
    );
    if (items.length === 0) return;

    const currentIndex = items.indexOf(document.activeElement as HTMLElement);
    let nextIndex: number | null = null;
    if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % items.length;
    if (event.key === "ArrowUp") nextIndex = (currentIndex - 1 + items.length) % items.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = items.length - 1;
    if (nextIndex === null) return;

    event.preventDefault();
    items[nextIndex]?.focus();
  };

  if (actions.length === 0) return null;

  return (
    <div ref={containerRef} className="relative flex w-full sm:w-auto sm:ml-auto">
      <Button
        ref={triggerRef}
        type="button"
        variant="secondary"
        size="lg"
        className="h-[44px] w-full whitespace-nowrap sm:h-10 sm:w-auto"
        aria-expanded={open}
        aria-controls={menuId}
        aria-haspopup="menu"
        data-testid="form-actions-more"
        onClick={() => setOpen((current) => !current)}
      >
        <span>{t("common.actions.more")}</span>
        <ChevronDown
          size={16}
          className={cn("transition-transform motion-reduce:transition-none", open && "rotate-180")}
          aria-hidden="true"
        />
      </Button>
      {open ? (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          className="absolute right-0 top-full z-50 mt-2 grid min-w-56 max-w-[calc(100vw-2rem)] gap-1 rounded-md border border-border bg-card p-1 shadow-lg"
          onKeyDown={handleMenuKeyDown}
        >
          {actions.map((action, index) => (
            <div
              key={action.id}
              role="none"
              data-form-action-group-start={index === 0 ? "true" : undefined}
              className={cn(index === 0 && "border-t border-border pt-1")}
            >
              <DangerMenuItem action={action} onInvoked={() => closeMenu(false)} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function FormActionBar({
  ariaLabel,
  dangerActions = [],
  primaryActions = [],
  secondaryActions = [],
  status,
  testId,
}: FormActionBarProps) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      data-testid={testId}
      className="grid min-w-0 gap-2 border-t border-border pt-4"
    >
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        {primaryActions.map((action) => (
          <VisibleAction key={action.id} action={action} variant="primary" />
        ))}
        {secondaryActions.map((action) => (
          <VisibleAction key={action.id} action={action} variant="secondary" />
        ))}
        {status ? <div className="min-w-0 sm:flex-1">{status}</div> : null}
        <DangerActionsMenu actions={dangerActions} />
      </div>
    </div>
  );
}
