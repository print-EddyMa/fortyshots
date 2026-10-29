import { useEffect, useRef, useState } from 'react';
import { loadStructure } from '../lib/data';
import { formula as fmtFormula } from '../lib/format';
import type { Structure } from '../lib/types';

const SYMBOLS =
  'X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi'.split(
    ' ',
  );

let lib: Promise<any> | null = null;
function load3Dmol(): Promise<any> {
  lib ??= import('3dmol/build/3Dmol-min.js').then((m: any) => {
    const w = window as any;
    if (m?.createViewer) return m;
    if (m?.default?.createViewer) return m.default;
    if (w.$3Dmol?.createViewer) return w.$3Dmol;
    throw new Error('3Dmol did not load');
  });
  return lib;
}

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

function toXyz(s: Structure): string {
  const lines = [String(s.numbers.length), s.id];
  s.numbers.forEach((z, i) => {
    const p = s.pos[i];
    lines.push(`${SYMBOLS[z] ?? 'X'} ${p[0]} ${p[1]} ${p[2]}`);
  });
  return lines.join('\n');
}

function centroid(ps: number[][], idx: number[]) {
  const c = { x: 0, y: 0, z: 0 };
  for (const i of idx) {
    c.x += ps[i][0];
    c.y += ps[i][1];
    c.z += ps[i][2];
  }
  const n = idx.length || 1;
  return { x: c.x / n, y: c.y / n, z: c.z / n };
}

function hillFormula(s: Structure): string {
  const ads = new Set(s.ads);
  const counts: Record<string, number> = {};
  s.numbers.forEach((z, i) => {
    if (ads.has(i)) return;
    const k = SYMBOLS[z] ?? 'X';
    counts[k] = (counts[k] ?? 0) + 1;
  });
  const g = Object.values(counts).reduce((a, b) => {
    while (b) [a, b] = [b, a % b];
    return a;
  }, 0) || 1;
  const keys = Object.keys(counts).sort((a, b) => (a === 'C' ? -1 : b === 'C' ? 1 : a === 'H' ? -1 : b === 'H' ? 1 : a.localeCompare(b)));
  return keys.map((k) => `${k}${counts[k] / g > 1 ? counts[k] / g : ''}`).join('');
}

export interface ViewerProps {
  id: string | null;
  /** DFT result is known for this structure (show relaxed geometry). */
  revealed: boolean;
  /** Changing this number plays the morph (initial -> relaxed) once. */
  morphKey?: number | null;
  /** Called when the morph finishes playing (used by tests and the landing tour). */
  onMorphDone?: () => void;
  label?: string;
}

