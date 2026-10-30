"""Chemist helpers (prospective mode, off by default): parent choice, per-parent mutation options, schema check.

The Chemist only proposes; physics/prospective.py (container) builds, relaxes, checks and ranks the mutants.
Everything here mirrors physics/mutate.py so a spec that passes `check()` is one the builder can execute:

  functionalize  {"op": "functionalize", "group": NH2|OH|F|CH3, "site": near_co2|near_metal|far_from_metal}
                 on an aromatic C-H (an H whose only neighbour is a 3-coordinate C); the builder resolves the site
                 descriptor to one concrete C-H of the parent framework
  swap_metal     {"op": "swap_metal", "from": M, "to": M'} with M in the parent and M, M' in one
                 charge-preserving family: Mg Zn Co Ni (2+) or Al Fe Sc (3+)

Aromatic C-H sites are counted on the framework atoms of data/structures/<id>.json (ODAC initial geometry; the
same files render_structure uses, no labels), with the neighbour rule of ase.neighborlist.NeighborList with
natural_cutoffs(mult=1.1) and its default skin 0.3 A per atom.
"""
import json
from pathlib import Path

import numpy as np

MAX_PARENT_ATOMS = 120
MAX_PARENTS = 5
MAX_MUTATIONS = 5
GROUPS = ("NH2", "OH", "F", "CH3")
SITES = ("near_co2", "near_metal", "far_from_metal")
SWAP_FAMILIES = (("Mg", "Zn", "Co", "Ni"), ("Al", "Fe", "Sc"))

# ase.data.covalent_radii[0:97] (Angstrom), so the site count matches physics/mutate.py without ase on the Mac.
COVALENT_RADII = np.array([
    0.20, 0.31, 0.28, 1.28, 0.96, 0.84, 0.76, 0.71, 0.66, 0.57, 0.58, 1.66, 1.41, 1.21, 1.11, 1.07, 1.05, 1.02, 1.06,
    2.03, 1.76, 1.70, 1.60, 1.53, 1.39, 1.39, 1.32, 1.26, 1.24, 1.32, 1.22, 1.22, 1.20, 1.19, 1.20, 1.20, 1.16, 2.20,
    1.95, 1.90, 1.75, 1.64, 1.54, 1.47, 1.46, 1.42, 1.39, 1.45, 1.44, 1.42, 1.39, 1.39, 1.38, 1.39, 1.40, 2.44, 2.15,
    2.07, 2.04, 2.03, 2.01, 1.99, 1.98, 1.98, 1.96, 1.94, 1.92, 1.92, 1.89, 1.90, 1.87, 1.87, 1.75, 1.70, 1.62, 1.51,
    1.44, 1.41, 1.36, 1.36, 1.32, 1.45, 1.46, 1.48, 1.40, 1.50, 1.50, 2.60, 2.21, 2.15, 2.06, 2.00, 1.96, 1.90, 1.87,
    1.80, 1.69])
CUTOFF_MULT, SKIN = 1.1, 0.3


def swap_partners(metal: str) -> list[str]:
    for fam in SWAP_FAMILIES:
        if metal in fam:
            return [m for m in fam if m != metal]
    return []


def _neighbours(numbers: np.ndarray, pos: np.ndarray, cell: np.ndarray) -> np.ndarray:
    """Boolean adjacency under periodic boundaries (all 27 images), the NeighborList rule used by the builder."""
    r = COVALENT_RADII[np.clip(numbers, 0, len(COVALENT_RADII) - 1)] * CUTOFF_MULT + SKIN
    cut = r[:, None] + r[None, :]
    shifts = np.array([[i, j, k] for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)], float) @ cell
    d = pos[:, None, None, :] - pos[None, :, None, :] + shifts[None, None, :, :]
    dmin = np.sqrt((d ** 2).sum(-1)).min(-1)
    adj = dmin < cut
    np.fill_diagonal(adj, False)
    return adj


