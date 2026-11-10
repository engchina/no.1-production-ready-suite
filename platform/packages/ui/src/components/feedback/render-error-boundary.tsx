import { Component, type ReactNode } from "react";
import { RefreshCw } from "lucide-react";

import { logBrowserDiagnostic, type BrowserDiagnostic } from "../../lib/diagnostic-log";
import { PageBody } from "../app-shell/PageBody";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";

export interface RenderErrorBoundaryLabels {
  /** 名前（`label`）が無いときの見出し。 */
  title: string;
  /** 名前（`label`）があるときの見出し。 */
  titleWithLabel: (label: string) => string;
  description: string;
  retry: string;
}

/** 3 製品で同じ文にするため `packages/ui` が持つ（`DEFAULT_PROCESS_FAILURE_LABELS` と同じ）。 */
export const DEFAULT_RENDER_ERROR_BOUNDARY_LABELS: RenderErrorBoundaryLabels = {
  title: "この項目を表示できません",
  titleWithLabel: (label) => `「${label}」を表示できません`,
  description:
    "画面の描画中に問題が発生しました。再試行しても解消しない場合は、ページを再読み込みしてください。他の項目は引き続き利用できます。",
  retry: "再試行",
};

export interface RenderErrorBoundaryProps {
  /** 隔離する範囲。描画中に throw してもこの範囲だけがエラーの表示へ替わる。 */
  children: ReactNode;
  /** 診断のログの `service_name`。 */
  serviceName: BrowserDiagnostic["serviceName"];
  /** エラーの表示に出す範囲の名前（例: カードの見出し）。省略すると汎用の見出し。 */
  label?: string;
  labels?: Partial<RenderErrorBoundaryLabels>;
  /**
   * `page`: route の本文を包む境界。エラーの表示を本文の余白（`PageBody`）の中に出す。
   * `inline`（既定）: カードなどを包む境界。置かれた場所にそのまま出す。
   */
  placement?: "inline" | "page";
}

interface RenderErrorBoundaryState {
  error: Error | null;
}

/**
 * 描画の例外を受け止める境界（#4800。RAG の CardErrorBoundary を移した）。
 *
 * React は境界が無いと、throw した部品の祖先をルートまで unmount する。route の本文を包めばサイドナビを残して本文だけを
 * エラーの表示（再試行つき）に替え、独立したカードを包めば 1 枚の例外でほかのカードを消さない。
 * 別の画面へ移ったら状態を解くには、呼ぶ側が `key`（`location.pathname` など）を渡す。
 * 対象は描画中の例外だけで、API のエラーは各画面が自分のエラーの表示で扱う。
 */
export class RenderErrorBoundary extends Component<RenderErrorBoundaryProps, RenderErrorBoundaryState> {
  state: RenderErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): RenderErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error): void {
    logBrowserDiagnostic("ERROR", { event: "card_render_failed", serviceName: this.props.serviceName, error });
  }

  private handleReset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    const { label } = this.props;
    const labels = { ...DEFAULT_RENDER_ERROR_BOUNDARY_LABELS, ...this.props.labels };
    const banner = (
      <Banner
        severity="danger"
        title={label ? labels.titleWithLabel(label) : labels.title}
        action={
          <Button variant="secondary" size="sm" onClick={this.handleReset} icon={RefreshCw}>
            {labels.retry}
          </Button>
        }
      >
        {labels.description}
      </Banner>
    );
    return this.props.placement === "page" ? (
      <PageBody data-render-error-boundary="">{banner}</PageBody>
    ) : (
      <div data-render-error-boundary="">{banner}</div>
    );
  }
}
