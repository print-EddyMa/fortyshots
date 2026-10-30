"""Prospective mode, per run: build, MACE-relax, check, describe and rank the Chemist's mutants (container only).

Input: a finished run directory runs/<id>. For every `mutation` event with status "proposed" (at most --max, in
ledger order):
  1. build the mutant from the parent's ODAC initial framework (physics/mutate.py; legacy specs are normalized);
  2. relax cell and positions with MACE-MP-0 medium, float32 as the cache, FIRE + FrechetCellFilter,
     fmax 0.05 eV/A, <= 300 steps; keep <= 30 evenly spaced frames of the trajectory;
  3. validity checks (physics/validity.py): clash ratio >= 0.75, |dV| < 10%, converged, charge preserved;
  4. descriptors exactly as physics/descriptors.py (pymatgen composition, Zeo++ pores; the ODAC OMS flag is
     inherited from the parent) and MACE E_ads with CO2 and H2O at the parent's best MACE placements, carried
     over in fractional coordinates (single points, no gas relaxation, the cache's E(MOF+gas)-E(MOF)-E(gas));
  5. rank the valid mutants with the run's own surrogate: bench.surrogate.fit_predict (the pre-registered RF of
     arm A3 and fit_surrogate, seed 0) trained on the MOFs this run bought (ids from its dft_result events, DFT
     S recomputed from data/labels.parquet for those rows only); mean and per-tree std of predicted S.
  Optional: --eqv2-python <venvB python> adds eqV2-ODAC single points (physics/eqv2_check.py), shown greyed.

Output, never in the ledger and never in a benchmark number:
  runs/<id>/mutants/<mid>.json   viewer format (id, cell, numbers, ads = the carried-over CO2, pos = built,
                                 pos_relaxed) plus frames [{cell, pos}], spec, validity, mace, descriptors, label
  runs/<id>/mutants/index.json   one row per proposal: spec, parent, status, validity, predicted S +- std, rank,
                                 greyed MACE (and eqV2) values, the "unvalidated · ML only" label
  runs/<id>/mutations.jsonl      sidecar: one `mutation` event (status "built" | "invalid") per proposal, same
                                 envelope as the ledger, seq continuing after the ledger's last event

Idempotent: proposals that already have a sidecar event are not rebuilt (an existing mutant file is reused);
the index and the ranking are recomputed every time. --force rebuilds everything and rewrites the sidecar.
Proposals not finished inside --max-minutes are left for the next invocation.

usage: python -I physics/prospective.py runs/<id> [--data data] [--frames data/_build/frames.pkl] [--max 5]
       [--steps 300] [--workers 2] [--threads 2] [--max-minutes 15] [--force] [--eqv2-python PATH]
"""
import argparse
import json
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import mutate
import validity

LABEL = mutate.LABEL
MACE_NOTE = "ML binding at an unrelaxed placement: no ranking signal on this task, shown for transparency"
NOTE = ("Prospective mutants proposed by the Chemist, built and relaxed with MACE-MP-0 and ranked by this run's "
        "random-forest surrogate. Nothing here was checked by DFT, and none of it enters any benchmark number.")
RF_SEED = 0  # fortyshots/tools.py RF_SEED: the same surrogate the agent's fit_surrogate uses
DESC_KEYS = ["formula", "metals", "n_metal_atoms", "natoms", "volume_A3", "density_gcm3", "oms", "lcd_A", "pld_A",
             "asa_m2g", "void_fraction", "metal_cost_tier"]
MACE_KEYS = ["E_CO2_mace", "E_H2O_mace", "S_mace"]
SITE_TEXT = {"near_co2": "the C-H nearest the CO2 pocket", "near_metal": "the C-H nearest a metal",
             "far_from_metal": "the C-H farthest from any metal"}


def dac_score(e_co2, e_h2o):
    return float(e_co2 + max(0.0, e_co2 - e_h2o + 0.1))


def now_iso():
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_jsonl(path):
    out = []
    if not Path(path).exists():
        return out
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return out


def spec_text(spec, site_index=None):
    if spec.get("op") == "functionalize":
        site = spec.get("site")
        at = SITE_TEXT.get(site, str(site)) + (
            f" (C-H #{site_index})" if site_index is not None and not str(site).isdigit() else "")
        return f"functionalize {spec.get('group')} at {at}"
    if spec.get("op") == "swap_metal":
        return f"swap {spec.get('from')} → {spec.get('to')}"
    return json.dumps(spec)


# ---- worker (one MACE calculator per process) -------------------------------------------------------------------
_W = {}


