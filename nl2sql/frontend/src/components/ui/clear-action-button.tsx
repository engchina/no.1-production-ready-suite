import { X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button, type ButtonProps } from "@engchina/production-ready-ui";

export interface ClearActionButtonProps
  extends Omit<ButtonProps, "aria-label" | "children" | "type" | "variant"> {
  ariaLabel?: string;
  dataTestId?: string;
  label: string;
  matchButtonHeight?: boolean;
}

export function ClearActionButton({
  ariaLabel,
  className,
  dataTestId,
  label,
  matchButtonHeight = false,
  size = "sm",
  ...props
}: ClearActionButtonProps) {
  return (
    <Button
      type="button"
      variant="secondary"
      size={size}
      touchTarget={!matchButtonHeight}
      className={cn("whitespace-nowrap", className)}
      aria-label={ariaLabel ?? label}
      data-testid={dataTestId}
      {...props} icon={X}>
      <span>{label}</span>
    </Button>
  );
}
