"""Chemistry extras for the website: the landing hero's pore map and DDEC6 partial charges.

    python -m bench.chem hero [--id ID]          -> results/hero.json
    python -m bench.chem ddec --src DIR          -> results/ddec.json

hero: picks one DFT hit by a fixed rule (below) and writes a periodic grid of the distance from each point to the
nearest framework atom's van der Waals surface, from the DFT-relaxed framework in data/structures/<id>.json. The
site draws the isosurface at the CO2 probe radius (1.65 A, half its kinetic diameter): the surface of the space a
CO2 molecule's centre can reach, i.e. the channels.

ddec: reads ODAC23's DDEC6 charges (dl.fbaipublicfiles.com/dac/datasets/ddec.tar.gz, CC-BY-4.0; download it
yourself, it is 153 MB and is never committed) and maps them onto the framework atoms of every structure file by
element and periodic position. Written keyed by our hashed ids only; refcodes stay in id_map.parquet.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from bench.common import ROOT, TEST_POOLS

_ELEMENTS = ("X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb "
           "Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta "
           "W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr")
SYMBOLS = _ELEMENTS.split()
# Bondi / Alvarez van der Waals radii (A); metals without a Bondi value use 2.0.
VDW = {"H": 1.20, "B": 1.92, "C": 1.70, "N": 1.55, "O": 1.52, "F": 1.47, "Si": 2.10, "P": 1.80, "S": 1.80,
       "Cl": 1.75, "Br": 1.85, "I": 1.98, "Se": 1.90, "As": 1.85}
PROBE = 1.65  # CO2 kinetic diameter 3.3 A
SPACING = 0.45  # target grid spacing, A
SCALE = 20  # uint8 grid value = round(distance * SCALE), i.e. 0.05 A steps up to 12.75 A
HERO_MIN_MOVE = 0.5  # A; the CO2 must visibly move between the ODAC initial and DFT-relaxed poses


def load_struct(root: Path, mid: str) -> dict:
    return json.loads((root / "data" / "structures" / f"{mid}.json").read_text())


def co2_move(root: Path, mid: str) -> float:
    """Distance (A) the CO2 centroid moves from the ODAC initial placement to the DFT-relaxed pose."""
    s = load_struct(root, mid)
    a = np.array(s["pos"], dtype=float)[s["ads"]].mean(0)
    b = np.array(s["pos_relaxed"], dtype=float)[s["ads"]].mean(0)
    return float(np.linalg.norm(b - a))


def pick_hero(root: Path) -> str:
    """The test-pool hit with the largest cavity (LCD), among those whose CO2 moves at least HERO_MIN_MOVE in the
    DFT relaxation: open channels to look into, and a relaxation you can see."""
    lab = pd.read_parquet(root / "data" / "labels.parquet")
    des = pd.read_parquet(root / "data" / "descriptors.parquet")
    uni = pd.read_parquet(root / "data" / "universe.parquet")
    df = lab.merge(des, on="id").merge(uni[["id", "pool"]], on="id")
    c = df[df.hit & df.pool.isin(TEST_POOLS)].sort_values("lcd_A", ascending=False)
    for mid in c.id:
        if (root / "data" / "structures" / f"{mid}.json").exists() and co2_move(root, mid) >= HERO_MIN_MOVE:
            return str(mid)
    raise SystemExit("no hit qualifies for the hero")


def pore_grid(s: dict, spacing: float = SPACING) -> tuple[list[int], np.ndarray]:
    """Distance (A) from each fractional grid point to the nearest framework vdW surface, periodic."""
    cell = np.array(s["cell"], dtype=float)
    ads = set(s["ads"])
    fw = [i for i in range(len(s["numbers"])) if i not in ads]
    pos = np.array(s["pos_relaxed"], dtype=float)[fw]
    rad = np.array([VDW.get(SYMBOLS[z], 2.0) for z in np.array(s["numbers"])[fw]])
    n = [max(8, int(np.ceil(np.linalg.norm(cell[k]) / spacing))) for k in range(3)]
    g = np.stack(np.meshgrid(*[np.arange(m) / m for m in n], indexing="ij"), -1).reshape(-1, 3)
    pts = g @ cell
    frac = pos @ np.linalg.inv(cell)
    frac -= np.floor(frac)
    shifts = np.array([[i, j, k] for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)], dtype=float)
    imgs = ((frac[None, :, :] + shifts[:, None, :]).reshape(-1, 3)) @ cell
    rimg = np.tile(rad, len(shifts))
    best = np.full(len(pts), np.inf)
    for a in range(0, len(pts), 2048):
        d = np.linalg.norm(pts[a:a + 2048, None, :] - imgs[None, :, :], axis=-1) - rimg[None, :]
        best[a:a + 2048] = d.min(1)
    return n, best.reshape(n)


def cmd_hero(root: Path, mid: str | None) -> int:
    mid = mid or pick_hero(root)
    s = load_struct(root, mid)
    n, d = pore_grid(s)
    q = np.clip(np.round(d * SCALE), 0, 255).astype(np.uint8)  # negative (inside atoms) -> 0
    lab = pd.read_parquet(root / "data" / "labels.parquet").set_index("id").loc[mid]
    des = pd.read_parquet(root / "data" / "descriptors.parquet").set_index("id").loc[mid]
    uni = pd.read_parquet(root / "data" / "universe.parquet").set_index("id").loc[mid]
    out = {
        "id": mid,
        "rule": f"largest-cavity test-pool hit whose CO2 moves >= {HERO_MIN_MOVE:g} A in the DFT relaxation",
        "co2_move_A": round(co2_move(root, mid), 2),
        "pool": str(uni.pool),
        "formula": str(des.formula),
        "metals": str(des.metals),
        "E_CO2": round(float(lab.E_CO2), 3),
        "E_H2O": round(float(lab.E_H2O), 3),
        "lcd_A": round(float(des.lcd_A), 2),
        "pld_A": round(float(des.pld_A), 2),
        "void_fraction": round(float(des.void_fraction), 3),
        "pore": {
            "n": n,
            "order": "a slowest, c fastest; point (i, j, k) at fractional (i/na, j/nb, k/nc)",
            "scale": SCALE,
            "probe_A": PROBE,
            "geometry": "pos_relaxed (DFT-relaxed framework)",
            "radii": "Bondi/Alvarez van der Waals; 2.0 A for metals",
            "data": base64.b64encode(q.tobytes()).decode(),
        },
    }
    dest = root / "results" / "hero.json"
    dest.write_text(json.dumps(out, separators=(",", ":")))
    frac_open = float((d > PROBE).mean())
    print(f"hero: {mid} {des.formula} E_CO2 {lab.E_CO2:.2f} E_H2O {lab.E_H2O:.2f}; grid {n}; "
          f"{frac_open:.0%} of the cell reachable by a {PROBE} A probe -> {dest} ({dest.stat().st_size / 1024:.0f} KB)")
    return 0


def read_ddec(path: Path) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray]:
    lines = path.read_text().splitlines()
    nat = int(lines[0].split()[0])
    head = lines[1]
    cell = np.array(_cell(head))
    el, xyz, q = [], [], []
    for ln in lines[2:2 + nat]:
        p = ln.split()
        el.append(p[0])
        xyz.append([float(p[1]), float(p[2]), float(p[3])])
        q.append(float(p[4]))
    return cell, el, np.array(xyz), np.array(q)


def _cell(head: str) -> list[list[float]]:
    inner = head.split("unitcell", 1)[1]
    vecs = [v.strip(" [],{}") for v in inner.split("{")[1:4]]
    return [[float(x) for x in v.split("}")[0].split()] for v in vecs]


def cmd_ddec(root: Path, src: Path, tol: float = 0.25) -> int:
    idmap = pd.read_parquet(root / "data" / "id_map.parquet")
    uni = pd.read_parquet(root / "data" / "universe.parquet")
    ids = set(uni.id)
    charges, skipped = {}, {}
    for mid, ref in zip(idmap.id, idmap.refcode, strict=True):
        if mid not in ids:
            continue
        f = src / ref / "DDEC6_even_tempered_net_atomic_charges.xyz"
        sp = root / "data" / "structures" / f"{mid}.json"
        if not f.exists() or not sp.exists():
            skipped[mid] = "missing file"
            continue
        s = json.loads(sp.read_text())
        cell, el, xyz, q = read_ddec(f)
        ads = set(s["ads"])
        fw = [i for i in range(len(s["numbers"])) if i not in ads]
        if len(fw) != len(el):
            skipped[mid] = f"atom count {len(fw)} vs {len(el)}"
            continue
        C = np.array(s["cell"], dtype=float)
        inv = np.linalg.inv(C)
        ours = np.array(s["pos"], dtype=float)[fw] @ inv
        theirs = xyz @ np.linalg.inv(np.array(cell))
        dfrac = ours[:, None, :] - theirs[None, :, :]
        dfrac -= np.round(dfrac)
        dist = np.linalg.norm(dfrac @ C, axis=-1)
        sym = np.array([SYMBOLS[z] for z in np.array(s["numbers"])[fw]])
        dist[sym[:, None] != np.array(el)[None, :]] = np.inf
        j = dist.argmin(1)
        worst = float(dist[np.arange(len(fw)), j].max())
        if worst > tol or len(set(j.tolist())) != len(fw):
            skipped[mid] = f"no one-to-one match within {tol} A (worst {worst:.2f})"
            continue
        out = [None] * len(s["numbers"])
        for k, i in enumerate(fw):
            out[i] = round(float(q[j[k]]), 2)
        charges[mid] = out
    dest = root / "results" / "ddec.json"
    dest.write_text(json.dumps({
        "source": "ODAC23 DDEC6 net atomic charges (Meta FAIR, CC-BY-4.0), dl.fbaipublicfiles.com/dac/datasets/ddec.tar.gz",
        "units": "e; one value per atom of data/structures/<id>.json, null for the adsorbate",
        "match": f"element and periodic position at the ODAC initial geometry, one-to-one within {tol} A",
        "n": len(charges),
        "skipped": skipped,
        "charges": charges,
    }, separators=(",", ":")))
    print(f"ddec: {len(charges)} MOFs mapped, {len(skipped)} skipped -> {dest} ({dest.stat().st_size / 1024:.0f} KB)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("what", choices=["hero", "ddec"])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--id", default=None, help="hero: use this MOF instead of the rule")
    ap.add_argument("--src", type=Path, default=None, help="ddec: the extracted ddec/ directory")
    a = ap.parse_args(argv)
    if a.what == "hero":
        return cmd_hero(a.root, a.id)
    if not a.src or not a.src.is_dir():
        ap.error("ddec needs --src pointing at the extracted ddec/ directory")
    return cmd_ddec(a.root, a.src)


if __name__ == "__main__":
    sys.exit(main())
