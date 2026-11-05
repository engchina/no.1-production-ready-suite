import { useLayoutEffect, useRef } from "react";
import { useUpdateNodeInternals } from "@xyflow/react";
import type { GraphNodeDimensions } from "@production-ready/ui/graph";

/** DOMへの実測反映後、接続点も公開APIで再計測する。 */
export function useStructureNodeMeasurements(measurements: ReadonlyMap<string, GraphNodeDimensions>, visibleIds: ReadonlySet<string>) {
  const updateNodeInternals = useUpdateNodeInternals();
  const previous = useRef<ReadonlyMap<string, GraphNodeDimensions>>(new Map());
  useLayoutEffect(() => {
    const current = new Map([...measurements].filter(([id]) => visibleIds.has(id)));
    const changed = [...current].filter(([id, size]) => {
      const before = previous.current.get(id);
      return !before || before.width !== size.width || before.height !== size.height;
    }).map(([id]) => id);
    previous.current = current;
    if (changed.length) updateNodeInternals(changed);
  }, [measurements, visibleIds, updateNodeInternals]);
}
