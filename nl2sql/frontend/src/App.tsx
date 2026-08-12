import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useRef,
  type ReactNode,
  type RefObject,
} from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useNavigationType,
} from "react-router-dom";

import { AppSidebar } from "@/components/layout/AppSidebar";
import { PageHeader } from "@/components/PageHeader";
import { TimedLoadingState } from "@/components/ProcessingState";
import { DatabaseGate } from "@/components/system/DatabaseGate";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { useUiStore } from "@/lib/ui-store";
import { useAuth } from "@/features/security/AuthProvider";
import { ROUTE_PERMISSIONS, firstAllowedRoute } from "@/features/security/route-permissions";

const LoginPage = lazy(() =>
  import("@/features/security/AuthPages").then((module) => ({ default: module.LoginPage }))
);
const PasswordChangePage = lazy(() =>
  import("@/features/security/AuthPages").then((module) => ({
    default: module.PasswordChangePage,
  }))
);
const ForbiddenPage = lazy(() =>
  import("@/features/security/AuthPages").then((module) => ({
    default: module.ForbiddenPage,
  }))
);
const Nl2SqlWorkbench = lazy(() =>
  import("@/features/nl2sql/Nl2SqlWorkbench").then((module) => ({
    default: module.Nl2SqlWorkbench,
  }))
);
const DirectSqlPage = lazy(() =>
  import("@/features/nl2sql/pages/DirectSqlPage").then((module) => ({
    default: module.DirectSqlPage,
  }))
);
const SqlAnalysisPage = lazy(() =>
  import("@/features/nl2sql/pages/SqlAnalysisPage").then((module) => ({
    default: module.SqlAnalysisPage,
  }))
);
const SqlToQuestionPage = lazy(() =>
  import("@/features/nl2sql/pages/SqlToQuestionPage").then((module) => ({
    default: module.SqlToQuestionPage,
  }))
);
const AdminSqlPage = lazy(() =>
  import("@/features/nl2sql/pages/AdminSqlPage").then((module) => ({
    default: module.AdminSqlPage,
  }))
);
const TableManagementPage = lazy(() =>
  import("@/features/nl2sql/pages/TableManagementPage").then((module) => ({
    default: module.TableManagementPage,
  }))
);
const ViewManagementPage = lazy(() =>
  import("@/features/nl2sql/pages/ViewManagementPage").then((module) => ({
    default: module.ViewManagementPage,
  }))
);
const DataManagementPage = lazy(() =>
  import("@/features/nl2sql/pages/DataManagementPage").then((module) => ({
    default: module.DataManagementPage,
  }))
);
const SampleDataPage = lazy(() =>
  import("@/features/nl2sql/pages/SampleDataPage").then((module) => ({
    default: module.SampleDataPage,
  }))
);
const CommentManagementPage = lazy(() =>
  import("@/features/nl2sql/pages/MetadataSqlManagementPage").then((module) => ({
    default: module.CommentManagementPage,
  }))
);
const AnnotationManagementPage = lazy(() =>
  import("@/features/nl2sql/pages/MetadataSqlManagementPage").then((module) => ({
    default: module.AnnotationManagementPage,
  }))
);
const ProfileManagementPage = lazy(() =>
  import("@/features/nl2sql/pages/ProfileManagementPage").then((module) => ({
    default: module.ProfileManagementPage,
  }))
);
const OntologyBuildPage = lazy(() =>
  import("@/features/nl2sql/pages/OntologyBuildPage").then((module) => ({
    default: module.OntologyBuildPage,
  }))
);
const GlossaryRulesPage = lazy(() =>
  import("@/features/nl2sql/pages/GlossaryRulesPage").then((module) => ({
    default: module.GlossaryRulesPage,
  }))
);
const GlobalRulesPage = lazy(() =>
  import("@/features/nl2sql/pages/GlobalRulesPage").then((module) => ({
    default: module.GlobalRulesPage,
  }))
);
const FeedbackManagementPage = lazy(() =>
  import("@/features/nl2sql/pages/FeedbackManagementPage").then((module) => ({
    default: module.FeedbackManagementPage,
  }))
);
const QuestionClassifierModelsPage = lazy(() =>
  import("@/features/nl2sql/pages/QuestionLearningPage").then((module) => ({
    default: module.QuestionClassifierModelsPage,
  }))
);
const HistoryPage = lazy(() =>
  import("@/features/nl2sql/pages/HistoryPage").then((module) => ({
    default: module.HistoryPage,
  }))
);
const EvaluationPage = lazy(() =>
  import("@/features/nl2sql/pages/EvaluationPage").then((module) => ({
    default: module.EvaluationPage,
  }))
);
const OciSettingsClient = lazy(() =>
  import("@/components/settings/OciSettingsClient").then((module) => ({
    default: module.OciSettingsClient,
  }))
);
const UploadStorageSettingsClient = lazy(() =>
  import("@/components/settings/UploadStorageSettingsClient").then((module) => ({
    default: module.UploadStorageSettingsClient,
  }))
);
const ModelSettingsClient = lazy(() =>
  import("@/components/settings/ModelSettingsClient").then((module) => ({
    default: module.ModelSettingsClient,
  }))
);
const DatabaseSettingsClient = lazy(() =>
  import("@/components/settings/DatabaseSettingsClient").then((module) => ({
    default: module.DatabaseSettingsClient,
  }))
);
const SystemTablesCard = lazy(() =>
  import("@/components/settings/SystemTablesCard").then((module) => ({
    default: module.SystemTablesCard,
  }))
);
const AppearanceSettings = lazy(() =>
  import("@/components/settings/AppearanceSettings").then((module) => ({
    default: module.AppearanceSettings,
  }))
);
const SecurityUsersPage = lazy(() =>
  import("@/features/security/SecurityUsersPage").then((module) => ({
    default: module.SecurityUsersPage,
  }))
);
const SecurityRolesPage = lazy(() =>
  import("@/features/security/SecurityRolesPage").then((module) => ({
    default: module.SecurityRolesPage,
  }))
);
const SecurityAuditPage = lazy(() =>
  import("@/features/security/SecurityAuditPage").then((module) => ({
    default: module.SecurityAuditPage,
  }))
);
const SecurityDeepSecPage = lazy(() =>
  import("@/features/security/SecurityDeepSecPage").then((module) => ({
    default: module.SecurityDeepSecPage,
  }))
);

