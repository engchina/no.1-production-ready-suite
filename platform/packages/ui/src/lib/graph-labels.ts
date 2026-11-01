/** 図の共通操作を読み上げる日本語。製品の種類名やノード名は各製品のi18nから渡す。 */
export const GRAPH_CANVAS_JA = {
  "node.a11yDescription.default": "Enter または Space で選択します。選択後、矢印キーで移動できます。",
  "node.a11yDescription.keyboardDisabled": "Enter または Space で選択します。",
  "node.a11yDescription.ariaLiveMessage": ({ direction, x, y }: { direction: string; x: number; y: number }) => {
    const names: Record<string, string> = { left: "左", right: "右", up: "上", down: "下" };
    return `ノードを${names[direction] ?? direction}へ移動しました。位置 X ${x}、Y ${y}。`;
  },
  "edge.a11yDescription.default": "Enter または Space で関係を選択します。",
  "controls.ariaLabel": "図の操作",
  "controls.zoomIn.ariaLabel": "拡大",
  "controls.zoomOut.ariaLabel": "縮小",
  "controls.fitView.ariaLabel": "全体表示",
  "controls.interactive.ariaLabel": "図の操作を切り替える",
  "minimap.ariaLabel": "グラフの小地図",
  "handle.ariaLabel": "接続点",
};
