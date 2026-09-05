/** 業務プロファイルの許可オブジェクト選択に関する純粋ロジック。 */

/** 引用符と大文字小文字の揺れを吸収した突合キー。 */
export function normalizeObjectKey(name: string) {
  return name.replaceAll('"', "").toUpperCase();
}

export interface SchemaBulkSelectionInput {
  /** 現在の選択(allowedTables / allowedViews)。 */
  current: readonly string[];
  /** サーバから取得した対象 object 名(正規化済み・OWNER.OBJECT)。 */
  snapshot: readonly string[];
  /** 対象スキーマの接頭辞(`OWNER.`)。 */
  ownerPrefix: string;
  /** true=全選択 / false=全解除。 */
  select: boolean;
  /**
   * 検索フィルタ適用中か。適用中は snapshot(=ヒットした object)だけを対象にし、
   * フィルタ外の選択済み object は保持する。
   */
  filtered: boolean;
}

/**
 * スキーマ単位の一括選択/解除を適用する。
 *
 * フィルタ適用中に「このスキーマを全選択/全解除」がスキーマ全体へ波及すると
 * 許可オブジェクト(=アクセススコープ)が意図せず広がるため、適用範囲を
 * snapshot に限定する。
 */
export function applySchemaBulkSelection({
  current,
  snapshot,
  ownerPrefix,
  select,
  filtered,
}: SchemaBulkSelectionInput): string[] {
  const scope = new Set(snapshot.map(normalizeObjectKey));
  const prefix = normalizeObjectKey(ownerPrefix);
  const retained = current.filter((name) => {
    const key = normalizeObjectKey(name);
    return filtered ? !scope.has(key) : !key.startsWith(prefix);
  });
  return select ? [...retained, ...snapshot] : retained;
}

/**
 * 1 object の選択をトグルする。
 *
 * 保存済みの値は引用符付き・大文字小文字混在(`"APP"."ORDERS"` / `app.orders`)の
 * ことがある。チェック表示は正規化キーで判定しているため、トグル側も同じキーで
 * 突合しないと「チェックは付くのに外せず、重複が積まれる」状態になる。
 */
export function toggleObjectSelection(current: readonly string[], name: string): string[] {
  const key = normalizeObjectKey(name);
  const next = current.filter((item) => normalizeObjectKey(item) !== key);
  return next.length === current.length ? [...current, key] : next;
}

/** 選択済みの突合用集合(表示・件数・一括操作で共有する)。 */
export function selectedObjectKeys(selected: readonly string[]): Set<string> {
  return new Set(selected.map(normalizeObjectKey));
}