/**
 * ナビ切替で state を破棄したくない「AI 活用」4画面。常時マウントし表示のみ切替する。
 * module 直下で JSX を一度だけ生成し、同一 instance を維持する(再マウント=state破棄を防ぐ)。
 */
const KEEP_ALIVE_PAGES = [
  { path: APP_ROUTES.query, element: <Nl2SqlWorkbench /> },
  { path: APP_ROUTES.sqlAnalysis, element: <SqlAnalysisPage /> },
  { path: APP_ROUTES.sqlToQuestion, element: <SqlToQuestionPage /> },
  { path: APP_ROUTES.directSql, element: <DirectSqlPage /> },
];
const KEEP_ALIVE_PATHS = new Set<string>(KEEP_ALIVE_PAGES.map((page) => page.path));

export function App() {
  const navigate = useNavigate();
  useEffect(() => {
    const handleForbidden = () => navigate(APP_ROUTES.forbidden, { replace: true });
    window.addEventListener("app-auth-forbidden", handleForbidden);
    return () => window.removeEventListener("app-auth-forbidden", handleForbidden);
  }, [navigate]);

  return (
    <Routes>
      <Route path={APP_ROUTES.login} element={<PublicRoute element={<LoginPage />} />} />
      <Route
        path={APP_ROUTES.passwordChange}
        element={<PublicRoute element={<PasswordChangePage />} />}
      />
      <Route
        path={APP_ROUTES.forbidden}
        element={<PublicRoute element={<ForbiddenPage />} />}
      />
      <Route path="*" element={<AuthenticatedApplication />} />
    </Routes>
  );
}

function PublicRoute({ element }: { element: ReactNode }) {
  return <Suspense fallback={<FullPageRouteLoadingFallback />}>{element}</Suspense>;
}

