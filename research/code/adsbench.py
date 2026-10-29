"""Benchmark: CO2/H2O adsorption energy on rigid MOF with a universal MLIP (+D3).
E_ads = min_config[E(MOF+gas)] - E(MOF) - E(gas). Framework frozen, gas relaxed.
usage: python -I adsbench.py <model> <cif> [<cif> ...]
"""
import sys, time, os, json, warnings
warnings.filterwarnings("ignore")
import numpy as np, torch
from ase.io import read
from ase import Atoms
from ase.build import molecule
from ase.constraints import FixAtoms
from ase.optimize import FIRE, BFGS
from ase.calculators.mixing import SumCalculator

torch.set_num_threads(int(os.environ.get("NT", "4")))
MODEL = sys.argv[1]
cifs = sys.argv[2:]
rng = np.random.default_rng(0)


def get_calc(name):
    if name.startswith("mace"):
        from mace.calculators import mace_mp
        m = {"mace-mp0": "medium", "mace-mpa": "medium-mpa-0"}[name]
        return mace_mp(model=m, device="cpu", default_dtype="float32", dispersion=True)
    if name.startswith("orb"):
        from orb_models.forcefield import pretrained
        from orb_models.forcefield.inference.calculator import ORBCalculator
        fn = {"orb-omat": pretrained.orb_v3_conservative_inf_omat,
              "orb-mpa": pretrained.orb_v3_conservative_inf_mpa,
              "orb-direct-omat": pretrained.orb_v3_direct_20_omat}[name]
        from orb_models.forcefield.inference.d3_model import D3SumModel, AlchemiDFTD3
        model, adapter = fn(device="cpu", precision="float32-high")
        model = D3SumModel(model, AlchemiDFTD3(functional="PBE", damping="BJ", cutoff=float(os.environ.get("D3CUT", "22.0"))))
        return ORBCalculator(model, adapter, device="cpu")
    raise ValueError(name)


calc = get_calc(MODEL)


def supercell(a, minw=10.0):
    cell = a.cell
    vol = abs(np.linalg.det(cell))
    widths = [vol / np.linalg.norm(np.cross(cell[(i + 1) % 3], cell[(i + 2) % 3])) for i in range(3)]
    rep = [int(np.ceil(minw / w)) for w in widths]
    return a.repeat(rep), rep


def energy(at):
    at.calc = calc
    return at.get_potential_energy()


def gas(name):
    g = molecule(name)
    g.set_cell([20, 20, 20]); g.center(); g.pbc = True
    g.calc = calc
    BFGS(g, logfile=None).run(fmax=0.02, steps=200)
    return g.get_potential_energy()


def insert(frame, mol, n_try=2000):
    """random insertion: COM at a point with min distance to framework >= 2.6 A"""
    pos = frame.get_positions(); cell = frame.cell
    best = []
    for _ in range(n_try):
        f = rng.random(3); p = f @ cell.array
        d = frame.get_distances(0, range(len(frame)), mic=True) if False else None
        dv = pos - p
        dv = dv - np.round(dv @ np.linalg.inv(cell.array)) @ cell.array
        dmin = np.sqrt((dv ** 2).sum(1)).min()
        if dmin > 2.6:
            m = mol.copy(); m.rotate(rng.random() * 360, rng.normal(size=3)); m.translate(p - m.get_center_of_mass())
            best.append((dmin, m))
        if len(best) >= 64:
            break
    return best


t_gas = time.time()
EG = {g: gas(g) for g in ["CO2", "H2O"]}
t_gas = time.time() - t_gas
results = []
for cif in cifs:
    name = os.path.basename(cif)[:-4]
    a = read(cif); a.pbc = True
    sc, rep = supercell(a)
    t0 = time.time(); Ef = energy(sc.copy()); t_sp = time.time() - t0
    out = {"mof": name, "natoms": len(sc), "rep": rep, "t_single_point_s": round(t_sp, 2)}
    for g in ["CO2", "H2O"]:
        cands = insert(sc, molecule(g))
        # screen: single points on 6 random placements, relax best 2 (adsorbate only)
        idx = rng.choice(len(cands), size=min(6, len(cands)), replace=False)
        t0 = time.time(); scored = []
        for i in idx:
            s = sc + cands[i][1]
            s.set_constraint(FixAtoms(indices=list(range(len(sc)))))
            scored.append((energy(s), s))
        scored.sort(key=lambda x: x[0])
        best = 1e9; steps = 0
        for e0, s in scored[:2]:
            s.calc = calc
            opt = FIRE(s, logfile=None); opt.run(fmax=0.05, steps=150); steps += opt.nsteps
            best = min(best, s.get_potential_energy())
        out[f"Eads_{g}"] = round(best - Ef - EG[g], 3)
        out[f"t_{g}_s"] = round(time.time() - t0, 1); out[f"steps_{g}"] = steps
    print(json.dumps(out), flush=True)
    results.append(out)
print(json.dumps({"model": MODEL, "t_gas_s": round(t_gas, 1)}))
