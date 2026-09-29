import type { IngestionJobPhase } from "@/lib/api";
import type { I18nKey } from "@/lib/i18n";

/**
 * 「選択中レシピの設定」の項目と並び順の正本（#523）。
 *
 * 上の要約（現在値のカード）と下の上書きの一覧（グローバルを継承 / 上書き）は、どちらもこの
 * 配列だけから作る。2 つの表示で項目や順番を別々に書かない。
 *
 * 並びは取込の実際の処理順（`rag/backend/app/rag/ingestion.py` と
 * `rag/backend/app/api/routes/documents.py` の自動進行）にそろえる。
 * - PREPROCESS: ファイル準備 → 「ファイル準備後に抽出へ進む」のゲート
 *   （`ingest` が preprocess の artifact を保存した直後に `rag_auto_parse_after_preprocess_enabled` を見て止まる）
 * - EXTRACT: 文書解析 → Vision（`_attach_vision`）→ 項目抽出（`_attach_extraction_fields`）
 *   → 章節木（`_attach_navigation_tree`）→ 「抽出後に Chunk 作成へ進む」のゲート（REVIEW で止まり、
 *   `rag_auto_chunk_after_extract_enabled` なら CHUNK の job を積む）
 * - CHUNK: 文書分割 → 文脈ヘッダ（`_chunks_with_context_headers` は分割の直後に chunk metadata へ付ける）
 *   → 「Chunk 後に Embedding / 索引へ進む」のゲート（CHUNKED で止まり、
 *   `rag_auto_index_after_chunk_enabled` なら INDEX の job を積む）
 * - INDEX: embedding と索引の保存の後に関係情報（`_save_index` / `_save_embeddings_for_chunk_set`
 *   の `graph_indexing`）を作る
 */

type SelectConfigField =
  | "preprocess_profile"
  | "parser_adapter_backend"
  | "chunking_strategy"
  | "graph_profile";

type BooleanConfigField =
  | "auto_parse_after_preprocess_enabled"
  | "vision_enabled"
  | "field_extraction_enabled"
  | "navigation_summary_enabled"
  | "auto_chunk_after_extract_enabled"
  | "chunk_context_header_enabled"
  | "auto_index_after_chunk_enabled";

export type RecipeConfigItem =
  | { field: SelectConfigField; kind: "select"; label: I18nKey; phase: IngestionJobPhase }
  | { field: BooleanConfigField; kind: "boolean"; label: I18nKey; phase: IngestionJobPhase };

export type RecipeConfigField = RecipeConfigItem["field"];

export const RECIPE_CONFIG_ITEMS = [
  {
    field: "preprocess_profile",
    kind: "select",
    label: "knowledgeBases.adapter.field.preprocessProfile",
    phase: "PREPROCESS",
  },
  {
    field: "auto_parse_after_preprocess_enabled",
    kind: "boolean",
    label: "knowledgeBases.adapter.field.autoParseAfterPreprocess",
    phase: "PREPROCESS",
  },
  {
    field: "parser_adapter_backend",
    kind: "select",
    label: "knowledgeBases.adapter.field.parserBackend",
    phase: "EXTRACT",
  },
  {
    field: "vision_enabled",
    kind: "boolean",
    label: "knowledgeBases.adapter.field.vision",
    phase: "EXTRACT",
  },
  {
    field: "field_extraction_enabled",
    kind: "boolean",
    label: "knowledgeBases.adapter.field.fieldExtraction",
    phase: "EXTRACT",
  },
  {
    field: "navigation_summary_enabled",
    kind: "boolean",
    label: "knowledgeBases.adapter.field.navigationSummary",
    phase: "EXTRACT",
  },
  {
    field: "auto_chunk_after_extract_enabled",
    kind: "boolean",
    label: "knowledgeBases.adapter.field.autoChunkAfterExtract",
    phase: "EXTRACT",
  },
  {
    field: "chunking_strategy",
    kind: "select",
    label: "knowledgeBases.adapter.field.chunkingStrategy",
    phase: "CHUNK",
  },
  {
    field: "chunk_context_header_enabled",
    kind: "boolean",
    label: "documents.processingConfig.contextHeader",
    phase: "CHUNK",
  },
  {
    field: "auto_index_after_chunk_enabled",
    kind: "boolean",
    label: "knowledgeBases.adapter.field.autoIndexAfterChunk",
    phase: "CHUNK",
  },
  {
    field: "graph_profile",
    kind: "select",
    label: "knowledgeBases.adapter.field.graphProfile",
    phase: "INDEX",
  },
] as const satisfies readonly RecipeConfigItem[];

/** 上書きの件数（n / 全体）を数える対象。項目の正本と同じ集合。 */
export const RECIPE_CONFIG_FIELDS: RecipeConfigField[] = RECIPE_CONFIG_ITEMS.map(
  (item) => item.field
);

/** 工程の区切りの見出し。上のレシピの工程表示（DocumentRecipeManager の PHASES）と同じ文言。 */
const PHASE_LABELS: Record<IngestionJobPhase, I18nKey> = {
  PREPROCESS: "flow.step.preprocess",
  EXTRACT: "flow.step.extract",
  CHUNK: "flow.step.chunk",
  INDEX: "flow.step.indexing",
};

const PHASE_ORDER: IngestionJobPhase[] = ["PREPROCESS", "EXTRACT", "CHUNK", "INDEX"];

export interface RecipeConfigGroup {
  phase: IngestionJobPhase;
  /** 1 始まりの工程番号（見出しの「1. ファイル準備」） */
  index: number;
  label: I18nKey;
  items: RecipeConfigItem[];
}

/** 項目を工程ごとに分ける。工程の順と、工程の中の項目の順は RECIPE_CONFIG_ITEMS のまま。 */
export function recipeConfigGroups(
  items: readonly RecipeConfigItem[] = RECIPE_CONFIG_ITEMS
): RecipeConfigGroup[] {
  return PHASE_ORDER.flatMap((phase, position) => {
    const phaseItems = items.filter((item) => item.phase === phase);
    if (!phaseItems.length) return [];
    return [{ phase, index: position + 1, label: PHASE_LABELS[phase], items: phaseItems }];
  });
}
