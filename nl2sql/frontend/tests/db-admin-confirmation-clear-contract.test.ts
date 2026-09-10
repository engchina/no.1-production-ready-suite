import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dbAdminShared = readFileSync(
  new URL("../src/features/nl2sql/components/DbAdminShared.tsx", import.meta.url),
  "utf8",
);
const tableManagementPage = readFileSync(
  new URL("../src/features/nl2sql/pages/TableManagementPage.tsx", import.meta.url),
  "utf8",
);
const dataManagementPage = readFileSync(
  new URL("../src/features/nl2sql/pages/DataManagementPage.tsx", import.meta.url),
  "utf8",
);
const profileManagementPage = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8",
);
const clearActionButton = readFileSync(
  new URL("../src/components/ui/clear-action-button.tsx", import.meta.url),
  "utf8",
);
const fileDropzone = readFileSync(
  new URL("../src/components/ui/file-dropzone.tsx", import.meta.url),
  "utf8",
);

function section(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `${start} が見つかりません`);
  assert.notEqual(endIndex, -1, `${end} が見つかりません`);
  return source.slice(startIndex, endIndex);
}

test("DDL/comment/annotation runners expose a shared clear action that resets guarded execution state", () => {
  const runner = section(
    dbAdminShared,
    "export function StatementRunnerCard",
    "/** 検索フィルタ付き",
  );

  assert.match(dbAdminShared, /import \{ ClearActionButton \}/u);
  assert.match(runner, /const canClearRunner = Boolean\(sql \|\| confirmation \|\| result \|\| message \|\| executionRun\)/u);
  assert.match(
    runner,
    /const clearRunner = \(\) => \{[\s\S]*setSql\(""\);[\s\S]*setConfirmation\(""\);[\s\S]*setResult\(null\);[\s\S]*setMessage\(""\);[\s\S]*setExecutionRun\(null\);[\s\S]*setSqlFileResetSignal/u,
  );
  assert.match(
    runner,
    /<ClearActionButton[\s\S]*label=\{t\("workspace\.clearInput"\)\}[\s\S]*matchButtonHeight[\s\S]*disabled=\{!canClearRunner \|\| loading\}[\s\S]*onClick=\{clearRunner\}/u,
  );
});

test("new guarded clear actions can opt in to same-row button height", () => {
  assert.match(clearActionButton, /matchButtonHeight\?: boolean/u);
  assert.match(clearActionButton, /matchButtonHeight = false/u);
  assert.match(clearActionButton, /size = "sm"/u);
  assert.match(clearActionButton, /!matchButtonHeight && "h-\[44px\]"/u);
});

test("table import wizard clear action resets import form, result, and dropzone validation state", () => {
  const importWizard = section(tableManagementPage, "function ImportWizard", "function schemaRefreshRequiresFull");

  assert.match(tableManagementPage, /import \{ ClearActionButton \}/u);
  assert.match(importWizard, /resetSignal=\{fileResetSignal\}/u);
  assert.match(
    importWizard,
    /<ClearActionButton[\s\S]*label=\{t\("dbAdmin\.runner\.clear"\)\}[\s\S]*matchButtonHeight[\s\S]*disabled=\{!canClear \|\| loading\}[\s\S]*onClick=\{onClear\}/u,
  );
  assert.match(
    tableManagementPage,
    /const canClearImportWizard = Boolean\([\s\S]*importTable[\s\S]*importSheet[\s\S]*importFilename[\s\S]*importBase64[\s\S]*importConfirmation[\s\S]*importResult[\s\S]*importError[\s\S]*importSchemaRefreshNeedsFull/u,
  );
  assert.match(
    tableManagementPage,
    /const clearImportWizard = \(\) => \{[\s\S]*setImportTable\(""\);[\s\S]*setImportSheet\(""\);[\s\S]*setImportConfirmation\(""\);[\s\S]*clearImportFile\(\);[\s\S]*\};/u,
  );
});

test("data management CSV and synthetic guarded actions expose clear buttons", () => {
  const csvWorkspace = section(dataManagementPage, "function CsvUploadWorkspace", "function SyntheticWorkspace");
  const syntheticWorkspace = section(dataManagementPage, "function SyntheticWorkspace", "function DbProfileRefreshNotice");

  assert.match(dataManagementPage, /import \{ ClearActionButton \}/u);
  assert.match(csvWorkspace, /resetSignal=\{fileResetSignal\}/u);
  assert.match(
    csvWorkspace,
    /<ClearActionButton[\s\S]*label=\{t\("dbAdmin\.runner\.clear"\)\}[\s\S]*matchButtonHeight[\s\S]*disabled=\{!canClearUpload \|\| loading\}[\s\S]*onClick=\{onClearUpload\}/u,
  );
  assert.match(
    dataManagementPage,
    /const clearCsvUpload = \(\) => \{[\s\S]*clearCsvFile\(\);[\s\S]*setCsvMode\("insert"\);[\s\S]*setCsvConfirmation\(""\);[\s\S]*setCsvUploadError\(""\);[\s\S]*\};/u,
  );
  assert.match(dataManagementPage, /const canClearSyntheticGeneration = Boolean\([\s\S]*syntheticSelectedTables[\s\S]*syntheticConfirmation[\s\S]*syntheticResultLimitInput/u);
  assert.match(
    syntheticWorkspace,
    /<ClearActionButton[\s\S]*label=\{t\("dbAdmin\.runner\.clear"\)\}[\s\S]*matchButtonHeight[\s\S]*!canClearSyntheticGeneration[\s\S]*onClick=\{onClearSyntheticGeneration\}/u,
  );
  assert.match(
    dataManagementPage,
    /const clearSyntheticGeneration = \(\) => \{[\s\S]*setSyntheticSelectedTables\(\[\]\);[\s\S]*setSyntheticPrompt\(""\);[\s\S]*setSyntheticConfirmation\(""\);[\s\S]*setSyntheticRows\(1\);[\s\S]*clearSyntheticResultState\(\{ resetLimit: true \}\);/u,
  );
});

test("business profile clear action resets only the Oracle execution gate and job state", () => {
  const editor = section(profileManagementPage, "function ProfileEditor", "function ProfileSaveResultRegion");
  const clearOracleExecution = section(
    profileManagementPage,
    "const clearOracleExecution = () => {",
    "  const editor = (",
  );

  assert.match(profileManagementPage, /import \{ ClearActionButton \}/u);
  assert.match(editor, /canClearOracleExecution: boolean/u);
  assert.match(editor, /onOracleExecutionClear: \(\) => void/u);
  assert.match(
    editor,
    /<ClearActionButton[\s\S]*label=\{t\("dbAdmin\.runner\.clear"\)\}[\s\S]*matchButtonHeight[\s\S]*size="md"[\s\S]*disabled=\{!canClearOracleExecution \|\| saving\}[\s\S]*onClick=\{onOracleExecutionClear\}/u,
  );
  assert.match(profileManagementPage, /const canClearOracleExecution = Boolean\([\s\S]*oracleConfirmation[\s\S]*syncJobParam/u);
  assert.match(
    profileManagementPage,
    /const clearOracleExecution = \(\) => \{[\s\S]*setOracleConfirmation\(""\);[\s\S]*setRebuildAgentAssets\(false\);[\s\S]*setOracleSyncJobId\(""\);[\s\S]*nextParams\.delete\("syncJobId"\);/u,
  );
  assert.doesNotMatch(clearOracleExecution, /setProfileName\(""\)/u);
});

test("file dropzone reset signal clears local validation errors for external clear actions", () => {
  assert.match(fileDropzone, /resetSignal\?: string \| number/u);
  assert.match(fileDropzone, /resetSignal = 0/u);
  assert.match(
    fileDropzone,
    /useEffect\(\(\) => \{[\s\S]*dragDepthRef\.current = 0;[\s\S]*setIsDragActive\(false\);[\s\S]*setValidationError\(""\);[\s\S]*\}, \[resetSignal\]\);/u,
  );
});
