// @engchina/production-ready-ui — 公開 API バレル
//
// デザイントークン CSS は別 export（"@engchina/production-ready-ui/tokens.css"）で取り込む。

// --- lib ---
export { cn } from "./lib/utils";
export {
  INFORMATION_LIST_VISIBLE_ROWS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  INFORMATION_TABLE_FIXED_VISIBLE_ROWS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_LIST_ROW_CLASS,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_COMPACT_LIST_FIVE_ROW_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
} from "./lib/list-density";

// --- UI primitives ---
export { Button, buttonVariants, type ButtonProps, type ButtonVariantToneProps } from "./components/ui/button";
export { Tooltip, type TooltipProps, type TooltipPlacement } from "./components/ui/tooltip";
export { Tabs, TabPanel, type TabItem, type TabsProps } from "./components/ui/tabs";
export { TextField, type TextFieldProps, type TextFieldSize } from "./components/ui/text-field";
export {
  SecretField,
  type SecretFieldProps,
  type SecretFieldClearOption,
} from "./components/ui/secret-field";
export { RequiredBadge } from "./components/ui/required-badge";
export {
  ExecutionConfirmationField,
  executionConfirmationStatus,
  DEFAULT_EXECUTION_CONFIRMATION_LABELS,
  type ExecutionConfirmationFieldProps,
  type ExecutionConfirmationLabels,
  type ExecutionConfirmationStatus,
} from "./components/ui/execution-confirmation-field";
export { Spinner, type SpinnerProps } from "./components/ui/spinner";
export {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "./components/ui/card";
export {
  Skeleton,
  TableSkeleton,
  ListSkeleton,
  FormSkeleton,
  SKELETON_CLASS,
  type SkeletonRows,
  type TableSkeletonProps,
  type ListSkeletonProps,
  type FormSkeletonProps,
} from "./components/ui/skeleton";
export { Switch, type SwitchProps } from "./components/ui/switch";
export { ToggleChip } from "./components/ui/toggle-chip";
export { FieldError } from "./components/ui/field-error";
export { FormStatus } from "./components/ui/form-status";
export {
  SelectField,
  type SelectFieldOption,
} from "./components/ui/select-field";
export { Banner } from "./components/ui/banner";
export { MessageText, type MessageTextProps } from "./components/ui/message-text";
export { Toaster, type ToasterProps } from "./components/ui/toast";
export {
  ConfirmProvider,
  useConfirm,
  type ConfirmOptions,
  type ConfirmDefaultLabels,
} from "./components/ui/confirm-dialog";
export { ContentActionBar } from "./components/ui/content-action-bar";
export {
  FormActionBar,
  entityActionToFormAction,
  type FormActionBarProps,
  type FormActionDescriptor,
} from "./components/ui/form-action-bar";
export {
  DisclosureChevron,
  type DisclosureChevronProps,
} from "./components/ui/disclosure-chevron";
export {
  Disclosure,
  type DisclosureProps,
  type DisclosureSize,
  type DisclosureSurface,
  type DisclosureTone,
  type DisclosureVariant,
} from "./components/ui/disclosure";
export {
  FloatingActionMenu,
  type FloatingMenuPlacement,
} from "./components/ui/floating-menu";
export { restoreMenuTriggerFocus } from "./lib/menu-focus";
export {
  BulkSelectionActions,
  type BulkSelectionActionsProps,
} from "./components/ui/bulk-selection-actions";
export {
  ClearActionButton,
  type ClearActionButtonProps,
} from "./components/ui/clear-action-button";
export {
  toneIcon,
  toneText,
  toneSurface,
  toneRole,
  type FeedbackTone,
} from "./components/ui/feedback-tone";

// --- feedback / state views ---
export {
  LoadingState,
  ErrorState,
  EmptyState,
} from "./components/feedback/state-views";
export {
  ProcessingIndicator,
  TimedLoadingState,
  useOperationTiming,
  DEFAULT_PROCESSING_LABELS,
  type ProcessingIndicatorProps,
  type TimedLoadingStateProps,
  type ProcessingPlacement,
  type ProcessingActivityIcon,
  type ProcessingLabels,
  type UseOperationTimingOptions,
  type OperationTiming,
} from "./components/feedback/processing-state";
export {
  operationTimestampMs,
  elapsedMsSince,
  elapsedMsBetween,
  formatElapsedClock,
  type OperationTimestamp,
} from "./lib/operation-timing";
export {
  BlockedPageNotice,
  type BlockedPageNoticeProps,
} from "./components/feedback/blocked-page-notice";
export {
  ActionResultRegion,
  type ActionResultRegionProps,
} from "./components/feedback/action-result-region";

// --- data ---
export {
  StatusBadge,
  type StatusVariant,
} from "./components/data/status-badge";
export {
  Pagination,
  usePagination,
  offsetPagination,
  offsetForPage,
  DEFAULT_PAGE_SIZE,
  type PaginationProps,
  type PaginationRange,
  type UsePaginationOptions,
} from "./components/data/pagination";
export {
  PagedDataTable,
  type PagedDataTableProps,
  type PaginationLabels,
} from "./components/data/paged-data-table";
export {
  RowTitleButton,
  type RowTitleButtonProps,
  type RowTitleButtonMaxLines,
} from "./components/data/row-title-button";
export {
  RowActionMenu,
  ObjectActionBar,
  splitObjectActions,
  visibleEntityActions,
  type EntityAction,
  type EntityActionTone,
} from "./components/data/object-actions";
export {
  DataTable,
  type DataTableProps,
  type DataTableColumn,
  type DataTableSort,
  type DataTableRowProps,
  type DataTableVisibleRows,
  type SortDirection,
} from "./components/data/data-table";

// --- app shell / layout ---
export { AppShell } from "./components/app-shell/AppShell";
export {
  useSidebarCollapsed,
  DEFAULT_NAV_DRAWER_LABELS,
  NAV_DRAWER_QUERY,
  type NavDrawerLabels,
} from "./components/app-shell/nav-drawer";
export { Sidebar, SidebarAccountFooter, type SidebarFooterAction, type SidebarProps } from "./components/app-shell/Sidebar";
export { PageHeader, type PageHeaderAction } from "./components/app-shell/PageHeader";
export { PageBody, Section } from "./components/app-shell/PageBody";
export {
  FixedSplitPane,
  DEFAULT_FIXED_SPLIT_PANE_LABELS,
  type FixedSplitPaneProps,
  type FixedSplitPaneLabels,
} from "./components/app-shell/fixed-split-pane";
export * from "./lib/fixed-split-pane";
export {
  Breadcrumbs,
  type BreadcrumbItem,
} from "./components/app-shell/Breadcrumbs";

// --- navigation types ---
export type {
  NavItem,
  NavSection,
  NavLinkComponent,
  SidebarLabels,
} from "./navigation/types";

// --- stores ---
export {
  createUiStore,
  type UiState,
  type CreateUiStoreOptions,
  type ThemePreference,
} from "./store/ui-store";
export { initTheme, applyTheme, resolveDark, type ThemeStore } from "./theme";
export {
  useToastStore,
  toast,
  type ToastItem,
  type ToastOptions,
  type ToastAction,
} from "./store/toast-store";
