import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const profilePage = readFileSync(
  new URL("../src/features/nl2sql/pages/ProfileManagementPage.tsx", import.meta.url),
  "utf8",
);
const ontologyPage = readFileSync(
  new URL("../src/features/nl2sql/pages/OntologyBuildPage.tsx", import.meta.url),
  "utf8",
);
const ontologyBuildSection = readFileSync(
  new URL("../src/features/nl2sql/ontology/OntologyBuildSection.tsx", import.meta.url),
  "utf8",
);
const ontologyMermaidPanel = readFileSync(
  new URL("../src/features/nl2sql/ontology/OntologyMermaidPanel.tsx", import.meta.url),
  "utf8",
);
const messages = readFileSync(new URL("../src/lib/i18n.ts", import.meta.url), "utf8");

test("profile save queues Oracle sync without touching Ontology or detail refresh", () => {
  assert.match(profilePage, /\/oracle-sync-jobs/u);
  assert.match(profilePage, /"Idempotency-Key"/u);
  assert.match(profilePage, /BUSINESS_SELECT_AI_DB_PROFILES_URL/u);
  assert.doesNotMatch(profilePage, /BUSINESS_SELECT_AI_DB_PROFILES_DETAIL_URL/u);
  assert.doesNotMatch(profilePage, /\/ontology-view/u);
  assert.doesNotMatch(profilePage, /\/select-ai-profile/u);
  assert.doesNotMatch(profilePage, /select-ai-agent\/assets\/refresh/u);
});

test("profile Oracle sync exposes progress, failure recovery and retry", () => {
  assert.match(profilePage, /refetchInterval/u);
  assert.match(profilePage, /aria-live="polite"/u);
  assert.match(profilePage, /\/oracle-sync-jobs\/\$\{oracleSyncJob\.job_id\}\/retry/u);
  assert.match(messages, /業務 Profile は保存されましたが、Oracle 反映に失敗しました/u);
  assert.doesNotMatch(profilePage, /function OracleMutationResult/u);
  assert.doesNotMatch(profilePage, /profile-oracle-result/u);
  assert.doesNotMatch(messages, /Oracle Profile 反映結果/u);
});

test("Ontology page uses AI build as the only user-facing build action", () => {
  assert.doesNotMatch(ontologyPage, /\/ontology-view\/materialize/u);
  assert.doesNotMatch(ontologyPage, /ontologyViewStale/u);
  assert.doesNotMatch(ontologyPage, /data-testid="ontology-view-lifecycle"/u);
  assert.match(ontologyPage, /OntologyBuildSection/u);
  assert.match(ontologyBuildSection, /ontology-build-markdown/u);
});

test("Ontology publish refreshes the graph and refresh-tokened Mermaid export", () => {
  assert.match(ontologyPage, /const \[mermaidRefreshToken, setMermaidRefreshToken\] = useState\(0\)/u);
  assert.match(ontologyPage, /await refreshOntologyView\(\);\s*setMermaidRefreshToken\(\(token\) => token \+ 1\)/u);
  assert.match(ontologyPage, /onPublished=\{handleOntologyPublished\}/u);
  assert.match(ontologyPage, /graphRevisionId=\{ontologyGraphRevisionId\}/u);
  assert.match(ontologyPage, /refreshToken=\{mermaidRefreshToken\}/u);
  assert.match(ontologyBuildSection, /onPublished\?: \(\) => void \| Promise<void>/u);
  assert.match(ontologyBuildSection, /void onPublished\?\.\(\)/u);
});

test("Mermaid export panel displays revision identity and mismatch recovery", () => {
  assert.match(ontologyMermaidPanel, /graphRevisionId\?: string/u);
  assert.match(ontologyMermaidPanel, /refreshToken\?: number/u);
  assert.match(ontologyMermaidPanel, /handledRefreshTokenRef/u);
  assert.match(ontologyMermaidPanel, /setMermaidRevisionId\(data\.ontology_revision_id\)/u);
  assert.match(ontologyMermaidPanel, /ontology-graph-revision-id/u);
  assert.match(ontologyMermaidPanel, /ontology-mermaid-revision-id/u);
  assert.match(ontologyMermaidPanel, /ontology-mermaid-revision-mismatch/u);
  assert.match(messages, /SQL 生成用 Mermaid ER 技術表現/u);
  assert.match(messages, /編集用・標準図示ではありません/u);
});

test("Mermaid export appears before grounding graph and supports code/rendered tabs", () => {
  assert.match(ontologyPage, /<OntologyMermaidPanel[\s\S]*?<OntologyQueryPlayground/u);
  assert.match(ontologyMermaidPanel, /ManagementTabs/u);
  assert.match(ontologyMermaidPanel, /type MermaidPanelTab = "code" \| "graph"/u);
  assert.match(ontologyMermaidPanel, /await import\("mermaid"\)/u);
  assert.match(ontologyMermaidPanel, /mermaidApi\.render/u);
  assert.match(ontologyMermaidPanel, /ontology-mermaid-rendered-graph/u);
  assert.match(ontologyMermaidPanel, /ontology-mermaid-graph-controls/u);
  assert.match(ontologyMermaidPanel, /ontology-mermaid-graph-zoom-in/u);
  assert.match(ontologyMermaidPanel, /ontology-mermaid-graph-zoom-out/u);
  assert.match(ontologyMermaidPanel, /ontology-mermaid-graph-fit/u);
  assert.match(ontologyMermaidPanel, /data-transform-scale/u);
  assert.match(ontologyMermaidPanel, /data-transform-ready/u);
  assert.match(ontologyMermaidPanel, /data-content-bounds-width/u);
  assert.match(ontologyMermaidPanel, /data-content-bounds-height/u);
  assert.match(ontologyMermaidPanel, /data-content-bounds-source/u);
  assert.match(ontologyMermaidPanel, /MERMAID_GRAPH_MAX_SCALE = 40/u);
  assert.match(ontologyMermaidPanel, /MERMAID_GRAPH_FIT_MAX_SCALE = 40/u);
  assert.match(ontologyMermaidPanel, /MERMAID_GRAPH_FIT_PADDING = 8/u);
  assert.match(ontologyMermaidPanel, /measureMermaidSvgContent/u);
  assert.match(ontologyMermaidPanel, /getCTM\(\)/u);
  assert.match(ontologyMermaidPanel, /svg\.getBBox\(\)/u);
  assert.match(ontologyMermaidPanel, /onPointerDown=\{handlePointerDown\}/u);
  assert.match(ontologyMermaidPanel, /onClick=\{fitGraph\}/u);
  assert.match(messages, /Mermaid ER 表現を表示/u);
  assert.match(messages, /Mermaid ER コードの描画結果/u);
  assert.match(messages, /グラフを拡大/u);
  assert.match(messages, /グラフを縮小/u);
  assert.match(messages, /グラフ全体を表示/u);
});

test("all profiles share the physical delete flow and retained-state confirmation", () => {
  assert.match(
    messages,
    /そのすべてのオントロジー範囲設定、Oracle DBMS_CLOUD_AI Profile、Select AI Agent 関連アセットを完全に削除/u
  );
  assert.match(messages, /監査履歴は削除されません/u);
  assert.doesNotMatch(profilePage, /profile\.id !== "default"/u);
  assert.doesNotMatch(profilePage, /if \(profile\.id === "default"\) return;/u);
  assert.match(profilePage, /\/api\/nl2sql\/profiles\/\$\{encodeURIComponent\(profile\.id\)\}/u);
});
