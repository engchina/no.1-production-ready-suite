import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Banner, EmptyState, PageHeader } from "@engchina/production-ready-ui";

import { apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { OntologyBuildSection } from "../ontology/OntologyBuildSection";
import { ProfileOntologyEditor } from "../ontology/ProfileOntologyEditor";
import {
  EMPTY_PROFILE_ONTOLOGY_DRAFT,
  type ProfileOntologyDraftPayload,
  type ProfileOntologyDraftState,
} from "../ontology/ProfileOntologyEditorCore";
import type { OntologyGraph } from "../ontology/types";
import type { Nl2SqlProfile } from "../types";

/**
 * オントロジー構築（旧: 業務プロファイル編集の末尾セクション）を独立ページ化。
 * 上部でプロファイルを選び（`?profile=` 同期）、AI 構築→承認→公開と物理・業務モデル編集を行う。
 * データ供給（ontology-view 取得 / draft 保存 / スキーマ更新）は旧 ProfileManagementPage から移設。
 */
export function OntologyBuildPage() {
  const [profiles, setProfiles] = useState<Nl2SqlProfile[]>([]);
  const [profilesLoaded, setProfilesLoaded] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const [ontologyDraft, setOntologyDraft] = useState<ProfileOntologyDraftState>({ nodes: {}, edges: {} });
  const [ontologyGraph, setOntologyGraph] = useState<OntologyGraph | null>(null);
  const [ontologyWarnings, setOntologyWarnings] = useState<string[]>([]);
  const [ontologyViewNonce, setOntologyViewNonce] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const activeProfiles = useMemo(() => profiles.filter((profile) => !profile.archived), [profiles]);
  const profileParam = searchParams.get("profile");
  const selectedProfile = useMemo(() => {
    if (activeProfiles.length === 0) return null;
    return activeProfiles.find((profile) => profile.id === profileParam) ?? activeProfiles[0];
  }, [activeProfiles, profileParam]);

  const loadProfiles = async () => {
    setError("");
    try {
      setProfiles(await apiGet<Nl2SqlProfile[]>("/api/nl2sql/profiles"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("profiles.error.load"));
    } finally {
      setProfilesLoaded(true);
    }
  };

  useEffect(() => {
    void loadProfiles();
  }, []);

  useEffect(() => {
    const targetId = selectedProfile?.id ?? null;
    if (!targetId) {
      setOntologyDraft({ nodes: {}, edges: {} });
      setOntologyGraph(null);
      setOntologyWarnings([]);
      return;
    }
    let cancelled = false;
    void apiGet<{
      profile_ontology_view?: {
        table_usages_ja?: Record<string, string>;
        draft_node_overrides?: Array<{
          node_id: string;
          business_name_ja?: string;
          table_usage?: string;
        }>;
        draft_edge_overrides?: Array<{
          edge_id: string;
          cardinality?: ProfileOntologyDraftState["edges"][string]["cardinality"];
          allowed_path?: boolean;
        }>;
      };
      ontology_graph?: OntologyGraph;
      warnings_ja?: string[];
    }>(`/api/nl2sql/profiles/${encodeURIComponent(targetId)}/ontology-view`)
      .then((data) => {
        if (cancelled) return;
        const view = data.profile_ontology_view;
        setOntologyGraph(data.ontology_graph ?? null);
        setOntologyWarnings(data.warnings_ja ?? []);
        setOntologyDraft({
          nodes: Object.fromEntries([
            ...Object.entries(view?.table_usages_ja ?? {}).map(([nodeId, usage]) => [
              nodeId,
              { table_usage: usage },
            ]),
            ...(view?.draft_node_overrides ?? []).map((item) => [
              item.node_id,
              { business_name_ja: item.business_name_ja, table_usage: item.table_usage },
            ]),
          ]),
          edges: Object.fromEntries(
            (view?.draft_edge_overrides ?? []).map((item) => [
              item.edge_id,
              { cardinality: item.cardinality, allowed_path: item.allowed_path },
            ])
          ),
        });
      })
      .catch(() => {
        if (cancelled) return;
        setOntologyGraph(null);
        setOntologyWarnings([]);
        setOntologyDraft({ ...EMPTY_PROFILE_ONTOLOGY_DRAFT, nodes: {}, edges: {} });
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProfile?.id, ontologyViewNonce]);

  const saveOntologyDraft = async (payload: ProfileOntologyDraftPayload) => {
    if (!selectedProfile) throw new Error(t("profiles.empty.hint"));
    const path = `/api/nl2sql/profiles/${encodeURIComponent(selectedProfile.id)}/ontology-view`;
    const current = await apiGet<{ etag?: string; profile_ontology_view?: { etag?: string } }>(path);
    await apiPatch(path, {
      base_etag: current.profile_ontology_view?.etag ?? current.etag ?? "",
      table_usages_ja: payload.table_usage,
      allowed_path_ids: payload.allowed_path_ids,
      node_overrides: payload.node_overrides,
      edge_overrides: payload.edge_overrides,
      physical_scope: payload.physical_scope,
      schema_fingerprint: payload.schema_fingerprint,
    });
  };

  const refreshSchema = async () => {
    setRefreshing(true);
    setError("");
    try {
      await apiPost("/api/schema/refresh");
      await loadProfiles();
      setOntologyViewNonce((nonce) => nonce + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("profiles.error.load"));
    } finally {
      setRefreshing(false);
    }
  };

  const selectProfile = (id: string) => setSearchParams(id ? { profile: id } : {}, { replace: true });

  return (
    <>
      <PageHeader title={t("nav.ontologyBuild")} subtitle={t("ontologyBuild.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        {error ? <Banner severity="danger">{error}</Banner> : null}

        <section className="rounded-md border border-border bg-card p-4 shadow-sm">
          <label className="grid gap-1 text-sm font-medium text-foreground sm:max-w-md">
            <span>{t("ontologyBuild.profile.label")}</span>
            <select
              value={selectedProfile?.id ?? ""}
              onChange={(event) => selectProfile(event.currentTarget.value)}
              disabled={activeProfiles.length === 0}
              className="min-h-11 rounded-md border border-border bg-card px-3 py-2 focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted"
              data-testid="ontology-build-profile-select"
            >
              {activeProfiles.length === 0 ? (
                <option value="">{t("ontologyBuild.profile.empty")}</option>
              ) : (
                activeProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))
              )}
            </select>
          </label>
        </section>

        {profilesLoaded && activeProfiles.length === 0 ? (
          <EmptyState title={t("ontologyBuild.empty.title")} hint={t("ontologyBuild.empty.hint")} />
        ) : (
          <>
            <OntologyBuildSection
              profileId={selectedProfile?.id ?? null}
              onPublished={() => setOntologyViewNonce((nonce) => nonce + 1)}
            />
            <ProfileOntologyEditor
              graph={ontologyGraph}
              profileId={selectedProfile?.id ?? "new-profile"}
              selectedTables={selectedProfile?.allowed_tables ?? []}
              selectedViews={selectedProfile?.allowed_views ?? []}
              warnings={ontologyWarnings}
              onRefreshSchema={selectedProfile ? refreshSchema : undefined}
              refreshingSchema={refreshing}
              initialDraft={ontologyDraft}
              onSaveDraft={selectedProfile ? saveOntologyDraft : undefined}
              labels={{
                inspectorTitle: t("profiles.ontology.inspector"),
                tableUsage: t("profiles.ontology.usage"),
                cardinality: t("profiles.ontology.cardinality"),
                allowedPath: t("profiles.ontology.allowedPath"),
              }}
            />
          </>
        )}
      </main>
    </>
  );
}
