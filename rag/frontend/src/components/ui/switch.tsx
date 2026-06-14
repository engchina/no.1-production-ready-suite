import { forwardRef, type ButtonHTMLAttributes, type MouseEvent } from "react";

import { cn } from "@/lib/utils";

export interface SwitchProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange" | "role"> {
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
}

export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(
  ({ checked, disabled, className, onCheckedChange, onClick, type = "button", ...props }, ref) => {
    const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
      onClick?.(event);
      if (!event.defaultPrevented) {
        onCheckedChange?.(!checked);
      }
    };

    return (
      <button
        ref={ref}
        type={type}
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={handleClick}
        className={cn(
          "relative inline-flex h-[24px] min-h-[24px] w-[44px] min-w-[44px] shrink-0 cursor-pointer appearance-none items-center rounded-full border border-transparent p-0 leading-none transition-colors duration-200 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card disabled:cursor-not-allowed disabled:opacity-50",
          checked ? "bg-primary" : "bg-border",
          className
        )}
        {...props}
      >
        <span
          aria-hidden
          className={cn(
            "pointer-events-none absolute left-[2px] top-1/2 h-[20px] w-[20px] -translate-y-1/2 rounded-full bg-primary-foreground shadow-sm transition-transform duration-200 ease-out",
            checked ? "translate-x-[20px]" : "translate-x-0"
          )}
        />
      </button>
    );
  }
);

Switch.displayName = "Switch";
