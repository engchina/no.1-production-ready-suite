// 管理画面で「ID/コード」と「表示名」を組で見せるときの表示規則。
// ID/コードは一意で安定したキーなので主表示にし、表示名は補助表示にする。

function cleaned(value?: string | null) {
  return value?.trim() ?? "";
}

/** 補助表示する表示名。空、または ID と同じなら重複行を出さないため空文字を返す。 */
export function identitySecondaryName(id: string, name?: string | null) {
  const secondary = cleaned(name);
  return secondary && secondary !== cleaned(id) ? secondary : "";
}

/** ID と表示名を 1 つの文字列で併記するときの表記（例: `data_user（データユーザー）`）。 */
export function identityInlineLabel(id: string, name?: string | null) {
  const secondary = identitySecondaryName(id, name);
  return secondary ? `${id}（${secondary}）` : id;
}
