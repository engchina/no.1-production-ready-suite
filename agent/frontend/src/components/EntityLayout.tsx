import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import {
  Breadcrumbs,
  Button,
  EmptyState,
  FixedSplitPane,
  type FixedSplitWidePane,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

/** Agent の分割ペインの比率を保存する localStorage key の前置き（製品ごとに分ける）。 */
export const AGENT_SPLIT_STORAGE_PREFIX = "production-ready-agent.fixedSplitPane";

/**
 * A 型のエディタのパンくず（一覧 › 対象名）。一覧へのリンクは内部リンクなので、
 * 未保存の編集があれば共有の離脱ガードが破棄を確認する。
 */
export function EditorBreadcrumbs({
  listLabel,
  listHref,
  current,
}: {
  listLabel: string;
  listHref: string;
  current: string;
}) {
  return (
    <Breadcrumbs
      ariaLabel={t("common.breadcrumbs")}
      linkComponent={Link}
      items={[{ label: listLabel, href: listHref }, { label: current }]}
    />
  );
}

/**
 * 一覧の行の先頭セルに置く対象名のボタン。行のクリックと同じ「開く / 選ぶ」をキーボードでも行えるようにする
 * （UX 契約 page-archetypes.md §0-7。選択だけの導線は RowActionMenu に入れない）。
 * 情報一覧の選択行の構造コントロールなので、アクションボタン（共有 Button）の適用除外として生の button を使う。
 */
export function RowTitleButton({
  title,
  subtitle,
  onClick,
  current = false,
}: {
  title: string;
  subtitle?: string;
  onClick: () => void;
  current?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={current ? "true" : undefined}
      className="block min-w-0 max-w-full cursor-pointer rounded-sm text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
    >
      <span className="block break-words text-sm font-medium leading-5 text-fg underline-offset-2 hover:underline [overflow-wrap:anywhere]">
        {title}
      </span>
      {subtitle ? (
        <span className="mt-0.5 block break-words text-xs text-fg-muted [overflow-wrap:anywhere]">{subtitle}</span>
      ) : null}
    </button>
  );
}

/** URL の `?id=` が一覧に無いときの説明。黙って別の対象に置き換えず、一覧へ戻る導線を出す。 */
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
 * B 型（マスタ詳細の閲覧）の一覧 + 詳細。共有の FixedSplitPane に Agent の保存 key と文言を渡す。
 * xl 未満は縦積み（共有部品の既定）。
 */
export function AgentSplitPane({
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
      storagePrefix={AGENT_SPLIT_STORAGE_PREFIX}
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
