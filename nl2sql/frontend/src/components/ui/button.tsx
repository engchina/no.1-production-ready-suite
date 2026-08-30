import {
  Button as BaseButton,
  buttonVariants as sharedButtonVariants,
  cn,
  type ButtonProps as BaseButtonProps,
} from "@engchina/production-ready-ui";

export type ButtonProps = BaseButtonProps;

const BUTTON_TEXT_LAYOUT_CLASSNAME = "leading-5";

function semanticVariantClass(variant: ButtonProps["variant"]) {
  if (variant === "danger") {
    return "bg-danger-fill text-white hover:bg-danger-fill/90";
  }
  if (variant === "secondary") {
    return "border-control-border";
  }
  if (variant === "primary" || variant == null) {
    return "bg-primary-fill text-primary-fill-foreground hover:bg-primary-fill/90";
  }
  return undefined;
}

export function buttonVariants(options?: Parameters<typeof sharedButtonVariants>[0]) {
  return cn(
    sharedButtonVariants(options),
    BUTTON_TEXT_LAYOUT_CLASSNAME,
    semanticVariantClass(options?.variant),
    "disabled:border-border disabled:bg-disabled-bg disabled:text-disabled disabled:opacity-100"
  );
}

/**
 * App-local canonical Button.
 * Defaulting to type="button" prevents accidental form submit/page scroll when
 * action buttons are placed inside forms. Explicit submit/reset types are kept.
 */
export function Button({ type = "button", variant, className, ...props }: ButtonProps) {
  return (
    <BaseButton
      type={type}
      variant={variant}
      className={cn(
        BUTTON_TEXT_LAYOUT_CLASSNAME,
        semanticVariantClass(variant),
        "disabled:border-border disabled:bg-disabled-bg disabled:text-disabled disabled:opacity-100",
        className
      )}
      {...props}
    />
  );
}
