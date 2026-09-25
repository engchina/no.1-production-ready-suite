import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind クラスを安全に結合する（shadcn/ui 標準ユーティリティ）。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
