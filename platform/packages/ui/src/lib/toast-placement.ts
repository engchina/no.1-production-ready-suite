/**
 * Toast（一時通知）の置き場所（#411）。
 *
 * 通知は主操作を覆わない位置に出す。デザインシステムでは操作が次の場所に集まり、どれも画面の上端の見出しの面の
 * 「操作以外の部分」（ページのタイトル・製品名）とは重ならない。
 *
 * - ページの操作: PageHeader の右端（lg 以上は上端に貼り付く）。
 * - 対象・コンテンツの操作: ObjectActionBar / ContentActionBar は内容の面の右上（PageHeader のすぐ下に来やすい）。
 * - フォームの操作: FormActionBar は内容の末尾（左寄せ）。ページの末尾の操作はそれ以上スクロールできないため、
 *   必ず画面の下端に来る。375px では全幅。
 *
 * そのため、通知は上端の見出しの面に重ね、その面の操作は覆わない。
 *
 * - md 以上: PageHeader に重ねる（上端は PageHeader の上端から 1rem）。ページの操作（`[data-page-header-actions]`）は覆わない。
 *   - ページの操作が無い、またはページの操作の右に 14rem 以上空いている（lg 未満で操作がタイトルの下へ折り返したとき）:
 *     画面の右端から 1rem。
 *   - ページの操作が右端にある（通常）: ページの操作のすぐ左に右端をそろえる。
 *   - どちらにも 14rem の幅が取れないときは、PageHeader の下端の 1rem 下の右に出す。
 *   - PageHeader がスクロールで見えない・無いときは、画面の右上（上端・右端から 1rem）。
 * - md 未満: 上端の全幅。上端のバー（メニューのボタンと製品名）に重ね、メニューのボタンは覆わない。
 */

/** 画面の端・PageHeader・ページの操作との間隔。 */
const EDGE = "1rem";
const EDGE_REM = 1;
/** md 未満の上端の間隔（上端のバーに重ねる）。 */
const NARROW_TOP = "0.5rem";
/**
 * md 未満で、上端のバーのメニューのボタンを覆わないための左の間隔。
 * ボタンはバーの左の余白（`px-2` = 0.5rem）の後ろに 44px（`--control-height-touch`）。その後ろに 0.5rem 空ける。
 */
const NARROW_TOP_BAR_LEFT = "calc(1rem + var(--control-height-touch))";
/** md 以上の幅の上限。 */
const WIDE_WIDTH = "22rem";
/** PageHeader に重ねるときに必要な最小の幅（これより狭ければ PageHeader の下に出す）。 */
const MIN_HEADER_WIDTH_REM = 14;

export type ToastPlacementMode = "top-bar" | "page-header" | "below-page-header" | "top-right";

export interface ToastPlacementInput {
  /** md 未満（`NAV_DRAWER_QUERY`）。 */
  narrow: boolean;
  /** AppShell の上端のバー（md 未満のドロワーのナビ）がある。 */
  topBar: boolean;
  /** 画面（layout viewport）の幅（px）。 */
  viewportWidth: number;
  /** 1rem の px。 */
  remPx: number;
  /** PageHeader の画面上の位置（px）。PageHeader が無いときは null。 */
  header: { top: number; bottom: number; left: number } | null;
  /** ページの操作の並びの左端・右端（px）。ページの操作が無いときは null。 */
  headerActions: { left: number; right: number } | null;
}

export interface ToastPlacement {
  mode: ToastPlacementMode;
  style: {
    top: string;
    left?: string;
    right: string;
    width?: string;
    maxHeight: string;
  };
}

const SAFE_RIGHT = `max(${EDGE}, env(safe-area-inset-right))`;
const SAFE_TOP = `max(${EDGE}, env(safe-area-inset-top))`;

function maxHeight(top: string) {
  return `calc(100dvh - ${top} - ${EDGE})`;
}

