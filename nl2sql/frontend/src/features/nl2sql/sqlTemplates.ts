import type { DbAdminObjectDetail } from "./types";

/** データ SQL テンプレート生成(SQL Assist の apply_sql_template 再マップ)。
 * 選択中のテーブル詳細があれば実列名で、無ければ汎用ひな型を返す。
 */

function tableAndColumns(detail: DbAdminObjectDetail | null) {
  if (detail && detail.columns.length > 0) {
    return { table: detail.name, columns: detail.columns.map((column) => column.column_name) };
  }
  return { table: "TABLE_NAME", columns: ["COLUMN1", "COLUMN2"] };
}

export function buildInsertTemplate(detail: DbAdminObjectDetail | null): string {
  const { table, columns } = tableAndColumns(detail);
  const values = columns.map((name) => `:${name.toLowerCase()}`).join(", ");
  return `INSERT INTO ${table} (${columns.join(", ")})\nVALUES (${values});`;
}

export function buildMultiInsertTemplate(detail: DbAdminObjectDetail | null): string {
  const single = buildInsertTemplate(detail);
  return `${single}\n${single}`;
}

export function buildUpdateTemplate(detail: DbAdminObjectDetail | null): string {
  const { table, columns } = tableAndColumns(detail);
  const keyColumn = columns[0] ?? "COLUMN1";
  const setColumn = columns[1] ?? keyColumn;
  return `UPDATE ${table}\nSET ${setColumn} = :${setColumn.toLowerCase()}\nWHERE ${keyColumn} = :${keyColumn.toLowerCase()};`;
}

export function buildDeleteTemplate(detail: DbAdminObjectDetail | null): string {
  const { table, columns } = tableAndColumns(detail);
  const keyColumn = columns[0] ?? "COLUMN1";
  return `DELETE FROM ${table}\nWHERE ${keyColumn} = :${keyColumn.toLowerCase()};`;
}

export function buildMergeTemplate(detail: DbAdminObjectDetail | null): string {
  const { table, columns } = tableAndColumns(detail);
  const keyColumn = columns[0] ?? "COLUMN1";
  const updateColumns = columns.slice(1);
  const updateSet =
    updateColumns.length > 0
      ? updateColumns.map((name) => `t.${name} = s.${name}`).join(", ")
      : `t.${keyColumn} = s.${keyColumn}`;
  return [
    `MERGE INTO ${table} t`,
    `USING (SELECT ${columns.map((name) => `:${name.toLowerCase()} AS ${name}`).join(", ")} FROM DUAL) s`,
    `ON (t.${keyColumn} = s.${keyColumn})`,
    `WHEN MATCHED THEN UPDATE SET ${updateSet}`,
    `WHEN NOT MATCHED THEN INSERT (${columns.join(", ")})`,
    `VALUES (${columns.map((name) => `s.${name}`).join(", ")});`,
  ].join("\n");
}