def _init_worker(frames_path, mace_path, threads):
    import pandas as pd
    import torch
    from frames import load as load_frames
    from mace.calculators import mace_mp
    torch.set_num_threads(threads)
    _W["frames"] = load_frames(frames_path)
    _W["cache"] = pd.read_parquet(mace_path).set_index("id")
    _W["calc"] = mace_mp(model="medium", device="cpu", default_dtype="float32", dispersion=False)
    _W["gas"] = mutate.gas_energies(_W["calc"])


def _carried(ads_pos, parent_cell, cell):
    return np.asarray(mutate.carry_over(ads_pos, parent_cell, cell))


def descriptors(atoms, oms, mid):
    import descriptors as D
    m = {"id": mid, "cell": np.array(atoms.cell), "numbers": atoms.numbers, "positions": atoms.positions,
         "oms": bool(oms)}
    row = D.composition_row(m)
    z = D.zeo_guarded(m)
    row.update({k: z.get(k) for k in ("lcd_A", "pld_A", "asa_m2g", "void_fraction")})
    if "error" in z:
        row["zeo_error"] = z["error"]
    return row


def build_one(job):
    """job: {seq, parent, spec, steps, deadline, out}; returns a result dict (never raises for chemistry)."""
    t0 = time.time()
    pid, raw = job["parent"], job["spec"]
    res = {"proposal_seq": job["seq"], "parent": pid, "raw_spec": raw}
    if time.time() > job["deadline"]:
        return {**res, "deferred": True}
    frames, cache, calc = _W["frames"], _W["cache"], _W["calc"]
    try:
        if pid not in frames:
            raise ValueError("unknown parent")
        pm = frames[pid]
        if len(pm["numbers"]) > mutate.MAX_PARENT_ATOMS:
            raise ValueError(f"parent has {len(pm['numbers'])} > {mutate.MAX_PARENT_ATOMS} framework atoms")
        parent = mutate.parent_atoms(pm)
        co2 = json.loads(cache.at[pid, "co2_best_ads_pos"]) if pid in cache.index else None
        spec = mutate.normalize(raw, parent.get_chemical_symbols())
        res["spec"] = spec
        built, tag = mutate.build(parent, spec, co2)
    except ValueError as e:
        return {**res, "spec": res.get("spec", raw), "status": "invalid", "reason": str(e), "mutant_id": None,
                "dur_s": round(time.time() - t0, 1)}
    mid = f"{pid}-{tag}"
    res.update(mutant_id=mid, site_index=int(tag.split("s")[-1]) if spec["op"] == "functionalize" else None)
    path = Path(job["out"]) / f"{mid}.json"
    if path.exists() and not job.get("force"):
        doc = json.loads(path.read_text())
        return {**res, "status": "built" if doc["validity"]["valid"] else "invalid", "reused": True,
                "reason": None if doc["validity"]["valid"] else invalid_reason(doc["validity"]),
                "dur_s": round(time.time() - t0, 1)}
    relaxed, traj, fmax, steps = mutate.relax(built, calc, steps=job["steps"])
    relax_s = time.time() - t0
    checks = validity.report(parent, relaxed, spec, fmax, mutate.FMAX, steps, job["steps"])
    mace, co2_doc, h2o_doc = None, None, None
    ads_pos = None
    if pid in cache.index:
        mace, co2_full = mutate.binding(relaxed, pm, cache.loc[pid], calc, _W["gas"])
        mace = {k: round(float(v), 4) for k, v in mace.items()}
        n = len(relaxed)
        co2_doc = {"id": f"{mid}-CO2", "cell": np.array(co2_full.cell).tolist(), "numbers": co2_full.numbers.tolist(),
                   "pos": co2_full.positions.tolist(), "ads": list(range(n, len(co2_full)))}
        key = next(k for k in pm["ads"] if k.endswith("_w_" + cache.at[pid, "h2o_best_placement"]))
        h = pm["ads"][key]
        hpos = _carried(h["pos"], pm["cell"], relaxed.cell)
        h2o_doc = {"id": f"{mid}-H2O", "cell": np.array(relaxed.cell).tolist(),
                   "numbers": relaxed.numbers.tolist() + [int(z) for z in h["numbers"]],
                   "pos": relaxed.positions.tolist() + hpos.tolist(), "ads": list(range(n, n + len(h["numbers"])))}
        # the placement the MACE number was computed at, for the viewer (co2_best_ads_pos: carbon first, whole)
        ads_pos = np.asarray(co2, float)
    desc = descriptors(relaxed, pm["oms"], mid)
    co2_numbers = [6, 8, 8] if ads_pos is not None else []

    def with_ads(pos, cell):
        if ads_pos is None:
            return np.round(pos, 3).tolist()
        return np.round(np.vstack([pos, _carried(ads_pos, pm["cell"], cell)]), 3).tolist()

    sub = mutate.subsample(traj)
    frames_out = [{"cell": f["cell"], "pos": with_ads(np.asarray(f["pos"]), np.asarray(f["cell"]))} for f in sub]
    n = len(relaxed)
    doc = {"id": mid, "parent": pid, "spec": spec, "label": LABEL, "mace_note": MACE_NOTE,
           "cell": np.round(np.array(relaxed.cell), 4).tolist(),
           "numbers": relaxed.numbers.tolist() + co2_numbers, "ads": list(range(n, n + len(co2_numbers))),
           "pos": with_ads(built.positions, np.array(built.cell)),
           "pos_relaxed": with_ads(relaxed.positions, np.array(relaxed.cell)),
           "frames": frames_out, "n_steps": int(steps), "validity": checks, "mace": mace, "eqv2": None,
           "descriptors": desc, "relax_s": round(relax_s, 1), "total_s": round(time.time() - t0, 1)}
    path.write_text(json.dumps(doc, separators=(",", ":")))
    return {**res, "status": "built" if checks["valid"] else "invalid",
            "reason": None if checks["valid"] else invalid_reason(checks), "co2_doc": co2_doc, "h2o_doc": h2o_doc,
            "dur_s": round(time.time() - t0, 1)}


