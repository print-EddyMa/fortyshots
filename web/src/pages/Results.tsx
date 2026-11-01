import { Page } from '../components/Chrome';
import { ScatterChart } from '../components/Charts';
import { PeriodicTable } from '../components/PeriodicTable';
import { usePools } from '../lib/useData';

/** The science page: what these materials are, and why finding a good one is hard. Real ODAC23 data only. */
export function ResultsPage() {
  const pools = usePools();
  const rows = pools.data ? Object.values(pools.data.pools).flat() : [];
  const hits = rows.filter((r) => r.hit).length;
  const waterWins = rows.length ? rows.filter((r) => r.E_H2O < r.E_CO2).length / rows.length : 0;

  return (
    <Page current="/results">
      <section className="wrap page-head">
        <h1 className="h-section">
          <span>Rooms for chemistry.</span>
          <span className="second">Why finding the right one is hard.</span>
        </h1>
        <p className="lede">
          A metal–organic framework is a crystal built from metal corners and carbon struts, full of pores a few atoms wide. In
          humid air, most of those pores fill with water before CO2 can get in.
        </p>
      </section>

      <section className="wrap section">
        <h2 className="fig-title" id="why-hard">Water usually wins</h2>
        {pools.data ? (
          <>
            <p className="fig-sub">
              Each dot is a material. Above the line, water holds on more strongly than CO2: that is{' '}
              <span className="callout mono">{Math.round(waterWins * 100)}%</span> of them. Filled dots are the few that hold CO2 best.
            </p>
            <ScatterChart
              points={rows.map((r) => ({ x: r.E_H2O, y: r.E_CO2, strong: r.hit, id: r.id }))}
              xLabel="Water binding (eV)"
              yLabel="CO2 binding (eV)"
              height={360}
              ariaLabel={`CO2 versus water binding energy for ${rows.length} materials, ${hits} hold CO2 best`}
              annotation={`${rows.length.toLocaleString()} materials · ${hits} good ones`}
            />
          </>
        ) : (
          <p className="muted">{pools.error ?? 'Loading…'}</p>
        )}
      </section>

      <section className="wrap section" id="metals">
        <h2 className="fig-title">Which metals make the best rooms</h2>
        <p className="fig-sub">Darker means more of the materials with that metal capture CO2 well.</p>
        {pools.data ? (
          <PeriodicTable rows={rows} label="Periodic table of how often materials with each metal capture CO2 well" />
        ) : (
          <p className="muted">{pools.error ?? 'Loading…'}</p>
        )}
      </section>
    </Page>
  );
}
