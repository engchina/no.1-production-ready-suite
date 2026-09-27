import { useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigationType,
  useParams,
} from "react-router-dom";
import { ChevronLeft } from "lucide-react";

import { AppShell, PageBody, PageHeader } from "@engchina/production-ready-ui";
import { RequireAuth, useForbiddenRedirect } from "@engchina/production-ready-system-settings";

import { ForbiddenPage, LoginPage, PasswordChangePage } from "@/components/security/AuthPages";
import { ragIdentityKey, useAuth } from "@/components/security/AuthProvider";
import { SecurityPermissionsPage } from "@/components/security/SecurityPermissionsPage";
import { SecurityRolesPage } from "@/components/security/SecurityRolesPage";
import { SecurityUsersPage } from "@/components/security/SecurityUsersPage";
import { CardErrorBoundary } from "@/components/CardErrorBoundary";
import { DashboardClient } from "@/components/dashboard/DashboardClient";
import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { EvaluationClient } from "@/components/evaluation/EvaluationClient";
import { FeedbackClient } from "@/components/feedback/FeedbackClient";
import { FileListClient } from "@/components/file-list/FileListClient";
import { KnowledgeBaseManagementClient } from "@/components/knowledge-bases/KnowledgeBaseManagementClient";
import { KnowledgeBaseDetailClient } from "@/components/knowledge-bases/KnowledgeBaseDetailClient";
import { BusinessViewManagementClient } from "@/components/business-views/BusinessViewManagementClient";
import { Sidebar } from "@/components/layout/Sidebar";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { DatabaseGate } from "@/components/system/DatabaseGate";
import { ChatClient } from "@/components/chat/ChatClient";
import { SearchClient } from "@/components/search/SearchClient";
import { RememberedSearchParams } from "@/components/RememberedSearchParams";
import { DatabaseSettingsClient } from "@/components/settings/DatabaseSettingsClient";
import { HuggingFaceSettingsClient } from "@/components/settings/HuggingFaceSettingsClient";
import { AppearanceSettings } from "@/components/settings/AppearanceSettings";
import { ModelSettingsClient } from "@/components/settings/ModelSettingsClient";
import { OciSettingsClient } from "@/components/settings/OciSettingsClient";
import { ParserAdapterSettingsClient } from "@/components/settings/ParserAdapterSettingsClient";
import { ChunkingSettingsClient } from "@/components/settings/ChunkingSettingsClient";
import { PreprocessSettingsClient } from "@/components/settings/PreprocessSettingsClient";
import { ServicesManagementClient } from "@/components/settings/ServicesManagementClient";
import { RetrievalSettingsClient } from "@/components/settings/RetrievalSettingsClient";
import { GroundingSettingsClient } from "@/components/settings/GroundingSettingsClient";
import { GenerationSettingsClient } from "@/components/settings/GenerationSettingsClient";
import { PromptVersionsClient } from "@/components/settings/PromptVersionsClient";
import { GuardrailSettingsClient } from "@/components/settings/GuardrailSettingsClient";
import { VectorIndexSettingsClient } from "@/components/settings/VectorIndexSettingsClient";
import { EvaluationSettingsClient } from "@/components/settings/EvaluationSettingsClient";
import { GraphSettingsClient } from "@/components/settings/GraphSettingsClient";
import { AgenticSettingsClient } from "@/components/settings/AgenticSettingsClient";
import { PipelineHubClient } from "@/components/settings/PipelineHubClient";
import { UploadStorageSettingsClient } from "@/components/settings/UploadStorageSettingsClient";
import { UploadWorkspace } from "@/components/upload/UploadWorkspace";
import { APP_ROUTES } from "@/lib/routes";
import { canOpenRoute, defaultEntryRoute, settingsEntryRoute } from "@/lib/route-permissions";
import { t } from "@/lib/i18n";
import { useUiStore } from "@/lib/ui-store";

/** 認証画面とルートの保護が使う URL（共通の RequireAuth へ渡す。#214）。 */
const AUTH_ROUTES = {
  login: APP_ROUTES.login,
  passwordChange: APP_ROUTES.passwordChange,
  forbidden: APP_ROUTES.forbidden,
};

