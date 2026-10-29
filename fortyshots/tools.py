"""The agent's tools over one pool. Only request_dft (and the post-hoc leak-probe scorer) read labels.parquet."""
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from bench import surrogate

from . import render
from .config import BUDGET, dac_score
from .data import load_pool

SORT_KEYS = ["S_mace", "E_CO2_mace", "pld_A", "lcd_A", "density_gcm3", "void_fraction", "asa_m2g"]
FILTER_KEYS = ["metal_in", "metal_not_in", "oms", "pld_min", "pld_max", "lcd_min", "lcd_max", "cost_tier_max",
               "exclude_bought"]
LIST_COLS = ["id", "formula", "metals", "oms", "pld_A", "lcd_A", "density_gcm3", "void_fraction", "asa_m2g",
             "metal_cost_tier", "E_CO2_mace", "E_H2O_mace", "S_mace"]
DESC_COLS = ["id", "formula", "metals", "n_metal_atoms", "natoms", "volume_A3", "density_gcm3", "oms", "lcd_A", "pld_A",
             "asa_m2g", "void_fraction", "metal_cost_tier"]
DECIMALS = {"pld_A": 2, "lcd_A": 2, "density_gcm3": 3, "void_fraction": 3, "asa_m2g": 0, "volume_A3": 0}
MAX_IDS = 25
RF_SEED = 0

_ids = {"type": "array", "items": {"type": "string"}, "maxItems": MAX_IDS}
TOOL_SPECS = {
    "query_pool": {
        "description": "Filter and sort the candidate pool. Returns up to 25 rows of id, composition, pore descriptors "
                       "and the cheap simulator values (E_CO2_mace, E_H2O_mace, S_mace, eV; lower S is better).",
        "parameters": {"type": "object", "properties": {
            "filters": {"type": "object", "properties": {
                "metal_in": {"type": "array", "items": {"type": "string"}},
                "metal_not_in": {"type": "array", "items": {"type": "string"}},
                "oms": {"type": "boolean"},
                "pld_min": {"type": "number"}, "pld_max": {"type": "number"},
                "lcd_min": {"type": "number"}, "lcd_max": {"type": "number"},
                "cost_tier_max": {"type": "integer", "minimum": 1, "maximum": 3},
                "exclude_bought": {"type": "boolean"}}},
            "sort_by": {"type": "string", "enum": SORT_KEYS},
            "ascending": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_IDS}},
            "required": ["sort_by"]}},
    "get_descriptors": {
        "description": "Full descriptor rows (formula, metals, open metal sites, pore sizes, surface area, density, "
                       "metal cost tier) for up to 25 ids.",
        "parameters": {"type": "object", "properties": {"ids": _ids}, "required": ["ids"]}},
    "cheap_score": {
        "description": "Cheap simulator values for up to 25 ids: E_CO2_mace, E_H2O_mace and S_mace in eV.",
        "parameters": {"type": "object", "properties": {"ids": _ids}, "required": ["ids"]}},
    "fit_surrogate": {
        "description": "Fit a random forest on the DFT labels bought so far (features: descriptors plus cheap simulator "
                       "values) and return the top_k unbought MOFs by predicted S minus kappa times its spread. "
                       "Needs at least 5 bought labels.",
        "parameters": {"type": "object", "properties": {
            "kappa": {"type": "number", "minimum": 0, "maximum": 5},
            "top_k": {"type": "integer", "minimum": 1, "maximum": MAX_IDS}}}},
    "calibrate_oracle": {
        "description": "Compare the cheap simulator score S_mace with the DFT score S over every MOF bought so far. "
                       "Returns agreement statistics and draws a parity plot.",
        "parameters": {"type": "object", "properties": {}}},
    "render_structure": {
        "description": "Render one MOF framework with CO2 at its best cheap-simulator placement (512 px image) and list "
                       "the framework atoms nearest to the CO2 carbon.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    "request_dft": {
        "description": "Buy DFT labels (E_CO2, E_H2O, hit) for these ids. Budget-enforced.",
        "parameters": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}},
                                                        "rationale": {"type": "string"}},
                       "required": ["ids", "rationale"]}},
}
EXPLORE_TOOLS = ["query_pool", "get_descriptors", "cheap_score", "fit_surrogate", "calibrate_oracle", "render_structure"]
ARM_TOOLS = {
    "A5": EXPLORE_TOOLS,
    "A6": [t for t in EXPLORE_TOOLS if t != "fit_surrogate"],
    "A4": ["query_pool", "get_descriptors", "cheap_score", "render_structure"],
}


