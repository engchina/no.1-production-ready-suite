export interface GraphPathLabel {
  /** 階層の最後の段。ノードの主な表示名。 */
  primary: string;
  /** 直近の親の段。さらに上があれば省略の印を付ける。 */
  context?: string;
}

/**
 * 階層のパスを、ノードに出す主な表示名と文脈に分ける（#4079）。
 * 長い共通の接頭辞で区別に要る末尾が行数の切り詰めに隠れないよう、末尾の側から表示する。
 * 完全な名前はノードの `title` と選択の詳細に渡す。区切りの形は製品が渡す。
 */
export function graphPathLabel(name: string, separator: string): GraphPathLabel {
  const parts = name.split(separator).map((part) => part.trim()).filter(Boolean);
  const primary = parts.at(-1);
  if (!primary) return { primary: name };
  const parent = parts.at(-2);
  if (!parent) return { primary };
  return { primary, context: parts.length > 2 ? `…${separator}${parent}` : parent };
}
