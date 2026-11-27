import { useEffect, useState } from "react";
import { Network } from "lucide-react";
import { Disclosure } from "@production-ready/ui";
import { WikiWorkspace } from "@/components/wiki-search/WikiSearchClient";
import { t } from "@/lib/i18n";

export function KnowledgeBaseGraphView({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const [open, setOpen] = useState(() => window.location.hash === "#wiki-graph");
  useEffect(() => {
    if (window.location.hash === "#wiki-graph") document.getElementById("wiki-graph")?.scrollIntoView({ block: "start" });
  }, [knowledgeBaseId]);
  return (
    <Disclosure
      surface="sunken"
      summary={t("wiki.graph.title")}
      description={t("wiki.graph.hint")}
      icon={Network}
      open={open}
      onOpenChange={setOpen}
      data-testid="wiki-graph-disclosure"
    >
      {open && (
        <div id="wiki-graph">
          <WikiWorkspace key={knowledgeBaseId} knowledgeBaseId={knowledgeBaseId} />
        </div>
      )}
    </Disclosure>
  );
}
