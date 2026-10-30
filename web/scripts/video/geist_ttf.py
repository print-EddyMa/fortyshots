"""Make static Geist Mono TTFs for ffmpeg (drawtext and libass need TTF/OTF, the site ships WOFF2).

    .venv/bin/python web/scripts/video/geist_ttf.py [--out media/fonts]

Reads the variable `@fontsource-variable/geist-mono` latin WOFF2 from web/node_modules, decodes it with
fontTools (Brotli comes from Node's zlib when the Python `brotli` module is missing), pins the weight axis and
writes GeistMono-Regular.ttf (400) and GeistMono-Medium.ttf (500), plus metrics.json (advance width per em,
used to place two-tone text side by side). The OFL licence is copied next to them.

Needs fontTools (a matplotlib dependency, so it is in the repo's .venv) and `node` on PATH. assemble.sh runs
this when the fonts are missing and falls back to DejaVu Sans Mono / Menlo if it fails.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "web" / "node_modules" / "@fontsource-variable" / "geist-mono"


def _node_brotli() -> types.ModuleType:
    """A stand-in `brotli` module whose decompress() runs Node's built-in Brotli."""
    mod = types.ModuleType("brotli")

    def decompress(data: bytes) -> bytes:
        js = "const z=require('zlib');const c=[];process.stdin.on('data',d=>c.push(d)).on('end',()=>process.stdout.write(z.brotliDecompressSync(Buffer.concat(c))))"
        return subprocess.run(["node", "-e", js], input=data, capture_output=True, check=True).stdout

    mod.decompress = decompress  # type: ignore[attr-defined]
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "media" / "fonts"))
    a = ap.parse_args()
    out = Path(a.out)
    src = PKG / "files" / "geist-mono-latin-wght-normal.woff2"
    if not src.exists():
        print(f"missing {src}; run `npm ci` in web/", file=sys.stderr)
        return 1
    try:
        import brotli  # noqa: F401
    except ImportError:
        sys.modules["brotli"] = _node_brotli()
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    out.mkdir(parents=True, exist_ok=True)
    raw = TTFont(str(src))
    raw.flavor = None
    buf = io.BytesIO()
    raw.save(buf)
    metrics = {}
    for weight, style in ((400, "Regular"), (500, "Medium")):
        f = TTFont(io.BytesIO(buf.getvalue()))
        if "fvar" in f:
            f = instancer.instantiateVariableFont(f, {"wght": weight}, updateFontNames=False)
        name = f["name"]
        for rec in list(name.names):
            if rec.nameID in (2, 17):
                name.setName(style, rec.nameID, rec.platformID, rec.platEncID, rec.langID)
            if rec.nameID in (1, 16):
                name.setName("Geist Mono", rec.nameID, rec.platformID, rec.platEncID, rec.langID)
            if rec.nameID == 4:
                name.setName(f"Geist Mono {style}", rec.nameID, rec.platformID, rec.platEncID, rec.langID)
            if rec.nameID == 6:
                name.setName(f"GeistMono-{style}", rec.nameID, rec.platformID, rec.platEncID, rec.langID)
        f["OS/2"].usWeightClass = weight
        path = out / f"GeistMono-{style}.ttf"
        f.save(str(path))
        upm = f["head"].unitsPerEm
        adv = f["hmtx"]["zero"][0] if "zero" in f["hmtx"].metrics else f["hmtx"]["M"][0]
        metrics[style] = {"file": path.name, "advance_em": round(adv / upm, 4), "ascender_em": round(f["hhea"].ascent / upm, 4)}
        print(f"wrote {path} (advance {adv}/{upm} em)")
    (out / "metrics.json").write_text(json.dumps({"family": "Geist Mono", **metrics}, indent=1))
    lic = PKG / "LICENSE"
    if lic.exists():
        shutil.copyfile(lic, out / "GeistMono-OFL-1.1.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
