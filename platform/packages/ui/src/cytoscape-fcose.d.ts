// cytoscape-fcose は型を持たない（MIT。拡張の登録だけに使う）。
declare module "cytoscape-fcose" {
  import type { Ext } from "cytoscape";
  const fcose: Ext;
  export default fcose;
}
