import pandas as pd, numpy as np, re, sys
df = pd.read_csv(sys.argv[1])
def mof(n): return n.split("_w_")[0]
def base(n):
    m = mof(n); p = m.rsplit("_", 2)
    return p[0] if (len(p) == 3 and p[2] in "0123" and re.match(r"^0\.\d+$", p[1] or "")) else m
df["base"] = df.name.map(base); df["ads"] = df.name.map(lambda n: n.split("_w_")[1].rsplit("_", 1)[0].replace("_random", ""))
lab = df[df.split.isin(["train", "val"]) & (df.y_relaxed.abs() < 5)]
for split in ["train", "val"]:
    p = lab[(lab.split == split) & (lab.defective == False)]
    co2 = p[p.ads == "CO2"].groupby("base").y_relaxed.min(); h2o = p[p.ads == "H2O"].groupby("base").y_relaxed.min()
    nat = p.groupby("base").natoms.min()
    b = pd.concat([co2.rename("co2"), h2o.rename("h2o"), nat.rename("natoms")], axis=1).dropna()
    hit = (b.co2 < -0.5) & (b.co2 < b.h2o)
    win = (b.co2 <= -0.5) & (b.co2 >= -1.2) & (b.co2 < b.h2o - 0.1)
    print(split, "MOFs", len(b), "hits(strong & selective)", int(hit.sum()), f"{hit.mean():.1%}", "| window[-1.2,-0.5]&margin0.1", int(win.sum()), f"{win.mean():.1%}",
          "| natoms<=200:", int((b.natoms <= 200).sum()), "hits<=200:", int((hit & (b.natoms <= 200)).sum()))
    b.assign(hit=hit, win=win).to_csv(f"data/pool_{split}.csv")
