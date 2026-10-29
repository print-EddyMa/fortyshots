"""Label-free descriptors for every MOF in pools P0-P5 and V -> data/descriptors.parquet.

Framework = the ODAC23 initial framework geometry (adsorbate removed). Pore geometry from Zeo++ (pyzeo),
default Zeo++ (CCDC) atomic radii, standard (not high-accuracy) Voronoi network:
  lcd_A, pld_A    largest included / free sphere diameter
  asa_m2g         accessible surface area, probe radius 1.86 A (N2), 2000 MC samples per atom
  void_fraction   geometric void fraction, point probe (radius 0), 50,000 MC samples
pyzeo's high-accuracy mode (-ha) hangs on this build, so it is not used. Each MOF runs in a forked
child with a timeout; failures leave NaN. Zeo++ Monte Carlo uses its own fixed internal seed.

metal_cost_tier is the costliest tier among the framework's metals (organic-only frameworks: 1).
Tiers are an order-of-magnitude raw-material proxy (USGS Mineral Commodity Summaries 2024 metal
prices and crustal abundance), not a synthesis cost:
  1 cheap   commodity metals, roughly < $15/kg: Li Na K Mg Ca Sr Ba Al Ti Mn Fe Cu Zn
  2 mid     specialty metals, roughly $15-300/kg, not toxic: Co Ni Cr V Zr Y Mo W Sn Bi Nb Ce La
  3 costly  everything else: noble metals (Ru Rh Pd Ag Os Ir Pt Au), Sc Ga In Ge Hf Ta Re Te Cs Rb Be,
            the other lanthanides and actinides, and toxic heavy metals (Cd Hg Pb Tl), whose handling
            and regulatory cost rules them out for DAC at scale.

usage: python -I physics/descriptors.py data/_build/frames.pkl data [n_workers]
"""
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frames import load as load_frames

POOLS = ["P0", "P1", "P2", "P3", "P4", "P5", "V"]
TIER1 = {"Li", "Na", "K", "Mg", "Ca", "Sr", "Ba", "Al", "Ti", "Mn", "Fe", "Cu", "Zn"}
TIER2 = {"Co", "Ni", "Cr", "V", "Zr", "Y", "Mo", "W", "Sn", "Bi", "Nb", "Ce", "La"}
ZEO_TIMEOUT_S = 120
COLUMNS = ["id", "formula", "metals", "n_metal_atoms", "natoms", "volume_A3", "density_gcm3", "oms",
           "lcd_A", "pld_A", "asa_m2g", "void_fraction", "metal_cost_tier"]


def cost_tier(metals):
    tiers = [1 if m in TIER1 else 2 if m in TIER2 else 3 for m in metals]
    return max(tiers, default=1)


def structure(m):
    from pymatgen.core import Lattice, Structure
    return Structure(Lattice(m["cell"]), m["numbers"], m["positions"], coords_are_cartesian=True)


def composition_row(m):
    s = structure(m)
    metals = sorted({sp.symbol for sp in s.composition.elements if sp.is_metal})
    return {"id": m["id"], "formula": s.composition.reduced_formula, "metals": ",".join(metals),
            "n_metal_atoms": int(sum(1 for sp in s.species if sp.is_metal)), "natoms": len(s),
            "volume_A3": float(s.volume), "density_gcm3": float(s.density), "oms": bool(m["oms"]),
            "metal_cost_tier": cost_tier(metals)}


def _field(text, key):
    toks = text.split()
    return float(toks[toks.index(key) + 1])


def zeo(m):
    """Pore descriptors with Zeo++. Silences Zeo++'s C-level stdout."""
    from pymatgen.io.zeopp import ZeoCssr
    from pyzeo.extension import surface_area, volume
    from pyzeo.netstorage import AtomNetwork
    with tempfile.TemporaryDirectory() as d:
        cssr, res = os.path.join(d, "f.cssr"), os.path.join(d, "f.res")
        ZeoCssr(structure(m)).write_file(cssr)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        net = AtomNetwork.read_from_CSSR(cssr, True, None)
        net.calculate_free_sphere_parameters(res)
        with open(res) as fh:
            lcd, pld = (float(x) for x in fh.read().split()[1:3])
        sa = surface_area(net, 1.86, 1.86, 2000, False).decode()
        vol = volume(net, 0.0, 0.0, 50000, False).decode()
    return {"lcd_A": lcd, "pld_A": pld, "asa_m2g": _field(sa, "ASA_m^2/g:"),
            "void_fraction": _field(vol, "AV_Volume_fraction:")}


def _child(m, conn):
    try:
        conn.send(zeo(m))
    except Exception as e:  # noqa: BLE001
        conn.send({"error": repr(e)})
    conn.close()


def zeo_guarded(m, timeout=ZEO_TIMEOUT_S):
    ctx = mp.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=_child, args=(m, child))
    p.start()
    child.close()
    out = parent.recv() if parent.poll(timeout) else {"error": "timeout"}
    p.join(1)
    if p.is_alive():
        p.kill()
        p.join()
    return out


def run_shard(args):
    frames_path, ids, out_path = args
    frames = load_frames(frames_path)
    done = set()
    if os.path.exists(out_path):
        with open(out_path) as fh:
            done = {json.loads(line)["id"] for line in fh if line.strip()}
    with open(out_path, "a") as fh:
        for i in ids:
            if i in done:
                continue
            t0 = time.time()
            row = {"id": i, **zeo_guarded(frames[i]), "sec": round(time.time() - t0, 2)}
            fh.write(json.dumps(row) + "\n")
            fh.flush()


def main():
    frames_path, data = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    uni = pd.read_parquet(f"{data}/universe.parquet")
    ids = sorted(uni[uni.pool.isin(POOLS)].id)
    zdir = f"{data}/_build/zeo"
    os.makedirs(zdir, exist_ok=True)
    jobs = [(frames_path, ids[k::n], f"{zdir}/shard-{k}.jsonl") for k in range(n)]
    t0 = time.time()
    if n == 1:
        run_shard(jobs[0])
    else:
        with mp.get_context("fork").Pool(n) as pool:
            pool.map(run_shard, jobs)
    print(f"zeo++ done in {time.time() - t0:.0f} s", flush=True)

    frames = load_frames(frames_path)
    comp = pd.DataFrame([composition_row(frames[i]) for i in ids])
    zrows = []
    for k in range(n):
        with open(f"{zdir}/shard-{k}.jsonl") as fh:
            zrows += [json.loads(line) for line in fh if line.strip()]
    z = pd.DataFrame(zrows).drop_duplicates("id", keep="last")
    failed = z[z.get("error", pd.Series(index=z.index, dtype=object)).notna()] if "error" in z else z.iloc[:0]
    df = comp.merge(z.drop(columns=[c for c in ("error", "sec") if c in z]), on="id", how="left")
    df[COLUMNS].sort_values("id").to_parquet(f"{data}/descriptors.parquet", index=False)
    print(f"{len(df)} MOFs; zeo++ failures {len(failed)}; median zeo s/MOF {z.sec.median():.2f}")
    if len(failed):
        print(failed[["id", "error"]].to_string())


if __name__ == "__main__":
    main()
