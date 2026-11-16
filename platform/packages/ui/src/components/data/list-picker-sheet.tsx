import { type LucideIcon, Plus, X } from "lucide-react";
import { type ReactNode, type RefObject, useRef, useState } from "react";

import { Button } from "../ui/button";
import { SideSheet } from "../ui/side-sheet";
import { ListPicker, type ListPickerProps, type ListPickerSearch } from "./list-picker";

export interface ListPickerSheetProps
  extends Omit<
    ListPickerProps,
    "title" | "headingLevel" | "description" | "actions" | "fixedHeight" | "fill" | "className" | "search" | "testId"
  > {
  /** 開いているか。閉じている間もシートは描いたまま（`SideSheet`）。候補の取得は製品が開いている間だけにする。 */
  open: boolean;
  /** 閉じる要求（×・キャンセル・Escape・scrim のタップ）。製品が `open` を false にする。 */
  onClose: () => void;
  /** シートの見出しとダイアログの名前（翻訳済み。例:「文書を追加」）。開くボタンと同じ語にし、説明は足さない。 */
  title: string;
  /** 閉じるボタン（×）の名前（翻訳済み）。 */
  closeLabel: string;
  /**
   * 候補の検索欄。画面に出すのは placeholder だけで、`label` は読み上げの名前にする（見える label は出さない）。
   * 開いたときにフォーカスする。
   */
  search?: ListPickerSearch;
  /** 確定のボタンの文言（翻訳済み。例:「3 件を追加」）。 */
  submitLabel: string;
  submitIcon?: LucideIcon;
  onSubmit: () => void;
  /** 確定の処理中（ボタンの `loading`。選択も切り替えない）。 */
  submitting?: boolean;
  /** 確定できない間（既定は選択が 0 件のとき）。 */
  submitDisabled?: boolean;
  /** 取り消しのボタンの文言（翻訳済み。例:「キャンセル」）。 */
  cancelLabel: string;
  /** 確定の操作の行の名前（読み上げ。翻訳済み）。 */
  actionsLabel: string;
  /**
   * 確定の結果（例: 一部を追加できなかったときの `FormStatus`）。確定の操作の行の直上に出す。
   * モーダルの中の結果は Toast にしない（design-system README §4「通知（Toast）」）。
   */
  status?: ReactNode;
  /** 閉じたときにフォーカスを戻す先（開くボタン）。省略時は開く前にフォーカスがあった要素。 */
  returnFocusRef?: RefObject<HTMLElement | null>;
  /**
   * シートの data-testid（`role="dialog"` の要素）。中の一覧は `<testId>-list`（フッターは `<testId>-list-footer`）、
   * 確定は `<testId>-submit`、取り消しは `<testId>-cancel`、scrim は `<testId>-scrim`。
   */
  testId?: string;
}

/**
 * 一覧から選んで追加するシート（#5279）。大量の候補から複数を選んで追加する操作（RAG の「文書を追加」など）を、
 * ページの中に差し込まず、右から出す `SideSheet`（md 40rem・sm 未満は全画面）で開く。
 *
 * - 見出しの行は `title`（開くボタンと同じ語）と ×。中は検索（placeholder だけ）と `ListPicker` の一覧で、残りの高さを一覧に使う。
 * - 下部に「N 件を追加 / キャンセル」を固定し、確定の結果（一部失敗など）はその直上に出す。
 * - 開いたら検索欄へフォーカスし、閉じたら開くボタンへ戻す。閉じ方は ×・キャンセル・Escape・scrim と、製品が決める確定の成功。
 * - 開くボタンは開くだけにする（開閉を切り替えない。`aria-haspopup="dialog"`）。
 * - 開くたびに一覧の中の状態を作り直す。選択と検索語は製品が持つので、製品も開くときに空にする。
 */
export function ListPickerSheet({
  open,
  onClose,
  title,
  closeLabel,
  search,
  submitLabel,
  submitIcon = Plus,
  onSubmit,
  submitting = false,
  submitDisabled,
  cancelLabel,
  actionsLabel,
  status,
  returnFocusRef,
  testId,
  id,
  selectedKeys,
  disabled,
  ...pickerProps
}: ListPickerSheetProps) {
  const searchRef = useRef<HTMLInputElement | null>(null);
  // 開くたびに一覧の中の状態（「選択中だけ表示」・入力中の検索語・キーボードの位置）を作り直す。
  // シート（SideSheet）は作り直さない（開閉の動きとフォーカスの戻り先を保つ）。
  const [session, setSession] = useState({ open, key: 0 });
  if (session.open !== open) setSession({ open, key: open ? session.key + 1 : session.key });
  const cannotSubmit = submitDisabled ?? selectedKeys.size === 0;

  const footer = (
    <div className="grid min-w-0 gap-2">
      {status ? <div className="min-w-0">{status}</div> : null}
      <div role="group" aria-label={actionsLabel} className="flex min-w-0 flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="primary"
          icon={submitIcon}
          loading={submitting}
          disabled={cannotSubmit}
          onClick={() => {
            if (!cannotSubmit && !submitting) onSubmit();
          }}
          data-testid={testId ? `${testId}-submit` : undefined}
        >
          {submitLabel}
        </Button>
        <Button
          type="button"
          variant="secondary"
          icon={X}
          disabled={submitting}
          onClick={onClose}
          data-testid={testId ? `${testId}-cancel` : undefined}
        >
          {cancelLabel}
        </Button>
      </div>
    </div>
  );

  return (
    <SideSheet
      open={open}
      onClose={() => {
        // 追加の処理中は閉じない（結果をシートの中に出すため）。
        if (!submitting) onClose();
      }}
      title={title}
      closeLabel={closeLabel}
      side="right"
      size="md"
      fullScreenBelowSm
      id={id}
      returnFocusRef={returnFocusRef}
      initialFocusRef={search ? searchRef : undefined}
      footer={footer}
      data-testid={testId}
    >
      <ListPicker
        key={session.key}
        {...pickerProps}
        id={`${id}-list`}
        selectedKeys={selectedKeys}
        disabled={disabled || submitting}
        search={search ? { ...search, ref: searchRef, labelHidden: true } : undefined}
        fill
        // シートの面がそのまま入れ物になるので、ListPicker の枠（カード）は描かない。
        className="rounded-none border-0 bg-transparent p-0"
        testId={testId ? `${testId}-list` : undefined}
      />
    </SideSheet>
  );
}
