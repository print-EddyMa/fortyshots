import { useEffect, useState } from 'react';
import { loadIndex, loadManifest, loadPools, loadResults, type Manifest } from './data';
import type { IndexEntry, Pools, Results } from './types';

function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): { data: T | null; error: string | null } {
  const [state, set] = useState<{ data: T | null; error: string | null }>({ data: null, error: null });
  useEffect(() => {
    let dead = false;
    fn().then(
      (data) => !dead && set({ data, error: null }),
      (e) => !dead && set({ data: null, error: String(e?.message ?? e) }),
    );
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export const useResults = () => useAsync<Results>(loadResults);
export const usePools = () => useAsync<Pools>(loadPools);
export const useIndex = () => useAsync<IndexEntry[]>(loadIndex);
export const useManifest = () => useAsync<Manifest | null>(loadManifest);
export { useAsync };
