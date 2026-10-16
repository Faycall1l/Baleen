import { useCallback, useState } from 'react';

import { fetchPath, type PathResponse } from '../api';
import type { GraphNode } from '../types';

/** Path mode: armed from any inspector via PathFromButton; the next selected
 * node completes the pair, fetches the path and opens the path tile. */
export function usePathMode(select: (node: GraphNode | null) => void, openPathTile: () => void) {
  const [pathFrom, setPathFrom] = useState<GraphNode | null>(null);
  const [pathResult, setPathResult] = useState<PathResponse | null>(null);
  const [pathError, setPathError] = useState<string | null>(null);

  const arm = useCallback((node: GraphNode) => {
    setPathResult(null);
    setPathError(null);
    setPathFrom(node);
  }, []);

  const completeOrSelect = (node: GraphNode | null) => {
    if (node && pathFrom && node.id !== pathFrom.id) {
      const from = pathFrom;
      setPathFrom(null);
      setPathError(null);
      fetchPath(from.id, node.id)
        .then((res) => setPathResult(res))
        .catch((err: unknown) => {
          setPathResult(null);
          setPathError(err instanceof Error ? err.message : 'Path fetch failed');
        })
        .finally(openPathTile);
    } else if (node && pathFrom && node.id === pathFrom.id) {
      setPathFrom(null);
    }
    select(node);
  };

  return { pathFrom, setPathFrom, pathResult, pathError, arm, completeOrSelect };
}
