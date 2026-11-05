import type { SelectFieldOption } from "@production-ready/ui";

/** 新しく選択・保存できるモデルの接続先。OCI認証の候補とは分ける。 */
export const GENERATIVE_AI_REGION_OPTIONS = [
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] as const satisfies readonly SelectFieldOption<string>[];

export function isSelectableGenerativeAiRegion(value: string): boolean {
  return GENERATIVE_AI_REGION_OPTIONS.some((option) => option.value === value);
}
