// Periodic table of hit rates: for every metal present in the pools, how many MOFs contain it and how many of
// those are DFT hits (results/pools.json). Sequential grey-to-accent by hit rate; numbers in every cell.
import { useMemo, useState } from 'react';
import { NAMES, SYMBOLS, tablePos } from '../lib/chem';
import type { PoolRow } from '../lib/types';
import '../styles/chem.css';

interface Stat {
  z: number;
  sym: string;
  n: number;
  hits: number;
}

const MIN_N = 10; // below this the rate is too noisy to colour

function hexToRgb(h: string): [number, number, number] {
  const m = h.replace('#', '');
  const v = m.length === 3 ? m.split('').map((c) => c + c).join('') : m.slice(0, 6);
  return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)) as [number, number, number];
}

function mix(a: string, b: string, t: number): string {
  const A = hexToRgb(a);
  const B = hexToRgb(b);
  return `rgb(${A.map((x, i) => Math.round(x + (B[i] - x) * t)).join(',')})`;
}

const LOW = '#ececec';
const HIGH = '#c2410c';
/** Perceived lightness of the mixed colour, to pick black or white text. */
const dark = (t: number) => t > 0.55;

export function PeriodicTable({ rows, label }: { rows: PoolRow[]; label: string }) {
  const [sel, setSel] = useState<string | null>(null);
  const { stats, maxRate, base } = useMemo(() => {
    const m = new Map<string, Stat>();
    let hits = 0;
    for (const r of rows) {
      if (r.hit) hits++;
      const metals = new Set((r.metals ?? '').split(',').filter(Boolean));
      for (const sym of metals) {
        const z = SYMBOLS.indexOf(sym);
        if (z <= 0) continue;
        const s = m.get(sym) ?? { z, sym, n: 0, hits: 0 };
        s.n++;
        if (r.hit) s.hits++;
        m.set(sym, s);
      }
    }
    const rated = [...m.values()].filter((s) => s.n >= MIN_N);
    const maxRate = Math.max(0.01, ...rated.map((s) => s.hits / s.n));
    return { stats: m, maxRate, base: rows.length ? hits / rows.length : 0 };
  }, [rows]);

  const cells: JSX.Element[] = [];
  for (let z = 1; z <= 103; z++) {
    const pos = tablePos(z);
    if (!pos) continue;
    const [row, col] = pos;
    const sym = SYMBOLS[z];
    const s = stats.get(sym);
    const gridRow = row; // row 8 stays empty: the gap before the f-block
    const style: React.CSSProperties = { gridRow, gridColumn: col };
    if (!s) {
      cells.push(
        <div key={z} className="pt-cell" style={style} aria-hidden>
          <span className="sym">{sym}</span>
        </div>,
      );
      continue;
    }
    const rate = s.hits / s.n;
    const t = s.n >= MIN_N ? Math.min(1, rate / maxRate) : 0;
    const bg = s.n >= MIN_N ? mix(LOW, HIGH, t) : 'transparent';
    const name = NAMES[sym] ?? sym;
    const desc = `${name}: ${s.n} MOF${s.n === 1 ? '' : 's'} contain it, ${s.hits} hit${s.hits === 1 ? '' : 's'}${s.n >= MIN_N ? `, hit rate ${(rate * 100).toFixed(1)}%` : ', too few to rate'}`;
    cells.push(
      <button
        key={z}
        type="button"
        className={`pt-cell present${dark(t) ? ' light-text' : ''}`}
        style={{ ...style, background: bg, borderColor: s.n >= MIN_N ? 'transparent' : 'var(--rule-strong)', borderStyle: s.n >= MIN_N ? 'solid' : 'dashed' }}
        aria-label={desc}
        aria-pressed={sel === sym}
        title={desc}
        onClick={() => setSel(sel === sym ? null : sym)}
        onMouseEnter={() => setSel(sym)}
        onFocus={() => setSel(sym)}
      >
        <span className="sym">{sym}</span>
        <span className="n">
          {s.n}
          {s.hits > 0 ? `·${s.hits}` : ''}
        </span>
      </button>,
    );
  }
  const S = sel ? stats.get(sel) : null;
  const top = [...stats.values()].filter((s) => s.n >= MIN_N).sort((a, b) => b.hits / b.n - a.hits / a.n)[0];
  const shown = S ?? top;

  return (
    <figure style={{ margin: 0 }} aria-label={label}>
      <div className="ptable" role="group" aria-label={label}>
        {cells}
        <div className="gap" style={{ gridRow: 8 }} aria-hidden />
      </div>
      <div className="pt-scale" aria-hidden>
        <span>hit rate</span>
        <span className="mono">0%</span>
        <span className="bar">
          {Array.from({ length: 12 }, (_, i) => (
            <i key={i} style={{ background: mix(LOW, HIGH, i / 11) }} />
          ))}
        </span>
        <span className="mono">{(maxRate * 100).toFixed(0)}%</span>
        <span className="faint">· dashed: fewer than {MIN_N} MOFs, not rated · cell: MOFs · hits</span>
      </div>
      <p className="pt-detail" aria-live="polite">
        {shown ? (
          <>
            <span className="mono">{shown.sym}</span> {NAMES[shown.sym] ?? ''} · <span className="mono">{shown.n}</span> MOFs contain it ·{' '}
            <span className="mono">{shown.hits}</span> hit{shown.hits === 1 ? '' : 's'}
            {shown.n >= MIN_N ? (
              <>
                {' '}
                · hit rate {((shown.hits / shown.n) * 100).toFixed(1)}%, {((shown.hits / shown.n) / (base || 1)).toFixed(1)}× the
                base rate of {(base * 100).toFixed(1)}%
              </>
            ) : (
              ' · too few to rate'
            )}
            {!S && <span className="faint"> · highest rated hit rate; hover or tap a cell</span>}
          </>
        ) : (
          'Hover or tap a metal.'
        )}
      </p>
    </figure>
  );
}