def invalid_reason(c):
    why = []
    if c["clash_ratio"] < validity.CLASH_MIN:
        why.append(f"clash ratio {c['clash_ratio']} < {validity.CLASH_MIN}")
    if abs(c["dV_frac"]) >= validity.DV_MAX:
        why.append(f"volume changed {c['dV_frac']:+.1%}")
    if not c["converged"]:
        why.append(f"not converged (fmax {c['fmax']} after {c['steps']} steps)")
    if c["charge"] not in ("pass", "n/a", "indeterminate"):
        why.append(f"charge check {c['charge']}")
    if c["charge"] == "indeterminate" and not why:
        why.append("charge indeterminate across valence families")
    return "; ".join(why) or "failed a validity check"


# ---- run-level --------------------------------------------------------------------------------------------------
def bought_ids(events):
    ids = []
    for e in events:
        if e.get("kind") == "dft_result":
            ids += [str(r["id"]) for r in (e.get("payload") or {}).get("results") or []]
    return ids


def run_surrogate(data, events, docs):
    """Mean and std of predicted S for each valid mutant doc (fit on this run's bought labels)."""
    import pandas as pd
    sys.path.insert(0, str(ROOT))
    from bench.surrogate import fit_predict
    ids = list(dict.fromkeys(bought_ids(events)))
    desc = pd.read_parquet(data / "descriptors.parquet")
    mace = pd.read_parquet(data / "mace_cache.parquet", columns=["id", *MACE_KEYS])
    lab = pd.read_parquet(data / "labels.parquet", columns=["id", "E_CO2", "E_H2O"]).set_index("id")
    train = pd.DataFrame({"id": ids}).merge(desc, on="id").merge(mace, on="id")
    y = [dac_score(lab.at[i, "E_CO2"], lab.at[i, "E_H2O"]) for i in train["id"]]
    info = {"model": "bench.surrogate.fit_predict (RF, 500 trees)", "seed": RF_SEED, "n_train": len(train),
            "n_bought": len(ids)}
    if len(train) < 5 or not docs:
        return {}, {**info, "skipped": "fewer than 5 bought labels" if len(train) < 5 else "no valid mutant"}
    test = pd.DataFrame([{**{k: d["descriptors"].get(k) for k in DESC_KEYS}, **(d["mace"] or {}), "id": d["id"]}
                         for d in docs])
    for k in MACE_KEYS:
        if k not in test:
            test[k] = np.nan
    mean, std = fit_predict(train, y, test, seed=RF_SEED)
    return {d["id"]: (round(float(m), 4), round(float(s), 4)) for d, m, s in zip(docs, mean, std)}, info


