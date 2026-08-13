import { Button as BaseButton, buttonVariants, type ButtonProps as BaseButtonProps } from "@engchina/production-ready-ui";

export { buttonVariants };
export type ButtonProps = BaseButtonProps;

/**
 * App-local canonical Button.
 * Defaulting to type="button" prevents accidental form submit/page scroll when
 * action buttons are placed inside forms. Explicit submit/reset types are kept.
 */
export function Button({ type = "button", ...props }: ButtonProps) {
  return <BaseButton type={type} {...props} />;
}
