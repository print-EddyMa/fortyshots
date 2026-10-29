"""Shared science constants and data loading for the benchmark (light venv only)."""
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

BUDGET = 40
ROUNDS = 4
BATCH = 10
HIT_CO2 = -0.5
TEST_POOLS = ["P1", "P2", "P3", "P4", "P5"]
ALL_POOLS = ["P0", *TEST_POOLS]
ROOT = Path(__file__).resolve().parent.parent

ARM_LABELS = {
    "A0": "Random",
    "A1": "Heuristic (OMS, PLD, cost)",
    "A2": "MACE greedy",
    "A3": "RF-UCB",
    "A4": "Glimmer zero-shot",
    "A5": "Glimmer agent",
    "A6": "Agent, no surrogate",
}
BASELINE_ARMS = ["A0", "A1", "A2", "A3"]
LLM_ARMS = ["A4", "A5", "A6"]
MACE_COLS = ["E_CO2_mace", "E_H2O_mace", "S_mace"]
PREREG_TAG = "prereg-v1"


class MaceCoverageError(ValueError):
    """An analysed pool lacks cheap MACE values for some of its MOFs (results would be meaningless)."""


def dac_score(co2, h2o):
    """Lower is better: CO2 binding, penalised when water binds within 0.1 eV of CO2 or stronger."""
    co2 = np.asarray(co2, dtype=float)
    return co2 + np.maximum(0.0, co2 - np.asarray(h2o, dtype=float) + 0.1)


def is_hit(co2, h2o):
    co2 = np.asarray(co2, dtype=float)
    return (co2 < HIT_CO2) & (co2 < np.asarray(h2o, dtype=float))


@dataclass
class Data:
    universe: pd.DataFrame
    labels: pd.DataFrame
    mace: pd.DataFrame
    descriptors: pd.DataFrame

    def pool(self, name):
        """One row per MOF of the pool with descriptors, cheap MACE values and labels (sorted by id)."""
        u = self.universe[self.universe.pool == name][["id", "pool"]]
        df = u.merge(self.descriptors, on="id", how="left").merge(
            self.mace[["id", "E_CO2_mace", "E_H2O_mace", "S_mace"]], on="id", how="left").merge(
            self.labels, on="id", how="left")
        df["S"] = dac_score(df.E_CO2, df.E_H2O)
        return df.sort_values("id").reset_index(drop=True)

    def pool_hits(self, name):
        return int(self.pool(name).hit.sum())

    def mace_coverage(self, name):
        """Fraction of the pool's MOFs with finite E_CO2_mace, E_H2O_mace and S_mace."""
        df = self.pool(name)
        if not len(df):
            return 0.0
        return float(np.isfinite(df[MACE_COLS].to_numpy(dtype=float)).all(axis=1).mean())

    def require_mace(self, pools):
        """Raise MaceCoverageError unless every pool has complete MACE values; returns {pool: coverage}."""
        cov = {p: self.mace_coverage(p) for p in pools}
        bad = {p: c for p, c in cov.items() if c < 1.0}
        if bad:
            detail = ", ".join(f"{p} {c:.1%} ({round(c * len(self.pool(p)))}/{len(self.pool(p))})"
                               for p, c in bad.items())
            ok = [p for p in pools if p not in bad]
            raise MaceCoverageError(
                f"incomplete MACE values in data/mace_cache.parquet for analysed pool(s): {detail}. Missing cheap "
                f"values are never treated as worst; finish physics/mace_cache.py or analyse a subset with "
                f"--pools {' '.join(ok) if ok else '<pools with complete MACE>'}.")
        return cov


def load_data(data_dir=None):
    d = Path(data_dir) if data_dir else ROOT / "data"
    mace_path, desc_path = d / "mace_cache.parquet", d / "descriptors.parquet"
    mace = pd.read_parquet(mace_path) if mace_path.exists() else pd.DataFrame(
        columns=["id", "E_CO2_mace", "E_H2O_mace", "S_mace"])
    desc = pd.read_parquet(desc_path) if desc_path.exists() else pd.DataFrame(columns=["id"])
    return Data(pd.read_parquet(d / "universe.parquet"), pd.read_parquet(d / "labels.parquet"), mace, desc)


def _git(root, *args):
    try:
        return subprocess.run(["git", "-C", str(root), *args], check=False, capture_output=True, text=True,
                              timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None


def prereg_commit(root=ROOT, tag=PREREG_TAG):
    """Full commit sha of the pre-registration tag, or None when the tag (or git) is absent."""
    out = _git(root, "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
    return out.stdout.strip() if out is not None and out.returncode == 0 and out.stdout.strip() else None


def is_descendant(root, ancestor, sha):
    """True when commit `sha` is `ancestor` or a descendant of it; False otherwise (including unknown shas)."""
    if not sha:
        return False
    out = _git(root, "merge-base", "--is-ancestor", ancestor, str(sha))
    return out is not None and out.returncode == 0
