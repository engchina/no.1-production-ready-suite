import { useEffect, useMemo, useRef, useState } from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { MessagesSquare, Search, SlidersHorizontal, X } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Disclosure,
  FieldActionRow,
  FormGrid,
  FieldSkeleton,
  Skeleton,
  TimedLoadingState,
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
  wikiSelectionLimit,
  type WikiDraft,
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
  appliedMethod?: WikiMethod;
  appliedKb?: string;
  offset?: number;
  selectedUnit?: string;
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
    (!("selectedUnit" in value) || typeof value.selectedUnit === "string") &&
    (!("offset" in value) || (typeof value.offset === "number" && Number.isSafeInteger(value.offset) && value.offset >= 0)) &&
    (!("appliedMethod" in value) || ["vector", "keyword", "hybrid"].includes(String(value.appliedMethod))) &&
    (!("appliedKb" in value) || typeof value.appliedKb === "string") &&
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
  const client = useQueryClient();
  const manual = !knowledgeBaseId;
  const canChat =
    auth.hasPermission(MENU_PERMISSIONS.chat) && auth.hasPermission(MENU_PERMISSIONS.wikiSearch);
  const [state, setState] = useState(() => readWorkspace("wiki.search", initial, isSearch, knowledgeBaseId));
  const [criteria, setCriteria] = useState(false);
  const [run, setRun] = useState(0);
  const resultsRef = useRef<HTMLDivElement>(null);
  const profiles = useAllSearchAnswerProfiles({ status: "ACTIVE" }, manual);
  const profileId = state.profileId;
  const appliedMethod = state.appliedMethod ?? state.method;
  const scope: WikiScope = manual
    ? { kind: "profile", id: profileId, kb: state.appliedKb ?? state.kb }
    : { kind: "kb", id: knowledgeBaseId! };
  const available = useQuery({
    queryKey: ["wiki-kbs", profileId],
    queryFn: ({ signal }) => wikiApi.scope({ kind: "profile", id: profileId }, signal),
    enabled: manual && !!profileId,
    retry: false,
  });
  // 一度に選べる原文の上限（#5981）。Wiki 検索は範囲の応答、ナレッジベース詳細は選ぶ操作を出す前に範囲を読む。
  const kbScope = useQuery({
    queryKey: ["wiki-kb-scope", knowledgeBaseId],
    queryFn: ({ signal }) => wikiApi.scope({ kind: "kb", id: knowledgeBaseId! }, signal),
    enabled: !manual && canChat,
    retry: false,
  });
  const limitKnown = manual ? available.isSuccess : kbScope.isFetched;
  const selectionLimit = wikiSelectionLimit((manual ? available.data : kbScope.data)?.selection_limit);
  // 保存済みの選択が上限を超えていたら、先頭から上限までにする（画面・保存・チャットへ渡す内容のすべて）。
  const items = useMemo(
    () => (limitKnown && state.items.length > selectionLimit ? state.items.slice(0, selectionLimit) : state.items),
    [limitKnown, selectionLimit, state.items],
  );
  const chatProfiles = useQuery({
    queryKey: ["wiki-chat-profiles", knowledgeBaseId],
    queryFn: ({ signal }) =>
      request<WikiKb[]>(`/api/wiki-search/profiles-for-kb/${encodeURIComponent(knowledgeBaseId!)}`, {
        signal,
      }),
    enabled: !manual && canChat && items.length > 0,
    retry: false,
  });
  const isSearching = useIsFetching({ queryKey: ["wiki-explorer", scope, state.submitted ? "search" : "docs"] }) > 0;
  const scopePending = manual && !!profileId && available.isPending && !profiles.isPending;
  const kbProfiles = chatProfiles.data ?? [];
  const chatProfile = manual
    ? profileId
    : (kbProfiles.find((p) => p.id === profileId)?.id ?? (kbProfiles.length === 1 ? kbProfiles[0].id : ""));
  useEffect(() => {
    writeWorkspace("wiki.search", { ...state, items }, knowledgeBaseId);
  }, [state, items, knowledgeBaseId]);
  useEffect(() => {
    // 検索を実行したときだけ、画面下に隠れた経過時間または結果の見出しへ移動する。
    const region = resultsRef.current;
    if (!run || !region) return;
    const target = region.querySelector('[role="timer"]') ?? region.querySelector("h2");
    if (target && target.getBoundingClientRect().bottom > window.innerHeight) {
      region.scrollIntoView({ block: "start", inline: "nearest" });
    }
  }, [run]);
  const patch = (next: Partial<SearchState>) => setState((s) => ({ ...s, ...next }));
  const search = () => {
    if (!scope.id || isSearching || (manual && (!available.isSuccess || !state.query.trim()))) return;
    void client.invalidateQueries({ queryKey: ["wiki-explorer", scope, state.query.trim() ? "search" : "docs"], refetchType: "none" });
    patch({ submitted: state.query.trim(), root: "", selectedUnit: "", offset: 0, appliedKb: state.kb, appliedMethod: state.method,
      ...(scope.kind === "profile" && scope.kb !== state.kb ? { items: [] } : {}) });

    setRun((r) => r + 1);
  };
  // 選ぶ・外す。上限に達している間は足さない（自動で入れ替えない）。
  const pick = (p: WikiPick) =>
    setState((s) => {
      const kept = s.items.slice(0, selectionLimit);
      if (kept.some((x) => x.id === p.id)) return { ...s, items: kept.filter((x) => x.id !== p.id) };
      return kept.length < selectionLimit ? { ...s, items: [...kept, p] } : s;
    });
  const unpick = (ids: readonly string[]) =>
    setState((s) => ({ ...s, items: s.items.slice(0, selectionLimit).filter((x) => !ids.includes(x.id)) }));
  function chat() {
    if (!canChat || !chatProfile || !items.length) return;
    const draft: WikiDraft = { profileId: chatProfile, items, knowledgeBaseId, selectionLimit };
    writeWorkspace("chat.wikiSources", draft);
    writeWorkspace("chat.composer", state.submitted);
    for (const f of ["chat.conversationId", "chat.faqChoice", "chat.clarifyChoice"] as const)
      removeWorkspace(f);
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
              onValueChange={(method) => patch({ method })}
            />
            {manual && (scopePending ? <FieldSkeleton /> : (
              <SelectField
                id="wiki-search-kb"
                label={t("wiki.search.kb")}
                value={state.kb}
                options={[
                  { value: "", label: t("wiki.search.allKb") },
                  ...(available.data?.knowledge_bases ?? []).map((k) => ({ value: k.id, label: k.name })),
                ]}
                onValueChange={(kb) => patch({ kb })}
              />
            ))}
          </FormGrid>
        )}
      </Disclosure>
      <div className="border-t border-border pt-4">
        <FieldActionRow
          actions={
            <Button
              size="lg"
              icon={Search}
              loading={isSearching}
              disabled={!scope.id || noKbs || (manual && (!state.query.trim() || !available.isSuccess))}
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
      {/* ナレッジベース詳細は図の上の欄でナレッジベースの中を検索する（#5922）。質問の欄は Wiki 検索だけ。 */}
      {manual && (
        <Card>
          <CardHeader>
            <CardTitle>{t("wiki.search.conditions")}</CardTitle>
          </CardHeader>
          <CardContent>{inputs}</CardContent>
        </Card>
      )}
      {manual && (profiles.isPending || scopePending) && (
        <TimedLoadingState label={t(profiles.isPending ? "wiki.search.loadingProfiles" : "wiki.search.loadingScope")} operationKey={profileId} testId="wiki-scope-progress">
          <Skeleton className="h-20 w-full" />
        </TimedLoadingState>
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
        <div ref={resultsRef} className="space-y-4 scroll-mt-24">
          <WikiExplorer
            key={JSON.stringify([scope, run, state.submitted, appliedMethod])}
            scope={scope}
            kbs={available.data?.knowledge_bases ?? []}
            question={manual ? state.submitted : ""}
            method={appliedMethod}
            initialOffset={state.offset ?? 0}
            onOffset={(offset) => patch({ offset, root: "", selectedUnit: "" })}
            picks={items}
            // Wiki 検索の図は選んだ原文から作るので、チャットの権限が無くても選べる（チャットへ渡す操作だけ出さない）。
            // ナレッジベース詳細は、チャットへ渡すときだけ選ぶ（上限を読んでから操作を出す）。
            toggle={manual ? pick : canChat && limitKnown ? pick : undefined}
            unpick={unpick}
            selectionLimit={selectionLimit}
            initialSelection={state.selectedUnit}
            onSelectUnit={(selectedUnit) => patch({ selectedUnit })}
            searchButtonBusy={isSearching}
          />
          {(manual || canChat) && items.length > 0 && (
            <div className="space-y-2 rounded-md border border-border bg-surface p-3" data-testid="wiki-selected">
              {!manual && chatProfiles.isPending && (
                <TimedLoadingState label={t("wiki.search.loadingProfiles")} operationKey={knowledgeBaseId}><FieldSkeleton /></TimedLoadingState>
              )}
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
                items={items}
                label={t("wiki.search.selected")}
                count={t("wiki.search.selectedCount", { count: items.length, limit: selectionLimit })}
                removeLabel={(name) => t("fileReview.remove", { name })}
                focusAfterLast={() => document.getElementById("wiki-question")?.focus()}
                onRemove={(id) => unpick([id])}
                action={
                  // 狭い画面では操作を 1 行にまとめてタグの下へ回す（タグの欄を全幅に使う）。
                  <div className="flex flex-wrap items-center justify-end gap-2 max-sm:w-full">
                    <Button
                      variant="ghost"
                      icon={X}
                      onClick={() => {
                        patch({ items: [] });
                        document.getElementById("wiki-question")?.focus();
                      }}
                    >
                      {t("wiki.search.removeAll")}
                    </Button>
                    {canChat && (
                      <Button icon={MessagesSquare} disabled={!chatProfile} onClick={chat}>
                        {t("wiki.search.ask")}
                      </Button>
                    )}
                  </div>
                }
              />
            </div>
          )}
        </div>
      ) : (
        manual && !profiles.isPending && !profiles.isError && !scopePending && !available.isError && (
          <EmptyState title={t(profileId ? "wiki.search.start" : "searchAnswerProfileSelect.placeholder")} />
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
