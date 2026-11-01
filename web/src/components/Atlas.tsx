// Chemical-space atlas: every MOF of the six pools placed by a t-SNE of label-free descriptors and MACE values
// (bench/atlas.py -> results/atlas.json), with a campaign's 40 purchases drawn on top as a numbered path.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { AtlasData } from '../lib/chemData';
import { reducedMotion } from '../lib/mol3d';
import { num } from '../lib/format';
import { useWidth } from './Charts';
import '../styles/chem.css';

export interface AtlasSeq {
  key: string;
  label: string;
  /** Purchase order. */
  ids: string[];
  note?: string;
}

const STEP = 150; // ms per purchase
const ROUND_GAP = 800; // ms pause between rounds of ten
const FLASH = 1100;

const appearAt = (i: number, batch: number) => i * STEP + Math.floor(i / batch) * ROUND_GAP;

interface Colors {
  fg: string;
  muted: string;
  faint: string;
  rule: string;
  ruleStrong: string;
  accent: string;
  bg: string;
}

function readColors(el: Element): Colors {
  const c = getComputedStyle(el);
  const v = (n: string, f: string) => c.getPropertyValue(n).trim() || f;
  return {
    fg: v('--fg', '#0a0a0a'),
    muted: v('--fg-muted', '#666'),
    faint: v('--fg-faint', '#737373'),
    rule: v('--rule', '#e5e5e5'),
    ruleStrong: v('--rule-strong', '#d4d4d4'),
    accent: v('--accent', '#c2410c'),
    bg: v('--bg', '#fafafa'),
  };
}

interface Geo {
  w: number;
  h: number;
  px: (i: number) => number;
  py: (i: number) => number;
}

function useGeo(atlas: AtlasData, w: number, maxH: number): Geo {
  return useMemo(() => {
    const { x, y } = atlas.points;
    let x0 = Infinity;
    let x1 = -Infinity;
    let y0 = Infinity;
    let y1 = -Infinity;
    for (let i = 0; i < x.length; i++) {
      x0 = Math.min(x0, x[i]);
      x1 = Math.max(x1, x[i]);
      y0 = Math.min(y0, y[i]);
      y1 = Math.max(y1, y[i]);
    }
    const pad = 14;
    const aspect = (y1 - y0) / (x1 - x0 || 1);
    const h = Math.min(maxH, Math.round((w - 2 * pad) * aspect + 2 * pad));
    const s = Math.min((w - 2 * pad) / (x1 - x0 || 1), (h - 2 * pad) / (y1 - y0 || 1));
    const ox = (w - s * (x1 - x0)) / 2;
    const oy = (h - s * (y1 - y0)) / 2;
    return { w, h, px: (i: number) => ox + (x[i] - x0) * s, py: (i: number) => oy + (y1 - y[i]) * s };
  }, [atlas, w, maxH]);
}

export interface AtlasCanvasProps {
  atlas: AtlasData;
  pool: string;
  picks: string[];
  /** How far along the path is drawn, in ms of the animation clock (Infinity = all). */
  clock: number;
  pending?: string[];
  batch?: number;
  maxH?: number;
  dense?: boolean;
  ariaLabel: string;
}

