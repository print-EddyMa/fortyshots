import sys, pickle, io, lmdb
ALLOW = ("torch", "torch_geometric", "numpy", "collections", "builtins", "copyreg", "_codecs")
BAD = {"eval","exec","system","__import__","getattr","open","compile","input","breakpoint"}
class R(pickle.Unpickler):
    def find_class(self, mod, name):
        if mod.split(".")[0] in ALLOW and name not in BAD:
            return super().find_class(mod, name)
        raise pickle.UnpicklingError(f"blocked {mod}.{name}")
def load(v): return R(io.BytesIO(v)).load()
def summ(x):
    n = x.numel() if hasattr(x, "numel") else (x.size if hasattr(x, "size") and not callable(x.size) else None)
    return x if (n is None or n < 4) else ("shape", tuple(x.shape))
if __name__ == "__main__":
    env = lmdb.open(sys.argv[1], subdir=False, readonly=True, lock=False)
    with env.begin() as tx:
        print("entries", tx.stat()["entries"])
        for i,(k,v) in enumerate(tx.cursor()):
            d = load(v)
            if hasattr(d, "keys"):
                ks = d.keys() if callable(d.keys) else d.keys
                print(k, {kk: summ(getattr(d, kk)) for kk in ks})
            else: print(k, d)
            if i >= 1: break
