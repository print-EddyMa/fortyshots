import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { LlmStep, NoteItem, RunState, ToolLine, TraceItem } from '../lib/replay';
import { hhmmss, mutationText, num, ROLE_LABEL } from '../lib/format';
import { runAsset } from '../lib/data';

function argSummary(t: ToolLine): string {
  const a = t.args ?? {};
  switch (t.name) {
    case 'request_dft':
    case 'get_descriptors':
    case 'cheap_score':
      return String(Array.isArray(a.ids) ? a.ids.length : '');
    case 'query_pool': {
      const f = a.filters ?? {};
      const bits = Object.entries(f)
        .filter(([k]) => k !== 'exclude_bought')
        .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('|') : v}`);
      return [`${a.sort_by ?? ''}${a.ascending === false ? '↓' : '↑'}`, ...bits, a.limit != null ? `n=${a.limit}` : ''].filter(Boolean).join(', ');
    }
    case 'render_structure':
      return a.id ?? '';
    case 'fit_surrogate':
      return `κ=${a.kappa ?? 1}, top ${a.top_k ?? ''}`;
    default:
      return Object.keys(a).length ? JSON.stringify(a).slice(0, 40) : '';
  }
}

function secs(ms: number | null | undefined) {
  if (ms == null) return '';
  return ms < 1000 ? `${(ms / 1000).toFixed(1)} s` : `${(ms / 1000).toFixed(1)} s`;
}

function ToolRow({ t, runId }: { t: ToolLine; runId: string }) {
  return (
    <div className={`tool ${t.done ? '' : 'running'}`} data-call={t.call_id}>
      <span className="tool-call">
        <span className="faint">→ </span>
        {t.name}
        <span className="faint">(</span>
        {argSummary(t)}
        <span className="faint">)</span>
      </span>
      <span className="tool-dur faint">{t.done ? secs(t.dur_ms) : '…'}</span>
      {t.done && <span className={`tool-sum ${t.ok === false ? 'fail' : ''}`}>{t.summary}</span>}
      <span className="tool-id faint">{t.call_id}</span>
      {t.image && t.name !== 'render_structure' && (
        <img className="tool-img" src={runAsset(runId, t.image)} alt={`Image the agent saw: ${t.summary ?? t.name}`} loading="lazy" width={512} height={512} />
      )}
    </div>
  );
}

function Clamp({ text, lines = 3 }: { text: string; lines?: number }) {
  const [open, setOpen] = useState(false);
  const [over, setOver] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setOver(el.scrollHeight > el.clientHeight + 2);
  }, [text, open]);
  return (
    <div className="clamp-wrap">
      <p ref={ref} className={`reasoning ${open ? 'open' : ''}`} style={{ WebkitLineClamp: open ? 'unset' : lines }}>
        {text}
      </p>
      {(over || open) && (
        <button type="button" className="link-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? 'show less' : 'show full'}
        </button>
      )}
    </div>
  );
}

function Cited({ text, onCite }: { text: string; onCite: (c: string) => void }) {
  const parts = text.split(/(\[c\d{4}\])/g);
  return (
    <>
      {parts.map((p, i) => {
        const m = /^\[(c\d{4})\]$/.exec(p);
        return m ? (
          <button key={i} type="button" className="cite" onClick={() => onCite(m[1])}>
            {m[1]}
          </button>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        );
      })}
    </>
  );
}

function Step({ s, runId }: { s: LlmStep; runId: string }) {
  const tm = s.timings;
  return (
    <>
      <div className="t-head">
        <span className="t-role">{ROLE_LABEL[s.role] ?? s.role}</span>
        <span className="t-model mono">
          {s.model}
          {s.strength ? ` · ${s.strength}` : ''}
          {s.schema ? ` · ${s.schema}` : ''}
          {s.n_images ? ` · ${s.n_images} image${s.n_images > 1 ? 's' : ''}` : ''}
        </span>
        <span className="t-time mono faint">{hhmmss(s.t)}</span>
      </div>
      {s.reasoning && <Clamp text={s.reasoning} />}
      {s.message && <p className="t-msg">{s.message}</p>}
      {s.tools.map((t) => (
        <ToolRow key={t.call_id} t={t} runId={runId} />
      ))}
      {tm && (
        <div className="t-stats mono faint">
          {s.call_id} · {s.tokens ? `${(s.tokens.prompt / 1000).toFixed(1)}k in · ${s.tokens.reasoning + s.tokens.completion} out · ` : ''}
          {num(tm.predicted_per_second, 1)} tok/s · {secs(s.dur_ms)}
        </div>
      )}
    </>
  );
}

function Note({ n, runId, onCite }: { n: NoteItem; runId: string; onCite: (c: string) => void }): ReactNode {
  const p = n.payload ?? {};
  const head = (label: string, extra?: ReactNode) => (
    <div className="t-head">
      <span className="t-role">{label}</span>
      {extra && <span className="t-model mono">{extra}</span>}
      <span className="t-time mono faint">{hhmmss(n.t)}</span>
    </div>
  );
  switch (n.kind) {
    case 'header':
      return (
        <>
          {head('Run started', `${p.arm} · ${p.pool} · seed ${p.seed}`)}
          <p className="t-body">
            Budget {p.budget} DFT lookups in {p.rounds} rounds of {p.batch}. Mode {p.mode}. Network {p.network}.
          </p>
        </>
      );
    case 'proposal': {
      const picks = p.picks ?? [];
      const mean = picks.length ? picks.reduce((a: number, b: any) => a + (b.p_hit ?? 0), 0) / picks.length : null;
      return (
        <>
          {head('Proposer', p.stage === 'revised' ? 'Revised batch' : p.stage === 'probe' ? 'Probe batch' : 'Batch committed')}
          <p className="t-body">
            {picks.length} picks, mean P(hit) <span className="mono">{num(mean, 2)}</span>, stated before DFT.
            {Array.isArray(p.replaced) && p.replaced.length > 0 && (
              <>
                {' '}
                Replaced <span className="mono">{p.replaced.map((r: any) => (typeof r === 'string' ? r : r.id ?? JSON.stringify(r))).join(', ')}</span>.
              </>
            )}
          </p>
          <p className="t-ids mono faint">{picks.map((x: any) => x.id).join(' ')}</p>
        </>
      );
    }
    case 'critique':
      return (
        <>
          {head(
            'Critic',
            <>
              {p.visual_rho != null ? <>visual ρ {num(p.visual_rho, 2, true)}</> : 'review'}
              {p.vision === false ? ' · text table, no vision' : p.vision ? ' · vision' : ''}
            </>,
          )}
          {p.notes && <p className="t-body">{p.notes}</p>}
          {(p.vetoes ?? []).map((v: any) => (
            <p key={v.id} className="t-veto mono">
              <span className="faint">veto </span>
              {v.id}
              <span className="faint"> · {v.reason}</span>
            </p>
          ))}
        </>
      );
    case 'human':
      return (
        <>
          {head('Human gate', p.action === 'auto' ? 'auto' : p.action)}
          <p className="t-body muted">{p.action === 'auto' ? `Auto-approved: ${p.note || 'recorded run'}.` : `${p.action} ${p.ids?.length ?? 0} · ${p.note ?? ''}`}</p>
        </>
      );
    case 'dft_order':
      return null;
    case 'tool':
      return n.tool ? (
        <>
          {head(ROLE_LABEL[n.role] ?? n.role, 'tool')}
          <ToolRow t={n.tool} runId={runId} />
        </>
      ) : null;
    case 'dft_result': {
      const hits = (p.results ?? []).filter((r: any) => r.hit);
      return (
        <>
          {head('DFT revealed', `${p.spent} / 40`)}
          <p className="t-body">
            {(p.results ?? []).length} results, {hits.length} hit{hits.length === 1 ? '' : 's'}. {p.hits_total} total.
          </p>
          {hits.length > 0 && (
            <p className="t-ids mono">
              {hits.map((h: any) => (
                <span key={h.id} className="t-hit">
                  <span className="dot pass" aria-hidden /> {h.id} {num(h.E_CO2, 2)} eV
                </span>
              ))}
            </p>
          )}
        </>
      );
    }
    case 'calibration':
      return (
        <>
          {head('Calibration', `n = ${p.n}`)}
          <p className="t-body mono">
            Spearman {num(p.spearman, 2, true)} · hit AUC {num(p.auc, 2)}
          </p>
          {p.image && <img className="tool-img" src={runAsset(runId, p.image)} alt={`Parity plot the Critic read, n = ${p.n}`} loading="lazy" width={512} height={512} />}
        </>
      );
    case 'error':
      return (
        <>
          {head('Error', p.where)}
          <p className="t-body t-err">
            <span className="fail">{p.message}</span>
            <span className={p.recovered ? 'pass-text' : 'fail'}> · {p.recovered ? 'recovered' : 'not recovered'}</span>
          </p>
        </>
      );
    case 'report':
      return (
        <>
          {head('Reporter', p.source === 'template' ? 'template report' : 'report')}
          <p className="t-report">
            <Cited text={p.text ?? ''} onCite={onCite} />
          </p>
          <p className="small">
            {p.source === 'template'
              ? 'Template report built from the ledger (the model output was not usable).'
              : `written by ${p.written_by && !/mock/i.test(String(p.written_by)) ? p.written_by : p.written_by ? `${p.written_by} (mock)` : 'Muse Glimmer 30B'}`}
          </p>
        </>
      );
    case 'claimcheck':
      return (
        <>
          {head('Claim check', 'deterministic')}
          <p className="t-body mono">
            {p.n_claims} claims · {p.verified} verified · <span className={(p.flagged ?? []).length ? 'fail' : ''}>{(p.flagged ?? []).length} flagged</span>
          </p>
          {(p.flagged ?? []).map((f: any, i: number) => (
            <p key={i} className="t-flag fail">
              “{f.claim}” — {f.reason}
            </p>
          ))}
        </>
      );
    case 'summary':
      return (
        <>
          {head('Campaign finished', p.aborted ? 'aborted' : undefined)}
          <p className="t-body mono">
            {p.hits} hits / {p.spent} · first hit at {p.lookups_to_first_hit ?? '—'} · Brier {num(p.brier, 3)}
            {p.leak ? ` · leak AUC ${num(p.leak.auc, 2)} (n = ${p.leak.n})` : ''}
          </p>
        </>
      );
    case 'mutation':
      // proposals come from the Chemist (ledger); built/invalid results from the container (mutations.jsonl sidecar)
      return (
        <>
          {head(p.status === 'proposed' ? 'Chemist' : 'Prospective build', p.status === 'proposed' ? 'mutation' : 'container · MACE')}
          <p className="t-body mono">
            {p.parent} → {typeof p.spec === 'string' ? p.spec : mutationText(p.spec)} ·{' '}
            <span className={p.status === 'invalid' ? 'fail' : p.status === 'built' ? 'pass-text' : 'faint'}>{p.status}</span>
            {p.mutant_id ? ` · ${p.mutant_id}` : ''}
          </p>
          {p.note && <p className="small muted">{p.note}</p>}
          <p className="small faint">unvalidated · ML only</p>
        </>
      );
    case 'message':
    case 'reasoning':
      return (
        <>
          {head(ROLE_LABEL[n.role] ?? n.role)}
          <Clamp text={p.text ?? ''} />
        </>
      );
    default:
      return (
        <>
          {head(n.kind)}
          <p className="t-body mono faint">{JSON.stringify(p).slice(0, 160)}</p>
        </>
      );
  }
}

export function Trace({ state, runId, focusCall, active }: { state: RunState; runId: string; focusCall: string | null; active: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const items = state.trace.filter((t) => !(t.type === 'note' && t.kind === 'dft_order'));

  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [items.length]);

  // Images and expanding text change the height after render: keep following the end.
  useEffect(() => {
    const el = box.current;
    const inner = el?.firstElementChild?.nextElementSibling ?? el?.querySelector('.timeline');
    if (!el || !inner) return;
    const ro = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(inner);
    return () => ro.disconnect();
  }, []);

  const scrollToCall = (cid: string) => {
    const el = box.current?.querySelector<HTMLElement>(`[data-call="${cid}"]`);
    if (el) {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      el.classList.remove('flash');
      void el.offsetWidth;
      el.classList.add('flash');
    }
  };
  useEffect(() => {
    if (focusCall) {
      stick.current = false;
      requestAnimationFrame(() => scrollToCall(focusCall));
    }
  }, [focusCall]);

  return (
    <div
      ref={box}
      className="trace"
      onScroll={(e) => {
        const el = e.currentTarget;
        if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) stick.current = true;
      }}
      onWheel={(e) => {
        if (e.deltaY < 0) stick.current = false;
      }}
      onTouchMove={() => {
        stick.current = false;
      }}
      onKeyDown={(e) => {
        if (e.key === 'ArrowUp' || e.key === 'PageUp' || e.key === 'Home') stick.current = false;
      }}
      aria-live="off"
    >
      <ol className="timeline">
        {items.length === 0 && <li className="muted small">The trace fills in as the agent works.</li>}
        {items.map((it: TraceItem, i) => {
          const last = i === items.length - 1;
          const callAttr = it.type === 'llm' ? it.call_id : it.call_id ?? undefined;
          return (
            <li key={it.key} className={`t-item fade-up ${last && active ? 'current' : ''} k-${it.type === 'llm' ? 'llm' : it.kind}`} data-call={callAttr}>
              <span className="t-marker" aria-hidden />
              {it.type === 'llm' ? <Step s={it} runId={runId} /> : <Note n={it} runId={runId} onCite={scrollToCall} />}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
