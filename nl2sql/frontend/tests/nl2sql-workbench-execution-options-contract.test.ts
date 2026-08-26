import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

const workbenchSource = readFileSync(
  new URL("../src/features/nl2sql/Nl2SqlWorkbench.tsx", import.meta.url),
  "utf8"
);
const optionsPanelSource = readFileSync(
  new URL(
    "../src/features/nl2sql/components/Nl2SqlExecutionOptionsPanel.tsx",
    import.meta.url
  ),
  "utf8"
);
const generatedSqlPanelSource = readFileSync(
  new URL("../src/features/nl2sql/components/GeneratedSqlPanel.tsx", import.meta.url),
  "utf8"
);

test("NL2SQL workbench keeps one execution action and removes preview/session buttons", () => {
  assert.equal(workbenchSource.includes('t("nl2sql.action.preview")'), false);
  assert.equal(workbenchSource.includes('t("nl2sql.session.create")'), false);
  assert.equal(workbenchSource.includes('"/api/nl2sql/preview"'), false);
  assert.equal(workbenchSource.includes("previewToJob"), false);
  assert.equal(workbenchSource.includes("QueryOntologyFlow"), false);
  assert.equal(workbenchSource.includes('t("nl2sql.history.count"'), false);
  assert.match(workbenchSource, /<span>\{t\("nl2sql\.action\.run"\)\}<\/span>/);
});

test("execution options default to showing interpretation and show prompt artifacts", () => {
  assert.match(
    workbenchSource,
    /const \[useOntologyContext, setUseOntologyContext\] = useState\(true\);/
  );
  assert.match(
    workbenchSource,
    /const \[includeInterpretation, setIncludeInterpretation\] = useState\(true\);/
  );
  assert.match(
    workbenchSource,
    /const \[includeShowPrompt, setIncludeShowPrompt\] = useState\(true\);/
  );
  assert.match(
    workbenchSource,
    /const \[executionOptionsOpen, setExecutionOptionsOpen\] = useState\(false\);/
  );
  assert.match(workbenchSource, /use_ontology_context: useOntologyContext/);
  assert.match(workbenchSource, /include_interpretation: includeInterpretation/);
  assert.match(workbenchSource, /include_show_prompt: includeShowPrompt/);
});

test("execution options panel keeps Query Rewrite checkboxes and Select AI showprompt scope", () => {
  assert.match(optionsPanelSource, /nl2sql\.rewrite\.useGlossary/);
  assert.match(optionsPanelSource, /nl2sql\.rewrite\.useSchema/);
  assert.match(optionsPanelSource, /nl2sql\.executionOptions\.useOntology/);
  assert.match(optionsPanelSource, /nl2sql\.executionOptions\.includeInterpretation/);
  assert.match(optionsPanelSource, /nl2sql\.executionOptions\.includeShowPrompt/);
  assert.match(optionsPanelSource, /aria-expanded=\{open\}/);
  assert.match(optionsPanelSource, /aria-controls="nl2sql-execution-options-body"/);
  assert.match(optionsPanelSource, /engine !== "select_ai"/);
});

test("generated SQL summary renders ontology grounding and show prompt artifact panels", () => {
  assert.match(generatedSqlPanelSource, /data-testid="nl2sql-interpretation-panel"/);
  assert.match(generatedSqlPanelSource, /data-testid="nl2sql-sql-grounding-panel"/);
  assert.doesNotMatch(generatedSqlPanelSource, /useProfileOntologyView/);
  assert.match(generatedSqlPanelSource, /artifact\.ontology_graph/);
  assert.doesNotMatch(generatedSqlPanelSource, /nl2sql\.interpretation\.inputTitle/);
  assert.doesNotMatch(generatedSqlPanelSource, /nl2sql\.interpretation\.sqlTitle/);
  assert.match(generatedSqlPanelSource, /data-testid="nl2sql-show-prompt-panel"/);
  assert.match(generatedSqlPanelSource, /artifact\.prompt/);
  assert.match(generatedSqlPanelSource, /whitespace-pre-wrap/);
});