export function AtlasCanvas({ atlas, pool, picks, clock, pending = [], batch = 10, maxH = 640, dense, ariaLabel }: AtlasCanvasProps) {
  const [wrap, w] = useWidth<HTMLDivElement>(640);
  const geo = useGeo(atlas, w, maxH);
  const canvas = useRef<HTMLCanvasElement>(null);
  const colors = useRef<Colors | null>(null);
  const idx = useMemo(() => new Map(atlas.points.id.map((id, i) => [id, i])), [atlas]);
  const [tip, setTip] = useState<{ x: number; y: number; i: number } | null>(null);
  const small = w < 520;

  // draw
  useEffect(() => {
    const cv = canvas.current;
    if (!cv) return;
    colors.current ??= readColors(cv);
    const C = colors.current;
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    cv.width = Math.round(geo.w * dpr);
    cv.height = Math.round(geo.h * dpr);
    cv.style.height = `${geo.h}px`;
    const g = cv.getContext('2d');
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, geo.w, geo.h);
    const P = atlas.points;
    const n = P.id.length;
    const dot = (i: number, r: number) => {
      g.beginPath();
      g.arc(geo.px(i), geo.py(i), r, 0, Math.PI * 2);
    };
    // 1. other pools, faint
    g.fillStyle = C.ruleStrong;
    for (let i = 0; i < n; i++) if (P.pool[i] !== pool) {
      dot(i, dense ? 1 : 1.25);
      g.fill();
    }
    // 2. this pool
    g.globalAlpha = 0.75;
    g.fillStyle = C.faint;
    for (let i = 0; i < n; i++) if (P.pool[i] === pool && !P.hit[i]) {
      dot(i, dense ? 1.4 : 1.8);
      g.fill();
    }
    g.globalAlpha = 1;
    // 3. metal islands
    g.font = `${small ? 10 : 11}px ${getComputedStyle(cv).getPropertyValue('--mono') || 'monospace'}`;
    g.fillStyle = C.faint;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    if (!dense) {
      for (const isl of atlas.islands) {
        const x0 = atlas.points.x;
        // island coordinates are in the same space as the points: map through a fake index
        const k = nearestIndexTo(atlas, isl.x, isl.y);
        if (k >= 0 && x0.length) g.fillText(isl.metal, geo.px(k), geo.py(k) - 14);
      }
    }
    // 4. hits in this pool: filled, the targets (hidden from the agent until bought)
    g.fillStyle = C.fg;
    for (let i = 0; i < n; i++) if (P.pool[i] === pool && P.hit[i]) {
      dot(i, dense ? 2.2 : 2.8);
      g.fill();
    }
    // 5. pending proposals (console): hollow
    g.strokeStyle = C.accent;
    g.lineWidth = 1.25;
    for (const id of pending) {
      const i = idx.get(id);
      if (i == null) continue;
      dot(i, dense ? 4.5 : 6);
      g.stroke();
    }
    // 6. the path
    const shown: { i: number; k: number; age: number }[] = [];
    picks.forEach((id, k) => {
      const i = idx.get(id);
      if (i == null) return;
      const t = appearAt(k, batch);
      if (clock >= t) shown.push({ i, k, age: clock - t });
    });
    if (shown.length > 1) {
      g.strokeStyle = C.fg;
      g.globalAlpha = 0.28;
      g.lineWidth = 1;
      g.beginPath();
      shown.forEach((s, j) => (j ? g.lineTo(geo.px(s.i), geo.py(s.i)) : g.moveTo(geo.px(s.i), geo.py(s.i))));
      g.stroke();
      g.globalAlpha = 1;
    }
    const R = dense ? 5 : small ? 6 : 7.5;
    g.font = `500 ${dense ? 7.5 : small ? 8 : 9}px ${getComputedStyle(cv).getPropertyValue('--mono') || 'monospace'}`;
    for (const s of shown) {
      const hit = P.hit[s.i];
      const x = geo.px(s.i);
      const y = geo.py(s.i);
      if (hit && s.age < FLASH && Number.isFinite(clock)) {
        const f = s.age / FLASH;
        g.strokeStyle = C.accent;
        g.globalAlpha = 1 - f;
        g.lineWidth = 2;
        g.beginPath();
        g.arc(x, y, R + 4 + f * 22, 0, Math.PI * 2);
        g.stroke();
        g.globalAlpha = 1;
      }
      const grow = Number.isFinite(clock) ? Math.min(1, s.age / 160) : 1;
      g.beginPath();
      g.arc(x, y, R * (0.6 + 0.4 * grow), 0, Math.PI * 2);
      g.fillStyle = hit ? C.accent : C.fg;
      g.fill();
      g.lineWidth = 1.5;
      g.strokeStyle = C.bg;
      g.stroke();
      if (!dense || hit) {
        g.fillStyle = C.bg;
        g.fillText(String(s.k + 1), x, y + 0.5);
      }
    }
  }, [atlas, pool, picks, clock, pending, batch, geo, idx, dense, small]);

  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;
    let best = -1;
    let bd = 12 * 12;
    const P = atlas.points;
    // prefer this pool's points
    for (let i = 0; i < P.id.length; i++) {
      const dx = geo.px(i) - mx;
      const dy = geo.py(i) - my;
      const d = dx * dx + dy * dy + (P.pool[i] === pool ? 0 : 40);
      if (d < bd) {
        bd = d;
        best = i;
      }
    }
    setTip(best >= 0 ? { x: geo.px(best), y: geo.py(best), i: best } : null);
  };

  const P = atlas.points;
  const order = tip ? picks.indexOf(P.id[tip.i]) : -1;
  return (
    <div className="atlas-map" ref={wrap}>
      <canvas ref={canvas} role="img" aria-label={ariaLabel} onPointerMove={onMove} onPointerLeave={() => setTip(null)} style={{ height: geo.h }} />
      {tip && (
        <div className={`atlas-tip mono${tip.x > geo.w * 0.6 ? ' flip' : ''}`} style={{ left: tip.x, top: tip.y }}>
          <div>
            {P.id[tip.i]} <span className="faint">· {P.pool[tip.i]} · {P.metals[tip.i] || 'no metal'}</span>
          </div>
          <div>
            E<sub>CO2</sub> {num(P.E_CO2[tip.i], 2)} · E<sub>H2O</sub> {num(P.E_H2O[tip.i], 2)} eV{' '}
            <span className={P.hit[tip.i] ? '' : 'faint'}>· {P.hit[tip.i] ? 'hit' : 'not a hit'}</span>
          </div>
          {order >= 0 && <div className="faint">purchase {order + 1} of this campaign</div>}
        </div>
      )}
    </div>
  );
}