export function App() {
  // API の 403 は共通のイベントで受け、request ID を載せて権限なしの画面へ移す（#214）。
  useForbiddenRedirect(APP_ROUTES.forbidden);

  return (
    <Routes>
      <Route path={APP_ROUTES.login} element={<LoginPage />} />
      <Route path={APP_ROUTES.passwordChange} element={<PasswordChangePage />} />
      <Route path={APP_ROUTES.forbidden} element={<ForbiddenPage />} />
      <Route element={<ProtectedLayout />}>
        <Route path="/" element={<EntryRedirect />} />
        <Route path={APP_ROUTES.dashboard} element={<DashboardClient />} />
        <Route path={APP_ROUTES.upload} element={<UploadWorkspace />} />
        <Route path={APP_ROUTES.fileList} element={<FileListClient />} />
        <Route path={APP_ROUTES.knowledgeBases} element={<KnowledgeBaseManagementClient />} />
        <Route
          path={`${APP_ROUTES.knowledgeBases}/:id`}
          element={<KnowledgeBaseDetailRoute />}
        />
        <Route path={APP_ROUTES.businessViews} element={<BusinessViewManagementClient />} />
        <Route path={`${APP_ROUTES.documents}/:id`} element={<DocumentDetailRoute />} />
        <Route path={APP_ROUTES.chat} element={<ChatClient />} />
        <Route path={APP_ROUTES.search} element={<SearchClient />} />
        <Route path={APP_ROUTES.evaluation} element={<EvaluationClient />} />
        <Route
          path={APP_ROUTES.feedback}
          element={
            <RememberedSearchParams field="feedback.search">
              <FeedbackClient />
            </RememberedSearchParams>
          }
        />
        <Route path={APP_ROUTES.settingsPipeline} element={<SettingsPipelineRoute />} />
        <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
        <Route
          path={APP_ROUTES.settingsUploadStorage}
          element={<SettingsUploadStorageRoute />}
        />
        <Route
          path={APP_ROUTES.settingsParserAdapters}
          element={<SettingsParserAdaptersRoute />}
        />
        <Route path={APP_ROUTES.settingsPreprocess} element={<SettingsPreprocessRoute />} />
        <Route path={APP_ROUTES.settingsChunking} element={<SettingsChunkingRoute />} />
        <Route path={APP_ROUTES.settingsRetrieval} element={<SettingsRetrievalRoute />} />
        <Route path={APP_ROUTES.settingsGrounding} element={<SettingsGroundingRoute />} />
        <Route path={APP_ROUTES.settingsGeneration} element={<SettingsGenerationRoute />} />
        <Route path={APP_ROUTES.settingsPrompts} element={<SettingsPromptsRoute />} />
        <Route path={APP_ROUTES.settingsGuardrail} element={<SettingsGuardrailRoute />} />
        <Route path={APP_ROUTES.settingsVectorIndex} element={<SettingsVectorIndexRoute />} />
        <Route path={APP_ROUTES.settingsEvaluation} element={<SettingsEvaluationRoute />} />
        <Route path={APP_ROUTES.settingsGraph} element={<SettingsGraphRoute />} />
        <Route path={APP_ROUTES.settingsAgentic} element={<SettingsAgenticRoute />} />
        <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
        <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
        <Route path={APP_ROUTES.settingsHuggingface} element={<SettingsHuggingfaceRoute />} />
        <Route path={APP_ROUTES.settingsServices} element={<SettingsServicesRoute />} />
        <Route path={APP_ROUTES.settingsAppearance} element={<AppearanceSettings />} />
        <Route path={APP_ROUTES.securityUsers} element={<SecurityUsersPage />} />
        <Route path={APP_ROUTES.securityRoles} element={<SecurityRolesPage />} />
        <Route path={APP_ROUTES.securityPermissions} element={<SecurityPermissionsPage />} />
        <Route path="/settings" element={<SettingsEntryRedirect />} />
        <Route path="*" element={<EntryRedirect />} />
      </Route>
    </Routes>
  );
}

/** `/` と未知の URL は、権限のある既定の画面へ（ダッシュボード、無ければナビの最初の画面。#214）。 */
function EntryRedirect() {
  const { hasPermission } = useAuth();
  return <Navigate to={defaultEntryRoute(hasPermission)} replace />;
}

function SettingsEntryRedirect() {
  const { hasPermission } = useAuth();
  return <Navigate to={settingsEntryRoute(hasPermission)} replace />;
}

/**
 * 認証が必要な画面の入口。確認中・未認証・強制パスワード変更の振り分けは共通の RequireAuth（#220）、
 * URL ごとの権限（詳細画面のように複数の権限のどれかで開けるものを含む）はここで判定する（#214）。
 */
function ProtectedLayout() {
  return (
    <RequireAuth routes={AUTH_ROUTES}>
      <RoutePermissionGuard>
        <AuthorizedLayout />
      </RoutePermissionGuard>
    </RequireAuth>
  );
}

function RoutePermissionGuard({ children }: { children: ReactNode }) {
  const { hasPermission } = useAuth();
  const { pathname } = useLocation();
  if (!canOpenRoute(pathname, hasPermission)) {
    return <Navigate to={APP_ROUTES.forbidden} replace />;
  }
  return <>{children}</>;
}

