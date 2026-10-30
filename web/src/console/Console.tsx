import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { bestCandidate, derive, type RunState } from '../lib/replay';
import type { Playback } from '../lib/usePlayback';
import type { MutantIndex, MutantRow, Results, RunEvent } from '../lib/types';
import { loadMutants, runAsset } from '../lib/data';
import { Viewer } from '../components/Viewer';
import { DiscoveryChart, type CurveSeries } from '../components/Charts';
import { TopBar } from './TopBar';
import { Rounds } from './Rounds';
import { CandidateTable } from './CandidateTable';
import { Trace } from './Trace';
import { Queue } from './Queue';
import { ModelTab } from './ModelTab';
import { MapTab } from './MapTab';
import './console.css';

type Tab = 'trace' | 'queue' | 'map' | 'model';
const TABS: Tab[] = ['trace', 'queue', 'map', 'model'];

export interface ConsoleProps {
  runId: string;
  events: RunEvent[];
  pb: Playback;
  results: Results | null;
  embedded?: boolean;
  liveStatus?: string;
  liveNetwork?: string | null;
}

export function Console({ runId, events, pb, results, embedded, liveStatus, liveNetwork }: ConsoleProps) {
  const state = useMemo(() => derive(events, pb.cursor), [events, pb.cursor]);
  const [tab, setTab] = useState<Tab>('trace');
  const [selected, setSelected] = useState<string | null>(null);
  const [userPicked, setUserPicked] = useState(false);
  const [morphKey, setMorphKey] = useState<number | null>(null);
  const [focusCall, setFocusCall] = useState<string | null>(null);
  // prospective mutants (runs/<id>/mutants/index.json): selecting one shows its MACE relaxation in the viewer
  const [mutants, setMutants] = useState<MutantIndex | null>(null);
  const [mutantSel, setMutantSel] = useState<MutantRow | null>(null);
  const hasProposals = useMemo(() => events.some((e) => e.kind === 'mutation'), [events]);
  useEffect(() => {
    let dead = false;
    setMutants(null);
    setMutantSel(null);
    if (hasProposals) loadMutants(runId).then((m) => !dead && setMutants(m));
    return () => {
      dead = true;
    };
  }, [runId, hasProposals]);
  const lastRevealSeq = useRef<number | null>(null);

  const best = bestCandidate(state.cands);

  // On a new reveal while moving forward: select the best of the batch and play the pose morph.
  useEffect(() => {
    const r = state.lastReveal;
    if (!r) {
      lastRevealSeq.current = null;
      if (!userPicked) setSelected(state.cands[0]?.id ?? null);
      return;
    }
    if (r.seq === lastRevealSeq.current) return;
    const forward = lastRevealSeq.current == null || r.seq > lastRevealSeq.current;
    lastRevealSeq.current = r.seq;
    const batch = state.cands.filter((c) => r.ids.includes(c.id) && c.S != null);
    const pickFrom = batch.some((c) => c.hit) ? batch.filter((c) => c.hit) : batch;
    const top = pickFrom.reduce<(typeof batch)[number] | null>((a, b) => (a == null || b.S! < a.S! ? b : a), null);
    if (top) {
      setSelected(top.id);
      setUserPicked(false);
      if (forward && (pb.playing || pb.live)) setMorphKey(r.seq);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.lastReveal?.seq]);

  // Before any reveal, follow the newest proposal.
  useEffect(() => {
    if (state.lastReveal || userPicked) return;
    if (state.pending?.picks[0]) setSelected(state.pending.picks[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.pending?.seq]);

  const sel = state.cands.find((c) => c.id === selected) ?? null;
  const pendingCount = state.pending ? state.pending.picks.length : 0;

  const series: CurveSeries[] = [];
  const a0 = results?.arms.find((a) => a.arm === 'A0');
  const a3 = results?.arms.find((a) => a.arm === 'A3');
  if (a0) series.push({ key: 'A0', label: 'Random', curve: a0.curve, lo: a0.curve_lo, hi: a0.curve_hi, tone: 'faint', band: true });
  if (a3) series.push({ key: 'A3', label: 'RF-UCB', curve: a3.curve, lo: a3.curve_lo, hi: a3.curve_hi, tone: 'muted', band: true, dashed: true });
  series.push({ key: 'run', label: 'This run', curve: state.curve, tone: 'fg', step: true, width: 1.5, digits: 0 });

  const jumpToCall = (cid: string) => {
    setTab('trace');
    setFocusCall(cid);
  };

  return (
    <div className={`console${embedded ? ' embedded' : ''}`} data-theme="dark" data-phase={state.phase} data-cursor={pb.cursor} data-ended={pb.ended ? '1' : '0'}>
      <TopBar state={state} pb={pb} runId={runId} embedded={embedded} liveStatus={liveStatus} liveNetwork={liveNetwork} />
      <div className="c-grid">
        <aside className="c-left" aria-label="Rounds">
          <Rounds state={state} pb={pb} events={events} />
        </aside>
        <section className="c-center" aria-label="Candidates">
          <CandidateTable
            state={state}
            bestId={best?.id ?? null}
            selected={selected}
            onSelect={(id) => {
              setMutantSel(null);
              setSelected(id);
              setUserPicked(true);
            }}
            onEvidence={jumpToCall}
          />
          <div className="c-chart">
            <div className="c-head">
              <span>Discovery curve</span>
              <span className="faint mono-sm">
                {state.hits} hit{state.hits === 1 ? '' : 's'} after {state.spent}
              </span>
            </div>
            <DiscoveryChart series={series} height={embedded ? 140 : 180} current={{ k: state.spent, hits: state.hits }} labelWidth={110} />
          </div>
        </section>
        <aside className="c-right" aria-label="Structure and agent">
          <Viewer
            id={selected}
            revealed={sel?.S != null}
            morphKey={morphKey}
            mutant={mutantSel?.file ? { id: mutantSel.id, url: runAsset(runId, mutantSel.file) } : null}
          />
          <div className="c-tabs">
            <div className="tablist" role="tablist" aria-label="Agent panels">
              {TABS.map((t) => (
                <button
                  key={t}
                  role="tab"
                  id={`tab-${t}`}
                  aria-selected={tab === t}
                  aria-controls={`panel-${t}`}
                  tabIndex={tab === t ? 0 : -1}
                  onClick={() => setTab(t)}
                  onKeyDown={(e) => {
                    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                      const i = TABS.indexOf(tab) + (e.key === 'ArrowRight' ? 1 : -1);
                      const next = TABS[(i + TABS.length) % TABS.length];
                      setTab(next);
                      document.getElementById(`tab-${next}`)?.focus();
                    }
                  }}
                >
                  {t === 'trace' ? 'Trace' : t === 'queue' ? 'Queue' : t === 'map' ? 'Map' : 'Model'}
                  {t === 'queue' && pendingCount > 0 && <span className="tab-count mono">{pendingCount}</span>}
                </button>
              ))}
            </div>
            <div className="tabpanel" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
              {tab === 'trace' && <TraceMemo state={state} runId={runId} focusCall={focusCall} active={!pb.ended && (pb.playing || pb.live)} />}
              {tab === 'queue' && (
                <Queue state={state} runId={runId} live={pb.live} mutants={mutants} mutantSel={mutantSel?.id ?? null} onMutant={setMutantSel} />
              )}
              {tab === 'map' && <MapTab state={state} />}
              {tab === 'model' && <ModelTab state={state} />}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

const TraceMemo = memo(Trace, (a, b) => a.state === b.state && a.focusCall === b.focusCall && a.active === b.active);

export type { RunState };
