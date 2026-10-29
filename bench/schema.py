"""Minimal structural validators for results.json and pools.json (CONTRACTS.md). Return a list of errors.

Keys whose spec is wrapped in Optional(...) may be absent (CONTRACTS.md addenda); every other key is required."""
NUM = (int, float)
OPT_NUM = (int, float, type(None))


def _check(obj, spec, path, errs):
    if isinstance(spec, dict):
        if not isinstance(obj, dict):
            errs.append(f"{path}: expected object")
            return
        for k in spec.keys() - obj.keys():
            if not isinstance(spec[k], Optional):
                errs.append(f"{path}.{k}: missing")
        for k in obj.keys() - spec.keys():
            errs.append(f"{path}.{k}: unexpected key")
        for k in spec.keys() & obj.keys():
            _check(obj[k], spec[k], f"{path}.{k}", errs)
    elif isinstance(spec, list):
        if not isinstance(obj, list):
            errs.append(f"{path}: expected array")
            return
        item, n = spec[0], spec[1] if len(spec) > 1 else None
        if n is not None and len(obj) != n:
            errs.append(f"{path}: expected {n} items, got {len(obj)}")
        for i, x in enumerate(obj):
            _check(x, item, f"{path}[{i}]", errs)
    elif isinstance(spec, Optional):
        _check(obj, spec.inner, path, errs)
    elif isinstance(spec, Nullable):
        if obj is not None:
            _check(obj, spec.inner, path, errs)
    elif isinstance(spec, OneOf):
        if obj not in spec.values:
            errs.append(f"{path}: {obj!r} not in {spec.values}")
    elif spec is bool:
        if not isinstance(obj, bool):
            errs.append(f"{path}: expected bool")
    elif isinstance(obj, bool) or not isinstance(obj, spec):
        errs.append(f"{path}: expected {spec}, got {type(obj).__name__}")


class Nullable:
    def __init__(self, inner):
        self.inner = inner


class Optional:
    """A key that may be absent; when present it must match inner."""

    def __init__(self, inner):
        self.inner = inner


class OneOf:
    def __init__(self, *values):
        self.values = values


PAIR = [NUM, 2]
ARM = {"arm": str, "label": str, "kind": OneOf("baseline", "llm"), "n_runs": int, "hits_mean": NUM,
       "hits_ci": PAIR, "enrichment": OPT_NUM, "lookups_per_hit": OPT_NUM, "first_hit_mean": NUM,
       "curve": [NUM, 41], "curve_lo": [NUM, 41], "curve_hi": [NUM, 41], "harness_share": Optional(OPT_NUM)}
RESULTS = {
    "generated": str, "status": OneOf("mock", "pilot", "final"), "prereg_sha": str, "budget": int,
    "base_rate": NUM,
    "pools": [{"pool": str, "n": int, "hits": int, "mace_coverage": Optional(NUM)}],
    "arms": [ARM],
    "tests": [{"name": str, "diff": NUM, "ci": PAIR, "p": NUM, "ci_note": Optional(str)}],
    "calibration": Nullable({"brier": NUM, "n": int, "bins": [{"p_mean": NUM, "hit_rate": NUM, "n": int}]}),
    "critic": Nullable({"visual_rho_mae": OPT_NUM, "n": int, "n_text": Optional(int)}),
    "honesty": {"mace_auc": OPT_NUM, "mace_auc_ci": Nullable(PAIR), "gas_relax_auc": OPT_NUM,
                "dft_init_auc": OPT_NUM, "mace_mae_dft_geom": OPT_NUM, "eqv2_mae_dft_geom": OPT_NUM, "notes": str},
    "leak": Nullable({"L_auc": OPT_NUM, "L2_auc": OPT_NUM}),
    "claims": Nullable({"n": int, "verified": int, "flagged": int}),
    "ceilings": [{"pool": str, "oracle_hits": int, "dft_init_hits": int, "random_expected": NUM}],
}
POOL_ROW = {"id": str, "S_mace": OPT_NUM, "E_CO2_mace": OPT_NUM, "E_H2O_mace": OPT_NUM, "pld_A": OPT_NUM,
            "lcd_A": OPT_NUM, "oms": Nullable(bool), "metals": Nullable(str), "cost_tier": Nullable(int),
            "hit": bool, "E_CO2": NUM, "E_H2O": NUM}
POOL_NAMES = ["P0", "P1", "P2", "P3", "P4", "P5"]


def validate_results(obj):
    errs = []
    _check(obj, RESULTS, "results", errs)
    return errs


def validate_pools(obj):
    errs = []
    _check(obj, {"pools": {p: [POOL_ROW] for p in POOL_NAMES}}, "pools", errs)
    return errs