function AuthorizedLayout() {
  const { user } = useAuth();
  // 利用者や権限・対象範囲が変わったら、画面の state ごと作り直す（cache は AuthProvider が破棄する）。
  return <AppLayout key={user ? ragIdentityKey(user) : ""} />;
}

function AppLayout() {
  const location = useLocation();
  const navigationType = useNavigationType();
  // AppShell が出力する <main id="pr-main">（スキップリンクの移動先と同じ公開 ID）をスクロール復元に使う。
  const mainRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    mainRef.current = document.getElementById("pr-main");
  });
  const setSidebarCollapsed = useUiStore((state) => state.setSidebarCollapsed);
  useCollapseSidebarOnNarrowViewport(setSidebarCollapsed);
  useMainScrollRestoration(mainRef, location, navigationType);

  return (
    <AppShell
      className="fixed inset-0 h-auto"
      mainClassName="min-h-0"
      skipLinkLabel={t("common.skipToMain")}
      sidebar={
        <>
          <Sidebar />
          <CommandPalette />
        </>
      }
    >
      <DatabaseGate>
          {/* ページ本体の描画例外を main 内に閉じ込め、サイドナビと画面遷移を維持する(#67)。
              location をキーにして、別ページへ移動したらエラー状態を自動で解除する。 */}
          <CardErrorBoundary key={location.pathname}>
            <Outlet />
          </CardErrorBoundary>
      </DatabaseGate>
    </AppShell>
  );
}

type RouterLocation = ReturnType<typeof useLocation>;
type RouterNavigationType = ReturnType<typeof useNavigationType>;

function useCollapseSidebarOnNarrowViewport(setSidebarCollapsed: (collapsed: boolean) => void) {
  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const collapseIfNarrow = () => {
      if (media.matches) setSidebarCollapsed(true);
    };
    collapseIfNarrow();
    media.addEventListener("change", collapseIfNarrow);
    return () => media.removeEventListener("change", collapseIfNarrow);
  }, [setSidebarCollapsed]);
}

const mainScrollPositions = new Map<string, number>();

function useMainScrollRestoration(
  mainRef: RefObject<HTMLElement | null>,
  location: RouterLocation,
  navigationType: RouterNavigationType
) {
  const pathnameRef = useRef(location.pathname);
  const hashRef = useRef(location.hash);
  const scrollKey = mainScrollPositionKey(location);

  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main) return;

    const save = () => {
      mainScrollPositions.set(scrollKey, main.scrollTop);
    };
    main.addEventListener("scroll", save, { passive: true });

    return () => {
      main.removeEventListener("scroll", save);
    };
  }, [mainRef, scrollKey]);

  useLayoutEffect(() => {
    const main = mainRef.current;
    if (!main) return;

    const pathnameChanged = pathnameRef.current !== location.pathname;
    const hashChanged = hashRef.current !== location.hash;
    pathnameRef.current = location.pathname;
    hashRef.current = location.hash;

    if (!pathnameChanged && !hashChanged && navigationType !== "POP") return;

    const nextTop =
      navigationType === "POP" ? mainScrollPositions.get(scrollKey) ?? 0 : 0;
    const scroll = () => {
      if (location.hash && scrollHashTargetIntoView(location.hash)) return;
      main.scrollTo({ top: nextTop, left: 0, behavior: "auto" });
    };

    if (pathnameChanged) main.focus({ preventScroll: true });
    // 共有設定画面などは戻ったあとに非同期で読み込み直し、高さが後から伸びる。
    // 目標位置に届くまで最大 2 秒フレームごとに再試行し、利用者が操作したら止める。
    const deadline = performance.now() + 2000;
    let animationFrame = 0;
    const restore = () => {
      scroll();
      if (location.hash || Math.abs(main.scrollTop - nextTop) <= 1) return;
      if (performance.now() > deadline) return;
      animationFrame = window.requestAnimationFrame(restore);
    };
    const stop = () => window.cancelAnimationFrame(animationFrame);
    const userEvents = ["wheel", "touchstart", "keydown", "pointerdown"] as const;
    userEvents.forEach((type) => main.addEventListener(type, stop, { passive: true }));
    scroll();
    animationFrame = window.requestAnimationFrame(restore);
    return () => {
      stop();
      userEvents.forEach((type) => main.removeEventListener(type, stop));
    };
  }, [location.hash, location.pathname, mainRef, navigationType, scrollKey]);
}

function mainScrollPositionKey(location: RouterLocation) {
  return `${location.pathname}${location.search}${location.hash}`;
}

function scrollHashTargetIntoView(hash: string) {
  const id = decodeHashId(hash);
  if (!id) return false;

  const target = document.getElementById(id);
  if (!target) return false;

  target.scrollIntoView({ block: "start", inline: "nearest", behavior: "auto" });
  return true;
}

