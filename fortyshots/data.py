"""Loads the agent-visible tables for one pool. Never touches labels.parquet."""
from pathlib import Path

import pandas as pd

DESCRIPTOR_COLS = ["formula", "metals", "n_metal_atoms", "natoms", "volume_A3", "density_gcm3", "oms", "lcd_A",
                   "pld_A", "asa_m2g", "void_fraction", "metal_cost_tier"]
MACE_COLS = ["E_CO2_mace", "E_H2O_mace", "S_mace"]


class DataMissing(RuntimeError):
    pass


def _read(path: Path, columns=None) -> pd.DataFrame:
    if not path.exists():
        raise DataMissing(f"{path} not found (use --data-dir to point at a dataset)")
    return pd.read_parquet(path, columns=columns)


def load_pool(data_dir: Path, pool: str) -> pd.DataFrame:
    """id + descriptors + cheap MACE values + best CO2 pose for every MOF in the pool, sorted by id."""
    uni = _read(data_dir / "universe.parquet", ["id", "pool"])
    ids = uni.loc[uni["pool"] == pool, ["id"]]
    if ids.empty:
        raise DataMissing(f"pool {pool} has no rows in {data_dir / 'universe.parquet'}")
    desc = _read(data_dir / "descriptors.parquet", ["id", *DESCRIPTOR_COLS])
    mace = _read(data_dir / "mace_cache.parquet", ["id", *MACE_COLS, "co2_best_ads_pos"])
    df = ids.merge(desc, on="id", how="inner").merge(mace, on="id", how="inner")
    if len(df) < len(ids):
        missing = len(ids) - len(df)
        if missing > 0.05 * len(ids):
            raise DataMissing(f"{missing} of {len(ids)} {pool} MOFs lack descriptors or MACE values")
    df["oms"] = df["oms"].astype(bool)
    df["metal_cost_tier"] = df["metal_cost_tier"].astype(int)
    return df.sort_values("id").reset_index(drop=True)


def load_id_map(data_dir: Path) -> pd.DataFrame:
    """Leak probe only."""
    return _read(data_dir / "id_map.parquet")
