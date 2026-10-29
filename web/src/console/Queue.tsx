import { useEffect, useRef, useState } from 'react';
import type { RunState } from '../lib/replay';
import { loadPending, postHuman } from '../lib/data';
import { num } from '../lib/format';
import { Formula } from '../components/Formula';

// The human gate takes one decision per round (CONTRACTS addenda): approve the batch, or reject some picks,
// which the agent then replaces. `r` marks the current pick for rejection; `a` sends the decision.

type Decision = { round: number; action: 'approve' | 'reject'; ids: string[]; at: string; sent: 'local' | 'ok' | 'pending' };

const storeKey = (runId: string) => `fortyshots:human:${runId}`;

function loadLocal(runId: string): Record<number, Decision> {
  try {
    return JSON.parse(localStorage.getItem(storeKey(runId)) ?? '{}');
  } catch {
    return {};
  }
}

function saveLocal(runId: string, d: Record<number, Decision>) {
  try {
    localStorage.setItem(storeKey(runId), JSON.stringify(d));
  } catch {
    /* storage blocked: decisions stay in memory */
  }
}

export function Queue({ state, runId, live }: { state: RunState; runId: string; live: boolean }) {
  const [decisions, setDecisions] = useState<Record<number, Decision>>(() => (live ? {} : loadLocal(runId)));
  const [marked, setMarked] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState(0);
  const [serverPending, setServerPending] = useState<{ round?: number; ids?: string[] } | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement>(null);

  // Live: ask the server whether the run is actually blocked on us.
  useEffect(() => {
    if (!live) return;
    let dead = false;
    const poll = () => loadPending(runId).then((p) => !dead && setServerPending(p), () => undefined);
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      dead = true;
      window.clearInterval(id);
    };
  }, [live, runId]);

  const pending = state.pending;
  const lastRound = state.cands.reduce((m, c) => (c.order != null ? Math.max(m, c.round) : m), 0);
  const open = pending && !pending.decided ? pending : null;
  const round = open?.round ?? lastRound;
  const batch = open
    ? open.picks
    : state.cands
        .filter((c) => c.round === lastRound && c.order != null)
        .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
        .map((c) => ({ id: c.id, p_hit: c.p_hit ?? 0, rationale: c.rationale, evidence: c.evidence }));
  const ledger = state.humans.filter((h) => h.round === round).pop();
  const mine = decisions[round];
  const waiting = !!(live && open && serverPending?.round === open.round);
  const canDecide = !!open && !mine && (!live || waiting);

  useEffect(() => {
    setMarked(new Set());
    setCursor(0);
  }, [round, open?.seq]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-i="${cursor}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [cursor]);

  const toggle = (id: string) =>
    setMarked((m) => {
      const n = new Set(m);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const send = async () => {
    if (!open || mine) return;
    const ids = [...marked];
    const d: Decision = {
      round: open.round,
      action: ids.length ? 'reject' : 'approve',
      ids: ids.length ? ids : open.picks.map((p) => p.id),
      at: new Date().toISOString(),
      sent: live ? 'pending' : 'local',
    };
    const next = { ...decisions, [open.round]: d };
    setDecisions(next);
    setSendError(null);
    if (!live) {
      saveLocal(runId, next);
      return;
    }
    try {
      await postHuman(runId, { action: d.action, ids, note: `${d.action} from the console`, round: open.round });
      setDecisions((cur) => ({ ...cur, [open.round]: { ...d, sent: 'ok' } }));
    } catch (err) {
      setDecisions((cur) => {
        const c = { ...cur };
        delete c[open.round];
        return c;
      });
      setSendError(`Not accepted by the live server (${String((err as Error)?.message ?? err)}).`);
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const cur = batch[cursor];
    if (e.key === 'j' || e.key === 'ArrowDown') {
      setCursor((c) => Math.min(batch.length - 1, c + 1));
      e.preventDefault();
    } else if (e.key === 'k' || e.key === 'ArrowUp') {
      setCursor((c) => Math.max(0, c - 1));
      e.preventDefault();
    } else if (e.key === 'r' && cur && canDecide) {
      toggle(cur.id);
      e.preventDefault();
    } else if (e.key === 'a' && canDecide) {
      void send();
      e.preventDefault();
    }
  };

  const status = mine
    ? `${mine.action === 'approve' ? 'approved' : `rejected ${mine.ids.length}`}${mine.sent === 'local' ? ' in this browser' : mine.sent === 'ok' ? ', sent' : ', sending'}`
    : ledger
      ? ledger.action === 'auto'
        ? 'auto-approved in the ledger'
        : `${ledger.action}d by a human`
      : open
        ? live
          ? waiting
            ? 'waiting for you'
            : 'not waiting yet'
          : 'open'
        : '';

  return (
    <div className="queue">
      <p className="small q-mode">
        {live
          ? 'Live: your decision goes to the run on this machine. One decision per round.'
          : 'Static demo: decisions are saved in this browser only. Recorded runs were auto-approved; the ledger says so.'}
      </p>
      {batch.length === 0 ? (
        <p className="muted">Nothing to approve yet. The first batch appears after the Proposer commits.</p>
      ) : (
        <>
          <div className="c-head">
            <span>
              Round {round} batch{open ? ', before DFT' : ''}
            </span>
            <span className="faint mono-sm q-status">{status}</span>
          </div>
          <ol ref={listRef} className="q-list" tabIndex={0} aria-label="Approval queue. j and k move, r marks a pick to reject, a sends the decision." aria-describedby="q-keys" onKeyDown={onKey}>
            {batch.map((it, i) => {
              const info = state.rows[it.id] ?? {};
              const rej =
                marked.has(it.id) || (mine?.action === 'reject' && mine.ids.includes(it.id)) || (ledger?.action === 'reject' && ledger.ids.includes(it.id));
              return (
                <li key={it.id} data-i={i} aria-current={i === cursor ? 'true' : undefined} className={`q-item ${i === cursor ? 'cur' : ''} ${rej ? 'rej' : ''}`} onClick={() => setCursor(i)}>
                  <div className="q-main">
                    <span className="mono q-id">{it.id}</span>
                    <span className="mono faint q-f">
                      <Formula f={info.formula} />
                    </span>
                    <span className="mono q-p">
                      <span className="faint">P(hit) </span>
                      {num(it.p_hit, 2)}
                    </span>
                  </div>
                  <p className="q-why muted">{it.rationale}</p>
                  {canDecide ? (
                    <div className="q-act">
                      <button
                        type="button"
                        className="btn btn-secondary btn-xs reject"
                        aria-pressed={marked.has(it.id)}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggle(it.id);
                        }}
                      >
                        {marked.has(it.id) ? 'Keep' : 'Reject'}
                      </button>
                    </div>
                  ) : (
                    rej && (
                      <p className="q-rej small">
                        <span className="dot fail" aria-hidden /> rejected
                      </p>
                    )
                  )}
                </li>
              );
            })}
          </ol>
          {canDecide && (
            <div className="q-send">
              <button type="button" className="btn btn-secondary btn-sm approve" onClick={() => void send()}>
                {marked.size ? `Reject ${marked.size}, keep ${batch.length - marked.size}` : `Approve all ${batch.length}`}
              </button>
              {marked.size > 0 && <span className="small">The agent replaces rejected picks before buying.</span>}
            </div>
          )}
          {sendError && <p className="small fail">{sendError}</p>}
          <p className="mono-sm faint q-keys" id="q-keys">
            a approve · r reject · j/k move
          </p>
          {!live && Object.keys(decisions).length > 0 && (
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setDecisions({});
                saveLocal(runId, {});
              }}
            >
              clear my decisions
            </button>
          )}
        </>
      )}
      {state.mutations.length > 0 && (
        <>
          <div className="c-head q-decided-head">
            <span>Proposed mutants</span>
            <span className="faint mono-sm">unvalidated · ML only</span>
          </div>
          <ul className="q-decided">
            {state.mutations.map((m) => (
              <li key={m.seq} className="mono-sm">
                {m.payload.parent} → {typeof m.payload.spec === 'string' ? m.payload.spec : JSON.stringify(m.payload.spec)} · <span className="faint">{m.payload.status}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
