import sys, glob, os, csv, lmdb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from peek_lmdb import load
root, out = sys.argv[1], sys.argv[2]
F = ["split","name","sid","natoms","nco2","nh2o","nads","defective","oms","y_relaxed","y_init","supercell"]
w = csv.writer(open(out, "w")); w.writerow(F)
for split in ["train","val","test_no_targets","ood_big_no_targets","ood_linker_no_targets","ood_topology_no_targets","ood_linker_topology_no_targets"]:
    n=0
    for f in sorted(glob.glob(f"{root}/{split}/*.lmdb")):
        env = lmdb.open(f, subdir=False, readonly=True, lock=False)
        with env.begin() as tx:
            for k, v in tx.cursor():
                d = load(v)
                if not hasattr(d, "keys"): continue
                g = lambda a: getattr(d, a, None)
                sc = g("supercell"); sid = g("sid")
                w.writerow([split, g("name"), int(sid) if sid is not None else "", g("natoms"), g("nco2"), g("nh2o"), g("nads"), g("defective"), g("oms"),
                            g("y_relaxed"), g("y_init"), "x".join(map(str, list(sc))) if sc is not None else ""])
                n+=1
    print(split, n, flush=True)
