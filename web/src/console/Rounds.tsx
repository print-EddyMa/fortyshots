import type { RunState } from '../lib/replay';
import type { Playback } from '../lib/usePlayback';
import type { RunEvent } from '../lib/types';
import { dur, num } from '../lib/format';

export function Rounds({ state, pb, events }: { state: RunState; pb: Playback; events: RunEvent[] }) {
  const rounds = state.rounds.filter((r) => r.round >= 1);
  const jump = (round: number) => {
    const i = events.findIndex((e) => e.round === round);
    if (i >= 0 && !pb.live) pb.seekIndex(i);
  };
  const s = state.summary;
  return (
    <div className="rounds">
      <div className="c-head">
        <span>Rounds</span>
        <span className="faint mono-sm">
          {state.header ? `${state.header.rounds} × ${state.header.batch}` : ''}
        </span>
      </div>
      <ol className="round-list">
        {rounds.map((r) => {
          const secs = r.startT && r.endT ? (Date.parse(r.endT) - Date.parse(r.startT)) / 1000 : null;
          const current = r.round === state.round && r.status === 'running';
          return (
            <li key={r.round}>
              <button type="button" className={`round${current ? ' current' : ''}`} data-status={r.status} onClick={() => jump(r.round)} disabled={pb.live}>
                <span className="round-name">
                  <span className={`dot ${current ? 'accent pulse' : r.status === 'done' ? '' : 'hollow'}`} aria-hidden />
                  Round {r.round}
                </span>
                <span className="round-hits mono-sm">
                  {r.status === 'pending' ? <span className="faint">—</span> : <>{r.hits} <span className="faint">hit{r.hits === 1 ? '' : 's'}</span></>}
                </span>
                <span className="round-meta mono-sm faint">
                  {r.status === 'done' ? dur(secs) : r.status === 'running' ? 'running' : 'queued'}
                  {r.calibration?.spearman != null && <> · ρ {num(r.calibration.spearman, 2, true)}</>}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      <dl className="run-facts">
        <div>
          <dt>Hits</dt>
          <dd className="mono-sm">
            {state.hits} <span className="faint">/ {state.spent}</span>
          </dd>
        </div>
        <div>
          <dt>First hit</dt>
          <dd className="mono-sm">{s?.lookups_to_first_hit ?? (state.curve.findIndex((v) => v > 0) > 0 ? state.curve.findIndex((v) => v > 0) : '—')}</dd>
        </div>
        <div>
          <dt>Brier</dt>
          <dd className="mono-sm">{s?.brier != null ? num(s.brier, 3) : '—'}</dd>
        </div>
        <div>
          <dt>Arm · pool · seed</dt>
          <dd className="mono-sm">{state.header ? `${state.header.arm} · ${state.header.pool} · ${state.header.seed}` : '—'}</dd>
        </div>
      </dl>
    </div>
  );
}
