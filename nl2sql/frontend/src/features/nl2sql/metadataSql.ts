import type { DbAdminObjectDetail, SchemaCatalog } from "./types";

export function buildMetadataInputTexts(
  details: DbAdminObjectDetail[],
  catalog: SchemaCatalog | null,
  sampleLimit: number
) {
  const structure: string[] = [];
  const primaryKeys: string[] = [];
  const foreignKeys: string[] = [];
  const samples: string[] = [];
  const catalogByName = new Map(
    (catalog?.tables ?? []).map((table) => [table.table_name.toUpperCase(), table])
  );

  for (const detail of details) {
    const catalogTable = catalogByName.get(detail.name.toUpperCase());
    structure.push(
      [
        `OBJECT: ${detail.name}`,
        `TYPE: ${detail.object_type}`,
        `COMMENT: ${detail.comment || "-"}`,
        "COLUMNS:",
        ...detail.columns.map(
          (column) =>
            `- ${column.column_name}: ${column.data_type} ` +
            `NULLABLE=${column.nullable ? "Y" : "N"} ` +
            `COMMENT=${column.comment || column.logical_name || "-"}`
        ),
      ].join("\n")
    );

    const constraints = catalogTable?.constraints ?? [];
    const pk = constraints.filter((constraint) => /\sP(\(|$)/.test(constraint));
    if (pk.length > 0) primaryKeys.push(`OBJECT: ${detail.name}\n${pk.join("\n")}`);
    const fk = constraints.filter((constraint) => /\sR(\(|$)/.test(constraint));
    if (fk.length > 0) foreignKeys.push(`OBJECT: ${detail.name}\n${fk.join("\n")}`);

    if (sampleLimit > 0 && catalogTable) {
      const sampleLines = catalogTable.columns
        .map((column) => {
          const values = column.sample_values.slice(0, sampleLimit).join(", ");
          return values ? `${column.column_name}: ${values}` : "";
        })
        .filter(Boolean);
      if (sampleLines.length > 0) samples.push(`OBJECT: ${detail.name}\n${sampleLines.join("\n")}`);
    }
  }

  return {
    structureText: structure.join("\n\n"),
    primaryKeyText: primaryKeys.join("\n\n"),
    foreignKeyText: foreignKeys.join("\n\n"),
    sampleText: samples.join("\n\n"),
  };
}