export function Viewer({ id, revealed, morphKey, onMorphDone, label }: ViewerProps) {
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<any>(null);
  const model = useRef<any>(null);
  const halo = useRef<any>(null);
  const cellShapes = useRef<any[]>([]);
  const struct = useRef<Structure | null>(null);
  const anim = useRef<number | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'nogl' | 'missing'>('idle');
  const [geom, setGeom] = useState<'initial' | 'morphing' | 'relaxed'>('initial');
  const [showCell, setShowCell] = useState(true);
  const [showHalo, setShowHalo] = useState(true);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [glReady, setGlReady] = useState(false);
  const lastMorph = useRef<number | null | undefined>(undefined);

  // create viewer once
  useEffect(() => {
    let dead = false;
    const el = host.current;
    if (!el) return;
    load3Dmol()
      .then(($3Dmol) => {
        if (dead) return;
        const css = getComputedStyle(el);
        const bg = css.getPropertyValue('--bg-raised').trim() || '#141414';
        try {
          viewer.current = $3Dmol.createViewer(el, { backgroundColor: bg, antialias: true, id: `v-${Math.random().toString(36).slice(2)}` });
        } catch {
          setStatus('nogl');
          return;
        }
        if (!viewer.current) {
          setStatus('nogl');
          return;
        }
        setStatus('idle');
        setLoadedId(null);
        setGlReady(true);
        const stop = () => viewer.current?.spin(false);
        el.addEventListener('pointerdown', stop);
        el.addEventListener('wheel', stop, { passive: true });
      })
      .catch(() => setStatus('nogl'));
    const ro = new ResizeObserver(() => viewer.current?.resize());
    ro.observe(el);
    return () => {
      dead = true;
      ro.disconnect();
      if (anim.current) cancelAnimationFrame(anim.current);
      try {
        viewer.current?.clear();
      } catch {
        /* noop */
      }
      viewer.current = null;
    };
  }, []);

  const setCoords = (f: number) => {
    const s = struct.current;
    const m = model.current;
    if (!s || !m) return;
    const atoms = m.selectedAtoms({});
    for (let i = 0; i < atoms.length && i < s.pos.length; i++) {
      const a = s.pos[i];
      const b = s.pos_relaxed?.[i] ?? a;
      atoms[i].x = a[0] + (b[0] - a[0]) * f;
      atoms[i].y = a[1] + (b[1] - a[1]) * f;
      atoms[i].z = a[2] + (b[2] - a[2]) * f;
    }
    m.molObj = null; // force 3Dmol to rebuild geometry from the new coordinates
    drawHalo(f);
  };

  const drawHalo = (f: number) => {
    const v = viewer.current;
    const s = struct.current;
    if (!v || !s) return;
    if (halo.current) {
      v.removeShape(halo.current);
      halo.current = null;
    }
    if (!showHaloRef.current || !s.ads.length) return;
    const a = centroid(s.pos, s.ads);
    const b = centroid(s.pos_relaxed ?? s.pos, s.ads);
    const accent = getComputedStyle(host.current!).getPropertyValue('--accent-halo').trim() || '#f59e4b';
    halo.current = v.addSphere({
      center: { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, z: a.z + (b.z - a.z) * f },
      radius: 2.6,
      color: accent,
      opacity: 0.38,
    });
  };
  const showHaloRef = useRef(showHalo);
  showHaloRef.current = showHalo;

  const drawCell = () => {
    const v = viewer.current;
    const s = struct.current;
    if (!v || !s) return;
    for (const sh of cellShapes.current) v.removeShape(sh);
    cellShapes.current = [];
    if (!showCell) return;
    const [A, B, C] = s.cell;
    const P = (i: number, j: number, k: number) => ({
      x: i * A[0] + j * B[0] + k * C[0],
      y: i * A[1] + j * B[1] + k * C[1],
      z: i * A[2] + j * B[2] + k * C[2],
    });
    const color = getComputedStyle(host.current!).getPropertyValue('--fg-faint').trim() || '#6a6a6a';
    const edges: [number[], number[]][] = [];
    for (const [i, j, k] of [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, 0, 1], [0, 1, 1], [1, 1, 1]]) {
      if (i === 0) edges.push([[i, j, k], [1, j, k]]);
      if (j === 0) edges.push([[i, j, k], [i, 1, k]]);
      if (k === 0) edges.push([[i, j, k], [i, j, 1]]);
    }
    for (const [a, b] of edges) {
      cellShapes.current.push(v.addLine({ start: P(a[0], a[1], a[2]), end: P(b[0], b[1], b[2]), color, dashed: false }));
    }
  };

  // load structure when id changes
  useEffect(() => {
    if (!id || !glReady || !viewer.current) return;
    if (loadedId === id) return;
    let dead = false;
    setStatus('loading');
    loadStructure(id)
      .then((s) => {
        if (dead || !viewer.current) return;
        if (anim.current) cancelAnimationFrame(anim.current);
        struct.current = s;
        const v = viewer.current;
        v.clear();
        halo.current = null;
        cellShapes.current = [];
        model.current = v.addModel(toXyz(s), 'xyz');
        v.setStyle({}, { stick: { radius: 0.12, colorscheme: 'Jmol' }, sphere: { scale: 0.24, colorscheme: 'Jmol' } });
        v.setStyle({ index: s.ads }, { stick: { radius: 0.16, colorscheme: 'Jmol' }, sphere: { scale: 0.34, colorscheme: 'Jmol' } });
        drawCell();
        setCoords(revealed ? 1 : 0);
        setGeom(revealed ? 'relaxed' : 'initial');
        v.zoomTo({ index: s.ads.length ? s.ads : undefined });
        v.zoomTo();
        v.zoom(1.2);
        v.render();
        if (!reducedMotion()) v.spin('y', 0.1);
        setLoadedId(id);
        setStatus('ready');
      })
      .catch(() => !dead && setStatus('missing'));
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, glReady]);

  // reveal without a morph (e.g. scrubbing) -> jump to relaxed
  useEffect(() => {
    if (status !== 'ready' || geom === 'morphing') return;
    const want = revealed ? 'relaxed' : 'initial';
    if (want !== geom) {
      setCoords(revealed ? 1 : 0);
      viewer.current?.render();
      setGeom(want);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealed, status]);

  const playMorph = () => {
    const v = viewer.current;
    if (!v || !struct.current) return;
    if (anim.current) cancelAnimationFrame(anim.current);
    if (reducedMotion()) {
      setCoords(1);
      v.render();
      setGeom('relaxed');
      onMorphDone?.();
      return;
    }
    setGeom('morphing');
    const HOLD = 250; // hold the initial pose for a beat so the change reads
    const D = 2000;
    let start: number | null = null;
    setCoords(0);
    v.render();
    const frame = (now: number) => {
      start ??= now;
      const el = now - start;
      if (el < HOLD) {
        anim.current = requestAnimationFrame(frame);
        return;
      }
      const t = Math.min(1, (el - HOLD) / D);
      setCoords(ease(t));
      v.render();
      if (t < 1) anim.current = requestAnimationFrame(frame);
      else {
        anim.current = null;
        setGeom('relaxed');
        host.current?.setAttribute('data-morphed', String(Date.now()));
        onMorphDone?.();
      }
    };
    anim.current = requestAnimationFrame(frame);
  };

  // morph trigger
  useEffect(() => {
    if (status !== 'ready' || loadedId !== id) return;
    if (morphKey == null || morphKey === lastMorph.current) return;
    lastMorph.current = morphKey;
    if (revealed) playMorph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [morphKey, status, loadedId, id]);

  useEffect(() => {
    if (status !== 'ready') return;
    drawCell();
    viewer.current?.render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCell]);

  useEffect(() => {
    if (status !== 'ready') return;
    drawHalo(geom === 'initial' ? 0 : 1);
    viewer.current?.render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHalo]);

  const s = struct.current && struct.current.id === id ? struct.current : null;
  const caption =
    status === 'nogl'
      ? '3D view needs WebGL.'
      : status === 'missing'
        ? `${id ?? ''} · structure file not in this build`
        : !id
          ? 'No candidate selected.'
          : s
            ? `${s.id} · ${fmtFormula(hillFormula(s))} · ${s.numbers.length} atoms · ${
                geom === 'initial' ? 'ODAC initial placement, DFT sealed' : geom === 'morphing' ? 'relaxing…' : 'DFT-relaxed geometry, ODAC23'
              }`
            : `${id} · loading`;

  return (
    <figure className="viewer" aria-label={label ?? 'Structure viewer'}>
      <div className="c-head viewer-head">
        <span>Structure</span>
        {status === 'ready' && (
          <div className="viewer-ctl">
            <button type="button" aria-pressed={showCell} onClick={() => setShowCell((x) => !x)}>
              cell
            </button>
            <button type="button" aria-pressed={showHalo} onClick={() => setShowHalo((x) => !x)}>
              guest
            </button>
            {revealed && (
              <button type="button" onClick={playMorph}>
                replay
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                viewer.current?.zoomTo();
                viewer.current?.zoom(1.2);
                viewer.current?.render();
              }}
            >
              reset
            </button>
          </div>
        )}
      </div>
      <div className="viewer-frame">
        <div ref={host} className="viewer-host" data-geom={geom} data-status={status} data-id={loadedId ?? ''} />
        {status === 'nogl' && <div className="viewer-empty small">3D view needs WebGL.</div>}
      </div>
      <p className="viewer-cap mono" aria-live="polite">{caption}</p>
    </figure>
  );
}