const nearestCache = new WeakMap<AtlasData, Map<string, number>>();
/** Index of the point closest to (x, y) in embedding space (islands are drawn at their nearest MOF). */
function nearestIndexTo(atlas: AtlasData, x: number, y: number): number {
  let m = nearestCache.get(atlas);
  if (!m) nearestCache.set(atlas, (m = new Map()));
  const key = `${x},${y}`;
  const hit = m.get(key);
  if (hit != null) return hit;
  let best = -1;
  let bd = Infinity;
  for (let i = 0; i < atlas.points.x.length; i++) {
    const d = (atlas.points.x[i] - x) ** 2 + (atlas.points.y[i] - y) ** 2;
    if (d < bd) {
      bd = d;
      best = i;
    }
  }
  m.set(key, best);
  return best;
}

/** Animated atlas for the landing page: one campaign at a time, round by round. */
export function AtlasPlayer({ atlas, pool, seqs, batch = 10 }: { atlas: AtlasData; pool: string; seqs: AtlasSeq[]; batch?: number }) {
  const [key, setKey] = useState(seqs[0]?.key ?? '');
  const seq = seqs.find((s) => s.key === key) ?? seqs[0];
  // `#/...?atlas=paused` holds the empty map until Replay is pressed (used by the video recorder)
  const holdFirst = useRef(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('atlas') === 'paused');
  const [clock, setClock] = useState(() => (reducedMotion() ? Infinity : 0));
  const [run, setRun] = useState(0);
  const [inView, setInView] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const idx = useMemo(() => new Map(atlas.points.id.map((id, i) => [id, i])), [atlas]);
  const total = seq ? appearAt(seq.ids.length - 1, batch) + FLASH : 0;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver((es) => setInView(es.some((e) => e.isIntersecting)), { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!inView || reducedMotion()) {
      if (reducedMotion()) setClock(Infinity);
      return;
    }
    if (holdFirst.current) {
      setClock(-1);
      return;
    }
    let raf = 0;
    let start: number | null = null;
    const step = (now: number) => {
      start ??= now;
      const t = now - start;
      setClock(t);
      if (t < total) raf = requestAnimationFrame(step);
      else setClock(Infinity);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [inView, key, run, total]);

  if (!seq) return null;
  const hitAt = (k: number) => !!atlas.points.hit[idx.get(seq.ids[k]) ?? -1];
  const shownN = seq.ids.filter((_, k) => clock >= appearAt(k, batch)).length;
  const hitsSoFar = seq.ids.slice(0, shownN).filter((_, k) => hitAt(k)).length;
  const poolN = atlas.points.pool.filter((p) => p === pool).length;
  const poolHits = atlas.points.pool.filter((p, i) => p === pool && atlas.points.hit[i]).length;
  const rounds = Math.ceil(seq.ids.length / batch);

  return (
    <div className="atlas" ref={box} data-shown={shownN} data-seq={seq.key}>
      <AtlasCanvas
        atlas={atlas}
        pool={pool}
        picks={seq.ids}
        clock={clock}
        batch={batch}
        ariaLabel={`Map of ${atlas.n} MOFs. ${seq.label} bought ${seq.ids.length} in pool ${pool}; ${seq.ids.filter((_, k) => hitAt(k)).length} were hits.`}
      />
      <div className="atlas-side">
        {seqs.length > 1 && (
        <div className="seg seg-sm" role="group" aria-label="Campaign on the map">
          {seqs.map((s) => (
            <button
              key={s.key}
              type="button"
              aria-pressed={s.key === seq.key}
              onClick={() => {
                setKey(s.key);
                holdFirst.current = false;
                setRun((r) => r + 1);
                if (!reducedMotion()) setClock(0);
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
        )}
        <div>
          <div className="atlas-count" aria-live="polite">
            {hitsSoFar}
            <span className="unit">
              good material{hitsSoFar === 1 ? '' : 's'} found in {shownN} test{shownN === 1 ? '' : 's'}
            </span>
          </div>
          {seq.note && <p className="small" style={{ marginTop: 8 }}>{seq.note}</p>}
        </div>
        <div className="atlas-rounds">
          {Array.from({ length: rounds }, (_, r) => {
            const ks = seq.ids.map((_, k) => k).filter((k) => Math.floor(k / batch) === r);
            const on = ks.some((k) => clock >= appearAt(k, batch));
            const h = ks.filter((k) => clock >= appearAt(k, batch) && hitAt(k)).length;
            return (
              <div key={r} data-on={on ? 1 : 0}>
                <span>Round {r + 1}</span>
                <span className="atlas-ticks" aria-hidden>
                  {ks.map((k) => (
                    <i key={k} data-s={clock >= appearAt(k, batch) ? (hitAt(k) ? 'hit' : 'miss') : 'off'} />
                  ))}
                </span>
                <span className="mono">{on ? `${h} hit${h === 1 ? '' : 's'}` : '—'}</span>
              </div>
            );
          })}
        </div>
        <div className="atlas-key">
          <span>
            <svg width="12" height="12" aria-hidden>
              <circle cx="6" cy="6" r="1.8" fill="var(--fg-faint)" />
            </svg>
            candidate material in this set ({poolN}); other sets lighter
          </span>
          <span>
            <svg width="12" height="12" aria-hidden>
              <circle cx="6" cy="6" r="2.8" fill="var(--fg)" />
            </svg>
            good material ({poolHits} in this set), hidden until tested
          </span>
          <span>
            <svg width="12" height="12" aria-hidden>
              <circle cx="6" cy="6" r="5.5" fill="var(--fg)" />
            </svg>
            tested, numbered in order
          </span>
          <span>
            <svg width="12" height="12" aria-hidden>
              <circle cx="6" cy="6" r="5.5" fill="var(--accent)" />
            </svg>
            tested and good
          </span>
        </div>
        {!reducedMotion() && (
          <button
            type="button"
            className="btn btn-secondary btn-xs atlas-replay"
            style={{ alignSelf: 'flex-start' }}
            onClick={() => {
              holdFirst.current = false;
              setRun((r) => r + 1);
              setClock(0);
            }}
          >
            Replay
          </button>
        )}
      </div>
    </div>
  );
}
