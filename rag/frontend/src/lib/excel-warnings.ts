import { t } from "@/lib/i18n";

/** 表頭の推定の信頼度が低いシートの warning（rag_parser_core の EXCEL_HEADER_LOW_CONFIDENCE_WARNING。#1229）。 */
const HEADER_LOW_CONFIDENCE = "excel_header_low_confidence";

function splitWarning(warning: string): [string, string[]] {
  const [code, ...args] = warning.split(":");
  return [code.trim(), args];
}

/**
 * Excel の前処理の warning（`<code>:<シート名>[:<件数>]`）の日本語の説明。Excel の warning でなければ null。
 * シート名に「:」を含むときも、件数の付く warning 以外はシート名として残りをつなげる。
 */
export function excelWarningLabel(warning: string): string | null {
  const [code, args] = splitWarning(warning);
  switch (code) {
    case HEADER_LOW_CONFIDENCE:
      return t("documents.excelWarning.headerLowConfidence", { sheet: args.join(":") });
    case "excel_formula_without_cached_value": {
      const count = args.length > 1 ? args[args.length - 1] : "";
      const sheet = args.length > 1 ? args.slice(0, -1).join(":") : args.join(":");
      return t("documents.excelWarning.formulaWithoutValue", { sheet, count });
    }
    case "excel_sheet_not_found":
      return t("documents.excelWarning.sheetNotFound", { sheet: args.join(":") });
    case "excel_range_sheet_not_found":
      return t("documents.excelWarning.rangeSheetNotFound", { sheet: args.join(":") });
    default:
      return null;
  }
}

/** 表頭の推定の信頼度が低いシート（重複は除く）。文書レシピが確認（REVIEW）で止まった理由。 */
export function lowConfidenceHeaderSheets(warnings: readonly string[]): string[] {
  const sheets: string[] = [];
  for (const warning of warnings) {
    const [code, args] = splitWarning(warning);
    const sheet = args.join(":");
    if (code === HEADER_LOW_CONFIDENCE && sheet && !sheets.includes(sheet)) sheets.push(sheet);
  }
  return sheets;
}
