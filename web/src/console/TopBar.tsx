import type { RunState } from '../lib/replay';
import type { Playback, Speed } from '../lib/usePlayback';
import { clock, num } from '../lib/format';
import { href } from '../lib/router';

export function modelPill(state: RunState): string | null {
  const h = state.header;
  if (!h) return null;
  const tps = state.llm.length ? state.llm.slice(-6).reduce((a, b) => a + b.tps, 0) / Math.min(6, state.llm.length) : null;
  return [h.model.name, h.model.quant, 'local', tps != null ? `${num(tps, 1)} tok/s` : null].filter(Boolean).join(' · ');
}

export function TopBar({
  state,
  pb,
  runId,
  embedded,
  liveStatus,
  liveNetwork,
}: {
  state: RunState;
  pb: Playback;
  runId: string;
  embedded?: boolean;
  liveStatus?: string;
  liveNetwork?: string | null;
}) {
  const h = state.header;
  const done = state.phase === 'done';
  const statusWord = pb.live
    ? liveStatus === 'error'
      ? 'reconnecting'
      : done || liveStatus === 'closed'
        ? 'finished'
        : state.pending && !state.pending.decided && state.phase !== 'buy'
          ? 'live · batch awaiting approval'
          : 'live'
    : done
      ? 'finished'
      : pb.playing
        ? `replaying ${pb.speed}×`
        : 'paused';
  const active = !done && (pb.live || pb.playing);
  const pill = modelPill(state);
  return (
    <div className="c-top">
      <div className="c-top-id">
        {!embedded && (
          <a className="c-home" href={href('/')} aria-label="fortyshots home">
            fortyshot<span className="faint">s</span>
          </a>
        )}
        <p className="c-goal" title={h?.goal}>
          {h?.goal ?? 'Loading run…'}
        </p>
        <span className="pill c-runid" title="Run id">
          {runId}
        </span>
        {h?.mode === 'mock' && <span className="c-mock small">mock run</span>}
      </div>
      <div className="c-top-meta">
        <span className="c-status">
          <span className={`dot ${active ? 'accent pulse' : done ? '' : 'hollow'}`} aria-hidden />
          <span>{statusWord}</span>
        </span>
        <span className="mono-sm c-lookups">
          <span className="faint">lookups</span> {state.spent} <span className="faint">/ {state.budget}</span>
        </span>
        {pill && !embedded && <span className="pill c-model">{pill}</span>}
        {(h || liveNetwork) && (
          <span className="mono-sm c-net" title={pb.live && liveNetwork ? 'Network state of this machine, checked every 5 s' : 'Network state recorded in the run header'}>
            <span className="faint">network</span> {pb.live && liveNetwork ? liveNetwork : h?.network}
          </span>
        )}
      </div>
      <div className="c-controls">
        {pb.live ? (
          <button type="button" className="btn btn-secondary btn-xs" onClick={() => pb.setFollowing(!pb.following)} aria-pressed={!pb.following}>
            {pb.following ? 'Pause view' : 'Follow live'}
          </button>
        ) : (
          <>
            <button type="button" className="btn btn-secondary btn-xs c-play" onClick={pb.toggle} aria-label={pb.playing ? 'Pause replay' : 'Play replay'}>
              {pb.playing ? 'Pause' : pb.ended ? 'Replay' : 'Play'}
            </button>
            <div className="seg seg-sm" role="group" aria-label="Replay speed">
              {([1, 8, 32] as Speed[]).map((s) => (
                <button key={s} type="button" aria-pressed={pb.speed === s} onClick={() => pb.setSpeed(s)}>
                  {s}×
                </button>
              ))}
            </div>
            <label className="c-scrub">
              <span className="sr-only">Replay position</span>
              <input
                type="range"
                min={0}
                max={Math.max(1, Math.round(pb.total))}
                step={100}
                value={Math.round(pb.vt)}
                onChange={(e) => pb.seek(Number(e.target.value))}
                aria-valuetext={`${clock(pb.vt)} of ${clock(pb.total)}`}
              />
            </label>
            <span className="mono-sm faint c-clock">
              {clock(pb.vt)} / {clock(pb.total)}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
