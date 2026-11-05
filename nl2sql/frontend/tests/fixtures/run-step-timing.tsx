import { AppShell, PageBody, PageHeader, Section, RunTimeline, type RunTimelineStep } from "@production-ready/ui";
import { createRoot } from "react-dom/client";
import timing from "../../../../platform/packages/ui/tests/fixtures/run-step-timing.json";
import "../../src/globals.css";

createRoot(document.getElementById("root")!).render(<AppShell sidebar={null}>
  <PageHeader wide title="処理の経過" />
  <PageBody wide><Section title="同じ記録の表示"><RunTimeline steps={timing as RunTimelineStep[]} /></Section></PageBody>
</AppShell>);
