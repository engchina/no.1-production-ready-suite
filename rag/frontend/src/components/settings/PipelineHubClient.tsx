"use client";

import { Link } from "react-router-dom";

import { NAV_SECTIONS, type NavItem } from "@/components/layout/nav-config";
import { APP_ROUTES } from "@/lib/routes";
import { ja, t, type I18nKey } from "@/lib/i18n";

// 「ナレッジ構築(取込)」工程の href。これ以外の検索・回答設定工程は「検索・回答」に分類する。
const INGESTION_HREFS = new Set<string>([
  APP_ROUTES.settingsPreprocess,
  APP_ROUTES.settingsParserAdapters,
  APP_ROUTES.settingsChunking,
  APP_ROUTES.settingsVectorIndex,
  APP_ROUTES.settingsGraph,
]);

/** nav ラベルキー(nav.settingsX)から説明キー(settings.x.subtitle)を導く。 */
function subtitleKeyOf(labelKey: string): I18nKey {
  const raw = labelKey.replace(/^nav\.settings/, "");
  const camel = raw.charAt(0).toLowerCase() + raw.slice(1);
  return `settings.${camel}.subtitle` as I18nKey;
}

function stageDescription(item: NavItem): string {
  const key = subtitleKeyOf(item.labelKey);
  return key in ja ? t(key) : "";
}

/**
 * 検索・回答設定の俯瞰ハブ。サイドバーの「検索・回答設定」セクション(処理順)を
 * ナレッジ構築 / 検索・回答 の 2 フェーズに分け、各工程へのカード導線を 1 画面で提供する。
 * セクション項目を動的に読むため、工程の増減に追従して drift しない。
 */
export function PipelineHubClient() {
  const section = NAV_SECTIONS.find((item) => item.titleKey === "nav.section.pipeline");
  const stages = (section?.items ?? []).filter((item) => item.href !== APP_ROUTES.settingsPipeline);
  const ingestion = stages.filter((item) => INGESTION_HREFS.has(item.href));
  const query = stages.filter((item) => !INGESTION_HREFS.has(item.href));

  return (
    <div className="space-y-6 p-8">
      <PhaseGroup
        title={t("settings.pipeline.phase.ingestion")}
        hint={t("settings.pipeline.phase.ingestionHint")}
        stages={ingestion}
        startIndex={1}
      />
      <PhaseGroup
        title={t("settings.pipeline.phase.query")}
        hint={t("settings.pipeline.phase.queryHint")}
        stages={query}
        startIndex={ingestion.length + 1}
      />
    </div>
  );
}

function PhaseGroup({
  title,
  hint,
  stages,
  startIndex,
}: {
  title: string;
  hint: string;
  stages: NavItem[];
  startIndex: number;
}) {
  if (stages.length === 0) return null;
  return (
    <section className="space-y-3" aria-label={title}>
      <div>
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <p className="mt-0.5 text-xs text-muted">{hint}</p>
      </div>
      <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {stages.map((item, index) => (
          <li key={item.href}>
            <StageCard item={item} step={startIndex + index} />
          </li>
        ))}
      </ol>
    </section>
  );
}

function StageCard({ item, step }: { item: NavItem; step: number }) {
  const Icon = item.icon;
  const name = t(item.labelKey);
  return (
    <Link
      to={item.href}
      aria-label={t("settings.pipeline.openStage", { name })}
      className="flex h-full gap-3 rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
        <Icon size={18} aria-hidden />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-foreground">
          <span className="tnum text-muted">{step}.</span> {name}
        </span>
        <span className="mt-0.5 block text-xs leading-relaxed text-muted">
          {stageDescription(item)}
        </span>
      </span>
    </Link>
  );
}
