import { useEffect, useRef, useState, type ReactNode } from 'react';
import { num } from '../lib/format';

/** Width of an element, tracked with ResizeObserver. */
export function useWidth<T extends HTMLElement>(initial = 600): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth || initial);
    const ro = new ResizeObserver(([e]) => setW(Math.max(120, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [initial]);
  return [ref, w];
}

function ticks(lo: number, hi: number, n = 5): number[] {
  const span = hi - lo || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v / step) * step);
  return out;
}

/** Spread inline labels vertically so they do not overlap. */
function spread<T extends { y: number }>(items: T[], gap = 13, min = 0, max = Infinity): T[] {
  const s = [...items].sort((a, b) => a.y - b.y);
  for (let i = 1; i < s.length; i++) if (s[i].y - s[i - 1].y < gap) s[i].y = s[i - 1].y + gap;
  const over = s.length ? s[s.length - 1].y - max : 0;
  if (over > 0) for (const x of s) x.y -= over;
  for (let i = 0; i < s.length; i++) if (s[i].y < min) s[i].y = min + i * gap;
  return s;
}

export interface CurveSeries {
  key: string;
  label: string;
  curve: number[];
  lo?: number[];
  hi?: number[];
  tone: 'fg' | 'muted' | 'faint' | 'accent';
  band?: boolean;
  dashed?: boolean;
  step?: boolean;
  width?: number;
  digits?: number;
}

const toneVar = { fg: 'var(--fg)', muted: 'var(--fg-muted)', faint: 'var(--fg-faint)', accent: 'var(--accent)' };

