import { useEffect, useRef, useState } from 'react';
import { Console } from '../console/Console';
import { LIVE } from '../lib/config';
import { loadEvents, loadNetwork, loadSidecar, pickShowcase, subscribeLive } from '../lib/data';
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
  const proposed = useRef(false); // live: the ledger has Chemist proposals, so a sidecar may follow

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
      const push = (e: RunEvent) => {
        if (seen.has(e.seq)) return;
        seen.add(e.seq);
        if (e.kind === 'mutation') proposed.current = true;
        buffer.current.push(e);
      };
      const stop = subscribeLive(runId, push, (st) => {
        setLiveStatus(st);
        // after the summary: append the prospective-mode sidecar, if the container has built this run's mutants
        if (st === 'closed' && proposed.current) loadSidecar(runId).then((side) => side.forEach(push));
      });
      return () => {
        stop();
        window.clearInterval(flush);
      };
    }
    let dead = false;
    // the prospective-mode sidecar (built/invalid mutants) plays after the ledger; it is never part of it
    // (fetched only for runs whose ledger has Chemist proposals, so other runs make no 404 requests)
    loadEvents(runId)
      .then(async (ev) => {
        const last = ev.length ? ev[ev.length - 1].seq : -1;
        const side = ev.some((e) => e.kind === 'mutation') ? await loadSidecar(runId) : [];
        return [...ev, ...side.filter((e) => e.seq > last)];
      })
      .then(
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
      <p className="console-intro">
        <a href="#/">fortyshots</a> · Replaying a search: 40 tests chosen from 500 candidates. A hit holds CO2 more strongly
        than water.
      </p>
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
