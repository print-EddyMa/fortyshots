"""Parse the private ODAC23 extract once into a compact per-MOF index (container only, never committed).

For every MOF in the universe it keeps the framework (cell, numbers, positions from the first config in
name order, the same frame `mace_cache.py` uses for E(MOF)), the ODAC open-metal-site flag (any config),
and the adsorbate positions of every placement, keyed by config name.

usage: python -I physics/frames.py data/_build/configs.extxyz data/_build/frames.pkl
"""
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def build(src):
    from ase.io import iread
    from build_data import hid
    mofs = {}
    for a in iread(src, index=":"):
        info = a.info
        i = hid(info["mof"])
        tags = a.get_tags()
        m = mofs.setdefault(i, {"id": i, "oms": False, "ads": {}, "_first": None})
        m["oms"] = m["oms"] or bool(info.get("oms", False))
        m["ads"][info["name"]] = {"gas": info["gas"], "pos": a.positions[tags > 0].astype(float),
                                  "numbers": a.numbers[tags > 0].astype(int)}
        if m["_first"] is None or info["name"] < m["_first"][0]:
            fw = tags == 0
            m["_first"] = (info["name"], np.array(a.cell, dtype=float), a.numbers[fw].astype(int),
                           a.positions[fw].astype(float))
    for m in mofs.values():
        _, m["cell"], m["numbers"], m["positions"] = m.pop("_first")
    return mofs


def load(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


def main():
    src, out = sys.argv[1], sys.argv[2]
    mofs = build(src)
    with open(out, "wb") as fh:
        pickle.dump(mofs, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print("mofs", len(mofs), "configs", sum(len(m["ads"]) for m in mofs.values()))


if __name__ == "__main__":
    main()
