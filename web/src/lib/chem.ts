// Chemistry helpers shared by the 3D views, the atlas and the periodic table. No data lives here, only
// element facts (symbols, table positions, van der Waals radii) and periodic geometry.
import type { Structure } from './types';

export const SYMBOLS =
  'X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr'.split(
    ' ',
  );

export const NAMES: Record<string, string> = {
  Li: 'lithium', Be: 'beryllium', Na: 'sodium', Mg: 'magnesium', Al: 'aluminium', K: 'potassium', Ca: 'calcium', Sc: 'scandium',
  Ti: 'titanium', V: 'vanadium', Cr: 'chromium', Mn: 'manganese', Fe: 'iron', Co: 'cobalt', Ni: 'nickel', Cu: 'copper', Zn: 'zinc',
  Ga: 'gallium', Ge: 'germanium', Rb: 'rubidium', Sr: 'strontium', Y: 'yttrium', Zr: 'zirconium', Nb: 'niobium', Mo: 'molybdenum',
  Tc: 'technetium', Ru: 'ruthenium', Rh: 'rhodium', Pd: 'palladium', Ag: 'silver', Cd: 'cadmium', In: 'indium', Sn: 'tin', Sb: 'antimony',
  Cs: 'caesium', Ba: 'barium', La: 'lanthanum', Ce: 'cerium', Pr: 'praseodymium', Nd: 'neodymium', Pm: 'promethium', Sm: 'samarium',
  Eu: 'europium', Gd: 'gadolinium', Tb: 'terbium', Dy: 'dysprosium', Ho: 'holmium', Er: 'erbium', Tm: 'thulium', Yb: 'ytterbium',
  Lu: 'lutetium', Hf: 'hafnium', Ta: 'tantalum', W: 'tungsten', Re: 'rhenium', Os: 'osmium', Ir: 'iridium', Pt: 'platinum', Au: 'gold',
  Hg: 'mercury', Tl: 'thallium', Pb: 'lead', Bi: 'bismuth', Th: 'thorium', U: 'uranium', Np: 'neptunium', Pu: 'plutonium',
};

/** Metals, for "nearest metal" in the binding-site view (alkali, alkaline earth, transition, post-transition, f-block). */
export function isMetal(z: number): boolean {
  return (
    z === 3 || z === 4 || (z >= 11 && z <= 13) || (z >= 19 && z <= 31) || (z >= 37 && z <= 50) || (z >= 55 && z <= 84) || z >= 87
  );
}

/** [row, column] in the 18-column table (1-based); f-block on rows 9 and 10 starting at column 3. */
export function tablePos(z: number): [number, number] | null {
  if (z === 1) return [1, 1];
  if (z === 2) return [1, 18];
  if (z <= 10) return [2, z <= 4 ? z - 2 : z + 8];
  if (z <= 18) return [3, z <= 12 ? z - 10 : z];
  if (z <= 36) return [4, z - 18];
  if (z <= 54) return [5, z - 36];
  if (z >= 57 && z <= 71) return [9, z - 57 + 3];
  if (z >= 89 && z <= 103) return [10, z - 89 + 3];
  if (z <= 56) return [6, z - 54];
  if (z <= 86) return [6, z - 86 + 18];
  if (z <= 88) return [7, z - 86];
  return [7, z - 118 + 18];
}

const VDW: Record<string, number> = { H: 1.2, B: 1.92, C: 1.7, N: 1.55, O: 1.52, F: 1.47, Si: 2.1, P: 1.8, S: 1.8, Cl: 1.75, Br: 1.85, I: 1.98 };
export const vdw = (sym: string) => VDW[sym] ?? 2.0;

type V3 = [number, number, number];

export function inv3(m: number[][]): number[][] {
  const [a, b, c] = m;
  const det = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]);
  const r = [
    [b[1] * c[2] - b[2] * c[1], a[2] * c[1] - a[1] * c[2], a[1] * b[2] - a[2] * b[1]],
    [b[2] * c[0] - b[0] * c[2], a[0] * c[2] - a[2] * c[0], a[2] * b[0] - a[0] * b[2]],
    [b[0] * c[1] - b[1] * c[0], a[1] * c[0] - a[0] * c[1], a[0] * b[1] - a[1] * b[0]],
  ];
  return r.map((row) => row.map((v) => v / det));
}

