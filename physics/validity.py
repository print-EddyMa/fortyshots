"""Validity checks for ML-relaxed prospective mutants (all must pass for status "valid").

  clash      min over atom pairs of distance / (sum of covalent radii) >= 0.75
  volume     |V_relaxed / V_parent - 1| < 10%
  converged  max force <= fmax within the step cap
  charge     the swapped metal keeps the parent's oxidation state (pymatgen BVAnalyzer); "n/a" for
             functionalizations, "indeterminate" when bond valence fails, which counts as a pass only if
             both metals are in the same fixed valence family (DIVALENT / TRIVALENT below)
"""
import numpy as np

CLASH_MIN = 0.75
DV_MAX = 0.10
DIVALENT = {"Mg", "Ca", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Cd"}
TRIVALENT = {"Al", "Sc", "Cr", "Fe", "Ga", "In", "V"}


def clash_ratio(atoms):
    from ase.data import covalent_radii
    d = atoms.get_all_distances(mic=True)
    np.fill_diagonal(d, np.inf)
    r = covalent_radii[atoms.numbers]
    return float((d / (r[:, None] + r[None, :])).min())


def oxidation_state(atoms, element):
    """Mean bond-valence oxidation state of `element`, or None when BVAnalyzer fails."""
    from pymatgen.analysis.bond_valence import BVAnalyzer
    from pymatgen.io.ase import AseAtomsAdaptor
    try:
        s = AseAtomsAdaptor.get_structure(atoms)
        vals = BVAnalyzer().get_valences(s)
    except (ValueError, KeyError, RuntimeError):
        return None
    v = [val for val, sp in zip(vals, s.species) if sp.symbol == element]
    return float(np.mean(v)) if v else None


def same_family(a, b):
    return (a in DIVALENT and b in DIVALENT) or (a in TRIVALENT and b in TRIVALENT)


def charge_check(parent, mutant, spec):
    if spec.get("op") != "swap_metal":
        return "n/a"
    q0, q1 = oxidation_state(parent, spec["from"]), oxidation_state(mutant, spec["to"])
    if q0 is not None and q1 is not None:
        return "pass" if abs(q0 - q1) < 0.5 else "fail"
    return "indeterminate"


def report(parent, mutant, spec, fmax, fmax_target, steps, max_steps):
    dv = mutant.get_volume() / parent.get_volume() - 1
    charge = charge_check(parent, mutant, spec)
    checks = {
        "clash_ratio": round(clash_ratio(mutant), 3),
        "dV_frac": round(float(dv), 4),
        "fmax": round(float(fmax), 4),
        "steps": int(steps),
        "converged": bool(fmax <= fmax_target and steps <= max_steps),
        "charge": charge,
    }
    charge_ok = charge in ("pass", "n/a") or (
        charge == "indeterminate" and same_family(spec.get("from", ""), spec.get("to", "")))
    checks["valid"] = bool(checks["clash_ratio"] >= CLASH_MIN and abs(dv) < DV_MAX and checks["converged"]
                           and charge_ok)
    return checks
