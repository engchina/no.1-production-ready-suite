/**
 * データ領域の読み取りの状態を、3 製品で同じ規則で分ける（UX 契約 messaging.md §3.6.1。#4217）。
 *
 * 「確かに 0 件」と「読み取れなかった」を取り違えない。原因は応答の `error_code` で見分け、文言は照合しない。
 * TanStack Query に依存しない形にする（query の結果をそのまま渡せる）。
 */

/** 読み取りが時間の上限に達した（DB は混んでいるだけかもしれない）。 */
export const DATABASE_READ_TIMEOUT_CODE = "database_read_timeout";
/** DB に接続できない。 */
export const DATABASE_UNAVAILABLE_CODE = "database_unavailable";
/** 接続・時間の上限以外の理由で読み取りが失敗した。 */
export const DATABASE_READ_FAILED_CODE = "database_read_failed";

/** 製品が接続の失敗に使う code（NL2SQL・Agent の保存先の不通）。 */
const UNAVAILABLE_CODES: ReadonlySet<string> = new Set([
  DATABASE_UNAVAILABLE_CODE,
  "oracle_connection_unavailable",
  "agent_storage_unavailable",
]);

export type DataLoadFailureKind = "timeout" | "unavailable" | "other";

function errorCodeOf(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const code = (error as { errorCode?: unknown }).errorCode;
  return typeof code === "string" ? code : undefined;
}

function statusOf(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === "number" ? status : undefined;
}

/** 読み取りの失敗の原因（製品の `ApiError` の `errorCode` で見分ける）。 */
export function dataLoadFailureKind(error: unknown): DataLoadFailureKind {
  const code = errorCodeOf(error);
  if (code === DATABASE_READ_TIMEOUT_CODE) return "timeout";
  if (code !== undefined && UNAVAILABLE_CODES.has(code)) return "unavailable";
  return "other";
}

/** 自動の再試行の上限（最初の取得とは別に 2 回。その後は利用者の再試行に任せる）。 */
export const DATA_LOAD_MAX_RETRIES = 2;

/** 混んでいる DB を繰り返し読まないよう、DB の読み取りの失敗の後は間隔を空ける（ミリ秒）。 */
const DATABASE_RETRY_DELAYS_MS = [5_000, 15_000] as const;
const DEFAULT_RETRY_DELAYS_MS = [1_000, 2_000] as const;

/**
 * 自動の再試行の判定（TanStack Query の `retry`）。4xx（408 / 429 を除く）は同じ結果になるので試さない。
 * 製品の `QueryClient` の既定に渡す。
 */
export function retryDataLoad(failureCount: number, error: unknown): boolean {
  const status = statusOf(error);
  if (status !== undefined && status >= 400 && status < 500 && status !== 408 && status !== 429) {
    return false;
  }
  return failureCount < DATA_LOAD_MAX_RETRIES;
}

/** 自動の再試行の間隔（TanStack Query の `retryDelay`）。 */
export function dataLoadRetryDelay(attempt: number, error: unknown): number {
  const delays = dataLoadFailureKind(error) === "other" ? DEFAULT_RETRY_DELAYS_MS : DATABASE_RETRY_DELAYS_MS;
  return delays[Math.min(attempt, delays.length - 1)];
}

/**
 * 領域に何を出すか。
 *
 * - `loading`: まだ何も無い（自動の再試行の間も含む）。形の Skeleton と経過時間。
 * - `failed`: 内容が無いまま読み取りに失敗した。失敗の表示だけを出し、空の状態・件数を出さない。
 * - `stale`: 前回の内容はあるが、最新の読み取りに失敗した。前回の内容を残し、いつの内容かを示す。
 * - `ready`: 読み取れた（0 件かどうかはここで初めて判断してよい）。
 */
export type DataLoadPhase = "loading" | "failed" | "stale" | "ready";

/** `useQuery` の結果のうち判定に使うもの。 */
export interface DataLoadQuery {
  data: unknown;
  isError: boolean;
  /** 連続した取得の失敗の回数（自動の再試行の待ちの間は 1 以上）。 */
  failureCount?: number;
  /** 前の条件の内容を仮に出しているか（`keepPreviousData`）。 */
  isPlaceholderData?: boolean;
}

export interface DataLoadView {
  phase: DataLoadPhase;
  /**
   * 新しく作る操作を止めるか。読み取れていない間は、同じものを重ねて作ってしまうおそれがある。
   * 自動の再試行を待っている間も止める。
   */
  blockCreate: boolean;
}

/**
 * `isEmpty` は前回の内容が 0 件か。前回が 0 件のまま最新の読み取りに失敗したときは、残す内容が無く、
 * 「0 件」とも言い切れないので `failed` にする。
 */
export function dataLoadView(query: DataLoadQuery, { isEmpty = false }: { isEmpty?: boolean } = {}): DataLoadView {
  const retrying = (query.failureCount ?? 0) > 0;
  if (query.data === undefined) {
    return { phase: query.isError ? "failed" : "loading", blockCreate: query.isError || retrying };
  }
  if (query.isError && !query.isPlaceholderData) {
    return { phase: isEmpty ? "failed" : "stale", blockCreate: true };
  }
  return { phase: "ready", blockCreate: retrying };
}

/** 入力中の欄・開いているダイアログからフォーカスを奪わないための判定。 */
function shouldKeepFocus(active: Element | null): boolean {
  if (!active || active === document.body) return false;
  if (active.closest("[role='dialog'], [role='alertdialog']")) return true;
  if (active instanceof HTMLElement && active.isContentEditable) return true;
  return active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement || active instanceof HTMLSelectElement;
}

/**
 * 読み取りの失敗の復旧の操作へフォーカスを移す。利用者が入力している欄からは奪わない。
 * `onlyIfUnfocused` は、前回の内容を読める状態の失敗（読みかけ・操作中のフォーカスを動かさない）で使う。
 */
export function focusDataLoadRecovery(
  target: HTMLElement | null,
  { onlyIfUnfocused = false }: { onlyIfUnfocused?: boolean } = {},
): void {
  if (!target) return;
  const active = document.activeElement;
  if (onlyIfUnfocused ? active !== null && active !== document.body : shouldKeepFocus(active)) return;
  target.focus();
}
