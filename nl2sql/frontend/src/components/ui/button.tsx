import {
  Button as BaseButton,
  buttonVariants as sharedButtonVariants,
  cn,
  type ButtonProps as BaseButtonProps,
} from "@engchina/production-ready-ui";

import { StableLoadingIcon } from "./stable-loading-icon";

export type ButtonProps = BaseButtonProps & {
  /** アイコンだけの操作。aria-label を必ず指定する。 */
  iconOnly?: boolean;
  /** 入力横・compact ヘッダーは desktop でも 44px に揃える。 */
  touchTarget?: boolean;
  /** 確認前の危険操作。塗りの danger と視覚的強度を区別する。 */
  tone?: "default" | "danger";
};

const BUTTON_TEXT_LAYOUT_CLASSNAME = "leading-5";

function sizeClass(size: ButtonProps["size"]) {
  return `nl2sql-button--${size ?? "md"}`;
}

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

type ButtonVariantOptions = NonNullable<Parameters<typeof sharedButtonVariants>[0]> &
  Pick<ButtonProps, "iconOnly" | "touchTarget" | "tone">;

export function buttonVariants(options?: ButtonVariantOptions) {
  return cn(
    sharedButtonVariants(options),
    BUTTON_TEXT_LAYOUT_CLASSNAME,
    "nl2sql-button",
    `nl2sql-button--${options?.variant ?? "primary"}`,
    options?.iconOnly && "nl2sql-button--icon",
    options?.touchTarget && "nl2sql-button--touch",
    options?.tone === "danger" && "nl2sql-button--danger-tone",
    sizeClass(options?.size),
    semanticVariantClass(options?.variant),
    "disabled:border-border disabled:bg-disabled-bg disabled:text-disabled disabled:opacity-100"
  );
}

/**
 * App-local canonical Button.
 * Defaulting to type="button" prevents accidental form submit/page scroll when
 * action buttons are placed inside forms. Explicit submit/reset types are kept.
 */
export function Button({ type = "button", variant, size, className, iconOnly, touchTarget, tone, ...props }: ButtonProps) {
  const { loading, disabled, children, ...buttonProps } = props;

  return (
    <BaseButton
      type={type}
      variant={variant}
      size={size}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        buttonVariants({ variant, size, iconOnly, touchTarget, tone }),
        loading && "[&>svg:not([data-loading-icon])]:hidden",
        "disabled:border-border disabled:bg-disabled-bg disabled:text-disabled disabled:opacity-100",
        className
      )}
      {...buttonProps}
    >
      {loading ? <StableLoadingIcon size={16} /> : null}
      {children}
    </BaseButton>
  );
}
