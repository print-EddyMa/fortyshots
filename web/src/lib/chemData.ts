// Loaders for the chemistry artifacts written by bench/atlas.py and bench/chem.py (copied to docs/data/results/).
import { DATA } from './config';

const cache = new Map<string, Promise<any>>();
function get<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    const p = fetch(`${DATA}/${path}`, { cache: 'no-cache' }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${path}`);
      return r.json();
    });
    p.catch(() => cache.delete(path));
    cache.set(path, p);
  }
  return cache.get(path) as Promise<T>;
}

export interface HeroData {
  id: string;
  rule: string;
  pool: string;
  formula: string;
  metals: string;
  E_CO2: number;
  E_H2O: number;
  lcd_A: number;
  pld_A: number;
  void_fraction: number;
  pore: { n: [number, number, number]; scale: number; probe_A: number; geometry: string; radii: string; data: string };
}

export interface AtlasData {
  generated: string;
  method: string;
  features: string[];
  n: number;
  points: { id: string[]; pool: string[]; x: number[]; y: number[]; hit: boolean[]; metals: string[]; E_CO2: number[]; E_H2O: number[]; pld: number[] };
  islands: { metal: string; n: number; x: number; y: number; spread: number }[];
  paths: { arms: Record<string, string>; seed: number; by_pool: Record<string, Record<string, string[]>> };
}

export interface DdecData {
  source: string;
  units: string;
  match: string;
  n: number;
  charges: Record<string, (number | null)[]>;
}

export const loadHero = () => get<HeroData>('results/hero.json');
export const loadAtlas = () => get<AtlasData>('results/atlas.json');
export const loadDdec = () => get<DdecData>('results/ddec.json');
