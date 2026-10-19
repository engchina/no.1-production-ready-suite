import { PageBody } from "../app-shell/PageBody";
import { ListSkeleton, Skeleton } from "../ui/skeleton";
import { TimedLoadingState } from "./processing-state";

export interface PageLoadingStateProps {
  label: string;
  operationKey?: string | number | null;
  testId?: string;
  /** `PageBody` の `wide`。画面の `PageHeader` / `PageBody` と同じ値にする。 */
  wide?: boolean;
}

/**
 * ページ全体が使えない初期処理（認証の確認・DB の状態の確認・画面のコードの読み込み）の読み込み中（placement は page）。
 * ほかの領域と同じく、本文の先頭に本文と同じ幅の行で処理の名前と経過時間を出し、その下を本文の形の Skeleton で
 * 覆う（画面の中央に浮かせない。#2510）。`AppShell` の外（ログインの確認中など）でも同じ本文の幅と余白で出す。
 */
export function PageLoadingState({ label, operationKey, testId, wide = false }: PageLoadingStateProps) {
  return (
    <PageBody wide={wide}>
      <TimedLoadingState label={label} operationKey={operationKey} placement="page" testId={testId}>
        <Skeleton className="h-24 w-full" />
        <ListSkeleton rows={{ base: 3, md: 5 }} />
      </TimedLoadingState>
    </PageBody>
  );
}
