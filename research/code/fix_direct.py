import torch, sys, inspect
from fairchem.core.models.equiformer_v2.equiformer_v2 import EquiformerV2
ck = torch.load(sys.argv[1], map_location="cpu", weights_only=False)
cfg = ck["config"]["model"]
print({k: v for k, v in cfg.items() if k not in ("name",)})
sig = set(inspect.signature(EquiformerV2.__init__).parameters)
bad = [k for k in cfg if k not in sig and k != "name"]
print("unknown:", bad, "task:", ck["config"].get("task", {}).get("dataset"), ck["config"].get("trainer"))
