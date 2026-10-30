import { useEffect, useRef, useState } from 'react';
import { loadMutantDoc, loadStructure } from '../lib/data';
import { formula as fmtFormula } from '../lib/format';
import type { MutantDoc, Structure } from '../lib/types';
import { SYMBOLS, bindingContacts, type Contact } from '../lib/chem';
import { loadDdec } from '../lib/chemData';
import { load3Dmol } from '../lib/mol3d';
import '../styles/chem.css';

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
  /** A prospective mutant (runs/<id>/mutants/<mid>.json): loads from `url` and plays its MACE relaxation frames. */
  mutant?: { id: string; url: string } | null;
}

const FRAME_MS = 110;

export function Viewer({ id: candId, revealed: candRevealed, morphKey, onMorphDone, label, mutant }: ViewerProps) {
  const id = mutant?.id ?? candId;
  const revealed = mutant ? true : candRevealed;
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<any>(null);
  const model = useRef<any>(null);
  const halo = useRef<any>(null);
  const cellShapes = useRef<any[]>([]);
  const struct = useRef<(Structure & Partial<MutantDoc>) | null>(null);
  const anim = useRef<number | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'nogl' | 'missing'>('idle');
  const [geom, setGeom] = useState<'initial' | 'morphing' | 'relaxed'>('initial');
  const [showCell, setShowCell] = useState(true);
  const [showHalo, setShowHalo] = useState(true);
  const [showSite, setShowSite] = useState(true);
  const [charge, setCharge] = useState(false);
  const [charges, setCharges] = useState<(number | null)[] | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const siteShapes = useRef<any[]>([]);
  const siteActive = useRef(false);
  const siteLabels = useRef<any[]>([]);
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [frame, setFrame] = useState<number | null>(null);
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

  const applyPositions = (P: number[][], cell?: number[][]) => {
    const s = struct.current;
    const m = model.current;
    if (!s || !m) return;
    const atoms = m.selectedAtoms({});
    for (let i = 0; i < atoms.length && i < P.length; i++) {
      atoms[i].x = P[i][0];
      atoms[i].y = P[i][1];
      atoms[i].z = P[i][2];
    }
    m.molObj = null; // force 3Dmol to rebuild geometry from the new coordinates
    haloAt(s.ads.length ? centroid(P, s.ads) : null);
    if (cell) drawCell(cell);
  };

  const setCoords = (f: number) => {
    const s = struct.current;
    if (!s) return;
    if (s.frames?.length) return setFrameAt(f);
    applyPositions(s.pos.map((a, i) => {
      const b = s.pos_relaxed?.[i] ?? a;
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    }));
  };

  /** Mutants: position t in [0, 1] along the stored relaxation frames (linear between neighbouring frames). */
  const setFrameAt = (t: number) => {
    const fr = struct.current?.frames;
    if (!fr?.length) return;
    const u = Math.max(0, Math.min(1, t)) * (fr.length - 1);
    const k = Math.min(fr.length - 2, Math.floor(u));
    if (fr.length === 1 || k < 0) {
      applyPositions(fr[0].pos, fr[0].cell);
      setFrame(1);
      return;
    }
    const w = u - k;
    const lerp = (a: number[][], b: number[][]) => a.map((p, i) => [p[0] + (b[i][0] - p[0]) * w, p[1] + (b[i][1] - p[1]) * w, p[2] + (b[i][2] - p[2]) * w]);
    applyPositions(lerp(fr[k].pos, fr[k + 1].pos), lerp(fr[k].cell, fr[k + 1].cell));
    setFrame(Math.round(u) + 1);
  };

  /** Halo sphere around the guest at centre c (none while the binding site is drawn, or with the guest toggle off). */
  const haloAt = (c: { x: number; y: number; z: number } | null) => {
    const v = viewer.current;
    if (!v) return;
    if (halo.current) {
      v.removeShape(halo.current);
      halo.current = null;
    }
    if (!showHaloRef.current || !c || siteActive.current) return;
    const accent = getComputedStyle(host.current!).getPropertyValue('--accent-halo').trim() || '#f59e4b';
    halo.current = v.addSphere({ center: c, radius: 2.6, color: accent, opacity: 0.38 });
  };

  /** Halo at morph fraction f (0 initial, 1 relaxed); for a mutant, at the current relaxation frame. */
  const drawHalo = (f: number) => {
    const s = struct.current;
    if (!s || !s.ads.length) return haloAt(null);
    if (s.frames?.length) {
      const fr = s.frames[Math.round(Math.max(0, Math.min(1, f)) * (s.frames.length - 1))];
      return haloAt(centroid(fr.pos, s.ads));
    }
    const a = centroid(s.pos, s.ads);
    const b = centroid(s.pos_relaxed ?? s.pos, s.ads);
    haloAt({ x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, z: a.z + (b.z - a.z) * f });
  };
  const showHaloRef = useRef(showHalo);
  showHaloRef.current = showHalo;
  const showCellRef = useRef(showCell);
  showCellRef.current = showCell;

  // Binding site at the DFT-relaxed geometry: dashed lines and distances from each CO2 atom to its nearest
  // framework atom, plus the nearest metal (minimum image; a neighbour in the next cell gets a small ghost sphere).
  const drawSite = (on: boolean) => {
    const v = viewer.current;
    const s = struct.current;
    if (!v) return;
    for (const sh of siteShapes.current) v.removeShape(sh);
    for (const l of siteLabels.current) v.removeLabel(l);
    siteShapes.current = [];
    siteLabels.current = [];
    const was = siteActive.current;
    siteActive.current = !!(on && s && s.ads.length);
    // the halo would hide the dashed lines: off while the binding site is shown
    if (was !== siteActive.current) drawHalo(geomRef.current === 'initial' ? 0 : 1);
    if (!on || !s || !s.ads.length) {
      setContacts([]);
      return;
    }
    const cs = bindingContacts(s, 'relaxed');
    setContacts(cs);
    const P = s.pos_relaxed?.length ? s.pos_relaxed : s.pos;
    const css = getComputedStyle(host.current!);
    const accent = css.getPropertyValue('--accent-halo').trim() || '#f59e4b';
    const fg = css.getPropertyValue('--fg').trim() || '#ededed';
    const bg = css.getPropertyValue('--bg-raised').trim() || '#141414';
    cs.forEach((c, k) => {
      const a = { x: P[c.from][0], y: P[c.from][1], z: P[c.from][2] };
      const b = { x: c.toPos[0], y: c.toPos[1], z: c.toPos[2] };
      siteShapes.current.push(v.addCylinder({ start: a, end: b, radius: 0.05, color: c.metal ? accent : fg, dashed: true, dashLength: 0.18, gapLength: 0.14, fromCap: 1, toCap: 1 }));
      if (c.image) siteShapes.current.push(v.addSphere({ center: b, radius: 0.32, color: c.metal ? accent : '#9a9a9a', opacity: 0.85 }));
      // label the metal contact and the shortest contact; the rest are listed in the caption (labels would collide)
      if (!c.metal && k > 0) return;
      siteLabels.current.push(
        v.addLabel(`${c.fromSym}···${c.toSym} ${c.d.toFixed(2)} Å`, {
          position: { x: a.x + (b.x - a.x) * 0.72, y: a.y + (b.y - a.y) * 0.72, z: a.z + (b.z - a.z) * 0.72 },
          font: 'Geist Mono Variable, ui-monospace, monospace',
          fontSize: 11,
          fontColor: c.metal ? accent : fg,
          backgroundColor: bg,
          backgroundOpacity: 0.82,
          borderThickness: 0,
          alignment: 'center',
          inFront: true,
        }),
      );
    });
  };
  // never for a mutant: its CO2 is the parent's MACE placement carried over, not a DFT-relaxed pose
  const siteOn = () => showSite && revealedRef.current && geomRef.current === 'relaxed' && !struct.current?.frames?.length;
  const revealedRef = useRef(revealed);
  revealedRef.current = revealed;
  const geomRef = useRef(geom);
  geomRef.current = geom;

  const applyStyle = () => {
    const v = viewer.current;
    const s = struct.current;
    if (!v || !s) return;
    const q = chargeRef.current;
    if (q && q.length === s.numbers.length) {
      const col = (atom: any) => chargeColor(q[atom.index] ?? 0);
      v.setStyle({}, { stick: { radius: 0.12, colorfunc: col }, sphere: { scale: 0.26, colorfunc: col } });
    } else {
      v.setStyle({}, { stick: { radius: 0.12, colorscheme: 'Jmol' }, sphere: { scale: 0.24, colorscheme: 'Jmol' } });
    }
    v.setStyle({ index: s.ads }, { stick: { radius: 0.16, colorscheme: 'Jmol' }, sphere: { scale: 0.34, colorscheme: 'Jmol' } });
  };
  const chargeRef = useRef<(number | null)[] | null>(null);
  chargeRef.current = charge ? charges : null;

  const lastCell = useRef<number[][] | null>(null);
  const drawCell = (cellIn?: number[][]) => {
    const v = viewer.current;
    const s = struct.current;
    if (!v || !s) return;
    for (const sh of cellShapes.current) v.removeShape(sh);
    cellShapes.current = [];
    const cell = cellIn ?? lastCell.current ?? s.cell;
    lastCell.current = cell;
    if (!showCellRef.current) return;
    const [A, B, C] = cell;
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
    (mutant ? loadMutantDoc(mutant.url) : (loadStructure(id) as Promise<Structure & Partial<MutantDoc>>))
      .then((s) => {
        if (dead || !viewer.current) return;
        if (anim.current) cancelAnimationFrame(anim.current);
        struct.current = s;
        lastCell.current = null;
        setFrame(null);
        const v = viewer.current;
        v.clear();
        halo.current = null;
        cellShapes.current = [];
        model.current = v.addModel(toXyz(s), 'xyz');
        siteShapes.current = [];
        siteLabels.current = [];
        siteActive.current = false;
        setContacts([]);
        setCharges(null);
        applyStyle();
        drawCell();
        const frames = !!s.frames?.length;
        setCoords(revealed && !frames ? 1 : 0);
        setGeom(revealed && !frames ? 'relaxed' : 'initial');
        v.zoomTo({ index: s.ads.length ? s.ads : undefined });
        v.zoomTo();
        v.zoom(1.2);
        v.render();
        if (!reducedMotion()) v.spin('y', 0.1);
        setLoadedId(id);
        setStatus('ready');
        if (s.frames?.length) playMorph();
      })
      .catch(() => !dead && setStatus('missing'));
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, glReady, mutant?.url]);

  // reveal without a morph (e.g. scrubbing) -> jump to relaxed
  useEffect(() => {
    if (status !== 'ready' || geom === 'morphing') return;
    const want = revealed ? 'relaxed' : 'initial';
    if (want !== geom) {
      setCoords(revealed ? 1 : 0);
      geomRef.current = want;
      drawSite(siteOn());
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
      geomRef.current = 'relaxed';
      drawSite(siteOn());
      v.render();
      setGeom('relaxed');
      onMorphDone?.();
      return;
    }
    setGeom('morphing');
    drawSite(false);
    const HOLD = 250; // hold the initial pose for a beat so the change reads
    const nf = struct.current.frames?.length ?? 0;
    const D = nf ? Math.max(1500, nf * FRAME_MS) : 2000;
    const shape = nf ? (t: number) => t : ease; // relaxation frames play at their own pace
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
      setCoords(shape(t));
      v.render();
      if (t < 1) anim.current = requestAnimationFrame(frame);
      else {
        anim.current = null;
        geomRef.current = 'relaxed';
        drawSite(siteOn());
        v.render();
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
    drawSite(siteOn());
    viewer.current?.render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showSite, status, loadedId, revealed]);

  useEffect(() => {
    if (!charge || !id) return;
    let dead = false;
    loadDdec().then(
      (d) => !dead && setCharges(d.charges[id] ?? []),
      () => !dead && setCharges([]),
    );
    return () => {
      dead = true;
    };
  }, [charge, id]);

  useEffect(() => {
    if (status !== 'ready') return;
    applyStyle();
    viewer.current?.render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [charge, charges, status, loadedId]);

  const zoomSite = () => {
    const v = viewer.current;
    const s = struct.current;
    if (!v || !s?.ads.length) return;
    v.spin(false);
    v.zoomTo({ index: s.ads });
    v.zoom(0.55);
    v.render();
  };

  useEffect(() => {
    if (status !== 'ready' || geom === 'morphing') return;
    setCoords(geom === 'initial' ? 0 : 1);
    viewer.current?.render();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showHalo]);

  const s = struct.current && struct.current.id === id ? struct.current : null;
  const nFrames = s?.frames?.length ?? 0;
  const geomText = nFrames
    ? geom === 'initial'
      ? 'built, before relaxation'
      : geom === 'morphing'
        ? `MACE relaxation, frame ${frame ?? 1}/${nFrames}`
        : `MACE-relaxed (${s?.n_steps ?? '?'} steps) · unvalidated · ML only`
    : geom === 'initial'
      ? 'ODAC initial placement, DFT sealed'
      : geom === 'morphing'
        ? 'relaxing…'
        : 'DFT-relaxed geometry, ODAC23';
  const caption =
    status === 'nogl'
      ? '3D view needs WebGL.'
      : status === 'missing'
        ? `${id ?? ''} · structure file not in this build`
        : !id
          ? 'No candidate selected.'
          : s
            ? `${s.id} · ${fmtFormula(hillFormula(s))} · ${s.numbers.length - s.ads.length} atoms · ${geomText}`
            : `${id} · loading`;

  return (
    <figure className="viewer" aria-label={label ?? 'Structure viewer'}>
      <div className="c-head viewer-head">
        <span>{nFrames ? 'Mutant' : 'Structure'}</span>
        {status === 'ready' && (
          <div className="viewer-ctl">
            <button type="button" aria-pressed={showCell} onClick={() => setShowCell((x) => !x)}>
              cell
            </button>
            <button type="button" aria-pressed={showHalo} onClick={() => setShowHalo((x) => !x)}>
              guest
            </button>
            {revealed && !nFrames && (
              <button
                type="button"
                aria-pressed={showSite}
                title="Binding site: distances from CO2 to the nearest framework atoms and metal, DFT-relaxed"
                onClick={() => {
                  const on = !showSite;
                  setShowSite(on);
                  if (on) zoomSite();
                }}
              >
                site
              </button>
            )}
            {!nFrames && (
              <button type="button" aria-pressed={charge} title="Colour the framework by DDEC6 partial charge (ODAC23)" onClick={() => setCharge((x) => !x)}>
                charge
              </button>
            )}
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
        <div ref={host} className="viewer-host" data-geom={geom} data-status={status} data-id={loadedId ?? ''} data-frame={frame ?? ''} />
        {status === 'nogl' && <div className="viewer-empty small">3D view needs WebGL.</div>}
      </div>
      <p className="viewer-cap mono" aria-live="polite">{caption}</p>
      {contacts.length > 0 && geom === 'relaxed' && (
        <p className="viewer-cap viewer-site mono" data-site={contacts.length}>
          binding site ·{' '}
          {contacts.map((c, i) => (
            <span key={i} className={c.metal ? 'accent' : undefined}>
              {i ? ' · ' : ''}
              {c.fromSym}···{c.toSym} {c.d.toFixed(2)} Å
            </span>
          ))}
        </p>
      )}
      {charge && (
        <p className="viewer-cap viewer-charge mono">
          {charges == null ? (
            'loading DDEC6 charges…'
          ) : charges.length ? (
            <>
              <span className="q-bar" aria-hidden>
                {[-0.9, -0.6, -0.3, 0, 0.4, 0.8, 1.2, 1.6].map((q) => (
                  <i key={q} style={{ background: chargeColor(q) }} />
                ))}
              </span>{' '}
              DDEC6 charge, −0.9 to +1.6 e · electron-poor in accent · ODAC23
            </>
          ) : (
            'no DDEC6 charges for this structure'
          )}
        </p>
      )}
    </figure>
  );
}

/** DDEC6 charge to colour: electron-rich (negative) light grey, neutral mid grey, electron-poor (positive) accent. */
export function chargeColor(q: number): string {
  const lerp = (a: number[], b: number[], t: number) => a.map((x, i) => Math.round(x + (b[i] - x) * Math.max(0, Math.min(1, t))));
  const mid = [96, 96, 96];
  const c = q < 0 ? lerp(mid, [236, 236, 236], -q / 0.9) : lerp(mid, [245, 158, 75], q / 1.6);
  return `rgb(${c.join(',')})`;
}
