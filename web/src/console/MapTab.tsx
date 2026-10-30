// Console "Map" tab: the run's purchases so far on the chemical-space atlas (results/atlas.json).
import { useEffect, useState } from 'react';
import { AtlasCanvas } from '../components/Atlas';
import { loadAtlas, type AtlasData } from '../lib/chemData';
import type { RunState } from '../lib/replay';

export function MapTab({ state }: { state: RunState }) {
  const [atlas, setAtlas] = useState<AtlasData | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    loadAtlas().then(setAtlas, () => setErr(true));
  }, []);
  const pool = state.header?.pool ?? null;
  if (err) return <p className="faint mono-sm">atlas.json is not in this build.</p>;
  if (!atlas || !pool) return <p className="faint mono-sm">loading map…</p>;
  const picks = state.cands
    .filter((c) => c.order != null)
    .sort((a, b) => a.order! - b.order!)
    .map((c) => c.id);
  const pending = state.pending && !state.pending.decided ? state.pending.picks.map((p) => p.id).filter((id) => !picks.includes(id)) : [];
  const hits = state.cands.filter((c) => c.hit).length;
  return (
    <div className="map-tab">
      <AtlasCanvas
        atlas={atlas}
        pool={pool}
        picks={picks}
        pending={pending}
        clock={Infinity}
        dense
        maxH={420}
        ariaLabel={`Map of the MOFs in ${pool}: ${picks.length} bought so far, ${hits} hits${pending.length ? `, ${pending.length} proposed` : ''}.`}
      />
      <p className="faint mono-sm" style={{ marginTop: 8 }}>
        {pool} on the t-SNE atlas · {picks.length} bought, {hits} hit{hits === 1 ? '' : 's'} (accent)
        {pending.length ? ` · ${pending.length} proposed (rings)` : ''} · filled dots: hits in the pool
      </p>
    </div>
  );
}
