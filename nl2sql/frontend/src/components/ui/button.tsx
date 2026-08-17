import {
  Button as BaseButton,
  buttonVariants as sharedButtonVariants,
  cn,
  type ButtonProps as BaseButtonProps,
} from "@engchina/production-ready-ui";

export type ButtonProps = BaseButtonProps;

const BUTTON_TEXT_LAYOUT_CLASSNAME = "leading-5";

export function buttonVariants(options?: Parameters<typeof sharedButtonVariants>[0]) {
  return cn(sharedButtonVariants(options), BUTTON_TEXT_LAYOUT_CLASSNAME);
}

/**
 * App-local canonical Button.
 * Defaulting to type="button" prevents accidental form submit/page scroll when
 * action buttons are placed inside forms. Explicit submit/reset types are kept.
 */
export function Button({ type = "button", className, ...props }: ButtonProps) {
  return (
    <BaseButton
      type={type}
      className={cn(BUTTON_TEXT_LAYOUT_CLASSNAME, className)}
      {...props}
    />
  );
}
