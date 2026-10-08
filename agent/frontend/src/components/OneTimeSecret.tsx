import { useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Banner, Button, FieldActionRow, FormStatus, TextField, toast } from "@production-ready/ui";

// 作成・発行の直後に 1 回だけ見せる秘密（API キー・Webhook の秘密。#790）。
// - 秘密は読み取り専用の入力欄だけに出す。Toast・console・Banner の本文には出さない
//   （Toast はスクリーンショット・通知の履歴・読み上げに残りやすい）。
// - コピーに失敗したら、秘密を含まない中立の文言を欄の直下に出し、欄の文字を選択して手でコピーできるようにする。
// - 起点の操作の直下・カードの全幅に置く（UX 契約 messaging.md §10.1）。「保管しました」で閉じる。

export type OneTimeSecretProps = {
  id: string;
  title: string;
  description: string;
  /** 入力欄のラベル（例:「API キー」）。 */
  label: string;
  value: string;
  copyLabel: string;
  copiedMessage: string;
  /** コピーに失敗したときの文言。秘密を含めない。 */
  copyFailedMessage: string;
  doneLabel: string;
  onDone: () => void;
  testId?: string;
  valueTestId?: string;
};

export function OneTimeSecret({
  id,
  title,
  description,
  label,
  value,
  copyLabel,
  copiedMessage,
  copyFailedMessage,
  doneLabel,
  onDone,
  testId,
  valueTestId,
}: OneTimeSecretProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopyFailed(false);
      toast.success(copiedMessage);
    } catch {
      setCopyFailed(true);
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }

  return (
    <Banner severity="success" title={title}>
      <div className="space-y-3" data-testid={testId}>
        <p>{description}</p>
        <FieldActionRow
          actions={
            <Button variant="secondary" icon={Copy} onClick={() => void copy()}>
              {copyLabel}
            </Button>
          }
          footer={copyFailed ? <FormStatus tone="info" message={copyFailedMessage} /> : null}
        >
          <TextField
            ref={inputRef}
            id={id}
            label={label}
            value={value}
            readOnly
            spellCheck={false}
            autoComplete="off"
            inputClassName="font-mono"
            onFocus={(event) => event.currentTarget.select()}
            data-testid={valueTestId}
          />
        </FieldActionRow>
        <Button variant="secondary" icon={Check} onClick={onDone}>
          {doneLabel}
        </Button>
      </div>
    </Banner>
  );
}