function FullPageRouteLoadingFallback() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4">
      <TimedLoadingState
        label={t("app.route.loading")}
        operationKey="route-loading-full"
        placement="page"
        testId="route-loading"
      />
    </main>
  );
}

function RouteLoadingFallback() {
  return (
    <div className="p-8">
      <TimedLoadingState
        label={t("app.route.loading")}
        operationKey="route-loading"
        placement="page"
        testId="route-loading"
      />
    </div>
  );
}

function AuthenticatedApplication() {
  const auth = useAuth();
  const location = useLocation();

  if (auth.status === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background p-4">
        <TimedLoadingState
          label={t("auth.loading")}
          operationKey="auth-session"
          placement="page"
          testId="auth-session-loading"
        />
      </main>
    );
  }
  if (auth.status === "unauthenticated") {
    return <Navigate to={APP_ROUTES.login} state={{ from: location.pathname }} replace />;
  }
  if (auth.user?.force_password_change) {
    return <Navigate to={APP_ROUTES.passwordChange} replace />;
  }

  const requiredPermission = ROUTE_PERMISSIONS[location.pathname];
  if (requiredPermission && !auth.hasPermission(requiredPermission)) {
    return <Navigate to={APP_ROUTES.forbidden} replace />;
  }

  return (
    <AppLayout>
      <DatabaseGate>
        <Suspense fallback={<RouteLoadingFallback />}>
          <KeepAlivePages />
          <Routes>
            <Route path={APP_ROUTES.dashboard} element={<Navigate to={firstAllowedRoute(auth.hasPermission)} replace />} />
            <Route path={APP_ROUTES.adminSql} element={<AdminSqlPage />} />
            <Route path={APP_ROUTES.tableManagement} element={<TableManagementPage />} />
            <Route path={APP_ROUTES.viewManagement} element={<ViewManagementPage />} />
            <Route path={APP_ROUTES.dataManagement} element={<DataManagementPage />} />
            <Route path={APP_ROUTES.sampleData} element={<SampleDataPage />} />
            <Route path={APP_ROUTES.commentManagement} element={<CommentManagementPage />} />
            <Route path={APP_ROUTES.annotationManagement} element={<AnnotationManagementPage />} />
            {/* 旧ルート互換: スキーマ管理はテーブルの管理へ、データ投入はデータの管理へ */}
            <Route path="/schema" element={<Navigate to={APP_ROUTES.tableManagement} replace />} />
            <Route path="/data-tools" element={<Navigate to={APP_ROUTES.dataManagement} replace />} />
            {/* 実体は KeepAlivePages で常時マウント。ここでは route match だけ成立させ警告を防ぐ。 */}
            {KEEP_ALIVE_PAGES.map((page) => (
              <Route key={page.path} path={page.path} element={null} />
            ))}
            <Route path={APP_ROUTES.profiles} element={<ProfileManagementPage />} />
            <Route path={APP_ROUTES.ontologyBuild} element={<OntologyBuildPage />} />
            <Route path={APP_ROUTES.glossaryRules} element={<GlossaryRulesPage />} />
            <Route path={APP_ROUTES.globalRules} element={<GlobalRulesPage />} />
            <Route path={APP_ROUTES.feedbackManagement} element={<FeedbackManagementPage />} />
            <Route path={APP_ROUTES.learning} element={<Navigate to={APP_ROUTES.feedbackManagement} replace />} />
            <Route
              path={APP_ROUTES.questionLearning}
              element={<Navigate to={APP_ROUTES.questionClassifierModels} replace />}
            />
            <Route path={APP_ROUTES.questionClassifierModels} element={<QuestionClassifierModelsPage />} />
            <Route path={APP_ROUTES.history} element={<HistoryPage />} />
            <Route path={APP_ROUTES.evaluation} element={<EvaluationPage />} />
            <Route path={APP_ROUTES.settingsOci} element={<SettingsOciRoute />} />
            <Route
              path={APP_ROUTES.settingsUploadStorage}
              element={<SettingsUploadStorageRoute />}
            />
            <Route path={APP_ROUTES.settingsModel} element={<ModelSettingsClient />} />
            <Route path={APP_ROUTES.settingsDatabase} element={<SettingsDatabaseRoute />} />
            <Route
              path={APP_ROUTES.settingsSystemTables}
              element={<SettingsSystemTablesRoute />}
            />
            <Route path={APP_ROUTES.settingsAppearance} element={<AppearanceSettings />} />
            <Route path={APP_ROUTES.securityUsers} element={<SecurityUsersPage />} />
            <Route path={APP_ROUTES.securityRoles} element={<SecurityRolesPage />} />
            <Route path={APP_ROUTES.securityAudit} element={<SecurityAuditPage />} />
            <Route path={APP_ROUTES.securityDeepSec} element={<SecurityDeepSecPage />} />
            <Route
              path={APP_ROUTES.legacyNl2sqlModelLearning}
              element={<Navigate to={`${APP_ROUTES.profiles}#profile-learning`} replace />}
            />
            <Route path="/settings" element={<Navigate to={APP_ROUTES.settingsOci} replace />} />
            <Route path="*" element={<Navigate to={firstAllowedRoute(auth.hasPermission)} replace />} />
          </Routes>
        </Suspense>
      </DatabaseGate>
    </AppLayout>
  );
}

