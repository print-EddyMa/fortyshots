"""Prospective mode: build, MACE-relax and check mutants proposed by the Chemist (container only).

Input: a JSON list of mutation specs, e.g. the `mutation` event payloads of a run:
  {"parent": "m3fa9c2e", "spec": {"op": "functionalize", "group": "NH2", "site": "near_co2"}}
      group in NH2 | OH | F | CH3; site is a descriptor (near_co2: the aromatic H closest to the carbon of the
      parent's best MACE CO2 placement; near_metal / far_from_metal: smallest / largest distance to any metal
      atom; ties go to the lower index) or an integer index into the parent's aromatic C-H sites (default 0)
  {"parent": "m3fa9c2e", "spec": {"op": "swap_metal", "from": "Zn", "to": "Mg"}}
      charge-preserving families only: Mg Zn Co Ni (2+), Al Fe Sc (3+)
Parents must have <= 120 framework atoms. physics/prospective.py is the per-run driver; this CLI takes a raw list.

For each spec: build the mutant from the parent's ODAC initial framework, relax cell and positions with
MACE-MP-0 medium (float32 as the cache, FIRE + FrechetCellFilter, fmax 0.05 eV/A, <= 300 steps), run
physics/validity.py,
and compute MACE binding (float32, as the cache) with CO2/H2O at the parent's best MACE placements carried
over in fractional coordinates. Results are "unvalidated, ML only" and never enter a benchmark number.

Output in <out_dir>: <mutant_id>.json (web viewer format: id, parent, spec, cell, numbers, ads [], pos (built,
unrelaxed), pos_relaxed, frames [{cell, pos}] <= 30 subsampled relaxation frames, mace, validity, label),
report.json (one row per spec, including rejected ones) and eqv2_inputs.json (mutant + CO2 docs for
physics/eqv2_check.py).

usage: OMP_NUM_THREADS=2 python -I physics/mutate.py <specs.json> <out_dir> [--frames data/_build/frames.pkl]
       [--mace data/mace_cache.parquet] [--steps 300]
"""
import argparse
import json
import os
import sys
import time
import warnings

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from frames import load as load_frames
from validity import report as validity_report

MAX_PARENT_ATOMS = 120
FMAX = 0.05
MAX_FRAMES = 30
GROUPS = ("NH2", "OH", "F", "CH3")
SITES = ("near_co2", "near_metal", "far_from_metal")
SWAP_FAMILIES = ({"Mg", "Zn", "Co", "Ni"}, {"Al", "Fe", "Sc"})
LABEL = "unvalidated · ML only"


def parent_atoms(m):
    from ase import Atoms
    return Atoms(numbers=m["numbers"], positions=m["positions"], cell=m["cell"], pbc=True)


def aromatic_ch_sites(a):
    """(H index, C index) for H bonded to a 3-coordinate C, in index order."""
    from ase.neighborlist import NeighborList, natural_cutoffs
    nl = NeighborList(natural_cutoffs(a, mult=1.1), self_interaction=False, bothways=True)
    nl.update(a)
    sym = a.get_chemical_symbols()
    sites = []
    for i, s in enumerate(sym):
        if s != "H":
            continue
        nb, _ = nl.get_neighbors(i)
        if len(nb) == 1 and sym[nb[0]] == "C" and len(nl.get_neighbors(nb[0])[0]) == 3:
            sites.append((i, int(nb[0])))
    return sites


def _perpendicular(u):
    w = np.cross(u, [0.0, 0.0, 1.0])
    if np.linalg.norm(w) < 1e-3:
        w = np.cross(u, [0.0, 1.0, 0.0])
    return w / np.linalg.norm(w)


def functionalize(a, h_idx, c_idx, group):
    """Replace an aromatic H by F, OH, NH2 or CH3 along the C-H bond (idealized, then relaxed)."""
    from ase import Atom
    b = a.copy()
    v = b.get_distance(c_idx, h_idx, mic=True, vector=True)
    u = v / np.linalg.norm(v)
    w = _perpendicular(u)
    c = b.positions[c_idx]
    del b[h_idx]
    if group == "F":
        b += Atom("F", c + 1.35 * u)
    elif group == "OH":
        o = c + 1.36 * u
        b += Atom("O", o)
        b += Atom("H", o + 0.97 * (0.33 * u + 0.94 * w))
    elif group == "NH2":
        n = c + 1.38 * u
        b += Atom("N", n)
        for s in (1, -1):
            b += Atom("H", n + 1.01 * (0.5 * u + s * 0.866 * w))
    elif group == "CH3":
        cc = c + 1.50 * u
        b += Atom("C", cc)
        z = np.cross(u, w)
        for ang in (0.0, 120.0, 240.0):
            r = np.deg2rad(ang)
            b += Atom("H", cc + 1.09 * (0.33 * u + 0.94 * (np.cos(r) * w + np.sin(r) * z)))
    else:
        raise ValueError(f"unknown group {group}")
    return b