def tool_schemas(names: list[str]) -> list[dict]:
    return [{"type": "function", "function": {"name": n, **TOOL_SPECS[n]}} for n in names]


class ToolError(ValueError):
    pass


@dataclass
class ToolOutput:
    ok: bool
    summary: str
    result: object
    image: str | None = None
    ids: list[str] = field(default_factory=list)

    def for_llm(self, call_id: str, name: str) -> str:
        body = json.dumps(self.result, separators=(",", ":"), ensure_ascii=False)
        return f"[{call_id}] {name}: {self.summary}\n{body}"


def _r(v, nd=3):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return round(float(v)) if nd == 0 else round(float(v), nd)
    return v


def _table(df: pd.DataFrame, cols: list[str]) -> dict:
    rows = [[_r(row[c], DECIMALS.get(c, 3)) for c in cols] for _, row in df[cols].iterrows()]
    return {"columns": cols, "rows": rows}


def _ids_arg(args: dict, key: str = "ids") -> list[str]:
    ids = args.get(key)
    if isinstance(ids, str):
        ids = [s.strip() for s in ids.replace(";", ",").split(",") if s.strip()]
    if not isinstance(ids, list) or not ids:
        raise ToolError(f"'{key}' must be a non-empty list of ids")
    return list(dict.fromkeys(str(i).strip() for i in ids))


