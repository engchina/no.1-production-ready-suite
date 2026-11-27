import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { MessagesSquare, Search, SlidersHorizontal } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  Disclosure,
  FieldActionRow,
  FormGrid,
  PageBody,
  PageHeader,
  SelectedSources,
  SelectField,
  TextareaField,
} from "@production-ready/ui";
import {
  SearchAnswerProfileSelect,
  SearchAnswerProfileSelectSkeleton,
} from "@/components/search-answer-profiles/SearchAnswerProfileSelect";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { useAuth } from "@/components/security/AuthProvider";
import { isSubmitEnter } from "@/lib/keyboard";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { useAllSearchAnswerProfiles } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { readWorkspace, removeWorkspace, writeWorkspace } from "@/lib/workspace-state";
import { request } from "@/lib/api";
import { WikiExplorer } from "./WikiExplorer";
import {
  isWikiDraft,
  wikiApi,
  type WikiKb,
  type WikiMethod,
  type WikiPick,
  type WikiScope,
} from "./wiki-api";

interface SearchState {
  profileId: string;
  kb: string;
  query: string;
  submitted: string;
  method: WikiMethod;
  root: string;
  items: WikiPick[];
}
const initial: SearchState = {
  profileId: "",
  kb: "",
  query: "",
  submitted: "",
  method: "hybrid",
  root: "",
  items: [],
};
function isSearch(value: unknown): value is SearchState {
  return (
    isWikiDraft(value) &&
    "kb" in value &&
    typeof value.kb === "string" &&
    "query" in value &&
    typeof value.query === "string" &&
    "submitted" in value &&
    typeof value.submitted === "string" &&
    "root" in value &&
    typeof value.root === "string" &&
    "method" in value &&
    ["vector", "keyword", "hybrid"].includes(String(value.method))
  );
}
export function WikiWorkspace({ knowledgeBaseId }: { knowledgeBaseId?: string }) {
  const auth = useAuth();
  const navigate = useNavigate();
  const manual = !knowledgeBaseId;
  const canChat =
    auth.hasPermission(MENU_PERMISSIONS.chat) && auth.hasPermission(MENU_PERMISSIONS.wikiSearch);
  const [state, setState] = useState(() => readWorkspace("wiki.search", initial, isSearch, knowledgeBaseId));
  const [criteria, setCriteria] = useState(false);
  const [run, setRun] = useState(0);
  const profiles = useAllSearchAnswerProfiles({ status: "ACTIVE" }, manual);
  const profileId = state.profileId;
  const scope: WikiScope = manual
    ? { kind: "profile", id: profileId, kb: state.kb }
    : { kind: "kb", id: knowledgeBaseId! };
  const available = useQuery({
    queryKey: ["wiki-kbs", profileId],
    queryFn: ({ signal }) => wikiApi.scope({ kind: "profile", id: profileId }, signal),
    enabled: manual && !!profileId,
    retry: false,
  });
  const chatProfiles = useQuery({
    queryKey: ["wiki-chat-profiles", knowledgeBaseId],
    queryFn: ({ signal }) =>
      request<WikiKb[]>(`/api/wiki-search/profiles-for-kb/${encodeURIComponent(knowledgeBaseId!)}`, {
        signal,
      }),
    enabled: !manual && canChat && state.items.length > 0,
    retry: false,
  });
  const kbProfiles = chatProfiles.data ?? [];
  const chatProfile = manual
    ? profileId
    : (kbProfiles.find((p) => p.id === profileId)?.id ?? (kbProfiles.length === 1 ? kbProfiles[0].id : ""));
  useEffect(() => {
    writeWorkspace("wiki.search", state, knowledgeBaseId);
  }, [state, knowledgeBaseId]);
  const patch = (next: Partial<SearchState>) => setState((s) => ({ ...s, ...next }));
  const search = () => {
    if (!scope.id) return;
    patch({ submitted: state.query.trim(), root: "" });
    setRun((r) => r + 1);
  };
  const pick = (p: WikiPick) =>
    setState((s) => ({
      ...s,
      items: s.items.some((x) => x.id === p.id) ? s.items.filter((x) => x.id !== p.id) : [...s.items, p],
    }));
  function chat() {
    if (!canChat || !chatProfile || !state.items.length) return;
    writeWorkspace("chat.wikiSources", { profileId: chatProfile, items: state.items });
    writeWorkspace("chat.composer", state.submitted);
    for (const f of ["chat.conversationId", "chat.faqChoice", "chat.clarifyChoice"] as const)
      removeWorkspace(f);
    if (!manual) writeWorkspace("wiki.search", { ...state, profileId: chatProfile, kb: knowledgeBaseId });
    navigate(`${APP_ROUTES.chat}?search_answer_profile_id=${encodeURIComponent(chatProfile)}`);
  }
  const noKbs = manual && !!profileId && available.isSuccess && !available.data.knowledge_bases.length;
  const inputs = (
    <div className="space-y-4">
      {manual &&
        (profiles.isPending ? (
          <SearchAnswerProfileSelectSkeleton />
        ) : profiles.isError ? (
          <ApiErrorState
            error={profiles.error}
            fallback={t("search.searchAnswerProfileError")}
            onRetry={() => void profiles.refetch()}
          />
        ) : (
          <SearchAnswerProfileSelect
            id="wiki-search-profile"
            items={profiles.data?.items ?? []}
            value={profileId || null}
            onChange={(id) =>
              setState({ ...initial, profileId: id, query: state.query, method: state.method })
            }
          />
        ))}
      <Disclosure
        surface="sunken"
        icon={SlidersHorizontal}
        summary={t("search.filters.advanced")}
        open={criteria}
        onOpenChange={setCriteria}
      >
        {criteria && (
          <FormGrid>
            <SelectField
              id="wiki-search-method"
              label={t("wiki.search.method")}
              value={state.method}
              options={(["hybrid", "vector", "keyword"] as const).map((value) => ({
                value,
                label: t(`wiki.search.${value}`),
              }))}
              onValueChange={(method) => patch({ method, root: "" })}
            />
            {manual && (
              <SelectField
                id="wiki-search-kb"
                label={t("wiki.search.kb")}
                value={state.kb}
                options={[
                  { value: "", label: t("wiki.search.allKb") },
                  ...(available.data?.knowledge_bases ?? []).map((k) => ({ value: k.id, label: k.name })),
                ]}
                onValueChange={(kb) => patch({ kb, root: "", items: [] })}
              />
            )}
          </FormGrid>
        )}
      </Disclosure>
      <div className="border-t border-border pt-4">
        <FieldActionRow
          actions={
            <Button
              size="lg"
              icon={Search}
              disabled={!scope.id || noKbs || (!state.query.trim() && manual)}
              onClick={search}
            >
              {t("search.button")}
            </Button>
          }
        >
          <TextareaField
            id="wiki-question"
            label={t("nav.wikiSearch")}
            labelHidden
            width="full"
            rows={manual ? 3.5 : 2}
            value={state.query}
            placeholder={t("search.placeholder")}
            maxLength={8000}
            onChange={(e) => patch({ query: e.target.value })}
            onKeyDown={(e) => {
              if (isSubmitEnter(e) && !e.shiftKey) {
                e.preventDefault();
                if (!manual || state.query.trim()) search();
              }
            }}
          />
        </FieldActionRow>
      </div>
    </div>
  );
  return (
    <section className="space-y-4">
      {manual ? (
        <Card>
          <CardContent className="pt-5">{inputs}</CardContent>
        </Card>
      ) : (
        inputs
      )}
      {available.isError && manual && (
        <ApiErrorState
          error={available.error}
          fallback={t("wiki.graph.error")}
          onRetry={() => void available.refetch()}
        />
      )}
      {noKbs ? (
        <EmptyState title={t("wiki.search.noScope")} />
      ) : scope.id && (!manual || (available.isSuccess && !!state.submitted)) ? (
        <>
          <WikiExplorer
            key={JSON.stringify([scope, run, state.submitted, state.method])}
            scope={scope}
            kbs={available.data?.knowledge_bases ?? []}
            question={state.submitted}
            method={state.method}
            picks={state.items}
            toggle={canChat ? pick : undefined}
            initialUnit={state.root}
            onRoot={(root) => patch({ root })}
          />
          {canChat && state.items.length > 0 && (
            <div className="space-y-2 rounded-md border border-border bg-surface p-3">
              {!manual && kbProfiles.length > 1 && (
                <SelectField
                  id="wiki-chat-profile"
                  label={t("searchAnswerProfiles.scope.label")}
                  placeholder={t("searchAnswerProfileSelect.placeholder")}
                  value={chatProfile}
                  required
                  options={kbProfiles.map((k) => ({ value: k.id, label: k.name }))}
                  onValueChange={(id) => patch({ profileId: id })}
                />
              )}
              {!manual && chatProfiles.isError && (
                <ApiErrorState
                  error={chatProfiles.error}
                  fallback={t("search.searchAnswerProfileError")}
                  onRetry={() => void chatProfiles.refetch()}
                />
              )}
              {!manual && chatProfiles.isSuccess && kbProfiles.length === 0 && (
                <p className="text-sm text-fg-muted">{t("wiki.search.noChatProfile")}</p>
              )}
              <SelectedSources
                items={state.items}
                label={t("wiki.search.selected")}
                removeLabel={(name) => t("fileReview.remove", { name })}
                focusAfterLast={() => document.getElementById("wiki-question")?.focus()}
                onRemove={(id) => patch({ items: state.items.filter((p) => p.id !== id) })}
                action={
                  <Button icon={MessagesSquare} disabled={!chatProfile} onClick={chat}>
                    {t("wiki.search.ask")}
                  </Button>
                }
              />
            </div>
          )}
        </>
      ) : (
        manual && (
          <Card>
            <CardContent className="pt-5">
              <EmptyState
                title={t(profileId ? "wiki.search.start" : "searchAnswerProfileSelect.placeholder")}
              />
            </CardContent>
          </Card>
        )
      )}
    </section>
  );
}
export function WikiSearchClient() {
  return (
    <>
      <PageHeader wide title={t("nav.wikiSearch")} subtitle={t("wiki.search.subtitle")} />
      <PageBody wide>
        <WikiWorkspace />
      </PageBody>
    </>
  );
}
