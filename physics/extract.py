"""Extract pristine single-adsorbate ODAC23 IS2R configs into a private build dir.

Runs in the fairchem venv (needs torch_geometric to unpickle the LMDB records).
Output (container only, never committed): <out>/configs.extxyz with name, mof, gas,
split, y_init, y_relaxed in info, and pos_relaxed stored as a per-atom array.

usage: python -I physics/extract.py <is2r_root> <out_dir> [max_atoms]
"""
import glob
import io
import os
import pickle
import sys
import warnings

warnings.filterwarnings("ignore")
import lmdb  # noqa: E402
import numpy as np  # noqa: E402
from ase import Atoms  # noqa: E402
from ase.io import write  # noqa: E402

ALLOW = ("torch", "torch_geometric", "numpy", "collections", "builtins", "copyreg", "_codecs")
BAD = {"eval", "exec", "system", "__import__", "getattr", "open", "compile", "input", "breakpoint"}


class SafeUnpickler(pickle.Unpickler):
    def find_class(self, mod, name):
        if mod.split(".")[0] in ALLOW and name not in BAD:
            return super().find_class(mod, name)
        raise pickle.UnpicklingError(f"blocked {mod}.{name}")


def load(v):
    return SafeUnpickler(io.BytesIO(v)).load()


def main():
    root, out = sys.argv[1], sys.argv[2]
    max_atoms = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    os.makedirs(out, exist_ok=True)
    frames = []
    for split in ["train", "val"]:
        for f in sorted(glob.glob(f"{root}/{split}/*.lmdb")):
            env = lmdb.open(f, subdir=False, readonly=True, lock=False)
            with env.begin() as tx:
                for _, v in tx.cursor():
                    d = load(v)
                    if not hasattr(d, "keys") or d.nads != 1 or d.defective:
                        continue
                    if int(d.natoms) > max_atoms or abs(float(d.y_relaxed)) >= 5:
                        continue
                    a = Atoms(numbers=d.atomic_numbers.numpy().astype(int), positions=d.pos.numpy(),
                              cell=d.cell.numpy()[0], pbc=True)
                    a.set_tags(d.tags.numpy().astype(int))
                    a.new_array("pos_relaxed", d.pos_relaxed.numpy().astype(float))
                    a.info.update(name=d.name, mof=d.name.split("_w_")[0], gas="CO2" if d.nco2 else "H2O",
                                  split=split, y_init=float(d.y_init), y_relaxed=float(d.y_relaxed),
                                  oms=bool(d.oms))
                    frames.append(a)
        print(split, "configs so far", len(frames), flush=True)
    write(f"{out}/configs.extxyz", frames, format="extxyz")
    print("configs", len(frames), "mofs", len({a.info["mof"] for a in frames}))


if __name__ == "__main__":
    main()
