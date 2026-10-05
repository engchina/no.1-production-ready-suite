import { ArrowDown, MessageSquarePlus, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import type { ReactNode, Ref } from "react";

import { cn } from "../../lib/utils";
import { Button } from "../ui/button";
import { SideSheet } from "../ui/side-sheet";
import { Skeleton } from "../ui/skeleton";
import type { ChatHistoryPanel } from "./use-chat-history-panel";

/** e2e で使う testid。省略したものは `testIdPrefix` から作る（既存の spec の testid を変えないため）。 */
export interface ChatLayoutTestIds {
  /** 履歴のパネル・シート（既定 `<prefix>-history`。シートの scrim は `<history>-scrim`）。 */
  history?: string;
  /** 履歴の開閉ボタン（既定 `<prefix>-history-toggle`）。 */
  historyToggle?: string;
  /** 会話の領域（既定 `<prefix>-panel`）。 */
  panel?: string;
  /** 上端の行の会話の名前（既定 `<prefix>-conversation-title`）。 */
  title?: string;
  /** 会話の欄（既定 `<prefix>-conversation`）。 */
  log?: string;
  /** 入力欄の領域（既定 `<prefix>-composer-region`）。 */
  composer?: string;
  /** 「最新へ」（既定 `<prefix>-latest`）。 */
  latest?: string;
}

export interface ChatLayoutNewConversation {
  /** 「新しい会話」（翻訳済み）。 */
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

export interface ChatLayoutLatest {
  /** 上を読んでいる間に新しい内容が届いた（`useChatAutoScroll` の `showLatest`）。 */
  visible: boolean;
  /** 「最新へ」のボタンの文言（翻訳済み）。 */
  label: string;
  /** 会話の欄を末尾へ動かす（`useChatAutoScroll` の `scrollToLatest`）。 */
  onClick: () => void;
}

export interface ChatLayoutProps {
  /** `useChatHistoryPanel` の戻り値。 */
  history: ChatHistoryPanel;
  /** 「会話の履歴」（翻訳済み）。パネル・シートの見出しと名前、開閉ボタンの名前に使う。 */
  historyTitle: string;
  /** シートを閉じるボタンの名前（翻訳済み。例:「会話の履歴を閉じる」）。 */
  historyCloseLabel: string;
  /** 履歴の中身（一覧・読み込み中・失敗・空・ページング）。 */
  historyContent: ReactNode;
  /** 会話の領域（`<section>`）の名前（翻訳済み）。 */
  label: string;
  /** 今の会話の名前。会話を選んでいない・まだ分からないときは `null` / `undefined`。 */
  conversationTitle?: string | null;
  /** 会話の名前を読み込んでいる（名前の位置に `Skeleton` を出す）。 */
  conversationTitleLoading?: boolean;
  /** 上端の行の右端の「新しい会話」。 */
  newConversation: ChatLayoutNewConversation;
  /** 履歴の開閉を押せない間（対象の一覧の読み込み中など。#1153）。 */
  historyToggleDisabled?: boolean;
  /** 会話の欄（`role="log"`）の名前（翻訳済み。例:「会話」）。 */
  logLabel: string;
  /** 会話の欄の要素（自動スクロール・引用への移動は、この要素の `scrollTo` で行う）。 */
  logRef?: Ref<HTMLDivElement>;
  /** 上を読んでいる間に新しい内容が届いたときの「最新へ」（会話の欄の下端の中央に重ねる）。 */
  latest?: ChatLayoutLatest;
  /** 会話の欄の中身（往復の一覧・読み込み中・失敗・空）。 */
  children: ReactNode;
  /** 入力欄の領域の中身（設定の行・入力欄と送信・通知）。 */
  composer: ReactNode;
  /** testid の接頭辞（RAG・Agent は `chat`、NL2SQL は `sql-chat`）。 */
  testIdPrefix?: string;
  testIds?: ChatLayoutTestIds;
}

/**
 * チャットの骨格（3 製品共通。UX 契約 page-archetypes.md §6、#1161）。
 *
 * - 会話の履歴: lg 以上は会話の左に `<aside>`（開くと 20rem の列、閉じている間も描いて `aria-controls` の先を保つ）、
 *   lg 未満はモーダルの `SideSheet`（会話を選ぶ・Escape・scrim・閉じるボタンで閉じ、開閉ボタンへフォーカスを戻す）。
 * - 会話の領域: 上端の行（履歴の開閉・今の会話の名前・「新しい会話」）、`role="log"` の会話の欄（中だけスクロール）、
 *   入力欄の領域の 3 段。lg 未満は高さを 70dvh（最小 28rem）にし、長い会話でページを伸ばさない。lg 以上は残りの高さ。
 * - 文言は翻訳済みを受け、業務の語彙を持たない。往復の表示・入力欄・履歴の中身は製品が渡す。
 */
export function ChatLayout({
  history,
  historyTitle,
  historyCloseLabel,
  historyContent,
  label,
  conversationTitle,
  conversationTitleLoading = false,
  newConversation,
  historyToggleDisabled = false,
  logLabel,
  logRef,
  latest,
  children,
  composer,
  testIdPrefix = "chat",
  testIds,
}: ChatLayoutProps) {
  const ids = {
    history: testIds?.history ?? `${testIdPrefix}-history`,
    historyToggle: testIds?.historyToggle ?? `${testIdPrefix}-history-toggle`,
    panel: testIds?.panel ?? `${testIdPrefix}-panel`,
    title: testIds?.title ?? `${testIdPrefix}-conversation-title`,
    log: testIds?.log ?? `${testIdPrefix}-conversation`,
    composer: testIds?.composer ?? `${testIdPrefix}-composer-region`,
    latest: testIds?.latest ?? `${testIdPrefix}-latest`,
  };
  return (
    <div
      className={cn(
        "grid min-w-0 gap-4 lg:min-h-0 lg:flex-1",
        history.inline && history.inlineOpen && "lg:grid-cols-[20rem_minmax(0,1fr)]"
      )}
      data-chat-layout=""
    >
      {history.inline ? (
        <aside
          id={history.id}
          aria-label={historyTitle}
          data-testid={ids.history}
          className={cn(
            "min-h-0 min-w-0 flex-col gap-3 rounded-lg border border-border bg-surface px-3 pb-3 pt-2 shadow-sm",
            history.inlineOpen ? "flex" : "hidden"
          )}
        >
          {/* 見出しの行は会話の領域の上端の行と高さをそろえる。 */}
          <h2 className="flex min-h-8 items-center px-1 text-sm font-medium text-fg">{historyTitle}</h2>
          {historyContent}
        </aside>
      ) : (
        <SideSheet
          open={history.sheetOpen}
          onClose={history.closeSheet}
          title={historyTitle}
          closeLabel={historyCloseLabel}
          id={history.id}
          returnFocusRef={history.toggleRef}
          bodyClassName="gap-3"
          data-testid={ids.history}
        >
          {historyContent}
        </SideSheet>
      )}

      <section
        aria-label={label}
        data-testid={ids.panel}
        className="flex h-[70dvh] min-h-[28rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-surface shadow-sm lg:h-auto lg:min-h-0"
      >
        {/* 上端の行: 履歴の開閉（左端）・今の会話の名前・新しい会話（右端）。履歴を閉じていても使える。 */}
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
          <Button
            ref={history.toggleRef}
            type="button"
            variant="ghost"
            size="sm"
            iconOnly
            icon={history.open ? PanelLeftClose : PanelLeftOpen}
            aria-label={historyTitle}
            aria-expanded={history.open}
            aria-controls={history.id}
            data-testid={ids.historyToggle}
            disabled={historyToggleDisabled}
            onClick={history.toggle}
          />
          <div className="min-w-0 flex-1">
            {conversationTitle ? (
              <h2 className="truncate text-sm font-medium text-fg" title={conversationTitle} data-testid={ids.title}>
                {conversationTitle}
              </h2>
            ) : conversationTitleLoading ? (
              <Skeleton className="h-4 w-40" />
            ) : null}
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={MessageSquarePlus}
            disabled={newConversation.disabled}
            onClick={newConversation.onClick}
          >
            {newConversation.label}
          </Button>
        </div>

        {/* 会話の欄。新しいメッセージを role="log" で知らせる（messaging.md §11.4）。 */}
        <div className="relative flex min-h-0 flex-1 flex-col">
          <div
            ref={logRef}
            role="log"
            aria-label={logLabel}
            data-testid={ids.log}
            className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 [scrollbar-gutter:stable]"
          >
            {children}
          </div>
          {latest?.visible ? (
            // 上を読んでいる間に届いた新しい内容へ戻る（引き戻さない。#1161）。会話の欄の外に置き、読み上げの log に混ぜない。
            <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                icon={ArrowDown}
                className="pointer-events-auto shadow-[var(--shadow-popover)]"
                data-testid={ids.latest}
                onClick={latest.onClick}
              >
                {latest.label}
              </Button>
            </div>
          ) : null}
        </div>

        <div className="shrink-0 space-y-2 border-t border-border p-3" data-testid={ids.composer}>
          {composer}
        </div>
      </section>
    </div>
  );
}
