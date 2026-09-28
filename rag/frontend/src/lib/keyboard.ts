/**
 * キーボード入力の判定。
 */

/** 判定に使う keydown event の最小形（React の KeyboardEvent と DOM の KeyboardEvent の両方を受ける）。 */
interface KeyboardEventLike {
  key: string;
  keyCode?: number;
  isComposing?: boolean;
  nativeEvent?: { isComposing?: boolean };
}

/**
 * IME で変換中（日本語入力の確定の Enter を含む）の keydown か。
 * Safari は確定の Enter で isComposing=false・keyCode=229 を返すため keyCode も見る。
 */
export function isImeComposing(event: KeyboardEventLike): boolean {
  return Boolean(event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229);
}

/** 入力欄の Enter で送信してよいか（IME の変換を確定する Enter では送信しない）。 */
export function isSubmitEnter(event: KeyboardEventLike): boolean {
  return event.key === "Enter" && !isImeComposing(event);
}
