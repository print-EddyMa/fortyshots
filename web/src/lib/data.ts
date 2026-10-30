import { DATA, LIVE } from './config';
import type { IndexEntry, MutantDoc, MutantIndex, Pools, Results, RunEvent, Structure } from './types';

const cache = new Map<string, Promise<unknown>>();

function get<T>(url: string, as: 'json' | 'text' = 'json'): Promise<T> {
  const key = `${as}:${url}`;
  if (!cache.has(key)) {
    const p = fetch(url, { cache: 'no-cache' }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${url}`);
      return as === 'json' ? r.json() : r.text();
    });
    p.catch(() => cache.delete(key));
    cache.set(key, p);
  }
  return cache.get(key) as Promise<T>;
}

export interface Manifest { built: string; sources: Record<string, string> }

export const loadManifest = () => get<Manifest>(`${DATA}/manifest.json`).catch(() => null);
export const loadResults = () => get<Results>(`${DATA}/results/results.json`);
export const loadPools = () => get<Pools>(`${DATA}/results/pools.json`);
export const loadStructure = (id: string) => get<Structure>(`${DATA}/structures/${id}.json`);

export function loadIndex(): Promise<IndexEntry[]> {
  return LIVE ? get<IndexEntry[]>('/api/runs') : get<IndexEntry[]>(`${DATA}/runs/index.json`);
}

export function parseJsonl(text: string): RunEvent[] {
  const out: RunEvent[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    try {
      out.push(JSON.parse(line));
    } catch {
      /* a partial trailing line in a file still being written */
    }
  }
  return out;
}

export async function loadEvents(runId: string): Promise<RunEvent[]> {
  return parseJsonl(await get<string>(`${DATA}/runs/${encodeURIComponent(runId)}/events.jsonl`, 'text'));
}

/** URL of a file inside a run directory (images the agent saw). The live server mounts runs/ at /data/runs. */
export const runAsset = (runId: string, path: string) => `${DATA}/runs/${encodeURIComponent(runId)}/${path}`;

/** Prospective mutants of a run (physics/prospective.py, runs/<id>/mutants/index.json); null when it has none. */
export const loadMutants = (runId: string) => get<MutantIndex>(runAsset(runId, 'mutants/index.json')).catch(() => null);

/** One mutant in viewer format plus its MACE relaxation frames. */
export const loadMutantDoc = (url: string) => get<MutantDoc>(url);

/** Sidecar `mutation` events (runs/<id>/mutations.jsonl): built/invalid results, never part of the ledger. */
export async function loadSidecar(runId: string): Promise<RunEvent[]> {
  try {
    return parseJsonl(await get<string>(runAsset(runId, 'mutations.jsonl'), 'text'));
  } catch {
    return [];
  }
}

/** Tail a run over SSE. Each message's data is one ledger line. Returns an unsubscribe function. */
export function subscribeLive(
  runId: string,
  onEvent: (e: RunEvent) => void,
  onStatus: (s: 'open' | 'error' | 'closed') => void,
): () => void {
  // The server names its messages `event` (one ledger line each) and sends `end` after the summary.
  const es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  const handle = (m: MessageEvent) => {
    try {
      onEvent(JSON.parse(m.data));
    } catch {
      /* ignore keep-alives */
    }
  };
  es.onopen = () => onStatus('open');
  es.onerror = () => onStatus(es.readyState === EventSource.CLOSED ? 'closed' : 'error');
  es.addEventListener('event', handle as EventListener);
  es.onmessage = handle;
  es.addEventListener('end', () => {
    es.close();
    onStatus('closed');
  });
  return () => es.close();
}

export async function postHuman(runId: string, body: { action: 'approve' | 'reject'; ids: string[]; note: string; round?: number }) {
  const r = await fetch(`/api/runs/${encodeURIComponent(runId)}/human`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json().catch(() => ({}));
}

/** Live only: the batch the run is waiting on ({round, ids}) or {} when nothing is pending. */
export async function loadPending(runId: string): Promise<{ round?: number; ids?: string[] }> {
  const r = await fetch(`/api/runs/${encodeURIComponent(runId)}/pending`, { cache: 'no-store' });
  return r.ok ? r.json() : {};
}

/** Live only: current network state of the machine running the lab. */
export async function loadNetwork(): Promise<string | null> {
  try {
    const r = await fetch('/api/network', { cache: 'no-store' });
    return r.ok ? ((await r.json()).network ?? null) : null;
  } catch {
    return null;
  }
}

/** The landing showcase: an entry with `showcase: true`, else the first A5 run on P1, else the first A5 run. */
export function pickShowcase(index: IndexEntry[]): IndexEntry | null {
  return (
    index.find((r) => r.showcase) ??
    index.find((r) => r.arm === 'A5' && r.pool === 'P1') ??
    index.find((r) => r.arm === 'A5') ??
    index[0] ??
    null
  );
}

export const hasMockRuns = (index: IndexEntry[] | null) => !!index?.some((r) => r.mode === 'mock' || r.run_id.startsWith('mock-'));

/** True when anything on screen is not from final, recorded data. */
export function isPreliminary(results: Results | null, index: IndexEntry[] | null): boolean {
  if (results && results.status !== 'final') return true;
  return !!index?.some((r) => r.mode === 'mock' || r.run_id.startsWith('mock-'));
}
