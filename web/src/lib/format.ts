export const DASH = '—';

/** Signed fixed number with a real minus sign. */
export function num(v: number | null | undefined, d = 2, signed = false): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  const s = Math.abs(v).toFixed(d);
  if (v < 0 && Number(s) !== 0) return `−${s}`;
  return signed && Number(s) !== 0 ? `+${s}` : s;
}

/** Headline-friendly count: integers stay integers, otherwise one decimal. */
export function count(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  return Math.abs(v - Math.round(v)) < 0.05 ? String(Math.round(v)) : v.toFixed(1);
}

export function pct(v: number | null | undefined, d = 0): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  return `${(v * 100).toFixed(d)}%`;
}

export function dur(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return DASH;
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h) return `${h}h ${String(m % 60).padStart(2, '0')}m`;
  return `${m}m ${String(s % 60).padStart(2, '0')}s`;
}

export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

export function hhmmss(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toISOString().slice(11, 19);
}

export function dateShort(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  const mon = d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' });
  return `${mon} ${d.getUTCDate()}, ${d.toISOString().slice(11, 16)} UTC`;
}

export function ci(lo: number | null | undefined, hi: number | null | undefined, d = 1): string {
  if (lo == null || hi == null) return DASH;
  return `${num(lo, d)}–${num(hi, d)}`;
}

/** Element-count formula as plain text, e.g. for titles and captions. */
export function formula(f: string | null | undefined): string {
  return f || DASH;
}

export const ARM_LABEL: Record<string, string> = {
  A0: 'Random',
  A1: 'Heuristic',
  A2: 'MACE-greedy',
  A3: 'RF-UCB',
  A4: 'Glimmer zero-shot',
  A5: 'Glimmer agent',
  A6: 'Agent, no surrogate',
  L: 'Leak probe',
  L2: 'Leak probe 2',
};

export const ROLE_LABEL: Record<string, string> = {
  director: 'Director',
  proposer: 'Proposer',
  critic: 'Critic',
  reporter: 'Reporter',
  chemist: 'Chemist',
  system: 'Harness',
  human: 'Human',
};

export function shortSha(s: string | null | undefined, n = 7): string {
  if (!s) return DASH;
  return /^[0-9a-f]+$/i.test(s) && s.length > n ? s.slice(0, n) : s;
}
