import type { ReactNode } from "react";
import { ArrowLeft, FilePen } from "lucide-react";

import {
  Banner,
  Button,
  Card,
  FixedSplitPane,
  PageBody,
  PageHeader,
  TimedLoadingState,
  type FixedSplitWidePane,
} from "@engchina/production-ready-ui";

import { EmptyState, ErrorState } from "@/components/StateViews";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";

/** RAG の分割ペインの比率を保存する localStorage key の前置き（製品ごとに分ける）。 */
export const RAG_SPLIT_STORAGE_PREFIX = "production-ready-rag.fixedSplitPane";

/** URL の `?id=` の対象が無いときの説明。黙って別の対象に置き換えず、一覧へ戻る導線を出す。 */
export function MissingEditorTarget({ id, onBack }: { id: string; onBack: () => void }) {
  return (
    <EmptyState
      title={t("editor.notFoundTitle")}
      hint={t("editor.notFoundHint", { id })}
      action={
        <Button variant="secondary" icon={ArrowLeft} onClick={onBack}>
          {t("common.backToList")}
        </Button>
      }
    />
  );
}

/**
 * A 型のエディタの対象（`?id=<id>` やパスの `:id`）を読み込んでいる間・見つからない・取得に失敗したときの画面。
 * 業務ビューとナレッジベースで共有する（#555）。エディタと同じ PageHeader（パンくず・一覧へ戻る）を先に出し、
 * 本文だけを読み込み中（`skeleton`）・「対象が見つかりません」・再試行に切り替える。見つからないときは
 * 別の対象へ置き換えない。
 */
export function EditorTargetState({
  id,
  listLabel,
  error,
  loadingLabel,
  loadingTestId,
  errorFallback,
  skeleton,
  onBack,
  onRetry,
}: {
  id: string;
  listLabel: string;
  /** 取得の失敗（読み込み中は null）。 */
  error: unknown;
  loadingLabel: string;
  loadingTestId: string;
  /** ApiError 以外の失敗のときの文言。 */
  errorFallback: string;
  /** 読み込み中に本文を覆う、内容の形をした Skeleton。 */
  skeleton: ReactNode;
  onBack: () => void;
  onRetry: () => void;
}) {
  const notFound = error instanceof ApiError && error.status === 404;
  return (
    <div>
      <PageHeader
        wide
        title={listLabel}
        // エディタと同じ左上の「一覧へ戻る」（#618）。見つからないときは本文の「一覧へ戻る」だけにし、同じボタンを重ねない。
        back={
          notFound
            ? undefined
            : {
                label: t("common.backToList"),
                ariaLabel: t("editor.backToListOf", { list: listLabel }),
                onClick: onBack,
                testId: "editor-back",
              }
        }
      />
      <PageBody wide>
        {!error ? (
          <TimedLoadingState
            label={loadingLabel}
            operationKey={`${loadingTestId}-${id}`}
            placement="page"
            testId={loadingTestId}
          >
            {skeleton}
          </TimedLoadingState>
        ) : notFound ? (
          <Card>
            <MissingEditorTarget id={id} onBack={onBack} />
          </Card>
        ) : (
          <ErrorState
            message={error instanceof ApiError ? error.message : errorFallback}
            onRetry={onRetry}
          />
        )}
      </PageBody>
    </div>
  );
}

/**
 * 一覧の上に出す「作成中の下書きがあります」（業務ビューとナレッジベースで共有。#555）。
 * 新規作成の下書きはエディタを閉じても同じタブに残るため、一覧から再開できるようにする。
 */
export function EditorDraftNotice({ message, onOpen }: { message: string; onOpen: () => void }) {
  return (
    <Banner
      severity="info"
      action={
        <Button size="sm" variant="secondary" icon={FilePen} onClick={onOpen}>
          {t("editor.actions.openDraft")}
        </Button>
      }
    >
      {message}
    </Banner>
  );
}

/**
 * B 型（マスタ詳細の閲覧）の一覧 + 詳細。共有の FixedSplitPane に RAG の保存 key と文言を渡す。
 * xl 未満は縦積み（共有部品の既定）。
 */
export function RagSplitPane({
  splitId,
  left,
  right,
  preferredWidePane = "right",
}: {
  splitId: string;
  left: ReactNode;
  right: ReactNode;
  preferredWidePane?: FixedSplitWidePane;
}) {
  return (
    <FixedSplitPane
      splitId={splitId}
      preferredWidePane={preferredWidePane}
      storagePrefix={RAG_SPLIT_STORAGE_PREFIX}
      labels={{
        separator: t("split.separator"),
        hint: t("split.hint"),
        equal: t("split.equal"),
        leftWide: t("split.leftWide"),
        rightWide: t("split.rightWide"),
      }}
      left={left}
      right={right}
    />
  );
}