function decodeHashId(hash: string) {
  const id = hash.slice(1);
  try {
    return decodeURIComponent(id);
  } catch {
    return id;
  }
}

function DocumentDetailRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return <Navigate to={APP_ROUTES.fileList} replace />;

  return (
    <div>
      <div className="border-b border-border bg-surface">
        <PageBody wide className="py-4">
        <Link
          to={APP_ROUTES.fileList}
          className="inline-flex items-center gap-1 text-sm text-fg-muted transition-colors hover:text-fg"
        >
          <ChevronLeft size={16} aria-hidden />
          {t("workspace.back")}
        </Link>
        </PageBody>
      </div>
      <PageBody wide>
        <DocumentWorkspace documentId={id} />
      </PageBody>
    </div>
  );
}

function KnowledgeBaseDetailRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return <Navigate to={APP_ROUTES.knowledgeBases} replace />;

  return (
    <div>
      <div className="border-b border-border bg-surface">
        <PageBody wide className="py-4">
        <Link
          to={APP_ROUTES.knowledgeBases}
          className="inline-flex items-center gap-1 text-sm text-fg-muted transition-colors hover:text-fg"
        >
          <ChevronLeft size={16} aria-hidden />
          {t("knowledgeBases.detail.back")}
        </Link>
        </PageBody>
      </div>
      <PageBody wide>
        <KnowledgeBaseDetailClient knowledgeBaseId={id} />
      </PageBody>
    </div>
  );
}

function SettingsPipelineRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsPipeline")}
        subtitle={t("settings.pipeline.subtitle")}
      />
      <PipelineHubClient />
    </div>
  );
}

function SettingsOciRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsOci")} subtitle={t("settings.oci.subtitle")} />
      <OciSettingsClient />
    </div>
  );
}

function SettingsUploadStorageRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsUploadStorage")}
        subtitle={t("settings.uploadStorage.subtitle")}
      />
      <UploadStorageSettingsClient />
    </div>
  );
}

function SettingsParserAdaptersRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsParserAdapters")}
        subtitle={t("settings.parserAdapters.subtitle")}
      />
      <ParserAdapterSettingsClient />
    </div>
  );
}

function SettingsPreprocessRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsPreprocess")}
        subtitle={t("settings.preprocess.subtitle")}
      />
      <PreprocessSettingsClient />
    </div>
  );
}

function SettingsServicesRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsServices")}
        subtitle={t("settings.services.subtitle")}
      />
      <ServicesManagementClient />
    </div>
  );
}

function SettingsChunkingRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsChunking")}
        subtitle={t("settings.chunking.subtitle")}
      />
      <ChunkingSettingsClient />
    </div>
  );
}

function SettingsRetrievalRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsRetrieval")}
        subtitle={t("settings.retrieval.subtitle")}
      />
      <RetrievalSettingsClient />
    </div>
  );
}

function SettingsGroundingRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsGrounding")}
        subtitle={t("settings.grounding.subtitle")}
      />
      <GroundingSettingsClient />
    </div>
  );
}

function SettingsGenerationRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsGeneration")}
        subtitle={t("settings.generation.subtitle")}
      />
      <GenerationSettingsClient />
    </div>
  );
}

function SettingsPromptsRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsPrompts")} subtitle={t("settings.prompts.subtitle")} />
      <PromptVersionsClient />
    </div>
  );
}

function SettingsGuardrailRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsGuardrail")}
        subtitle={t("settings.guardrail.subtitle")}
      />
      <GuardrailSettingsClient />
    </div>
  );
}

function SettingsVectorIndexRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsVectorIndex")}
        subtitle={t("settings.vectorIndex.subtitle")}
      />
      <VectorIndexSettingsClient />
    </div>
  );
}

function SettingsEvaluationRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsEvaluation")}
        subtitle={t("settings.evaluation.subtitle")}
      />
      <EvaluationSettingsClient />
    </div>
  );
}

function SettingsGraphRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsGraph")} subtitle={t("settings.graph.subtitle")} />
      <GraphSettingsClient />
    </div>
  );
}

function SettingsAgenticRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsAgentic")} subtitle={t("settings.agentic.subtitle")} />
      <AgenticSettingsClient />
    </div>
  );
}

function SettingsDatabaseRoute() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <DatabaseSettingsClient />
    </div>
  );
}

function SettingsHuggingfaceRoute() {
  return (
    <div>
      <PageHeader
        wide
        title={t("nav.settingsHuggingface")}
        subtitle={t("settings.huggingface.subtitle")}
      />
      <HuggingFaceSettingsClient />
    </div>
  );
}
