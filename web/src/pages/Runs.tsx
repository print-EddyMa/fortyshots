import { useState } from 'react';
import { Page, Prelim } from '../components/Chrome';
import { hasMockRuns, isPreliminary } from '../lib/data';
import { ARM_LABEL, dateShort, dur } from '../lib/format';
import { href } from '../lib/router';
import { useIndex, useResults } from '../lib/useData';
import type { IndexEntry } from '../lib/types';

export function RunsPage() {
  const index = useIndex();
  const results = useResults();
  const [arm, setArm] = useState<string>('all');
  const runs = index.data ?? [];
  const arms = [...new Set(runs.map((r) => r.arm))].sort();
  const shown = runs
    .filter((r) => arm === 'all' || r.arm === arm)
    .slice()
    .sort((a, b) => (b.started ?? '').localeCompare(a.started ?? ''));
  const prelim = isPreliminary(results.data, index.data);

  return (
    <Page current="/runs">
      <section className="wrap page-head">
        <h1 className="h-section">
          <span>Runs.</span>
          <span className="second">Every campaign, replayable from its ledger.</span>
        </h1>
        <p className="lede">
          Each run is the append-only event log the laptop wrote. Open one to replay it at 1×, 8× or 32×.
        </p>
        {prelim && <Prelim status={results.data?.status} mockRuns={hasMockRuns(index.data)} />}
      </section>
      <section className="wrap">
        <div className="filters">
          <div className="seg seg-sm" role="group" aria-label="Filter by arm">
            <button type="button" aria-pressed={arm === 'all'} onClick={() => setArm('all')}>
              All <span className="mono">{runs.length}</span>
            </button>
            {arms.map((a) => (
              <button key={a} type="button" aria-pressed={arm === a} onClick={() => setArm(a)}>
                {a}
              </button>
            ))}
          </div>
          {arm !== 'all' && <span className="small">{ARM_LABEL[arm] ?? arm}</span>}
        </div>
        {index.error && <p className="muted">Could not load the run index: {index.error}</p>}
        {!index.error && !index.data && <p className="muted">Loading runs…</p>}
        {index.data && shown.length === 0 && <p className="muted">No runs yet.</p>}
        {shown.length > 0 && (
          <div className="table-scroll" tabIndex={0}>
            <table className="data runs-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Arm</th>
                  <th className="r">Pool</th>
                  <th className="r">Seed</th>
                  <th className="r">Hits</th>
                  <th className="r">Duration</th>
                  <th>Started</th>
                  <th>
                    <span className="sr-only">Open</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r: IndexEntry & { goal?: string }) => {
                  const to = href(`/console/${encodeURIComponent(r.run_id)}`);
                  return (
                    <tr key={r.run_id}>
                      <td className="n">
                        <a href={to}>{r.run_id}</a>
                      </td>
                      <td>
                        <a href={to} tabIndex={-1}>
                          {ARM_LABEL[r.arm] ?? r.arm}
                        </a>
                        {r.showcase && <span className="small"> · showcase</span>}
                      </td>
                      <td className="n r">{r.pool}</td>
                      <td className="n r">{r.seed}</td>
                      <td className="n r">
                        {r.hits ?? '—'} <span className="faint">/ {r.spent ?? '—'}</span>
                      </td>
                      <td className="n r">{dur(r.duration_s)}</td>
                      <td className="n faint">{dateShort(r.started)}</td>
                      <td className="r">
                        <a href={to} tabIndex={-1} aria-hidden>
                          <span className="arrow">↗</span>
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="small" style={{ marginTop: 16 }}>
          Mode “mock” marks runs made with the deterministic fake model server; they test the pipeline and never count as results.
        </p>
      </section>
    </Page>
  );
}
