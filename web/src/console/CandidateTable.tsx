import { useMemo, useState } from 'react';
import type { Candidate, RunState } from '../lib/replay';
import { num } from '../lib/format';
import { Formula } from '../components/Formula';
import { HitWord } from '../components/Chrome';

type Key = 'order' | 'S_mace' | 'p_hit' | 'E_CO2' | 'E_H2O' | 'round';

function Sealed({ c, v }: { c: Candidate; v: number | null }) {
  if (v == null) return <span className="sealed faint" title="DFT sealed until bought">—</span>;
  return (
    <span key={c.revealSeq ?? 0} className="revealed">
      {num(v, 2)}
    </span>
  );
}

export function CandidateTable({
  state,
  bestId,
  selected,
  onSelect,
  onEvidence,
}: {
  state: RunState;
  bestId: string | null;
  selected: string | null;
  onSelect: (id: string) => void;
  onEvidence: (callId: string) => void;
}) {
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 } | null>(null);
  const rows = useMemo(() => {
    const val = (c: Candidate, k: Key): number => {
      switch (k) {
        case 'order':
          return c.order ?? 1e9;
        case 'S_mace':
          return state.rows[c.id]?.S_mace ?? 1e9;
        case 'p_hit':
          return c.p_hit ?? -1;
        case 'E_CO2':
          return c.E_CO2 ?? 1e9;
        case 'E_H2O':
          return c.E_H2O ?? 1e9;
        case 'round':
          return c.round;
      }
    };
    const xs = [...state.cands];
    if (sort) xs.sort((a, b) => (val(a, sort.key) - val(b, sort.key)) * sort.dir);
    else xs.sort((a, b) => b.round - a.round || (b.p_hit ?? 0) - (a.p_hit ?? 0) || (a.order ?? 0) - (b.order ?? 0));
    return xs;
  }, [state.cands, state.rows, sort]);

  const th = (k: Key | null, label: string, right = false, title?: string) => {
    const active = k && sort?.key === k;
    return (
      <th className={right ? 'r' : undefined} aria-sort={active ? (sort!.dir === 1 ? 'ascending' : 'descending') : undefined} title={title}>
        {k ? (
          <button
            type="button"
            className="th-sort"
            onClick={() => setSort((s) => (s?.key === k ? (s.dir === 1 ? { key: k, dir: -1 } : null) : { key: k, dir: 1 }))}
          >
            {label}
            <span className="faint sort-glyph" aria-hidden>
              {active ? (sort!.dir === 1 ? '▲' : '▼') : ''}
            </span>
          </button>
        ) : (
          label
        )}
      </th>
    );
  };

  return (
    <div className="cand">
      <div className="c-head">
        <span>Candidates</span>
        <span className="faint mono-sm">
          energies in eV · {state.cands.filter((c) => c.order != null).length} bought · {state.cands.filter((c) => c.order == null && !c.vetoed).length} proposed
        </span>
      </div>
      <div className="table-scroll cand-scroll" tabIndex={0} aria-label="Candidate table">
        <table className="data cand-table">
          <thead>
            <tr>
              {th('order', '#', true, 'Purchase order')}
              <th>ID</th>
              <th>Formula</th>
              {th('S_mace', 'MACE S', true, 'Cheap-oracle DAC score, eV')}
              {th('p_hit', 'P(hit)', true, 'Stated before DFT was revealed')}
              {th('E_CO2', 'DFT CO₂', true, 'DFT CO2 binding energy, eV')}
              {th('E_H2O', 'DFT H₂O', true, 'DFT H2O binding energy, eV')}
              <th>Hit</th>
              {th('round', 'Rd', true, 'Round')}
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={10} className="muted empty">
                  No candidates yet. The Director explores the pool first.
                </td>
              </tr>
            )}
            {rows.map((c) => {
              const info = state.rows[c.id] ?? {};
              const cls = [
                c.id === bestId ? 'best' : '',
                c.id === selected ? 'sel' : '',
                c.vetoed ? 'vetoed' : '',
              ]
                .filter(Boolean)
                .join(' ');
              return (
                <tr
                  key={c.id}
                  className={`fade-up ${cls}`}
                  onClick={() => onSelect(c.id)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelect(c.id);
                    }
                  }}
                  tabIndex={0}
                  aria-selected={c.id === selected}
                  title={c.vetoed ? `Vetoed by the Critic: ${c.vetoed}` : c.rationale}
                >
                  <td className="n r">{c.order ?? <span className="faint">—</span>}</td>
                  <td className="n">{c.id}</td>
                  <td className="n formula"><Formula f={info.formula} /></td>
                  <td className="n r">{num(info.S_mace, 2)}</td>
                  <td className="n r">{c.p_hit != null ? num(c.p_hit, 2) : <span className="faint">—</span>}</td>
                  <td className="n r">
                    <Sealed c={c} v={c.E_CO2} />
                  </td>
                  <td className="n r">
                    <Sealed c={c} v={c.E_H2O} />
                  </td>
                  <td>
                    {c.vetoed ? (
                      <span className="hitword">
                        <span className="dot warn" aria-hidden />
                        <span className="muted">vetoed</span>
                      </span>
                    ) : (
                      <HitWord hit={c.hit} />
                    )}
                  </td>
                  <td className="n r">{c.round}</td>
                  <td className="n">
                    {c.evidence.slice(0, 1).map((cid) => (
                      <button
                        key={cid}
                        type="button"
                        className="cite"
                        onClick={(e) => {
                          e.stopPropagation();
                          onEvidence(cid);
                        }}
                      >
                        {cid}
                      </button>
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
