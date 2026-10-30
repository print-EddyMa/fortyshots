// Landing hero: one real DFT hit from ODAC23 as a 2x2x2 supercell. The translucent surface is the space a CO2
// molecule's centre can reach (bench/chem.py pore grid, probe 1.65 A); CO2 and framework move from the ODAC
// initial placement to the DFT-relaxed geometry. Lazy: 3Dmol and the structure load when the frame is near view.
import { useEffect, useRef, useState } from 'react';
import { loadStructure } from '../lib/data';
import { loadHero, type HeroData } from '../lib/chemData';
import { SYMBOLS } from '../lib/chem';
import { easeInOut, load3Dmol, reducedMotion } from '../lib/mol3d';
import { num } from '../lib/format';
import { Formula } from './Formula';
import type { Structure } from '../lib/types';
import '../styles/chem.css';

type Phase = 'initial' | 'relaxing' | 'relaxed';

const HOLD_INITIAL = 1400;
const MORPH = 2600;
const HOLD_RELAXED = 9000;

function xyz(nums: number[], pos: Float32Array, n: number) {
  const lines = [String(n), 'supercell'];
  for (let i = 0; i < n; i++) lines.push(`${SYMBOLS[nums[i]] ?? 'X'} ${pos[3 * i]} ${pos[3 * i + 1]} ${pos[3 * i + 2]}`);
  return lines.join('\n');
}

/** Tile atoms `idx` of the structure into an N[0] x N[1] x N[2] supercell, at both geometries. */
function tile(s: Structure, idx: number[], N: number[]) {
  const n = idx.length * N[0] * N[1] * N[2];
  const A = new Float32Array(3 * n);
  const B = new Float32Array(3 * n);
  const Z: number[] = [];
  let k = 0;
  for (let a = 0; a < N[0]; a++)
    for (let b = 0; b < N[1]; b++)
      for (let c = 0; c < N[2]; c++) {
        const sh = [0, 1, 2].map((d) => a * s.cell[0][d] + b * s.cell[1][d] + c * s.cell[2][d]);
        for (const i of idx) {
          const p = s.pos[i];
          const q = s.pos_relaxed?.[i] ?? p;
          for (let d = 0; d < 3; d++) {
            A[3 * k + d] = p[d] + sh[d];
            B[3 * k + d] = q[d] + sh[d];
          }
          Z.push(s.numbers[i]);
          k++;
        }
      }
  return { A, B, Z, n };
}

/** The pore grid as 3Dmol VolumeData, tiled N times along each cell vector (plus one closing plane). */
function poreVolume($3Dmol: any, s: Structure, h: HeroData, N: number[]) {
  const [na, nb, nc] = h.pore.n;
  const raw = Uint8Array.from(atob(h.pore.data), (c) => c.charCodeAt(0));
  const Ma = N[0] * na + 1;
  const Mb = N[1] * nb + 1;
  const Mc = N[2] * nc + 1;
  const v = (k: number, n: number) => s.cell[k].map((x) => (x / n).toFixed(6)).join(' ');
  // negative counts: the voxel vectors are in angstrom (3Dmol's cube convention)
  const header = ['pore', `probe ${h.pore.probe_A}`, '0 0 0 0', `-${Ma} ${v(0, na)}`, `-${Mb} ${v(1, nb)}`, `-${Mc} ${v(2, nc)}`].join('\n');
  const vol = new $3Dmol.VolumeData(header, 'cube');
  const data = new Float32Array(Ma * Mb * Mc);
  let o = 0;
  for (let i = 0; i < Ma; i++)
    for (let j = 0; j < Mb; j++)
      for (let k = 0; k < Mc; k++) data[o++] = raw[((i % na) * nb + (j % nb)) * nc + (k % nc)] / h.pore.scale;
  vol.data = data;
  vol.size = { x: Ma, y: Mb, z: Mc };
  return vol;
}

