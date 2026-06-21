export const DEFAULT_CSV_TEXT = "CUSTOMER_ID,CUSTOMER_NAME\n1,青山商事\n2,東京製作所\n";

export interface CsvImportFormState {
  tableName: string;
  csvText: string;
  execute: boolean;
  replaceExisting: boolean;
}

export function defaultCsvImportForm(): CsvImportFormState {
  return {
    tableName: "imported_customers",
    csvText: DEFAULT_CSV_TEXT,
    execute: false,
    replaceExisting: false,
  };
}

export function csvImportPayload(state: CsvImportFormState) {
  return {
    table_name: state.tableName.trim(),
    csv_text: state.csvText,
    execute: state.execute,
    replace_existing: state.replaceExisting,
  };
}
