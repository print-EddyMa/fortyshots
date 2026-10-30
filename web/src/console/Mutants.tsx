import { useState } from 'react';
import type { RunState } from '../lib/replay';
import type { MutantIndex, MutantRow } from '../lib/types';
import { postHuman } from '../lib/data';
import { mutationText, num } from '../lib/format';
import { Formula } from '../components/Formula';

// Prospective mutants (physics/prospective.py): the Chemist's specs, built, MACE-relaxed, checked and ranked by the
// run's own surrogate. Unvalidated, ML only, never in a benchmark number. A human approves or rejects each one:
// static pages keep the decision in this browser, live mode posts it to the run (round = rounds + 1, the mutant id).

type MDecision = { action: 'approve' | 'reject'; reason: string; at: string; sent: 'local' | 'ok' | 'pending' };

const storeKey = (runId: string) => `fortyshots:mutants:${runId}`;

function loadLocal(runId: string): Record<string, MDecision> {
  try {
    return JSON.parse(localStorage.getItem(storeKey(runId)) ?? '{}');
  } catch {
    return {};
  }
}

function saveLocal(runId: string, d: Record<string, MDecision>) {
  try {
    localStorage.setItem(storeKey(runId), JSON.stringify(d));
  } catch {
    /* storage blocked: decisions stay in memory */
  }
}

const pctSigned = (v: number | null | undefined) => (v == null ? '—' : `${v < 0 ? '−' : '+'}${Math.abs(v * 100).toFixed(1)}%`);

function Checks({ m }: { m: MutantRow }) {
  const v = m.validity;
  if (!v) return <p className="m-checks mono-sm faint">{m.reason ?? 'not built'}</p>;
  return (
    <p className="m-checks mono-sm">
      <span className={`dot ${v.valid ? 'pass' : 'fail'}`} aria-hidden /> <span className={v.valid ? '' : 'fail'}>{v.valid ? 'valid' : 'invalid'}</span>
      <span className="faint">
        {' '}
        · clash {num(v.clash_ratio, 2)} · ΔV {pctSigned(v.dV_frac)} · {v.converged ? `converged in ${v.steps} steps` : `not converged after ${v.steps} steps`} · charge {v.charge}
      </span>
    </p>
  );
}

function Grey({ m, note }: { m: MutantRow; note: string }) {
  if (!m.mace) return null;
  const e = m.eqv2;
  return (
    <p className="m-grey mono-sm" title={note}>
      MACE E<sub>CO2</sub> {num(m.mace.E_CO2_mace, 2)} · E<sub>H2O</sub> {num(m.mace.E_H2O_mace, 2)} · S {num(m.mace.S_mace, 2)}
      {m.parent_mace ? ` (parent S ${num(m.parent_mace.S_mace, 2)})` : ''}
      {e && e.S_eqv2 != null ? ` · eqV2 S ${num(e.S_eqv2, 2)}` : ''}
    </p>
  );
}