export function resolveToastPlacement({
  narrow,
  topBar,
  viewportWidth,
  remPx,
  header,
  headerActions,
}: ToastPlacementInput): ToastPlacement {
  if (narrow) {
    const top = `max(${NARROW_TOP}, env(safe-area-inset-top))`;
    return {
      mode: "top-bar",
      style: {
        top,
        left: topBar ? NARROW_TOP_BAR_LEFT : `max(${EDGE}, env(safe-area-inset-left))`,
        right: SAFE_RIGHT,
        maxHeight: maxHeight(top),
      },
    };
  }

  const width = `min(${WIDE_WIDTH}, calc(100vw - 2 * ${EDGE}))`;
  const headerVisible = header !== null && header.bottom > 0;
  if (!headerVisible) {
    return { mode: "top-right", style: { top: SAFE_TOP, right: SAFE_RIGHT, width, maxHeight: maxHeight(SAFE_TOP) } };
  }

  const headerTop = `calc(${Math.max(0, Math.round(header.top))}px + ${EDGE})`;
  const minWidth = MIN_HEADER_WIDTH_REM * remPx;
  const gap = EDGE_REM * remPx;
  // ページの操作の右（画面の右端から 1rem まで）に置ける幅。操作が無ければ PageHeader の幅いっぱい。
  const rightSpace = viewportWidth - gap - (headerActions ? headerActions.right + gap : header.left + gap);
  if (!headerActions || rightSpace >= minWidth) {
    return {
      mode: "page-header",
      style: {
        top: headerTop,
        right: SAFE_RIGHT,
        width: `min(${WIDE_WIDTH}, ${Math.floor(rightSpace)}px)`,
        maxHeight: maxHeight(headerTop),
      },
    };
  }

  // ページの操作の左、PageHeader の左端（とそこからの 1rem）までに置ける幅。
  const leftSpace = headerActions.left - gap - (header.left + gap);
  if (leftSpace >= minWidth) {
    const right = `calc(${Math.round(viewportWidth - headerActions.left)}px + ${EDGE})`;
    return {
      mode: "page-header",
      style: { top: headerTop, right, width: `min(${WIDE_WIDTH}, ${Math.floor(leftSpace)}px)`, maxHeight: maxHeight(headerTop) },
    };
  }

  const top = `calc(${Math.round(header.bottom)}px + ${EDGE})`;
  return { mode: "below-page-header", style: { top, right: SAFE_RIGHT, width, maxHeight: maxHeight(top) } };
}

/** PageHeader の目印（`<header data-page-header>`）。 */
export const PAGE_HEADER_SELECTOR = "[data-page-header]";
/** PageHeader のページの操作の並びの目印。 */
export const PAGE_HEADER_ACTIONS_SELECTOR = "[data-page-header-actions]";
/** AppShell の上端のバーがあるとき（md 未満）の目印。 */
export const NAV_DRAWER_SHELL_SELECTOR = '[data-nav-mode="drawer"]';

/** 画面から置き場所の入力を読む。 */
export function readToastPlacementInput(doc: Document, narrow: boolean): ToastPlacementInput {
  const header = doc.querySelector<HTMLElement>(PAGE_HEADER_SELECTOR);
  const headerRect = header?.getBoundingClientRect() ?? null;
  const actions = header?.querySelector<HTMLElement>(PAGE_HEADER_ACTIONS_SELECTOR) ?? null;
  const view = doc.defaultView;
  const remPx = view ? parseFloat(view.getComputedStyle(doc.documentElement).fontSize) || 16 : 16;
  return {
    narrow,
    topBar: doc.querySelector(NAV_DRAWER_SHELL_SELECTOR) !== null,
    viewportWidth: doc.documentElement.clientWidth || view?.innerWidth || 0,
    remPx,
    header: headerRect ? { top: headerRect.top, bottom: headerRect.bottom, left: headerRect.left } : null,
    headerActions: actions
      ? (({ left, right }) => ({ left, right }))(actions.getBoundingClientRect())
      : null,
  };
}