export function HeroMof() {
  const frame = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const [hero, setHero] = useState<HeroData | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'nogl' | 'error'>('idle');
  const [phase, setPhase] = useState<Phase>('initial');
  const [near, setNear] = useState(false);
  const [paused, setPaused] = useState(() => reducedMotion());
  const ctl = useRef<{ replay: () => void; setPaused: (p: boolean) => void; visible: (v: boolean) => void } | null>(null);

  useEffect(() => {
    loadHero().then(setHero, () => setStatus('error'));
  }, []);

  // load only when the frame is (nearly) on screen; then keep animating only while visible
  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (es) => {
        const vis = es.some((e) => e.isIntersecting);
        if (vis) setNear(true);
        ctl.current?.visible(vis);
      },
      { rootMargin: '200px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!near || !hero || !host.current) return;
    let dead = false;
    let raf = 0;
    let viewer: any = null;
    let ro: ResizeObserver | null = null;
    setStatus('loading');
    Promise.all([load3Dmol(), loadStructure(hero.id)])
      .then(([$3Dmol, s]) => {
        if (dead || !host.current) return;
        const el = host.current;
        try {
          viewer = $3Dmol.createViewer(el, { backgroundColor: '#0d0d0d', antialias: true, id: 'hero-mof' });
        } catch {
          viewer = null;
        }
        if (!viewer) {
          setStatus('nogl');
          return;
        }
        const small = el.clientWidth < 640;
        const N = small ? [2, 2, 1] : [2, 2, 2];
        const ads = new Set(s.ads);
        const fwIdx = s.numbers.map((_, i) => i).filter((i) => !ads.has(i));
        const fw = tile(s, fwIdx, N);
        const gs = tile(s, s.ads, N);
        const fwModel = viewer.addModel(xyz(fw.Z, fw.A, fw.n), 'xyz');
        const gModel = viewer.addModel(xyz(gs.Z, gs.A, gs.n), 'xyz');
        fwModel.setStyle({}, { stick: { radius: 0.085, colorscheme: 'Jmol' }, sphere: { scale: 0.18, colorscheme: 'Jmol' } });
        fwModel.setStyle({ elem: 'H' }, { stick: { radius: 0.06, colorscheme: 'Jmol' }, sphere: { scale: 0.12, colorscheme: 'Jmol' } });
        gModel.setStyle({}, { stick: { radius: 0.17, colorscheme: 'Jmol' }, sphere: { scale: 0.36, colorscheme: 'Jmol' } });

        // unit-cell edges for every cell of the supercell; the outer box a little brighter
        const P = (i: number, j: number, k: number) => ({
          x: i * s.cell[0][0] + j * s.cell[1][0] + k * s.cell[2][0],
          y: i * s.cell[0][1] + j * s.cell[1][1] + k * s.cell[2][1],
          z: i * s.cell[0][2] + j * s.cell[1][2] + k * s.cell[2][2],
        });
        for (let i = 0; i <= N[0]; i++)
          for (let j = 0; j <= N[1]; j++)
            for (let k = 0; k <= N[2]; k++) {
              const outer = (a: number, b: number, na: number, nb: number) => (a === 0 || a === na) && (b === 0 || b === nb);
              if (i < N[0]) viewer.addLine({ start: P(i, j, k), end: P(i + 1, j, k), color: outer(j, k, N[1], N[2]) ? '#5a5a5a' : '#2c2c2c' });
              if (j < N[1]) viewer.addLine({ start: P(i, j, k), end: P(i, j + 1, k), color: outer(i, k, N[0], N[2]) ? '#5a5a5a' : '#2c2c2c' });
              if (k < N[2]) viewer.addLine({ start: P(i, j, k), end: P(i, j, k + 1), color: outer(i, j, N[0], N[1]) ? '#5a5a5a' : '#2c2c2c' });
            }

        try {
          const vv = poreVolume($3Dmol, s, hero, N);
          viewer.addIsosurface(vv, { isoval: hero.pore.probe_A, color: '#66737d', wireframe: true, linewidth: 1, opacity: 0.5, smoothness: 1 });
        } catch (e) {
          // no surface: the structure still shows
          console.warn('pore surface', e);
        }

        // accent halo around each CO2 (one per cell, as in the periodic DFT model)
        const halos: any[] = [];
        const centroid = (arr: Float32Array, cell: number) => {
          const k0 = cell * s.ads.length;
          const c = { x: 0, y: 0, z: 0 };
          for (let k = k0; k < k0 + s.ads.length; k++) {
            c.x += arr[3 * k];
            c.y += arr[3 * k + 1];
            c.z += arr[3 * k + 2];
          }
          return { x: c.x / s.ads.length, y: c.y / s.ads.length, z: c.z / s.ads.length };
        };
        const nCells = N[0] * N[1] * N[2];
        const drawHalos = (f: number) => {
          for (const h of halos) viewer.removeShape(h);
          halos.length = 0;
          for (let c = 0; c < nCells; c++) {
            const a = centroid(gs.A, c);
            const b = centroid(gs.B, c);
            const center = { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, z: a.z + (b.z - a.z) * f };
            halos.push(viewer.addSphere({ center, radius: 1.85, color: '#ffb366', opacity: 0.55 }));
          }
        };

        const setGeom = (f: number, withFramework: boolean) => {
          const g = gModel.selectedAtoms({});
          for (let k = 0; k < g.length; k++) {
            g[k].x = gs.A[3 * k] + (gs.B[3 * k] - gs.A[3 * k]) * f;
            g[k].y = gs.A[3 * k + 1] + (gs.B[3 * k + 1] - gs.A[3 * k + 1]) * f;
            g[k].z = gs.A[3 * k + 2] + (gs.B[3 * k + 2] - gs.A[3 * k + 2]) * f;
          }
          gModel.molObj = null;
          if (withFramework) {
            const a = fwModel.selectedAtoms({});
            for (let k = 0; k < a.length; k++) {
              a[k].x = fw.A[3 * k] + (fw.B[3 * k] - fw.A[3 * k]) * f;
              a[k].y = fw.A[3 * k + 1] + (fw.B[3 * k + 1] - fw.A[3 * k + 1]) * f;
              a[k].z = fw.A[3 * k + 2] + (fw.B[3 * k + 2] - fw.A[3 * k + 2]) * f;
            }
            fwModel.molObj = null;
          }
          drawHalos(f);
        };

        viewer.zoomTo();
        viewer.rotate(-18, 'x');
        viewer.rotate(28, 'y');
        viewer.zoom(small ? 1.05 : 1.12);

        // animation: spin slowly; play the relaxation, hold, start over. Reduced motion: relaxed pose, still.
        let isPaused = reducedMotion();
        let visible = true;
        let t0 = performance.now();
        let last = performance.now();
        let lastPhase: Phase | null = null;
        let lastF = -1;
        const setP = (p: Phase) => {
          if (p !== lastPhase) {
            lastPhase = p;
            setPhase(p);
          }
        };
        const tick = (now: number) => {
          raf = requestAnimationFrame(tick);
          const dt = Math.min(64, now - last);
          last = now;
          if (!visible || dead) return;
          if (isPaused) {
            t0 += dt;
            return;
          }
          viewer.rotate(dt * 0.0045, 'y');
          const el2 = (now - t0) % (HOLD_INITIAL + MORPH + HOLD_RELAXED);
          let f = 0;
          if (el2 < HOLD_INITIAL) {
            f = 0;
            setP('initial');
          } else if (el2 < HOLD_INITIAL + MORPH) {
            f = easeInOut((el2 - HOLD_INITIAL) / MORPH);
            setP('relaxing');
          } else {
            f = 1;
            setP('relaxed');
          }
          if (f !== lastF) {
            setGeom(f, true);
            lastF = f;
          }
          viewer.render();
        };
        if (isPaused) {
          setGeom(1, true);
          setP('relaxed');
          viewer.render();
        } else {
          setGeom(0, true);
          viewer.render();
        }
        raf = requestAnimationFrame(tick);
        ctl.current = {
          replay: () => {
            t0 = performance.now();
            lastF = -1;
            if (isPaused) {
              isPaused = false;
              setPaused(false);
            }
          },
          setPaused: (p: boolean) => {
            isPaused = p;
            if (p) {
              // stop where it is
              viewer.render();
            }
          },
          visible: (v: boolean) => {
            visible = v;
          },
        };
        ro = new ResizeObserver(() => {
          viewer?.resize();
          viewer?.render();
        });
        ro.observe(el);
        el.addEventListener('pointerdown', () => {
          isPaused = true;
          setPaused(true);
        });
        el.setAttribute('data-ready', '1');
        setStatus('ready');
      })
      .catch((e) => {
        console.error('hero', e);
        if (!dead) setStatus('error');
      });
    return () => {
      dead = true;
      cancelAnimationFrame(raf);
      ro?.disconnect();
      ctl.current = null;
      try {
        viewer?.clear();
      } catch {
        /* noop */
      }
    };
  }, [near, hero]);

  const phaseText =
    phase === 'initial' ? 'ODAC initial placement' : phase === 'relaxing' ? 'relaxing' : 'DFT-relaxed geometry';

  return (
    <figure className="specimen" ref={frame} aria-label={hero ? `3D model of MOF ${hero.id}, ${hero.formula}, a DFT hit from ODAC23` : '3D model of a MOF'}>
      <div className="specimen-frame">
        <div ref={host} className="specimen-host" data-phase={phase} data-status={status} />
        {status !== 'ready' && (
          <div className="specimen-empty mono">
            {status === 'nogl' ? '3D view needs WebGL.' : status === 'error' ? 'Structure not in this build.' : ''}
          </div>
        )}
        <div className="specimen-tl mono" aria-hidden={status !== 'ready'}>
          <span className="specimen-dot" data-phase={phase} />
          CO2 · {phaseText}
        </div>
        {status === 'ready' && (
          <div className="specimen-ctl">
            <button
              type="button"
              onClick={() => {
                const p = !paused;
                setPaused(p);
                ctl.current?.setPaused(p);
              }}
            >
              {paused ? 'play' : 'pause'}
            </button>
            <button type="button" onClick={() => ctl.current?.replay()}>
              replay
            </button>
          </div>
        )}
        {hero && (
          <figcaption className="specimen-cap mono">
            <span className="specimen-id">
              {hero.id} · <Formula f={hero.formula} /> · {hero.pool}
            </span>
            <span>
              E<sub>CO2</sub> {num(hero.E_CO2, 2)} eV · E<sub>H2O</sub> {num(hero.E_H2O, 2)} eV <span className="faint">· DFT, ODAC23</span>
            </span>
            <span className="faint specimen-legend">
              2×2×{status === 'ready' && host.current && host.current.clientWidth < 640 ? 1 : 2} cells · surface: where a CO2 centre fits ({hero.pore.probe_A} Å probe) · LCD {num(hero.lcd_A, 1)} Å
            </span>
          </figcaption>
        )}
      </div>
    </figure>
  );
}
