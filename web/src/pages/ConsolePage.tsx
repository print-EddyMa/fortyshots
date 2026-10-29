import { useEffect, useRef, useState } from 'react';
import { Console } from '../console/Console';
import { LIVE } from '../lib/config';
import { loadEvents, loadNetwork, pickShowcase, subscribeLive } from '../lib/data';
import { useIndex, useResults } from '../lib/useData';
import { usePlayback, type Speed } from '../lib/usePlayback';
import { href } from '../lib/router';
import type { Results, RunEvent } from '../lib/types';

function RunConsole({ runId, results, query }: { runId: string; results: Results | null; query: URLSearchParams }) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [liveStatus, setLiveStatus] = useState<string>('connecting');
  const [network, setNetwork] = useState<string | null>(null);

  useEffect(() => {
    if (!LIVE) return;
    let dead = false;
    const poll = () => loadNetwork().then((n) => !dead && setNetwork(n));
    poll();
    const id = window.setInterval(poll, 5000);
    return () => {
      dead = true;
      window.clearInterval(id);
    };
  }, []);
  const buffer = useRef<RunEvent[]>([]);

  useEffect(() => {
    setEvents([]);
    setError(null);
    if (LIVE) {
      const seen = new Set<number>();
      const flush = window.setInterval(() => {
        if (!buffer.current.length) return;
        const batch = buffer.current;
        buffer.current = [];
        setEvents((prev) => [...prev, ...batch]);
      }, 100);
      const stop = subscribeLive(
        runId,
        (e) => {
          if (seen.has(e.seq)) return;
          seen.add(e.seq);
          buffer.current.push(e);
        },
        setLiveStatus,
      );
      return () => {
        stop();
        window.clearInterval(flush);
      };
    }
    let dead = false;
    loadEvents(runId).then(
      (ev) => !dead && setEvents(ev),
      (e) => !dead && setError(String(e?.message ?? e)),
    );
    return () => {
      dead = true;
    };
  }, [runId]);

  const sp = Number(query.get('speed'));
  const speed: Speed = sp === 1 || sp === 32 ? sp : 8;
  const pb = usePlayback(events, { live: LIVE, speed, autoplay: query.get('paused') !== '1' });

  // `?seq=N` deep-links to the state right after ledger event N (used for screenshots and tests).
  const seqParam = Number(query.get('seq'));
  const seeked = useRef(false);
  useEffect(() => {
    if (seeked.current || LIVE || !events.length || !Number.isFinite(seqParam) || seqParam <= 0) return;
    const i = events.findIndex((e) => e.seq >= seqParam);
    if (i >= 0) pb.seekIndex(i);
    seeked.current = true;
  }, [events, seqParam, pb]);

  if (error) {
    return (
      <div className="console-error wrap">
        <p>Could not load run {runId}.</p>
        <p className="small">{error}</p>
        <p>
          <a className="link" href={href('/runs')}>
            All runs
          </a>
        </p>
      </div>
    );
  }
  return <Console runId={runId} events={events} pb={pb} results={results} liveStatus={liveStatus} liveNetwork={network} />;
}

export function ConsolePage({ runId, query }: { runId: string | null; query: URLSearchParams }) {
  const index = useIndex();
  const results = useResults();
  const showcase = index.data ? pickShowcase(index.data) : null;
  const id = runId ?? showcase?.run_id ?? null;

  useEffect(() => {
    if (!runId && id) {
      const q = query.toString();
      history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/console/${encodeURIComponent(id)}${q ? `?${q}` : ''}`);
    }
  }, [runId, id, query]);

  return (
    <main className="console-page" data-theme="dark" id="main">
      <h1 className="sr-only">Run console{id ? `: ${id}` : ''}</h1>
      {id ? (
        <RunConsole key={id} runId={id} results={results.data} query={query} />
      ) : (
        <div className="console-error wrap">
          <p className="muted">{index.error ? `Could not load the run index: ${index.error}` : index.data ? 'No runs yet.' : 'Loading runs…'}</p>
        </div>
      )}
    </main>
  );
}
