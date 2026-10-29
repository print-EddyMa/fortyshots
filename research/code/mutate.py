"""Candidate generation ops an LLM can call as tools: metal substitution + aromatic C-H functionalization.
usage: python -I mutate.py <cif> ; prints validity report for a few mutations."""
import sys, os, time, json, warnings; warnings.filterwarnings("ignore")
import numpy as np, torch
from ase.io import read
from ase.neighborlist import natural_cutoffs, NeighborList
from ase.data import covalent_radii
from ase import Atom
torch.set_num_threads(int(os.environ.get("NT", "2")))

METALS = {"Mg", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Al", "Cr", "Zr", "Ca", "Cd", "Ti", "V", "Sc", "In", "Ga"}


def neighbors(a):
    nl = NeighborList(natural_cutoffs(a, mult=1.1), self_interaction=False, bothways=True); nl.update(a); return nl


def aromatic_CH_sites(a):
    """H bonded to an sp2-like C (C with 3 neighbors incl. this H) -> candidate functionalization sites."""
    nl = neighbors(a); sym = a.get_chemical_symbols(); sites = []
    for i, s in enumerate(sym):
        if s != "H": continue
        nb, _ = nl.get_neighbors(i)
        if len(nb) == 1 and sym[nb[0]] == "C" and len(nl.get_neighbors(nb[0])[0]) == 3:
            sites.append((i, int(nb[0])))
    return sites


def functionalize(a, h_idx, c_idx, group):
    """Replace aromatic H by F / OH / NH2 / CH3 along the C-H vector (idealized geometry, then MLIP-relax)."""
    b = a.copy()
    v = b.get_distance(c_idx, h_idx, mic=True, vector=True); u = v / np.linalg.norm(v)
    c = b.positions[c_idx]
    # perpendicular in-plane-ish helper vector
    w = np.cross(u, [0, 0, 1.0]); w = w / np.linalg.norm(w) if np.linalg.norm(w) > 1e-3 else np.cross(u, [0, 1.0, 0]) / np.linalg.norm(np.cross(u, [0, 1.0, 0]))
    del b[h_idx]
    if group == "F":
        b += Atom("F", c + 1.35 * u)
    elif group == "OH":
        o = c + 1.36 * u; b += Atom("O", o); b += Atom("H", o + 0.97 * (0.33 * u + 0.94 * w))
    elif group == "NH2":
        n = c + 1.38 * u; b += Atom("N", n)
        for s in (+1, -1): b += Atom("H", n + 1.01 * (0.5 * u + s * 0.866 * w))
    elif group == "CH3":
        cc = c + 1.50 * u; b += Atom("C", cc); z = np.cross(u, w)
        for ang in (0, 120, 240):
            r = np.deg2rad(ang); d = 0.33 * u + 0.94 * (np.cos(r) * w + np.sin(r) * z); b += Atom("H", cc + 1.09 * d)
    return b


def substitute_metal(a, old, new):
    b = a.copy(); s = b.get_chemical_symbols(); b.set_chemical_symbols([new if x == old else x for x in s]); return b


def validity(a, tol=0.75):
    """min pair distance relative to covalent radii sum (clash check)."""
    d = a.get_all_distances(mic=True); np.fill_diagonal(d, 99)
    r = covalent_radii[a.numbers]; ratio = d / (r[:, None] + r[None, :])
    return float(ratio.min())


if __name__ == "__main__":
    from mace.calculators import mace_mp
    from ase.optimize import FIRE
    from ase.filters import FrechetCellFilter
    calc = mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)
    a = read(sys.argv[1]); a.pbc = True
    sites = aromatic_CH_sites(a)
    metals = sorted({x for x in a.get_chemical_symbols() if x in METALS})
    print("natoms", len(a), "formula", a.get_chemical_formula(), "metals", metals, "aromatic C-H sites", len(sites))
    jobs = []
    if sites: jobs += [("NH2", functionalize(a, *sites[0], "NH2")), ("F", functionalize(a, *sites[0], "F"))]
    if metals: jobs += [(f"{metals[0]}->Zn" if metals[0] != "Zn" else "Zn->Co", substitute_metal(a, metals[0], "Zn" if metals[0] != "Zn" else "Co"))]
    for name, b in jobs:
        b.calc = calc; v0 = b.get_volume(); clash0 = validity(b)
        t = time.time(); opt = FIRE(FrechetCellFilter(b), logfile=None); opt.run(fmax=0.1, steps=int(os.environ.get("STEPS", "60")))
        print(json.dumps(dict(mutation=name, natoms=len(b), clash_ratio_before=round(clash0, 2), clash_ratio_after=round(validity(b), 2),
                              dV_pct=round(100 * (b.get_volume() / v0 - 1), 2), steps=opt.nsteps, fmax=round(float(np.abs(b.get_forces()).max()), 3), t_s=round(time.time() - t, 1))), flush=True)