def run_eqv2(python, ckpt, docs, out_dir):
    """eqV2-ODAC single points for {mid: {CO2: doc, H2O: doc}}; {} when the cross-check is unavailable."""
    flat = [dict(d, id=f"{mid}|{gas}") for mid, g in docs.items() for gas, d in g.items() if d]
    if not flat:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        inp, out = Path(tmp) / "in.json", Path(tmp) / "out.json"
        inp.write_text(json.dumps(flat))
        cmd = [python, "-I", str(HERE / "eqv2_check.py"), str(inp), str(out)] + (["--ckpt", ckpt] if ckpt else [])
        r = subprocess.run(cmd, check=False, capture_output=True, text=True, env={**os.environ, "OMP_NUM_THREADS": "4"})
        if r.returncode != 0 or not out.exists():
            print(f"eqV2 cross-check failed: {(r.stderr or '').strip().splitlines()[-1:]}", file=sys.stderr)
            return {}
        rows = json.loads(out.read_text())
    res = {}
    for row in rows:
        mid, gas = row["id"].split("|")
        res.setdefault(mid, {})[f"E_{gas}_eqv2"] = round(float(row["eqv2_E_ads"]), 4)
    for v in res.values():
        if "E_CO2_eqv2" in v and "E_H2O_eqv2" in v:
            v["S_eqv2"] = round(dac_score(v["E_CO2_eqv2"], v["E_H2O_eqv2"]), 4)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--frames", default=None, help="default: <data>/_build/frames.pkl")
    ap.add_argument("--max", type=int, default=5, help="at most this many proposals per run")
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--threads", type=int, default=2, help="torch threads per worker")
    ap.add_argument("--max-minutes", type=float, default=15.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--eqv2-python", default=None)
    ap.add_argument("--eqv2-ckpt", default=os.environ.get("EQV2_CKPT"))
    a = ap.parse_args(argv)
    t_start = time.time()
    run_dir, data = Path(a.run_dir), Path(a.data)
    frames_path = a.frames or str(data / "_build" / "frames.pkl")
    events = read_jsonl(run_dir / "events.jsonl")
    if not events or not (run_dir / "summary.json").exists():
        sys.exit(f"{run_dir}: not a finished run (needs events.jsonl and summary.json)")
    run_id = events[0]["run_id"]
    proposals = [e for e in events if e.get("kind") == "mutation" and (e.get("payload") or {}).get("status") == "proposed"]
    if len(proposals) > a.max:
        print(f"{len(proposals)} proposals; building the first {a.max}", file=sys.stderr)
    proposals = proposals[: a.max]
    out_dir = run_dir / "mutants"
    out_dir.mkdir(exist_ok=True)
    side_path = run_dir / "mutations.jsonl"
    side = [] if a.force else read_jsonl(side_path)
    done = {e["payload"]["proposal_seq"] for e in side}
    todo = [e for e in proposals if e["seq"] not in done]
    print(f"{run_id}: {len(proposals)} proposals, {len(todo)} to build", file=sys.stderr)

    results, eq_docs = {}, {}
    if todo:
        deadline = t_start + a.max_minutes * 60
        jobs = [{"seq": e["seq"], "parent": e["payload"]["parent"], "spec": e["payload"]["spec"], "steps": a.steps,
                 "deadline": deadline, "out": str(out_dir), "force": a.force} for e in todo]
        workers = max(1, min(a.workers, len(jobs)))
        # non-daemonic workers (Zeo++ runs in a forked child of each), spawned so torch starts clean
        with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn"), initializer=_init_worker,
                                 initargs=(frames_path, str(data / "mace_cache.parquet"), a.threads)) as pool:
            for fut in as_completed([pool.submit(build_one, j) for j in jobs]):
                r = fut.result()
                results[r["proposal_seq"]] = r
                print(json.dumps({k: r.get(k) for k in ("proposal_seq", "mutant_id", "status", "reason", "dur_s",
                                                        "deferred", "reused")}), file=sys.stderr, flush=True)
                if r.get("co2_doc"):
                    eq_docs[r["mutant_id"]] = {"CO2": r.pop("co2_doc"), "H2O": r.pop("h2o_doc")}

    # sidecar events in proposal order (deterministic seq), only for proposals that finished
    last_seq = max(e["seq"] for e in events)
    rnd = next((e["round"] for e in reversed(events) if e["kind"] == "summary"), 0)
    new = []
    for e in todo:
        r = results.get(e["seq"])
        if not r or r.get("deferred"):
            continue
        mid = r.get("mutant_id")
        note = r.get("reason") or "valid: built, relaxed and checked"
        new.append({"seq": last_seq + 1 + len(side) + len(new), "t": now_iso(), "run_id": run_id, "round": rnd,
                    "role": "system", "kind": "mutation",
                    "payload": {"parent": r["parent"], "spec": r["spec"], "status": r["status"], "note": note,
                                "proposal_seq": e["seq"], "mutant_id": mid, "label": LABEL},
                    "call_id": None, "tokens": None, "timings": None, "dur_ms": int(r.get("dur_s", 0) * 1000)})
    side += new
    if new or a.force:  # the sidecar only grows; an up-to-date run leaves it byte for byte as it was
        tmp = side_path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in side))
        os.replace(tmp, side_path)

    # optional eqV2 cross-check for freshly built mutants
    if a.eqv2_python and eq_docs:
        t0 = time.time()
        eq = run_eqv2(a.eqv2_python, a.eqv2_ckpt, eq_docs, out_dir)
        for mid, vals in eq.items():
            p = out_dir / f"{mid}.json"
            doc = json.loads(p.read_text())
            doc["eqv2"] = vals
            p.write_text(json.dumps(doc, separators=(",", ":")))
        print(f"eqV2: {len(eq)} mutants in {time.time() - t0:.0f} s", file=sys.stderr)

    write_index(run_dir, events, side, proposals, data, t_start)


