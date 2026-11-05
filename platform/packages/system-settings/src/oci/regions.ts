import type { SelectFieldOption } from "@production-ready/ui";

/** OCI 認証のリージョン。既存のモデル設定の読取型にも使う。 */
export const OCI_REGION_OPTIONS = [
  { value: "ap-tokyo-1", label: "ap-tokyo-1" },
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] as const satisfies readonly SelectFieldOption<string>[];

export type OciRegion = (typeof OCI_REGION_OPTIONS)[number]["value"];
export const DEFAULT_GENERATIVE_AI_REGION: OciRegion = "ap-osaka-1";