def swap_metal(a, old, new):
    b = a.copy()
    b.set_chemical_symbols([new if x == old else x for x in b.get_chemical_symbols()])
    return b


def metal_indices(a):
    from pymatgen.core import Element
    syms = a.get_chemical_symbols()
    metal = {x for x in set(syms) if Element(x).is_metal}
    return [i for i, x in enumerate(syms) if x in metal]


def resolve_site(a, sites, site, co2_pos=None):
    """Index into `sites` (aromatic C-H list) for an integer site or a SITES descriptor; ValueError if impossible."""
    from ase.geometry import get_distances
    if isinstance(site, (int, np.integer)) or (isinstance(site, str) and site.isdigit()):
        k = int(site)
        if not 0 <= k < len(sites):
            raise ValueError(f"site {k} out of range (parent has {len(sites)} aromatic C-H sites)")
        return k
    if site not in SITES:
        raise ValueError(f"site must be an index or one of {SITES}")
    hpos = a.positions[[h for h, _ in sites]]
    if site == "near_co2":
        if co2_pos is None:
            raise ValueError("near_co2 needs the parent's best MACE CO2 placement")
        targets = np.asarray(co2_pos, float)[:1]  # carbon first (CONTRACTS addenda)
    else:
        idx = metal_indices(a)
        if not idx:
            raise ValueError("parent has no metal atom")
        targets = a.positions[idx]
    _, d = get_distances(hpos, targets, cell=a.cell, pbc=True)
    dmin = d.min(axis=1)
    return int(np.argmax(dmin)) if site == "far_from_metal" else int(np.argmin(dmin))


def normalize(spec, parent_symbols=()):
    """Builder spec from any Chemist spec, including the first schema ({kind: functionalize|metal_swap, site: free
    text, group, new_metal}); ValueError when it cannot be made executable."""
    s = dict(spec or {})
    op = s.get("op") or {"metal_swap": "swap_metal"}.get(s.get("kind"), s.get("kind"))
    if op == "functionalize":
        site = s.get("site", 0)
        if isinstance(site, str) and site not in SITES and not site.isdigit():
            site = "near_co2"  # free text such as "aromatic C-H"
        return {"op": op, "group": s.get("group"), "site": site}
    if op == "swap_metal":
        new = s.get("to") or s.get("new_metal")
        old = s.get("from")
        if not old:
            fam = next((f for f in SWAP_FAMILIES if new in f), set())
            cands = sorted({x for x in parent_symbols if x in fam and x != new})
            if len(cands) != 1:
                raise ValueError(f"swap to {new} does not say which metal it replaces")
            old = cands[0]
        return {"op": op, "from": old, "to": new}
    raise ValueError(f"unknown op {op}")


def build(parent, spec, co2_pos=None):
    """Return (mutant atoms, tag) or raise ValueError with a reason. The tag names the resolved site, so two
    descriptors that pick the same C-H give the same mutant id."""
    op = spec.get("op")
    if op == "functionalize":
        group = spec.get("group")
        if group not in GROUPS:
            raise ValueError(f"group must be one of {GROUPS}")
        sites = aromatic_ch_sites(parent)
        if not sites:
            raise ValueError("parent has no aromatic C-H site")
        k = resolve_site(parent, sites, spec.get("site", 0), co2_pos)
        return functionalize(parent, *sites[k], group), f"{group}s{k}"
    if op == "swap_metal":
        old, new = spec.get("from"), spec.get("to")
        if old not in parent.get_chemical_symbols():
            raise ValueError(f"parent has no {old}")
        if old == new or not any(old in f and new in f for f in SWAP_FAMILIES):
            raise ValueError(f"{old}->{new} is not a charge-preserving swap")
        return swap_metal(parent, old, new), f"{old}2{new}"
    raise ValueError(f"unknown op {op}")


def relax(atoms, calc, fmax=FMAX, steps=300):
    from ase.filters import FrechetCellFilter
    from ase.optimize import FIRE
    a = atoms.copy()
    a.calc = calc
    traj = [(np.array(a.cell).copy(), a.positions.copy())]
    opt = FIRE(FrechetCellFilter(a), logfile=None)
    opt.attach(lambda: traj.append((np.array(a.cell).copy(), a.positions.copy())), interval=1)
    opt.run(fmax=fmax, steps=steps)
    f = float(np.linalg.norm(a.get_forces(), axis=1).max())
    return a, traj, f, opt.nsteps


def subsample(traj, n=MAX_FRAMES):
    idx = np.unique(np.linspace(0, len(traj) - 1, min(n, len(traj))).round().astype(int))
    return [{"cell": np.round(traj[i][0], 4).tolist(), "pos": np.round(traj[i][1], 3).tolist()} for i in idx]


def carry_over(ads_pos, parent_cell, new_cell):
    frac = np.asarray(ads_pos) @ np.linalg.inv(parent_cell)
    return frac @ np.asarray(new_cell)


