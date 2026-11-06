import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GraphNodeDimensions } from "@production-ready/ui/graph";
import { useStructureNodeMeasurements } from "./use-structure-node-measurements";

const {update, phase} = vi.hoisted(() => ({
  update: vi.fn(),
  phase: {effect: (() => {}) as () => void, previous: {current: new Map<string, {width:number;height:number}>()}},
}));
vi.mock("react", () => ({
  useRef: () => phase.previous,
  useLayoutEffect: (effect: () => void) => {phase.effect=effect;},
}));
vi.mock("@xyflow/react", () => ({useUpdateNodeInternals: () => update}));
function Probe(sizes: ReadonlyMap<string, GraphNodeDimensions>, ids=new Set(["a","b"])) {
  useStructureNodeMeasurements(sizes,ids);
  // 実際のDOM計測は既存Playwrightで検証し、ここはcommit後の要求の契約を固定する。
  phase.effect();
}
beforeEach(() => {update.mockClear();phase.previous.current=new Map();});
describe("構造節点の実測後の接続点", () => {
  it("未測定は補完せず、計測した新規節点だけをDOM反映後に再計測する", () => {
    Probe(new Map());expect(update).not.toHaveBeenCalled();
    Probe(new Map([["a",{width:220,height:76}]]));expect(update).toHaveBeenLastCalledWith(["a"]);
    Probe(new Map([["a",{width:220,height:76}],["b",{width:220,height:80}]]));expect(update).toHaveBeenLastCalledWith(["b"]);
    expect(update).toHaveBeenCalledTimes(2);
  });
  it("同じ寸法や選択再描画では再計測を繰り返さない", () => {
    Probe(new Map([["a",{width:220,height:76}]]));
    Probe(new Map([["a",{width:220,height:76}]]));expect(update).toHaveBeenCalledTimes(1);
  });
  it("実際の寸法変更は再計測する", () => {
    Probe(new Map([["a",{width:220,height:76}]]));
    Probe(new Map([["a",{width:220,height:100}]]));expect(update).toHaveBeenLastCalledWith(["a"]);expect(update).toHaveBeenCalledTimes(2);
  });
  it("非表示・削除済み節点には要求せず、再表示時には接続点を確かめる", () => {
    const sizes=new Map([["a",{width:220,height:76}]]);
    Probe(sizes,new Set());expect(update).not.toHaveBeenCalled();
    Probe(sizes);Probe(sizes,new Set());expect(update).toHaveBeenCalledTimes(1);
    Probe(sizes);expect(update).toHaveBeenLastCalledWith(["a"]);expect(update).toHaveBeenCalledTimes(2);
  });
});
