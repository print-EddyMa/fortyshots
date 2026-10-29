"""512 px PNGs the agent looks at: a structure render and the cheap-vs-DFT parity plot."""
import io
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Element symbols by atomic number, Z = 1..103 (hard-coded: the light venv has no ase).
ELEMENTS = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y",
    "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce",
    "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir",
    "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm",
    "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"
)
SYMBOLS = {z: sym for z, sym in enumerate(ELEMENTS, start=1)}
COLORS = {"H": "#f2f2f2", "C": "#5a5a5a", "N": "#3b5bdb", "O": "#e03131", "F": "#69db7c", "Cl": "#2f9e44",
          "Br": "#a61e4d", "I": "#862e9c", "S": "#f2c94c", "P": "#f08c00", "B": "#ffa8a8", "Si": "#c0a080"}
METAL_COLOR = "#1c7ed6"
COVALENT = {"H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57, "S": 1.05, "P": 1.07, "Cl": 1.02, "Br": 1.20,
            "B": 0.84, "Si": 1.11, "I": 1.39}
NONMETALS = {"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Se", "Br", "I"}
INK, MUTED, ACCENT, HIT = "#1f2328", "#8c959f", "#f76707", "#e8590c"


def _rot(ax_deg: float, ay_deg: float) -> np.ndarray:
    a, b = np.radians(ax_deg), np.radians(ay_deg)
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    return rx @ ry


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, metadata={"Software": None})
    plt.close(fig)
    return buf.getvalue()