def gas_energies(calc):
    from ase.build import molecule
    from ase.optimize import BFGS
    out = {}
    for g in ("CO2", "H2O"):
        m = molecule(g)
        m.set_cell([25, 25, 25])
        m.center()
        m.pbc = True
        m.calc = calc
        BFGS(m, logfile=None).run(fmax=0.01, steps=200)
        out[g] = float(m.get_potential_energy())
    return out


def binding(mutant, parent_m, cache_row, calc, gas_e):
    """MACE E_ads at the parent's best placements (fractional carry-over); returns values and CO2 doc."""
    from ase import Atoms
    b = mutant.copy()
    b.calc = calc
    e_mof = float(b.get_potential_energy())
    vals, docs = {}, {}
    names = {"CO2": cache_row["co2_best_placement"], "H2O": cache_row["h2o_best_placement"]}
    for gas, label in names.items():
        key = next(k for k in parent_m["ads"] if k.endswith("_w_" + label))
        ads = parent_m["ads"][key]
        pos = carry_over(ads["pos"], parent_m["cell"], mutant.cell)
        full = mutant + Atoms(numbers=ads["numbers"], positions=pos)
        full.calc = calc
        vals[gas] = float(full.get_potential_energy()) - e_mof - gas_e[gas]
        docs[gas] = full
    s = vals["CO2"] + max(0.0, vals["CO2"] - vals["H2O"] + 0.1)
    return {"E_CO2_mace": vals["CO2"], "E_H2O_mace": vals["H2O"], "S_mace": s}, docs["CO2"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("specs")
    ap.add_argument("out_dir")
    ap.add_argument("--frames", default="data/_build/frames.pkl")
    ap.add_argument("--mace", default="data/mace_cache.parquet")
    ap.add_argument("--steps", type=int, default=300)
    a = ap.parse_args()
    import pandas as pd
    import torch
    from mace.calculators import mace_mp
    torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "2")))
    with open(a.specs) as fh:
        specs = json.load(fh)
    frames = load_frames(a.frames)
    cache = pd.read_parquet(a.mace).set_index("id")
    calc32 = mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)
    gas_e = gas_energies(calc32)
    os.makedirs(a.out_dir, exist_ok=True)
    rows, eqv2_docs = [], []
    for item in specs:
        pid, spec = item["parent"], item["spec"]
        row = {"parent": pid, "spec": spec}
        try:
            if pid not in frames:
                raise ValueError("unknown parent")
            pm = frames[pid]
            if len(pm["numbers"]) > MAX_PARENT_ATOMS:
                raise ValueError(f"parent has {len(pm['numbers'])} > {MAX_PARENT_ATOMS} atoms")
            parent = parent_atoms(pm)
            co2 = json.loads(cache.at[pid, "co2_best_ads_pos"]) if pid in cache.index else None
            spec = normalize(spec, parent.get_chemical_symbols())
            built, tag = build(parent, spec, co2)
        except ValueError as e:
            rows.append({**row, "status": "rejected", "note": str(e)})
            print(json.dumps(rows[-1]), flush=True)
            continue
        mid = f"{pid}-{tag}"
        t0 = time.time()
        relaxed, traj, fmax, steps = relax(built, calc32, steps=a.steps)
        checks = validity_report(parent, relaxed, spec, fmax, FMAX, steps, a.steps)
        mace = None
        if pid in cache.index:
            mace, co2_doc = binding(relaxed, pm, cache.loc[pid], calc32, gas_e)
            n = len(relaxed)
            eqv2_docs.append({"id": mid, "cell": np.array(co2_doc.cell).tolist(), "numbers": co2_doc.numbers.tolist(),
                              "pos": co2_doc.positions.tolist(), "ads": list(range(n, len(co2_doc)))})
        doc = {"id": mid, "parent": pid, "spec": spec, "label": LABEL, "cell": np.round(np.array(relaxed.cell), 4).tolist(),
               "numbers": relaxed.numbers.tolist(), "ads": [], "pos": np.round(built.positions, 3).tolist(),
               "pos_relaxed": np.round(relaxed.positions, 3).tolist(), "frames": subsample(traj),
               "mace": mace, "validity": checks, "relax_s": round(time.time() - t0, 1)}
        with open(os.path.join(a.out_dir, f"{mid}.json"), "w") as fh:
            json.dump(doc, fh, separators=(",", ":"))
        rows.append({**row, "id": mid, "status": "valid" if checks["valid"] else "invalid", "validity": checks,
                     "mace": mace, "relax_s": doc["relax_s"]})
        print(json.dumps(rows[-1]), flush=True)
    with open(os.path.join(a.out_dir, "report.json"), "w") as fh:
        json.dump(rows, fh, indent=1)
    with open(os.path.join(a.out_dir, "eqv2_inputs.json"), "w") as fh:
        json.dump(eqv2_docs, fh)


if __name__ == "__main__":
    main()
