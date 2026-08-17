/** 画面応答性を揃えるための API request class。 */
export const API_TIMEOUT_MS = {
  interactiveList: 60_000,
  interactiveDetail: 30_000,
  jobControl: 5_000,
} as const;

/** timeout 文言を policy とずれさせないための表示用変換。 */
export function requestTimeoutSeconds(timeoutMs: number): number {
  return Math.ceil(timeoutMs / 1000);
}