export function Mutants({
  index,
  state,
  runId,
  live,
  selected,
  onSelect,
}: {
  index: MutantIndex | null;
  state: RunState;
  runId: string;
  live: boolean;
  selected: string | null;
  onSelect: (m: MutantRow | null) => void;
}) {
  const [decisions, setDecisions] = useState<Record<string, MDecision>>(() => (live ? {} : loadLocal(runId)));
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  // what the replay has reached: proposals (ledger) and container results (sidecar events)
  const proposed = state.mutations.filter((m) => m.payload?.status === 'proposed');
  const results = new Map<number, any>();
  for (const m of state.mutations) if (m.payload?.proposal_seq != null) results.set(m.payload.proposal_seq, m.payload);
  if (!proposed.length) return null;

  const rows = index?.mutants ?? [];
  const byProp = new Map(rows.map((r) => [r.proposal_seq, r]));
  const visible = rows.filter((r) => proposed.some((p) => p.seq === r.proposal_seq) && results.has(r.proposal_seq));
  const waiting = proposed.filter((p) => !byProp.has(p.seq) || !results.has(p.seq));
  const nRounds = state.header?.rounds ?? 4;

  const decide = async (m: MutantRow, action: 'approve' | 'reject') => {
    const reason = (reasons[m.id] ?? '').trim();
    const d: MDecision = { action, reason, at: new Date().toISOString(), sent: live ? 'pending' : 'local' };
    const next = { ...decisions, [m.id]: d };
    setDecisions(next);
    setErr(null);
    if (!live) {
      saveLocal(runId, next);
      return;
    }
    try {
      await postHuman(runId, { action, ids: [m.id], note: `mutant ${m.id}: ${action}${reason ? ` (${reason})` : ''}`, round: nRounds + 1 });
      setDecisions((cur) => ({ ...cur, [m.id]: { ...d, sent: 'ok' } }));
    } catch (e) {
      setDecisions((cur) => {
        const c = { ...cur };
        delete c[m.id];
        return c;
      });
      setErr(`Not accepted by the live server (${String((e as Error)?.message ?? e)}).`);
    }
  };

  return (
    <section className="mutants" aria-label="Prospective mutants">
      <div className="c-head q-decided-head">
        <span>Prospective mutants</span>
        <span className="faint mono-sm">{index?.label ?? 'unvalidated · ML only'}</span>
      </div>
      {index && visible.length > 0 && (
        <p className="small muted m-note">
          Built and relaxed with MACE-MP-0, ranked by this run&rsquo;s random forest ({index.surrogate.n_train} bought labels). No DFT, and never part
          of a benchmark number.
        </p>
      )}
      {visible.length > 0 && (
        <ol className="q-list m-list" aria-label="Mutants, ranked by predicted S">
          {visible.map((m) => {
            const d = decisions[m.id];
            const cur = m.id === selected;
            const canView = !!m.file;
            return (
              <li
                key={m.id}
                className={`q-item m-item ${cur ? 'cur' : ''} ${d?.action === 'reject' ? 'rej' : ''}`}
                data-mutant={m.id}
                aria-current={cur ? 'true' : undefined}
                onClick={() => canView && onSelect(cur ? null : m)}
              >
                <div className="q-main m-main">
                  <span className="mono faint m-rank">{m.rank != null ? `#${m.rank}` : '—'}</span>
                  <span className="mono q-id">{m.id}</span>
                  <span className="mono faint q-f">
                    <Formula f={m.formula} />
                  </span>
                  <span className="mono q-p m-pred" title="Predicted DAC score S (lower is better): mean ± per-tree std of the run's RF">
                    <span className="faint">pred S </span>
                    {m.pred_S != null ? (
                      <>
                        {num(m.pred_S, 2)} <span className="faint">± {num(m.pred_std, 2)}</span>
                      </>
                    ) : (
                      '—'
                    )}
                  </span>
                </div>
                <p className="q-why m-spec">
                  <span className="mono">{m.spec_text || mutationText(m.spec)}</span>
                  <span className="muted">
                    {' '}
                    on <span className="mono">{m.parent}</span>
                    {m.parent_S != null && (
                      <>
                        {' '}
                        (DFT S {num(m.parent_S, 2)}
                        {m.parent_hit ? ', hit' : ''})
                      </>
                    )}
                  </span>
                </p>
                {m.rationale && <p className="q-why muted m-why">{m.rationale}</p>}
                <Checks m={m} />
                <Grey m={m} note={index?.mace_note ?? ''} />
                {m.status === 'built' && (
                  <div className="q-act m-act" onClick={(e) => e.stopPropagation()}>
                    {d ? (
                      <p className="small m-decided">
                        <span className={`dot ${d.action === 'approve' ? 'pass' : 'fail'}`} aria-hidden /> {d.action === 'approve' ? 'approved' : 'rejected'}
                        {d.reason ? `: ${d.reason}` : ''}
                        <span className="faint">{d.sent === 'local' ? ' · in this browser' : d.sent === 'ok' ? ' · sent' : ' · sending'}</span>
                      </p>
                    ) : (
                      <>
                        <input
                          className="m-reason mono-sm"
                          type="text"
                          maxLength={200}
                          placeholder="reason (optional)"
                          aria-label={`Reason for ${m.id}`}
                          value={reasons[m.id] ?? ''}
                          onChange={(e) => setReasons((r) => ({ ...r, [m.id]: e.target.value }))}
                        />
                        <button type="button" className="btn btn-secondary btn-xs approve" onClick={() => void decide(m, 'approve')}>
                          Approve
                        </button>
                        <button type="button" className="btn btn-secondary btn-xs reject" onClick={() => void decide(m, 'reject')}>
                          Reject
                        </button>
                      </>
                    )}
                  </div>
                )}
                {canView && !cur && <p className="mono-sm faint m-view">view relaxation ({m.n_frames} frames)</p>}
              </li>
            );
          })}
        </ol>
      )}
      {waiting.length > 0 && (
        <ul className="q-decided">
          {waiting.map((p) => (
            <li key={p.seq} className="mono-sm">
              {p.payload.parent} → {mutationText(p.payload.spec)} · <span className="faint">proposed{index ? ', building' : ', not built yet'}</span>
            </li>
          ))}
        </ul>
      )}
      {visible.some((m) => m.mace) && <p className="small faint m-mace-note">{index?.mace_note}</p>}
      {err && <p className="small fail">{err}</p>}
      {!live && Object.keys(decisions).length > 0 && (
        <button
          type="button"
          className="link-btn"
          onClick={() => {
            setDecisions({});
            saveLocal(runId, {});
          }}
        >
          clear my mutant decisions
        </button>
      )}
    </section>
  );
}
