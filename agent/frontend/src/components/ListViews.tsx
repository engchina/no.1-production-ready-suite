import { useEffect, type ReactNode } from "react";
import {
  ErrorState,
  offsetPagination,
  PagedDataTable as SharedPagedDataTable,
  Pagination,
  TimedLoadingState,
  type PagedDataTableProps as SharedPagedDataTableProps,
  type PaginationLabels,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { useWorkspaceState, type WorkspaceField, type WorkspaceValidator } from "@/lib/workspace-state";

const numberFormat = new Intl.NumberFormat("ja-JP");

/** 共通の Pagination に渡す Agent の文言（件数・ページ・前へ / 次へ）。 */
export function agentPaginationLabels(): PaginationLabels {
  return {
    summary: ({ start, end, total }) =>
      t("pager.range", {
        start: numberFormat.format(start),
        end: numberFormat.format(end),
        total: numberFormat.format(total),
      }),
    pageIndicator: (page, total) => t("pager.page", { page, total }),
    prev: t("pager.prev"),
    next: t("pager.next"),
    ariaLabel: t("pager.label"),
  };
}

const isPageNumber: WorkspaceValidator<number> = (value): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 1;

export type ListPageKey = WorkspaceField<"lists">;

export type PagedDataTableProps<T> = Omit<SharedPagedDataTableProps<T>, "paginationLabels" | "page" | "onPageChange"> & {
  /**
   * ページ番号を作業状態（このタブの sessionStorage）に残す一覧の名前（UX 契約 workspace-state.md）。
   * 一覧 → エディタ → 一覧と移動しても、再読込しても同じページに戻る。
   * 省略すると、ページは表示している間だけ保つ（開いている対象ごとに変わる一覧など）。
   */
  pageKey?: ListPageKey;
};

/**
 * クライアント側で全件を持つ一覧の標準形（#265、NL2SQL が基準）。
 * 共有の PagedDataTable（表頭の固定・md 未満 5 行 / md 以上 8 行の縦スクロール・10 件/ページ）に Agent の文言を渡す。
 * 定期的な再取得（Run・承認の 5 秒ごと）で行が変わってもページを戻さない（戻すのは `resetKey` が変わったときだけ）。
 */
export function PagedDataTable<T>({ pageKey, scrollAriaLabel, ...props }: PagedDataTableProps<T>) {
  // 0 件の空の状態（EmptyState など）は表の外に出す。表の中に置くと、横スクロールする幅の広い表の中央に寄り、
  // 375px で文言と「検索語をクリア」が切れる（RAG / NL2SQL も 0 件は表の代わりに空の状態を出す。#808）。
  if (props.rows.length === 0 && props.empty !== undefined && typeof props.empty !== "string") {
    return <>{props.empty}</>;
  }
  const shared = {
    ...props,
    // 表の中の縦スクロール領域に Tab で到達して読み上げられるようにする（WCAG 2.1.1）。
    scrollAriaLabel: scrollAriaLabel ?? listScrollLabel(props.ariaLabel),
    paginationLabels: agentPaginationLabels(),
  };
  return pageKey ? (
    <PersistedPagedDataTable<T> {...shared} pageKey={pageKey} />
  ) : (
    <SharedPagedDataTable<T> {...shared} />
  );
}

function PersistedPagedDataTable<T>({
  pageKey,
  ...props
}: Omit<SharedPagedDataTableProps<T>, "page" | "onPageChange"> & { pageKey: ListPageKey }) {
  const [page, setPage] = useWorkspaceState("lists", pageKey, 1, isPageNumber);
  return <SharedPagedDataTable<T> {...props} page={page} onPageChange={setPage} />;
}

/** 一覧の縦スクロール領域の読み上げ名（「〇〇一覧。スクロールできます。」）。 */
export function listScrollLabel(name: string | undefined): string | undefined {
  return name ? t("list.scrollLabel", { name }) : undefined;
}

/**
 * TanStack Query の初回の取得・失敗・成功で表示を分ける（UX 契約 messaging.md §3.6 / §3.7）。
 * 読み込み中は「〜を読み込んでいます」と経過時間（`TimedLoadingState`）の下に、内容の形の Skeleton を置いて寸法を予約する。
 * 同じ取得を別の領域でも待つときは、片方を `skeletonOnly` にして経過時間を 1 か所だけにする。
 */
export function QueryState<T>({
  query,
  loadingLabel,
  skeleton,
  skeletonOnly = false,
  testId = "query-loading",
  children,
}: {
  query: { isLoading: boolean; error: Error | null; data?: T };
  /** i18n 済みの「〜を読み込んでいます」。 */
  loadingLabel: string;
  /** 読み込み後の内容の形をした Skeleton（`TableSkeleton` / `ListSkeleton` / `FormSkeleton` など）。 */
  skeleton: ReactNode;
  skeletonOnly?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  if (query.isLoading) {
    if (skeletonOnly) {
      return (
        <div aria-busy="true" aria-label={loadingLabel} data-testid={testId}>
          {skeleton}
        </div>
      );
    }
    return (
      <TimedLoadingState label={loadingLabel} testId={testId}>
        {skeleton}
      </TimedLoadingState>
    );
  }
  if (query.error) {
    return <ErrorState message={query.error.message} retryLabel={t("common.retry")} />;
  }
  return <>{children}</>;
}

/** 作業状態に残す一覧のページ番号（`lists` の field）。サーバー側でページングする一覧で使う（#794）。 */
export function usePersistedPage(pageKey: ListPageKey): [number, (page: number) => void] {
  const [page, setPage] = useWorkspaceState("lists", pageKey, 1, isPageNumber);
  return [page, setPage];
}

/**
 * サーバー側でページングする一覧の下に置く共通の `Pagination`（#794。監査と同じ形）。
 * 1 ページしかないときは出さない。残していたページが範囲外（削除・期間の変更）になったら最後のページへ寄せる。
 */
export function ServerPagination({
  offset,
  limit,
  total,
  count,
  page,
  onPageChange,
  ariaLabel,
  testId,
}: {
  offset: number;
  limit: number;
  total: number;
  count: number;
  page: number;
  onPageChange: (page: number) => void;
  ariaLabel: string;
  testId: string;
}) {
  const paging = offsetPagination({ offset, limit, total, count });
  const lastPage = paging.totalPages;
  useEffect(() => {
    if (count === 0 && page > lastPage) onPageChange(lastPage);
  }, [count, page, lastPage, onPageChange]);
  const labels = agentPaginationLabels();
  return (
    <Pagination
      page={paging.page}
      totalPages={paging.totalPages}
      onPageChange={onPageChange}
      summary={labels.summary(paging.range)}
      pageIndicator={labels.pageIndicator?.(paging.page, paging.totalPages)}
      prevLabel={labels.prev}
      nextLabel={labels.next}
      ariaLabel={ariaLabel}
      testId={testId}
    />
  );
}