def aromatic_ch_count(structure: dict) -> int:
    """Number of aromatic C-H sites on the framework of a viewer-format structure ({cell, numbers, pos, ads})."""
    ads = set(structure.get("ads") or [])
    keep = [k for k in range(len(structure["numbers"])) if k not in ads]
    numbers = np.asarray(structure["numbers"], int)[keep]
    pos = np.asarray(structure["pos"], float)[keep]
    adj = _neighbours(numbers, pos, np.asarray(structure["cell"], float))
    deg = adj.sum(1)
    n = 0
    for h in np.flatnonzero(numbers == 1):
        nb = np.flatnonzero(adj[h])
        if len(nb) == 1 and numbers[nb[0]] == 6 and deg[nb[0]] == 3:
            n += 1
    return n


def site_count(data_dir: Path, mof_id: str) -> int | None:
    """Aromatic C-H count from data/structures/<id>.json, or None when the file is missing."""
    p = Path(data_dir) / "structures" / f"{mof_id}.json"
    if not p.exists():
        return None
    try:
        return aromatic_ch_count(json.loads(p.read_text()))
    except (ValueError, KeyError, IndexError):
        return None


def choose_parents(bought: dict, order: list[str], natoms: dict) -> list[str]:
    """Hits first, then the best DFT S; framework atoms <= MAX_PARENT_ATOMS; at most MAX_PARENTS."""
    ranked = sorted(order, key=lambda i: (not bought[i]["hit"], bought[i]["S"], i))
    return [i for i in ranked if int(natoms.get(i, 10 ** 6)) <= MAX_PARENT_ATOMS][:MAX_PARENTS]


def options(parents: list[str], metals: dict, data_dir: Path) -> dict:
    """{parent: {"sites": int | None, "swaps": {metal: [partners]}}}; parents with no possible mutation dropped.
    sites None (structure file missing) keeps functionalize allowed; the builder has the last word."""
    out = {}
    for p in parents:
        ms = [m for m in str(metals.get(p) or "").split(",") if m]
        swaps = {m: swap_partners(m) for m in ms if swap_partners(m)}
        sites = site_count(data_dir, p)
        if (sites is None or sites > 0) or swaps:
            out[p] = {"sites": sites, "swaps": swaps}
    return out


def to_spec(m) -> dict:
    """Builder spec (physics/mutate.py) from a validated schemas.Mutation."""
    if m.op == "functionalize":
        return {"op": "functionalize", "group": m.group, "site": m.site}
    return {"op": "swap_metal", "from": m.from_metal, "to": m.to_metal}


def check(m, opts: dict) -> str | None:
    """None when the builder can execute the mutation for this parent, else the reason it cannot."""
    o = opts.get(m.parent)
    if o is None:
        return f"{m.parent} is not one of the offered parents"
    if m.op == "functionalize":
        if m.group not in GROUPS:
            return f"group {m.group!r} is not one of {', '.join(GROUPS)}"
        if m.site not in SITES:
            return f"site {m.site!r} is not one of {', '.join(SITES)}"
        if o["sites"] == 0:
            return f"{m.parent} has no aromatic C-H site"
        return None
    if m.op == "swap_metal":
        if m.from_metal not in o["swaps"]:
            return f"{m.parent} has no swappable {m.from_metal or 'metal'}"
        if m.to_metal not in o["swaps"][m.from_metal]:
            return f"{m.from_metal}->{m.to_metal} is not a charge-preserving swap"
        return None
    return f"unknown op {m.op!r}"


def describe(parent_rows: list[str], opts: dict) -> str:
    """Parent table for the Chemist prompt: the bought facts plus what each parent allows."""
    lines = []
    for row in parent_rows:
        p = row.split(" ", 1)[0]
        o = opts[p]
        sites = "unknown" if o["sites"] is None else str(o["sites"])
        swaps = "; ".join(f"{m}->{'/'.join(t)}" for m, t in o["swaps"].items()) or "none"
        lines.append(f"{row} | aromatic C-H sites: {sites} | metal swaps: {swaps}")
    return "\n".join(lines)