/**
 * 4画面を lazy-mount(初回訪問時のみ mount)し、以後は unmount せず `display` で表示を切替える。
 * 未訪問ページは描画しないので初回ロードでの eager fetch を避けられる。
 */
function KeepAlivePages() {
  const { pathname } = useLocation();
  const auth = useAuth();
  const mounted = useRef(new Set<string>());
  if (
    KEEP_ALIVE_PATHS.has(pathname) &&
    auth.hasPermission(ROUTE_PERMISSIONS[pathname])
  ) {
    mounted.current.add(pathname);
  }

  return (
    <>
      {KEEP_ALIVE_PAGES.filter(
        (page) =>
          mounted.current.has(page.path) && auth.hasPermission(ROUTE_PERMISSIONS[page.path])
      ).map((page) => (
        <div key={page.path} style={{ display: page.path === pathname ? undefined : "none" }}>
          {page.element}
        </div>
      ))}
    </>
  );
}

function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigationType = useNavigationType();
  const mainRef = useRef<HTMLElement | null>(null);
  const setSidebarCollapsed = useUiStore((state) => state.setSidebarCollapsed);

  useCollapseSidebarOnNarrowViewport(setSidebarCollapsed);
  useMainScrollRestoration(mainRef, location, navigationType);

  return (
    <div className="flex">
      <AppSidebar />
      <main
        ref={mainRef}
        className="h-screen min-w-0 flex-1 overflow-y-auto [contain:layout] focus:outline-none"
        aria-label="メイン領域"
        tabIndex={-1}
      >
        {children}
      </main>
    </div>
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
    scroll();
    const animationFrame = window.requestAnimationFrame(scroll);
    return () => window.cancelAnimationFrame(animationFrame);
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

function SettingsOciRoute() {
  return (
    <>
      <PageHeader title={t("nav.settingsOci")} subtitle={t("settings.oci.subtitle")} />
      <OciSettingsClient />
    </>
  );
}

function SettingsUploadStorageRoute() {
  return (
    <>
      <PageHeader
        title={t("nav.settingsUploadStorage")}
        subtitle={t("settings.uploadStorage.subtitle")}
      />
      <UploadStorageSettingsClient />
    </>
  );
}

function SettingsDatabaseRoute() {
  return (
    <>
      <PageHeader title={t("nav.settingsDatabase")} subtitle={t("settings.database.subtitle")} />
      <DatabaseSettingsClient />
    </>
  );
}

function SettingsSystemTablesRoute() {
  return (
    <>
      <PageHeader
        title={t("nav.settingsSystemTables")}
        subtitle={t("settings.systemTables.subtitle")}
      />
      <div className="p-8">
        <SystemTablesCard />
      </div>
    </>
  );
}
