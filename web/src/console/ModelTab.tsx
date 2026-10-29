import type { RunState } from '../lib/replay';
import { num, shortSha } from '../lib/format';
import { Sparkline } from '../components/Charts';
import { modelPill } from './TopBar';

export function ModelTab({ state }: { state: RunState }) {
  const h = state.header;
  const L = state.llm;
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  const cached = L.reduce((a, b) => a + b.cache_n, 0);
  const fresh = L.reduce((a, b) => a + b.prompt_n, 0);
  const out = L.reduce((a, b) => a + b.predicted_n, 0);
  const pill = modelPill(state);
  const kv: [string, React.ReactNode][] = h
    ? [
        ['GGUF', h.model.gguf],
        ['sha256', h.model.sha256 ? <span title={h.model.sha256}>{h.model.sha256.slice(0, 16)}…</span> : '—'],
        ['Quant', h.model.quant],
        ['Server build', h.model.server_build],
        ['Flags', h.model.flags.join(' ')],
        ['Machine', `${h.machine.chip} · ${h.machine.ram_gb} GB · ${h.machine.os}`],
        ['Network', h.network],
        ['Prereg', shortSha(h.prereg_sha)],
        ['Git', shortSha(h.git_sha)],
      ]
    : [];
  return (
    <div className="model-tab" tabIndex={0} aria-label="Model attestation and throughput">
      {pill && <span className="pill model-pill-tab">{pill}</span>}
      <div className="c-head">
        <span>Attestation</span>
        <span className="faint mono-sm">from the run header</span>
      </div>
      <dl className="kv">
        {kv.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className="mono-sm">{v}</dd>
          </div>
        ))}
      </dl>
      <div className="c-head">
        <span>Decode speed</span>
        <span className="faint mono-sm">tok/s per call</span>
      </div>
      <Sparkline values={L.map((x) => x.tps)} label="Decode tokens per second for each model call" />
      <dl className="kv kv-nums">
        <div>
          <dt>Model calls</dt>
          <dd className="mono-sm">{L.length}</dd>
        </div>
        <div>
          <dt>Decode</dt>
          <dd className="mono-sm">{num(mean(L.map((x) => x.tps)), 1)} tok/s</dd>
        </div>
        <div>
          <dt>Prefill</dt>
          <dd className="mono-sm">{num(mean(L.map((x) => x.pps)), 0)} tok/s</dd>
        </div>
        <div>
          <dt>Prompt cache hits</dt>
          <dd className="mono-sm">{cached + fresh ? `${Math.round((100 * cached) / (cached + fresh))}%` : '—'} <span className="faint">({cached.toLocaleString('en-US')} tok)</span></dd>
        </div>
        <div>
          <dt>Generated</dt>
          <dd className="mono-sm">{out.toLocaleString('en-US')} tok</dd>
        </div>
        <div>
          <dt>Schema failures</dt>
          <dd className="mono-sm">{state.schemaFailures}</dd>
        </div>
        <div>
          <dt>Errors</dt>
          <dd className="mono-sm">
            {state.errors.length} <span className="faint">({state.errors.filter((e) => e.recovered).length} recovered)</span>
          </dd>
        </div>
      </dl>
    </div>
  );
}