def co2_atoms(pos3: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Splits a 3-atom CO2 into (carbon, oxygens): the carbon has the smallest summed distance."""
    d = np.linalg.norm(pos3[:, None] - pos3[None], axis=-1).sum(1)
    c = int(np.argmin(d))
    return pos3[c], np.delete(pos3, c, axis=0)


def load_framework(structures_dir: Path, mof_id: str) -> tuple[np.ndarray, list[str], np.ndarray]:
    """Framework atoms only (the stored adsorbate is never used: its placement is label-derived)."""
    s = json.loads((structures_dir / f"{mof_id}.json").read_text())
    keep = [i for i in range(len(s["numbers"])) if i not in set(s.get("ads", []))]
    pos = np.asarray(s["pos"], float)[keep]
    syms = [SYMBOLS.get(int(s["numbers"][i]), "X") for i in keep]
    return pos, syms, np.asarray(s["cell"], float)


def site_neighbours(pos, syms, cell, carbon, k: int = 3) -> list[dict]:
    """Nearest framework atoms to the CO2 carbon, minimum-image distances."""
    inv = np.linalg.inv(cell)
    d = pos - carbon
    d -= np.round(d @ inv) @ cell
    dist = np.linalg.norm(d, axis=1)
    order = np.argsort(dist)[:k]
    return [{"el": syms[i], "d_A": round(float(dist[i]), 2)} for i in order]


def render_structure(structures_dir: Path, mof_id: str, co2_pos: list | None) -> tuple[bytes, dict]:
    pos, syms, cell = load_framework(structures_dir, mof_id)
    co2 = np.asarray(co2_pos, float) if co2_pos is not None else None
    carbon, _ = co2_atoms(co2) if co2 is not None else (None, None)
    centre = cell.sum(0) / 2
    rot = _rot(18, -28)
    p = (pos - centre) @ rot.T
    fig = plt.figure(figsize=(5.12, 5.12), facecolor="white")
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.86])
    ax.set_axis_off()
    corners = np.array([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)], float) @ cell
    cp = (corners - centre) @ rot.T
    for a in range(8):
        for b in range(a + 1, 8):
            if (a ^ b).bit_count() == 1:
                ax.plot(cp[[a, b], 0], cp[[a, b], 1], color="#d0d7de", lw=0.8, zorder=0)
    rad = np.array([COVALENT.get(s, 1.35) for s in syms])
    dmat = np.linalg.norm(pos[:, None] - pos[None], axis=-1)
    bonded = (dmat < 1.15 * (rad[:, None] + rad[None])) & (dmat > 0.1)
    span = max(np.ptp(cp[:, 0]), np.ptp(cp[:, 1]), 1.0)
    pt_per_A = 368.6 / (1.16 * span)  # 5.12 in figure = 368.6 pt across the padded span
    zmin, zmax = p[:, 2].min(), p[:, 2].max() + 1e-9
    for i, j in zip(*np.nonzero(np.triu(bonded))):
        z = (p[i, 2] + p[j, 2]) / 2
        ax.plot(p[[i, j], 0], p[[i, j], 1], color="#adb5bd", lw=1.6, zorder=1 + (z - zmin) / (zmax - zmin))
    for i in np.argsort(p[:, 2]):
        depth = (p[i, 2] - zmin) / (zmax - zmin)
        col = COLORS.get(syms[i], METAL_COLOR if syms[i] not in NONMETALS else "#c0c0c0")
        size = 0.9 * rad[i] * pt_per_A * (0.8 + 0.3 * depth)
        ax.scatter(p[i, 0], p[i, 1], s=size ** 2, c=col, edgecolors="#495057", linewidths=0.5,
                   alpha=0.6 + 0.4 * depth, zorder=2 + depth)
    info = {"natoms_framework": len(syms)}
    if co2 is not None:
        q = (co2 - centre) @ rot.T
        ax.plot(q[:, 0], q[:, 1], color=ACCENT, lw=3.0, zorder=10)
        for k_, xyz in enumerate(q):
            is_c = np.allclose(co2[k_], carbon)
            ax.scatter(xyz[0], xyz[1], s=(1.2 * (0.76 if is_c else 0.66) * pt_per_A) ** 2,
                       c="#5a5a5a" if is_c else "#e03131", edgecolors=ACCENT, linewidths=2.0, zorder=11)
        info["site_neighbours"] = site_neighbours(pos, syms, cell, carbon)
        metal_d = [n["d_A"] for n in site_neighbours(pos, syms, cell, carbon, k=len(syms)) if n["el"] not in NONMETALS]
        info["nearest_metal_A"] = metal_d[0] if metal_d else None
    ratio = 0.86 / 0.96  # axes box height / width
    cx, cy = (cp[:, 0].max() + cp[:, 0].min()) / 2, (cp[:, 1].max() + cp[:, 1].min()) / 2
    w = 1.16 * max(np.ptp(cp[:, 0]), np.ptp(cp[:, 1]) / ratio)
    ax.set_xlim(cx - w / 2, cx + w / 2)
    ax.set_ylim(cy - w * ratio / 2, cy + w * ratio / 2)
    ax.set_aspect("equal")
    fig.text(0.03, 0.97, mof_id, va="top", fontsize=13, color=INK, family="monospace")
    fig.text(0.03, 0.925, "CO\u2082 at best cheap-oracle placement", va="top", fontsize=9, color=ACCENT)
    return _png(fig), info


def parity_plot(s_mace: list[float], s_dft: list[float], hits: list[bool]) -> bytes:
    x, y, h = np.asarray(s_mace, float), np.asarray(s_dft, float), np.asarray(hits, bool)
    fig, ax = plt.subplots(figsize=(5.12, 5.12), facecolor="white")
    lo = float(min(x.min(initial=0), y.min(initial=0))) - 0.2
    hi = float(max(x.max(initial=0), y.max(initial=0))) + 0.2
    ax.plot([lo, hi], [lo, hi], color="#d0d7de", lw=1, zorder=0)
    ax.scatter(x[~h], y[~h], s=36, c="#adb5bd", edgecolors="#495057", linewidths=0.5, label="not a hit", zorder=2)
    ax.scatter(x[h], y[h], s=48, c=HIT, edgecolors="#7f2704", linewidths=0.6, label="hit", zorder=3)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("cheap score S_mace (eV)", color=INK)
    ax.set_ylabel("DFT score S (eV)", color=INK)
    ax.set_title(f"Cheap oracle vs DFT, bought MOFs (n={len(x)})", color=INK, fontsize=11)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=MUTED)
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    fig.tight_layout()
    return _png(fig)
