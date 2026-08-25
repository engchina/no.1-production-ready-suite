import { X } from "lucide-react";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Button, type ButtonProps } from "./button";

export interface ClearActionButtonProps
  extends Omit<ButtonProps, "aria-label" | "children" | "size" | "type" | "variant"> {
  ariaLabel?: string;
  dataTestId?: string;
  label?: string;
}

export function ClearActionButton({
  ariaLabel,
  className,
  dataTestId,
  label = t("common.fileDropzone.clear"),
  ...props
}: ClearActionButtonProps) {
  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      className={cn("h-[44px] whitespace-nowrap", className)}
      aria-label={ariaLabel ?? label}
      data-testid={dataTestId}
      {...props}
    >
      <X size={15} aria-hidden="true" />
      <span>{label}</span>
    </Button>
  );
}
