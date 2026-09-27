/**
 * Cookie セッションの CSRF 対策（double submit）。Cookie 名は製品ごとに違うため引数で受け取る（#220）。
 */
export const CSRF_HEADER_NAME = "X-CSRF-Token";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** document.cookie から値を 1 つ読む。ブラウザ外や未設定なら null。 */
export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

/** 状態を変える method（POST / PUT / PATCH / DELETE）のときだけ、Cookie の値を `X-CSRF-Token` として返す。 */
export function csrfHeader(cookieName: string, method = "GET"): Record<string, string> {
  if (!UNSAFE_METHODS.has(method.toUpperCase())) return {};
  const token = readCookie(cookieName);
  return token ? { [CSRF_HEADER_NAME]: token } : {};
}
