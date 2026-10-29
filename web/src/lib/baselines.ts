// In-browser re-roll of the cheap baselines (A0 random, A1 heuristic, A2 MACE-greedy) on results/pools.json.
// Mirrors the PREREG definitions; A3 (RF-UCB) needs a random forest and is not re-rolled here.
import type { PoolRow } from './types';

/** mulberry32: small, fast, seedable. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function shuffled<T>(xs: T[], r: () => number): T[] {
  const a = xs.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(r() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

export type BaselineArm = 'A0' | 'A1' | 'A2';

export function pick(pool: PoolRow[], arm: BaselineArm, seed: number, budget = 40): PoolRow[] {
  const r = rng(seed * 2654435761 + (arm === 'A0' ? 0 : arm === 'A1' ? 1 : 2));
  if (arm === 'A0') return shuffled(pool, r).slice(0, budget);
  // Ties are broken by a seeded shuffle, so the seed matters only where the rule does not decide.
  const base = shuffled(pool, r);
  if (arm === 'A1') {
    // bench/arms.py: OMS and 3.3 <= PLD <= 6 first, then OMS outside the window, then the rest;
    // within a group cheaper metal_cost_tier first, then higher metal-site density (if pools.json carries it).
    const group = (p: PoolRow) => (p.oms && p.pld_A >= 3.3 && p.pld_A <= 6.0 ? 0 : p.oms ? 1 : 2);
    const tier = (p: PoolRow) => p.cost_tier ?? (p as any).metal_cost_tier ?? 3;
    const dens = (p: PoolRow) => (p as any).metal_density ?? 0;
    return base
      .map((p, i) => ({ p, i }))
      .sort((a, b) => group(a.p) - group(b.p) || tier(a.p) - tier(b.p) || dens(b.p) - dens(a.p) || a.i - b.i)
      .slice(0, budget)
      .map((x) => x.p);
  }
  // A2: ascending S_mace, MOFs without a cheap value last.
  const s = (p: PoolRow) => (p.S_mace == null || !Number.isFinite(p.S_mace) ? Infinity : p.S_mace);
  return base
    .map((p, i) => ({ p, i }))
    .sort((a, b) => (s(a.p) === s(b.p) ? a.i - b.i : s(a.p) - s(b.p)))
    .slice(0, budget)
    .map((x) => x.p);
}

export function curveOf(picks: PoolRow[]): number[] {
  const c = [0];
  for (const p of picks) c.push(c[c.length - 1] + (p.hit ? 1 : 0));
  return c;
}

/** Mean hits@budget over many seeds (for context next to a single re-roll). */
export function meanHits(pool: PoolRow[], arm: BaselineArm, seeds = 200, budget = 40): number {
  let s = 0;
  for (let i = 0; i < seeds; i++) s += pick(pool, arm, 100000 + i, budget).filter((p) => p.hit).length;
  return s / seeds;
}
