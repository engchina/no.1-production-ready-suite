import { Disclosure, StatusBadge } from "@production-ready/ui";
import { t } from "@/lib/i18n";
import { DefinitionFields } from "./ontologyResultPresentation";
import {
  conceptDisplayNames,
  summarizeOntologyGraph,
  visibleDifferences,
  type PublicationDefinition,
  type PublicationDifference,
} from "./publicationSummary";

/**
 * 公開前の確認で、graph として何ができたか（オブジェクト・関係・プロパティ）を先に示し、
 * 概念ごとの差分は畳む（#1720。内部の概念・同じ名前のカードを縦に並べない）。
 */
export function PublicationGraphSummary({ definitions, differences }: {
  definitions: PublicationDefinition[];
  differences: PublicationDifference[];
}) {
  const summary = summarizeOntologyGraph(definitions);
  const names = conceptDisplayNames([
    ...definitions,
    ...differences.flatMap(difference => [difference.before, difference.after]).filter((value): value is PublicationDefinition => Boolean(value)),
  ]);
  const shown = visibleDifferences(differences);
  const counts = [
    { key: "objects", label: t("markdownOntology.graph.objects"), value: summary.objectCount },
    { key: "links", label: t("markdownOntology.graph.links"), value: summary.linkCount },
    { key: "properties", label: t("markdownOntology.graph.properties"), value: summary.propertyCount },
    ...(summary.otherCount ? [{ key: "others", label: t("markdownOntology.graph.others"), value: summary.otherCount }] : []),
  ];
  return <div className="grid min-w-0 gap-3">
    <section className="grid min-w-0 gap-3 rounded-md border border-border bg-surface p-4" aria-label={t("markdownOntology.graph.title")} data-testid="ontology-publication-graph">
      <h4 className="text-sm font-semibold">{t("markdownOntology.graph.title")}</h4>
      <dl className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-4">
        {counts.map(count => <div key={count.key} className="grid min-w-0 gap-1">
          <dt className="text-xs text-fg-muted">{count.label}</dt>
          <dd className="tnum text-lg font-semibold">{count.value}</dd>
        </div>)}
      </dl>
      {summary.objectCount > 0 && summary.linkCount === 0 && <p className="text-xs leading-relaxed text-fg-muted">{t("markdownOntology.graph.noLinks")}</p>}
      {summary.objects.length > 0 && <ul className="grid min-w-0 gap-2" aria-label={t("markdownOntology.graph.objectList")}>
        {summary.objects.map(object => <li key={object.apiName} className="min-w-0">
          <Disclosure variant="plain" size="sm"
            summary={<span className="grid min-w-0 gap-0.5">
              <span className="break-words">{object.label}</span>
              {object.physical && <span className="break-all font-mono text-xs font-normal text-fg-muted">{object.physical}</span>}
              <span className="tnum text-xs font-normal text-fg-muted">{t("markdownOntology.graph.objectCounts", { properties: object.properties.length, links: object.links.length })}</span>
            </span>}>
            <dl className="grid min-w-0 gap-2 text-sm">
              <div className="grid min-w-0 gap-1">
                <dt className="text-xs text-fg-muted">{t("markdownOntology.graph.properties")}</dt>
                <dd className="break-words">{object.properties.join("、") || t("markdownOntology.noConcept")}</dd>
              </div>
              <div className="grid min-w-0 gap-1">
                <dt className="text-xs text-fg-muted">{t("markdownOntology.graph.links")}</dt>
                <dd className="break-words">{object.links.length ? <ul className="grid gap-1">{object.links.map(link => <li key={link}>{link}</li>)}</ul> : t("markdownOntology.noConcept")}</dd>
              </div>
            </dl>
          </Disclosure>
        </li>)}
      </ul>}
    </section>
    {shown.length > 0 && <Disclosure data-testid="ontology-publication-differences"
      summary={t("markdownOntology.differences")}
      meta={<span className="tnum text-xs text-fg-muted">{t("markdownOntology.occurrences", { count: shown.length })}</span>}
      summaryClassName="px-4 py-3 font-medium" contentClassName="grid gap-3 p-4">
      {shown.map(difference => {
        const definition = difference.after ?? difference.before;
        const apiName = String(definition?.api_name ?? "");
        return <Disclosure key={difference.id} data-testid="ontology-publication-difference"
          summary={names.get(apiName) ?? String(definition?.name_ja ?? difference.id)}
          meta={<StatusBadge variant={difference.before ? difference.after ? "info" : "danger" : "success"} label={t(difference.before ? difference.after ? "markdownOntology.changed" : "markdownOntology.removed" : "markdownOntology.added")} />}
          summaryClassName="px-4 py-3 font-medium" contentClassName="grid gap-4 p-4 md:grid-cols-2">
          <div className="min-w-0 space-y-3"><h5 className="text-sm font-semibold text-fg-muted">{t("markdownOntology.before")}</h5><DefinitionFields definition={difference.before ?? {}} /></div>
          <div className="min-w-0 space-y-3"><h5 className="text-sm font-semibold text-fg-muted">{t("markdownOntology.after")}</h5><DefinitionFields definition={difference.after ?? {}} /></div>
        </Disclosure>;
      })}
    </Disclosure>}
  </div>;
}