def write_index(run_dir, events, side, proposals, data, t_start):
    out_dir = run_dir / "mutants"
    by_seq = {e["payload"]["proposal_seq"]: e for e in side}
    prop = {e["seq"]: e for e in proposals}
    lab_rows = {}
    for e in events:
        if e.get("kind") == "dft_result":
            for r in (e.get("payload") or {}).get("results") or []:
                lab_rows[str(r["id"])] = r
    import pandas as pd
    desc = pd.read_parquet(data / "descriptors.parquet").set_index("id")
    cache = pd.read_parquet(data / "mace_cache.parquet", columns=["id", *MACE_KEYS]).set_index("id")
    docs = {}
    for mid in {e["payload"]["mutant_id"] for e in side if e["payload"].get("mutant_id")}:
        p = out_dir / f"{mid}.json"
        if p.exists():
            docs[mid] = json.loads(p.read_text())
    valid = [docs[m] for m in sorted(docs) if docs[m]["validity"]["valid"]]
    pred, sur = run_surrogate(data, events, valid)
    order = sorted(pred, key=lambda m: (pred[m][0], m))
    rows = []
    for seq in sorted(by_seq):
        ev = by_seq[seq]["payload"]
        mid, pid = ev.get("mutant_id"), ev["parent"]
        d = docs.get(mid) if mid else None
        b = lab_rows.get(pid)
        pr = pred.get(mid)
        rows.append({
            "id": mid or f"{pid}-p{seq}", "proposal_seq": seq, "file": f"mutants/{mid}.json" if d else None,
            "parent": pid, "parent_formula": desc.at[pid, "formula"] if pid in desc.index else None,
            "parent_S": round(dac_score(b["E_CO2"], b["E_H2O"]), 4) if b else None,
            "parent_hit": bool(b["hit"]) if b else None,
            "parent_mace": ({k: round(float(cache.at[pid, k]), 4) for k in MACE_KEYS} if pid in cache.index else None),
            "spec": ev["spec"], "spec_text": spec_text(ev["spec"], _site_index(mid)),
            "rationale": (prop.get(seq) or {}).get("payload", {}).get("note", ""),
            "status": ev["status"], "reason": None if ev["status"] == "built" else ev["note"],
            "validity": d["validity"] if d else None,
            "formula": d["descriptors"]["formula"] if d else None, "natoms": len(d["numbers"]) - len(d["ads"]) if d else None,
            "descriptors": ({k: d["descriptors"].get(k) for k in ("lcd_A", "pld_A", "asa_m2g", "void_fraction",
                                                                   "density_gcm3", "volume_A3")} if d else None),
            "pred_S": pr[0] if pr else None, "pred_std": pr[1] if pr else None,
            "rank": order.index(mid) + 1 if pr else None,
            "mace": d["mace"] if d else None, "eqv2": d.get("eqv2") if d else None,
            "n_frames": len(d["frames"]) if d else 0, "steps": d["n_steps"] if d else None,
            "relax_s": d["relax_s"] if d else None, "label": LABEL})
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, r["proposal_seq"]))
    index = {"run_id": events[0]["run_id"], "generated": now_iso(), "label": LABEL, "note": NOTE,
             "mace_note": MACE_NOTE, "surrogate": sur, "n_proposed": len(proposals),
             "n_built": sum(r["status"] == "built" for r in rows), "mutants": rows,
             "wall_s": round(time.time() - t_start, 1)}
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))
    print(json.dumps({"run_id": index["run_id"], "proposed": len(proposals), "built": index["n_built"],
                      "ranked": len(pred), "wall_s": index["wall_s"]}))


def _site_index(mid):
    if not mid:
        return None
    tag = mid.split("-", 1)[1]
    return int(tag.rsplit("s", 1)[1]) if "s" in tag and tag.rsplit("s", 1)[1].isdigit() else None


if __name__ == "__main__":
    main()
