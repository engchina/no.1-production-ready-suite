import { IdentifierText } from "@/components/IdentifierText";

import { formatDbObjectName, type DbObjectNameSource } from "../dbObjectIdentity";

const SIZE_CLASS = {
  xs: "text-xs",
  sm: "text-sm",
  base: "text-base",
} as const;

export type DbObjectNameSize = keyof typeof SIZE_CLASS;

type DbObjectNameProps = {
  /** 一覧・バッジは xs、文中は sm、見出しは base。 */
  size?: DbObjectNameSize;
  /**
   * 押せる要素（選択ボタン・リンク）の中に置くときだけ true にする。
   * accent 色は「押せる」ことを示すため、見出し・バッジ・確認ダイアログ・結果表示では使わない。
   */
  interactive?: boolean;
  className?: string;
  "data-testid"?: string;
} & (
  | { object: DbObjectNameSource; value?: never }
  | { /** `formatDbObjectName` 済みの文字列。 */ value: string; object?: never }
);

/**
 * 表・ビュー名を `OWNER.OBJECT` 形式で表示する。等幅・semibold で、`.` と `_` の後で折り返す。
 * 文字列が必要な場面（トースト・aria-label・確認語）では `formatDbObjectName` を使う。
 */
export function DbObjectName({
  object,
  value,
  size = "sm",
  interactive = false,
  className = "",
  "data-testid": testId,
}: DbObjectNameProps) {
  const text = object ? formatDbObjectName(object) : (value ?? "");
  return (
    <IdentifierText
      value={text}
      data-testid={testId}
      className={[
        "font-mono font-semibold",
        SIZE_CLASS[size],
        interactive ? "text-accent-fg" : "text-fg",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    />
  );
}