/** Row vector times matrix (positions are rows; cell rows are lattice vectors). */
export const mul = (v: number[], m: number[][]): V3 => [
  v[0] * m[0][0] + v[1] * m[1][0] + v[2] * m[2][0],
  v[0] * m[0][1] + v[1] * m[1][1] + v[2] * m[2][1],
  v[0] * m[0][2] + v[1] * m[1][2] + v[2] * m[2][2],
];

export interface Contact {
  /** CO2 atom index in the structure. */
  from: number;
  /** Framework atom index in the structure. */
  to: number;
  /** Where the framework atom (or its periodic image) sits relative to the CO2 atom, Cartesian. */
  toPos: V3;
  /** True when the nearest copy is a periodic image, not the atom as drawn. */
  image: boolean;
  d: number;
  fromSym: string;
  toSym: string;
  metal: boolean;
}

/**
 * The binding site at a geometry (default: DFT-relaxed): for each CO2 atom its nearest framework atom, plus the
 * nearest metal to the molecule when that is a different atom and within `metalCut` A. Minimum image across the
 * periodic cell, so a contact can reach into the neighbouring cell.
 */
export function bindingContacts(s: Structure, geom: 'relaxed' | 'initial' = 'relaxed', metalCut = 5.0): Contact[] {
  const P = geom === 'relaxed' && s.pos_relaxed?.length ? s.pos_relaxed : s.pos;
  const inv = inv3(s.cell);
  const ads = new Set(s.ads);
  const near = (i: number, j: number) => {
    const d = [P[j][0] - P[i][0], P[j][1] - P[i][1], P[j][2] - P[i][2]];
    const f = mul(d, inv);
    const r = f.map((x) => Math.round(x));
    const c = mul([f[0] - r[0], f[1] - r[1], f[2] - r[2]], s.cell);
    const to: V3 = [P[i][0] + c[0], P[i][1] + c[1], P[i][2] + c[2]];
    const moved = Math.hypot(to[0] - P[j][0], to[1] - P[j][1], to[2] - P[j][2]) > 1e-3;
    return { d: Math.hypot(c[0], c[1], c[2]), to, image: moved };
  };
  const out: Contact[] = [];
  let bestMetal: Contact | null = null;
  for (const i of s.ads) {
    let best: Contact | null = null;
    s.numbers.forEach((z, j) => {
      if (ads.has(j)) return;
      const n = near(i, j);
      const c: Contact = {
        from: i, to: j, toPos: n.to, image: n.image, d: n.d, fromSym: SYMBOLS[s.numbers[i]], toSym: SYMBOLS[z] ?? 'X', metal: isMetal(z),
      };
      if (!best || n.d < best.d) best = c;
      if (c.metal && (!bestMetal || n.d < bestMetal.d)) bestMetal = c;
    });
    if (best) out.push(best);
  }
  const m = bestMetal as Contact | null;
  if (m && m.d <= metalCut && !out.some((c) => c.to === m.to && c.from === m.from)) {
    // keep only one line to that metal: drop a weaker same-target line if any
    out.push(m);
  }
  return out.sort((a, b) => a.d - b.d);
}

/** Hill-ish reduced formula of the framework (without the adsorbate). */
export function frameworkFormula(s: Structure): string {
  const ads = new Set(s.ads);
  const counts: Record<string, number> = {};
  s.numbers.forEach((z, i) => {
    if (ads.has(i)) return;
    const k = SYMBOLS[z] ?? 'X';
    counts[k] = (counts[k] ?? 0) + 1;
  });
  const g =
    Object.values(counts).reduce((a, b) => {
      while (b) [a, b] = [b, a % b];
      return a;
    }, 0) || 1;
  const keys = Object.keys(counts).sort((a, b) => (a === 'C' ? -1 : b === 'C' ? 1 : a === 'H' ? -1 : b === 'H' ? 1 : a.localeCompare(b)));
  return keys.map((k) => `${k}${counts[k] / g > 1 ? counts[k] / g : ''}`).join('');
}