class PoolEnv:
    def __init__(self, data_dir: Path, pool: str, img_dir: Path, budget: int = BUDGET, tools: list[str] | None = None):
        self.data_dir = Path(data_dir)
        self.pool_name = pool
        self.df = load_pool(self.data_dir, pool)
        self.by_id = self.df.set_index("id", drop=False)
        self.img_dir = Path(img_dir)
        self.budget = budget
        self.tools = list(tools if tools is not None else EXPLORE_TOOLS)
        self.bought: dict[str, dict] = {}
        self.order: list[str] = []
        self.last_ranking: list[str] = []
        self.last_listing: list[str] = []
        self.calibrations: list[dict] = []

    # ---- bookkeeping -------------------------------------------------------
    @property
    def spent(self) -> int:
        return len(self.order)

    @property
    def remaining(self) -> int:
        return self.budget - self.spent

    @property
    def ids(self) -> list[str]:
        return self.df["id"].tolist()

    def unbought(self) -> list[str]:
        return [i for i in self.ids if i not in self.bought]

    def mace_ranking(self) -> list[str]:
        return self.df.sort_values(["S_mace", "id"])["id"].tolist()

    def _check_ids(self, ids: list[str]) -> list[str]:
        bad = [i for i in ids if i not in self.by_id.index]
        if bad:
            raise ToolError(f"unknown ids: {', '.join(bad[:5])}")
        return ids

    # ---- dispatch ------------------------------------------------------------
    def call(self, name: str, args: dict | None, call_id: str) -> ToolOutput:
        args = args or {}
        if name not in self.tools and name != "request_dft":
            return ToolOutput(False, f"{name} is not available in this campaign", {"error": "unknown tool"})
        try:
            fn = getattr(self, name)
            return fn(call_id=call_id, **args) if name in ("calibrate_oracle", "render_structure") else fn(**args)
        except ToolError as e:
            return ToolOutput(False, f"error: {e}"[:120], {"error": str(e)})
        except TypeError as e:
            return ToolOutput(False, f"bad arguments for {name}"[:120], {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - a tool bug must not end a long campaign; the ledger records it
            return ToolOutput(False, f"internal error in {name}"[:120], {"error": f"{type(e).__name__}: {e}"[:300]})

    # ---- tools -----------------------------------------------------------------
    def query_pool(self, sort_by: str = "S_mace", filters: dict | None = None, ascending: bool = True,
                   limit: int = 25) -> ToolOutput:
        filters = dict(filters or {})
        unknown = set(filters) - set(FILTER_KEYS)
        if unknown:
            raise ToolError(f"unknown filters {sorted(unknown)}; valid: {FILTER_KEYS}")
        if sort_by not in SORT_KEYS:
            raise ToolError(f"sort_by must be one of {SORT_KEYS}")
        df = self.df
        metal_sets = df["metals"].fillna("").str.split(",").apply(lambda xs: {x.strip() for x in xs if x.strip()})
        mask = pd.Series(True, index=df.index)
        if filters.get("metal_in"):
            want = set(filters["metal_in"])
            mask &= metal_sets.apply(lambda s: bool(s & want))
        if filters.get("metal_not_in"):
            avoid = set(filters["metal_not_in"])
            mask &= metal_sets.apply(lambda s: not (s & avoid))
        if filters.get("oms") is not None:
            mask &= df["oms"] == bool(filters["oms"])
        for key, col, op in [("pld_min", "pld_A", "ge"), ("pld_max", "pld_A", "le"), ("lcd_min", "lcd_A", "ge"),
                             ("lcd_max", "lcd_A", "le"), ("cost_tier_max", "metal_cost_tier", "le")]:
            if filters.get(key) is not None:
                mask &= getattr(df[col], op)(float(filters[key]))
        if filters.get("exclude_bought", True):
            mask &= ~df["id"].isin(self.bought)
        hit = df[mask].sort_values([sort_by, "id"], ascending=[bool(ascending), True])
        limit = max(1, min(int(limit), MAX_IDS))
        out = hit.head(limit)
        self.last_listing = out["id"].tolist()
        res = {"n_match": int(mask.sum()), **_table(out, LIST_COLS)}
        summ = f"{int(mask.sum())} match; top {len(out)} by {sort_by} {'asc' if ascending else 'desc'}"
        return ToolOutput(True, summ, res, ids=self.last_listing)

    def get_descriptors(self, ids) -> ToolOutput:
        ids = self._check_ids(_ids_arg({"ids": ids})[:MAX_IDS])
        res = _table(self.by_id.loc[ids], DESC_COLS)
        return ToolOutput(True, f"descriptors for {len(ids)} MOFs", res, ids=ids)

    def cheap_score(self, ids) -> ToolOutput:
        ids = self._check_ids(_ids_arg({"ids": ids})[:MAX_IDS])
        res = _table(self.by_id.loc[ids], ["id", "E_CO2_mace", "E_H2O_mace", "S_mace"])
        return ToolOutput(True, f"cheap scores for {len(ids)} MOFs", res, ids=ids)

    def fit_surrogate(self, kappa: float = 1.0, top_k: int = 10) -> ToolOutput:
        if len(self.bought) < 5:
            raise ToolError(f"needs at least 5 bought labels, have {len(self.bought)}")
        kappa = float(kappa)
        top_k = max(1, min(int(top_k), MAX_IDS))
        train = self.df["id"].isin(self.bought).to_numpy()
        y = [self.bought[i]["S"] for i in self.df["id"][train]]
        # the same pre-registered RF as baseline A3 (bench/surrogate.py)
        mean, std = surrogate.fit_predict(self.df[train], y, self.df[~train], seed=RF_SEED)
        cand = self.df.loc[~train, ["id", "formula", "S_mace"]].copy()
        cand["pred_S"], cand["std"], cand["acq"] = mean, std, mean - kappa * std
        cand = cand.sort_values(["acq", "id"]).head(top_k)
        self.last_ranking = cand["id"].tolist()
        res = {"n_train": int(train.sum()), "kappa": kappa, **_table(cand, ["id", "formula", "pred_S", "std", "acq",
                                                                                 "S_mace"])}
        best = cand.iloc[0]
        return ToolOutput(True, f"RF on {int(train.sum())} labels; best {best['id']} pred_S {best['pred_S']:.2f}", res,
                          ids=self.last_ranking)

    def parity_rows(self) -> list[dict]:
        return [{"id": i, "S_mace": _r(self.by_id.at[i, "S_mace"]), "S_dft": self.bought[i]["S"],
                 "hit": self.bought[i]["hit"]} for i in self.order]

    def calibrate_oracle(self, call_id: str) -> ToolOutput:
        rows = self.parity_rows()
        n = len(rows)
        if n < 3:
            raise ToolError(f"needs at least 3 bought labels, have {n}")
        xm = np.array([r["S_mace"] for r in rows], float)
        yd = np.array([r["S_dft"] for r in rows], float)
        hits = np.array([r["hit"] for r in rows], bool)
        rho = spearmanr(xm, yd).statistic
        rho = None if np.isnan(rho) else round(float(rho), 3)
        auc = round(float(roc_auc_score(hits, -xm)), 3) if 0 < hits.sum() < n else None
        png = render.parity_plot(xm.tolist(), yd.tolist(), hits.tolist())
        image = self._save_png(f"calib_{call_id}.png", png)
        res = {"n": n, "n_hits": int(hits.sum()), "spearman": rho, "auc": auc}
        if auc is None:
            res["note"] = "auc undefined: bought set has " + ("no hits" if hits.sum() == 0 else "only hits")
        self.calibrations.append({**res, "image": image, "call_id": call_id})
        return ToolOutput(True, f"n={n} spearman={rho} auc={auc}", res, image=image)

    def render_structure(self, id: str, call_id: str) -> ToolOutput:
        mof = self._check_ids([str(id).strip()])[0]
        raw = self.by_id.at[mof, "co2_best_ads_pos"]
        co2 = json.loads(raw) if isinstance(raw, str) and raw else None
        try:
            png, info = render.render_structure(self.data_dir / "structures", mof, co2)
        except FileNotFoundError as e:
            raise ToolError(f"no structure file for {mof}") from e
        image = self._save_png(f"render_{mof}_{call_id}.png", png)
        res = {"id": mof, **info}
        near = info.get("site_neighbours") or []
        summ = f"rendered {mof}" + (f"; nearest to CO2 C: {near[0]['el']} {near[0]['d_A']} A" if near else "")
        return ToolOutput(True, summ, res, image=image, ids=[mof])

    def request_dft(self, ids, rationale: str = "") -> ToolOutput:
        ids = self._check_ids(_ids_arg({"ids": ids}))
        dup = [i for i in ids if i in self.bought]
        new = [i for i in ids if i not in self.bought]
        if self.spent + len(new) > self.budget:
            raise ToolError(f"refused: {len(new)} new lookups requested, {self.remaining} of {self.budget} remain")
        if not new:
            raise ToolError("every requested id was already bought")
        labels = pd.read_parquet(self.data_dir / "labels.parquet", columns=["id", "E_CO2", "E_H2O", "hit"])
        labels = labels[labels["id"].isin(new)].set_index("id")
        missing = [i for i in new if i not in labels.index]
        if missing:
            raise ToolError(f"no DFT label for {', '.join(missing[:5])}")
        results = []
        for i in new:
            e_co2, e_h2o = float(labels.at[i, "E_CO2"]), float(labels.at[i, "E_H2O"])
            row = {"id": i, "E_CO2": round(e_co2, 3), "E_H2O": round(e_h2o, 3), "S": round(dac_score(e_co2, e_h2o), 3),
                   "hit": bool(labels.at[i, "hit"])}
            self.bought[i] = row
            self.order.append(i)
            results.append(row)
        hits_total = sum(r["hit"] for r in self.bought.values())
        res = {"results": results, "hits_new": sum(r["hit"] for r in results), "hits_total": hits_total,
               "spent": self.spent, "remaining": self.remaining}
        if dup:
            res["already_bought_not_charged"] = dup
        summ = f"bought {len(new)}: {res['hits_new']} hits; {hits_total} hits total, {self.spent}/{self.budget} spent"
        return ToolOutput(True, summ, res, ids=new)

    def _save_png(self, name: str, png: bytes) -> str:
        self.img_dir.mkdir(parents=True, exist_ok=True)
        (self.img_dir / name).write_bytes(png)
        return f"img/{name}"


def score_probe(data_dir: Path, scores: dict[str, float]) -> dict:
    """Post-hoc scoring of the leak probes (L, L2) after every model call is finished."""
    labels = pd.read_parquet(Path(data_dir) / "labels.parquet", columns=["id", "hit"]).set_index("id")
    ids = [i for i in scores if i in labels.index]
    y = labels.loc[ids, "hit"].astype(bool).to_numpy()
    s = np.array([scores[i] for i in ids], float)
    auc = round(float(roc_auc_score(y, s)), 4) if 0 < y.sum() < len(y) else None
    return {"auc": auc, "n": len(ids), "n_hits": int(y.sum()), "hit": {i: bool(h) for i, h in zip(ids, y)}}