export function DiscoveryChart({
  series,
  budget = 40,
  height = 220,
  current,
  title,
  labelWidth = 120,
}: {
  series: CurveSeries[];
  budget?: number;
  height?: number;
  current?: { k: number; hits: number; label?: string } | null;
  title?: string;
  labelWidth?: number;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const narrow = w < 420;
  const m = { l: 28, r: narrow ? 8 : labelWidth, t: 10, b: 32 };
  const iw = Math.max(40, w - m.l - m.r);
  const ih = height - m.t - m.b;
  const ymaxRaw = Math.max(
    2,
    ...series.flatMap((s) => [...s.curve, ...(s.hi ?? [])].slice(0, budget + 1)),
    current ? current.hits : 0,
  );
  const ymax = Math.ceil(ymaxRaw * 1.08);
  const x = (k: number) => m.l + (k / budget) * iw;
  const y = (v: number) => m.t + ih - (v / ymax) * ih;
  const path = (c: number[], step = false) =>
    c
      .slice(0, budget + 1)
      .map((v, k) => (k === 0 ? `M${x(0)},${y(v)}` : step ? `H${x(k)}V${y(v)}` : `L${x(k)},${y(v)}`))
      .join('');
  const area = (lo: number[], hi: number[]) => {
    const n = Math.min(lo.length, hi.length, budget + 1);
    let d = `M${x(0)},${y(hi[0])}`;
    for (let k = 1; k < n; k++) d += `L${x(k)},${y(hi[k])}`;
    for (let k = n - 1; k >= 0; k--) d += `L${x(k)},${y(lo[k])}`;
    return d + 'Z';
  };
  const labels = spread(
    series
      .filter((s) => s.curve.length)
      .map((s) => {
        const last = s.curve[Math.min(budget, s.curve.length - 1)];
        return { key: s.key, text: `${s.label} ${num(last, s.digits ?? 1)}`, y: y(last) + 4, tone: s.tone };
      }),
    13,
    m.t + 4,
    m.t + ih + 4,
  );
  return (
    <div ref={ref} className="chart-wrap">
      <svg className="chart" width={w} height={height} role="img" aria-label={title ?? 'Discovery curve: hits against DFT lookups'}>
        <g className="grid">
          {ticks(0, ymax, 4).map((v) => (
            <line key={v} x1={m.l} x2={m.l + iw} y1={y(v)} y2={y(v)} />
          ))}
        </g>
        {ticks(0, ymax, 4).map((v) => (
          <text key={v} x={m.l - 8} y={y(v) + 4} textAnchor="end">
            {v}
          </text>
        ))}
        {[0, 10, 20, 30, 40].filter((k) => k <= budget).map((k) => (
          <text key={k} x={x(k)} y={m.t + ih + 16} textAnchor="middle">
            {k}
          </text>
        ))}
        <line className="axis" x1={m.l} x2={m.l + iw} y1={m.t + ih + 0.5} y2={m.t + ih + 0.5} />
        <text className="axis-title" x={m.l + iw} y={height - 2} textAnchor="end">
          DFT lookups
        </text>
        {series
          .filter((s) => s.band && s.lo && s.hi)
          .map((s) => (
            <path key={`b-${s.key}`} d={area(s.lo!, s.hi!)} fill={s.tone === 'fg' ? 'var(--band-2)' : 'var(--band-1)'} />
          ))}
        {series.map((s) => (
          <path
            key={s.key}
            d={path(s.curve, s.step)}
            fill="none"
            stroke={toneVar[s.tone]}
            strokeWidth={s.width ?? 1}
            strokeDasharray={s.dashed ? '3 3' : undefined}
            strokeLinejoin="round"
          />
        ))}
        {current && current.k > 0 && (
          <g>
            <circle cx={x(current.k)} cy={y(current.hits)} r={3.5} fill="var(--accent)" />
          </g>
        )}
        {!narrow &&
          labels.map((l) => (
            <text key={l.key} x={m.l + iw + 8} y={l.y} className={l.tone === 'fg' ? 'lbl-fg' : l.tone === 'accent' ? 'lbl-accent' : 'lbl-muted'}>
              {l.text}
            </text>
          ))}
      </svg>
      {narrow && (
        <div className="chart-legend small">
          {series.map((s) => (
            <span key={s.key}>
              <i style={{ background: toneVar[s.tone] }} aria-hidden /> {s.label} {num(s.curve[Math.min(budget, s.curve.length - 1)], s.digits ?? 1)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export interface ArmPoint {
  key: string;
  label: string;
  mean: number;
  lo: number;
  hi: number;
  strong?: boolean;
  sub?: string;
}

export function ArmsChart({ arms, reference, height }: { arms: ArmPoint[]; reference?: { v: number; label: string }; height?: number }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const narrow = w < 460;
  const rowH = 34;
  const m = { l: narrow ? 116 : 168, r: 56, t: 8, b: 30 };
  const h = height ?? m.t + m.b + rowH * arms.length;
  const iw = Math.max(40, w - m.l - m.r);
  const xmax = Math.max(2, ...arms.map((a) => a.hi)) * 1.1;
  const x = (v: number) => m.l + (v / xmax) * iw;
  return (
    <div ref={ref} className="chart-wrap">
      <svg className="chart" width={w} height={h} role="img" aria-label="Mean hits at 40 lookups by arm, with 95% intervals">
        <g className="grid">
          {ticks(0, xmax, narrow ? 3 : 5).map((v) => (
            <line key={v} x1={x(v)} x2={x(v)} y1={m.t} y2={h - m.b} />
          ))}
        </g>
        {ticks(0, xmax, narrow ? 3 : 5).map((v) => (
          <text key={v} x={x(v)} y={h - m.b + 16} textAnchor="middle">
            {v}
          </text>
        ))}
        <text className="axis-title" x={m.l + iw} y={h - 2} textAnchor="end">
          hits at 40 lookups
        </text>
        {reference && (
          <g>
            <line x1={x(reference.v)} x2={x(reference.v)} y1={m.t} y2={h - m.b} stroke="var(--rule-strong)" strokeDasharray="3 3" />
          </g>
        )}
        {arms.map((a, i) => {
          const cy = m.t + rowH * i + rowH / 2;
          return (
            <g key={a.key}>
              <text x={0} y={cy + 4} className={a.strong ? 'axis-title lbl-fg' : 'axis-title'} style={{ fontSize: 13 }}>
                {a.label}
              </text>
              <line x1={x(a.lo)} x2={x(a.hi)} y1={cy} y2={cy} stroke={a.strong ? 'var(--fg)' : 'var(--fg-faint)'} strokeWidth={a.strong ? 1.5 : 1} />
              <line x1={x(a.lo)} x2={x(a.lo)} y1={cy - 4} y2={cy + 4} stroke={a.strong ? 'var(--fg)' : 'var(--fg-faint)'} />
              <line x1={x(a.hi)} x2={x(a.hi)} y1={cy - 4} y2={cy + 4} stroke={a.strong ? 'var(--fg)' : 'var(--fg-faint)'} />
              <circle cx={x(a.mean)} cy={cy} r={a.strong ? 4.5 : 3.5} fill={a.strong ? 'var(--fg)' : 'var(--bg)'} stroke={a.strong ? 'var(--fg)' : 'var(--fg-muted)'} strokeWidth={1.25} />
              <text x={m.l + iw + 10} y={cy + 4} className={a.strong ? 'lbl-fg' : undefined}>
                {num(a.mean, 1)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export interface ScatterPoint {
  x: number;
  y: number;
  strong?: boolean;
  id?: string;
}

export function ScatterChart({
  points,
  xLabel,
  yLabel,
  diagonal = true,
  height = 300,
  annotation,
  domain,
  ariaLabel,
}: {
  points: ScatterPoint[];
  xLabel: string;
  yLabel: string;
  diagonal?: boolean;
  height?: number;
  annotation?: ReactNode;
  domain?: [number, number];
  ariaLabel: string;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const m = { l: 40, r: 12, t: 12, b: 34 };
  const iw = Math.max(40, w - m.l - m.r);
  const ih = height - m.t - m.b;
  const finite = points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  const all = finite.flatMap((p) => [p.x, p.y]).sort((a, b) => a - b);
  const q = (f: number) => all[Math.min(all.length - 1, Math.max(0, Math.floor(f * (all.length - 1))))] ?? 0;
  const lo = domain ? domain[0] : q(0.01);
  const hi = domain ? domain[1] : q(0.99);
  const inside = finite.filter((p) => p.x >= lo && p.x <= hi && p.y >= lo && p.y <= hi);
  const outside = finite.length - inside.length;
  const pad = (hi - lo) * 0.04 || 0.1;
  const d0 = lo - pad;
  const d1 = hi + pad;
  const x = (v: number) => m.l + ((v - d0) / (d1 - d0)) * iw;
  const y = (v: number) => m.t + ih - ((v - d0) / (d1 - d0)) * ih;
  const tk = ticks(d0, d1, 4);
  const sorted = [...inside].sort((a, b) => Number(!!a.strong) - Number(!!b.strong));
  return (
    <div ref={ref} className="chart-wrap">
      <svg className="chart" width={w} height={height} role="img" aria-label={ariaLabel}>
        <g className="grid">
          {tk.map((v) => (
            <line key={v} x1={m.l} x2={m.l + iw} y1={y(v)} y2={y(v)} />
          ))}
        </g>
        {tk.map((v) => (
          <text key={`y${v}`} x={m.l - 6} y={y(v) + 4} textAnchor="end">
            {num(v, 1)}
          </text>
        ))}
        {tk.map((v) => (
          <text key={`x${v}`} x={x(v)} y={m.t + ih + 16} textAnchor="middle">
            {num(v, 1)}
          </text>
        ))}
        {diagonal && <line x1={x(d0)} y1={y(d0)} x2={x(d1)} y2={y(d1)} stroke="var(--rule-strong)" strokeDasharray="4 4" />}
        {sorted.map((p, i) =>
          p.strong ? (
            <circle key={p.id ?? i} cx={x(p.x)} cy={y(p.y)} r={3} fill="var(--fg)" />
          ) : (
            <circle key={p.id ?? i} cx={x(p.x)} cy={y(p.y)} r={2.5} fill="none" stroke="var(--fg-faint)" strokeOpacity={0.6} />
          ),
        )}
        <text className="axis-title" x={m.l + iw} y={height - 2} textAnchor="end">
          {xLabel}
        </text>
        <text className="axis-title" x={m.l + 4} y={m.t + 10}>
          {yLabel}
        </text>
      </svg>
      {(annotation || outside > 0) && (
        <div className="small chart-note mono">
          {annotation}
          {outside > 0 ? ` · ${outside} outside the plotted range` : ''}
        </div>
      )}
    </div>
  );
}

export function ReliabilityChart({ bins, height = 260 }: { bins: { p_mean: number; hit_rate: number; n: number }[]; height?: number }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const m = { l: 40, r: 12, t: 12, b: 34 };
  const iw = Math.max(40, w - m.l - m.r);
  const ih = height - m.t - m.b;
  const top = Math.min(1, Math.max(0.5, ...bins.map((b) => Math.max(b.p_mean, b.hit_rate))) * 1.1);
  const x = (v: number) => m.l + (v / top) * iw;
  const y = (v: number) => m.t + ih - (v / top) * ih;
  const tk = ticks(0, top, 4);
  return (
    <div ref={ref} className="chart-wrap">
      <svg className="chart" width={w} height={height} role="img" aria-label="Reliability: stated P(hit) against observed hit rate">
        <g className="grid">
          {tk.map((v) => (
            <line key={v} x1={m.l} x2={m.l + iw} y1={y(v)} y2={y(v)} />
          ))}
        </g>
        {tk.map((v) => (
          <text key={`y${v}`} x={m.l - 6} y={y(v) + 4} textAnchor="end">
            {num(v, 1)}
          </text>
        ))}
        {tk.map((v) => (
          <text key={`x${v}`} x={x(v)} y={m.t + ih + 16} textAnchor="middle">
            {num(v, 1)}
          </text>
        ))}
        <line x1={x(0)} y1={y(0)} x2={x(top)} y2={y(top)} stroke="var(--rule-strong)" strokeDasharray="4 4" />
        <path d={bins.map((b, i) => `${i ? 'L' : 'M'}${x(b.p_mean)},${y(b.hit_rate)}`).join('')} fill="none" stroke="var(--fg-muted)" />
        {bins.map((b, i) => (
          <g key={i}>
            <circle cx={x(b.p_mean)} cy={y(b.hit_rate)} r={3.5} fill="var(--fg)" />
            <text x={x(b.p_mean) + 7} y={y(b.hit_rate) - 6}>
              n={b.n}
            </text>
          </g>
        ))}
        <text className="axis-title" x={m.l + iw} y={height - 2} textAnchor="end">
          stated P(hit)
        </text>
        <text className="axis-title" x={m.l + 4} y={m.t + 10}>
          observed hit rate
        </text>
      </svg>
    </div>
  );
}

export function Sparkline({ values, height = 48, label }: { values: number[]; height?: number; label: string }) {
  const [ref, w] = useWidth<HTMLDivElement>(300);
  if (!values.length) return <div ref={ref} className="faint small">—</div>;
  const lo = Math.min(...values) * 0.95;
  const hi = Math.max(...values) * 1.05;
  const x = (i: number) => (values.length === 1 ? w / 2 : 2 + (i / (values.length - 1)) * (w - 4));
  const y = (v: number) => 4 + (height - 8) * (1 - (v - lo) / (hi - lo || 1));
  return (
    <div ref={ref}>
      <svg className="chart" width={w} height={height} role="img" aria-label={label}>
        <line className="axis" x1={0} x2={w} y1={height - 0.5} y2={height - 0.5} />
        <path d={values.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join('')} fill="none" stroke="var(--fg-muted)" strokeWidth={1.25} />
        <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r={2.5} fill="var(--fg)" />
      </svg>
    </div>
  );
}
