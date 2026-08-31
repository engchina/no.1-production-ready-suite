import type { SVGProps } from "react";

import { cn } from "@/lib/utils";

export interface StableLoadingIconProps extends Omit<SVGProps<SVGSVGElement>, "width" | "height"> {
  /** アイコン寸法(px)。周囲の Lucide アイコンと揃える。 */
  size?: number;
}

/**
 * ボタン内で使う loading icon。
 * 180 度対称の active arc を回し、回転中も見た目の重心が上下へ流れないようにする。
 */
export function StableLoadingIcon({ size = 16, className, ...props }: StableLoadingIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.25}
      aria-hidden="true"
      data-loading-icon="true"
      className={cn("block shrink-0 animate-spin motion-reduce:animate-none", className)}
      {...props}
    >
      <circle data-loading-icon-track="true" cx="12" cy="12" r="8.25" opacity="0.22" />
      <path
        data-loading-icon-active="true"
        d="M12 3.75A8.25 8.25 0 0 1 20.25 12"
        strokeLinecap="round"
      />
      <path
        data-loading-icon-active="true"
        d="M12 20.25A8.25 8.25 0 0 1 3.75 12"
        strokeLinecap="round"
      />
    </svg>
  );
}
