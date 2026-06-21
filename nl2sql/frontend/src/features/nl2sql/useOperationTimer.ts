import { useEffect, useState } from "react";

import { elapsedSecondsSince } from "./operationTiming";

export { formatElapsed } from "./operationTiming";

export function useOperationTimer(active: boolean, startedAtMs: number | null) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  useEffect(() => {
    if (!active || !startedAtMs) {
      setElapsedSeconds(0);
      return undefined;
    }

    const update = () => {
      setElapsedSeconds(elapsedSecondsSince(startedAtMs));
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [active, startedAtMs]);

  return elapsedSeconds;
}
