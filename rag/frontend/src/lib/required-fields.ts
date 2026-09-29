/**
 * 必須の入力欄の検証と、送信に失敗したときのフォーカス（UX 契約 messaging.md §3.2。#521）。
 * 業務ビュー・ナレッジベースの作成・編集フォームで使う。
 */

/** 前後の空白を除いて空なら `message` を返す（空白だけの入力も未入力として扱う）。 */
export function requiredTextError(value: string, message: string): string | null {
  return value.trim() ? null : message;
}

/** 検証エラーのある最初の欄の ID（欄の並び順で渡す）。無ければ null。 */
export function firstInvalidFieldId(
  fields: ReadonlyArray<readonly [id: string, error: string | null | undefined]>
): string | null {
  return fields.find(([, error]) => Boolean(error))?.[0] ?? null;
}

/** 送信に失敗したとき、最初の不正な欄へフォーカスを移す。 */
export function focusFirstInvalidField(
  fields: ReadonlyArray<readonly [id: string, error: string | null | undefined]>
): void {
  const id = firstInvalidFieldId(fields);
  if (id) document.getElementById(id)?.focus();
}
