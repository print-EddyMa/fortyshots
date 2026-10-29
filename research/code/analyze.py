import pandas as pd, numpy as np, re, sys
df = pd.read_csv(sys.argv[1])
print(df.groupby("split").agg(n=("name","size"), uniq=("name","nunique")))
def mof(n): return n.split("_w_")[0]
def base(n):
    m = mof(n); p = m.rsplit("_",2)
    return p[0] if (len(p)==3 and p[2] in "0123" and re.match(r"^0\.\d+$", p[1] or "")) else m
df["mof"]=df.name.map(mof); df["base"]=df.name.map(base)
df["ads"]=df.name.map(lambda n: n.split("_w_")[1].rsplit("_",1)[0] if "_w_" in n else "")
print(df.ads.value_counts().head(8))
print(df.groupby("split").base.nunique())
tr = df[df.split=="train"]; print("dup names in train:", tr.name.duplicated().sum())
lab = df[df.split.isin(["train","val"])].drop_duplicates("name")
print("labeled unique configs", len(lab))
print(lab.y_relaxed.describe())
# per-MOF (pristine) min CO2 and H2O
p = lab[(lab.defective==False)]
co2 = p[p.ads=="CO2"].groupby("base").y_relaxed.min(); h2o = p[p.ads=="H2O"].groupby("base").y_relaxed.min()
both = pd.concat([co2.rename("co2"), h2o.rename("h2o")], axis=1).dropna()
print("pristine MOFs with CO2:", len(co2), "with H2O:", len(h2o), "both:", len(both))
print(both.describe())
crit = (both.co2 < -0.5) & (both.h2o > both.co2)
print("CO2<-0.5 eV & CO2 stronger than H2O:", crit.sum(), f"({crit.mean():.1%})")
crit2 = (both.co2 < -0.5) & (both.co2 > -1.5) & (both.h2o > both.co2)
print("window [-1.5,-0.5] & selective:", crit2.sum())
vb = set(df[df.split=="val"].base); tb=set(df[df.split=="train"].base)
print("val MOFs", len(vb), "overlap w/ train", len(vb & tb))
for s in ["test_no_targets","ood_big_no_targets","ood_linker_no_targets","ood_topology_no_targets"]:
    sb=set(df[df.split==s].base); print(s, len(sb), "overlap train", len(sb&tb))
both.to_csv(sys.argv[2])
