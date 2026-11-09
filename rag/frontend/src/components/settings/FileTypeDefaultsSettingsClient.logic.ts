/** 「形式別の既定」の表の下書きの計算（#4732）。選択肢そのものは backend が種類ごとに返す。 */
import { STRUCTURED_RECORDS_PREPROCESS_PROFILES } from "@/lib/chunking";
import type {
  FileTypeDefaultEntry,
  FileTypeDefaultRow,
  FileTypeDefaultsSettingsData,
  FileTypeDefaultsUpdate,
  FileTypeName,
  ParserAdapterBackend,
  PreprocessProfileName,
} from "@/lib/api";

export type FileTypeDraft = Record<FileTypeName, FileTypeDefaultEntry>;

const STRUCTURED_RECORDS_BACKEND: ParserAdapterBackend = "structured_records_jsonl";

function entryOf(row: FileTypeDefaultEntry): FileTypeDefaultEntry {
  return {
    preprocess_profile: row.preprocess_profile,
    parser_adapter_backend: row.parser_adapter_backend,
    chunking_strategy: row.chunking_strategy,
  };
}

export function draftFromSettings(settings: FileTypeDefaultsSettingsData): FileTypeDraft {
  return Object.fromEntries(
    settings.rows.map((row) => [row.file_type, entryOf(row)])
  ) as FileTypeDraft;
}

export function draftsEqual(left: FileTypeDraft, right: FileTypeDraft): boolean {
  return (Object.keys(left) as FileTypeName[]).every(
    (name) => JSON.stringify(left[name]) === JSON.stringify(right[name])
  );
}

/** 組み込みの既定の値（「既定に戻す」で下書きに入る。保存までは確定しない）。 */
export function draftFromBuiltin(settings: FileTypeDefaultsSettingsData): FileTypeDraft {
  return Object.fromEntries(
    settings.rows.map((row) => [row.file_type, entryOf(row.builtin)])
  ) as FileTypeDraft;
}

function sameEntry(left: FileTypeDefaultEntry, right: FileTypeDefaultEntry): boolean {
  return JSON.stringify(entryOf(left)) === JSON.stringify(entryOf(right));
}

/** 下書きの行が組み込みの既定と同じか（この環境で使えない行は「全体の既定に従う」と同じ）。 */
export function followsBuiltin(row: FileTypeDefaultRow, entry: FileTypeDefaultEntry): boolean {
  return sameEntry(row.builtin, entry);
}

/**
 * 保存する行。組み込みの既定と同じ行は送らない（保存値が無ければ組み込みの既定を使うため）。
 * 「全体の既定に従う」を選んだ行は、すべて null の行として保存する。
 */
export function updatePayload(
  draft: FileTypeDraft,
  rows: readonly FileTypeDefaultRow[]
): FileTypeDefaultsUpdate {
  const entries: FileTypeDefaultsUpdate["entries"] = {};
  for (const row of rows) {
    if (!followsBuiltin(row, draft[row.file_type])) {
      entries[row.file_type] = draft[row.file_type];
    }
  }
  return { entries };
}

/** 行のファイル準備に対応する解析エンジン（未設定は全体の既定のファイル準備で判定する）。 */
export function parserOptionsFor(
  row: FileTypeDefaultRow,
  preprocess: PreprocessProfileName | null
): ParserAdapterBackend[] {
  return row.parser_options[preprocess ?? row.inherited_preprocess_profile] ?? [];
}

/** 保存済みの値が今の選択肢に無い（サービスの停止など）ときも、表示から消さない。 */
export function withCurrentValue<T extends string>(options: readonly T[], current: T | null): T[] {
  return current !== null && !options.includes(current) ? [...options, current] : [...options];
}

export function isStructuredRecordsProfile(profile: PreprocessProfileName | null): boolean {
  return (STRUCTURED_RECORDS_PREPROCESS_PROFILES as readonly string[]).includes(profile ?? "");
}

/**
 * ファイル準備を変えたときの行。構造化レコードの JSONL を作る準備は解析エンジン「構造化レコード JSONL」が必須なので
 * 入れ、分割が未設定なら固定分割にする（レシピの画面と同じ）。それ以外は、選べなくなった解析エンジンを外す。
 */
export function entryWithPreprocess(
  row: FileTypeDefaultRow,
  entry: FileTypeDefaultEntry,
  preprocess: PreprocessProfileName | null
): FileTypeDefaultEntry {
  const next = { ...entry, preprocess_profile: preprocess };
  if (isStructuredRecordsProfile(preprocess)) {
    return {
      ...next,
      parser_adapter_backend: STRUCTURED_RECORDS_BACKEND,
      chunking_strategy: entry.chunking_strategy ?? "fixed_delimiter",
    };
  }
  const options = parserOptionsFor(row, preprocess);
  const parser = entry.parser_adapter_backend;
  return { ...next, parser_adapter_backend: parser && options.includes(parser) ? parser : null };
}
