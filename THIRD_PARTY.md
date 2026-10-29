# Third-party software

Software that fortyshots depends on, bundles or calls, with its license. Versions are the ones in use on Oct 29, 2026; `uv.lock` and `web/package-lock.json` are authoritative. Data and model licenses are in [DATA_LICENSES.md](DATA_LICENSES.md).

## Bundled into the website (`docs/`)

| Component | Version | License | Use |
|---|---|---|---|
| Geist and Geist Mono fonts (Vercel), via `@fontsource-variable/geist`, `@fontsource-variable/geist-mono` | 5.3.0 | SIL Open Font License 1.1 | Self-hosted fonts |
| 3Dmol.js (`3dmol`) | 2.4.2 | BSD-3-Clause | 3D structure viewer and the CO2 pose morph |
| 3Dmol.js dependencies: `iobuffer`, `netcdfjs`, `upng-js` / `pako` | 5.4.0, 3.0.0, 2.1.0 / 2.2.0 | MIT / MIT and Zlib | Loaded with 3Dmol.js |
| React, React DOM (`scheduler`) | 18.3.1 | MIT | UI |
| Observable Plot (`@observablehq/plot`) and d3 modules | {{PLOT_VERSION}} | ISC | Charts, if `web/package.json` lists it; otherwise delete this row |

Build tools (not shipped): Vite 5.4.21 (MIT), `@vitejs/plugin-react` 4.7.0 (MIT), TypeScript 5.9.3 (Apache-2.0).

## Python package `fortyshots` (runs on the Mac and in CI)

| Package | License |
|---|---|
| httpx | BSD-3-Clause |
| pydantic | MIT |
| numpy | BSD-3-Clause (with 0BSD, MIT, Zlib, CC0-1.0 parts) |
| pandas | BSD-3-Clause |
| pyarrow | Apache-2.0 |
| scikit-learn | BSD-3-Clause |
| scipy | BSD-3-Clause |
| matplotlib | Matplotlib License (PSF-based) |
| FastAPI, Starlette | MIT, BSD-3-Clause |
| uvicorn | BSD-3-Clause |
| sse-starlette | BSD-3-Clause |
| pytest, ruff (development) | MIT, MIT |

## Physics pipeline (container only, not needed to reproduce results)

| Package | License | Use |
|---|---|---|
| mace-torch | MIT | MACE-MP-0 single points and mutant relaxations |
| ASE | LGPL-2.1-or-later | Structure I/O and optimizers (used as a library, unmodified) |
| pymatgen | MIT | Structures, Zeo++ interface, charge checks |
| pyzeo (Zeo++ bindings) | Apache License (per its package metadata); Zeo++ itself carries its own license | Pore descriptors |
| PyTorch | BSD-3-Clause | MACE and EquiformerV2 |
| e3nn | MIT | MACE dependency |
| fairchem-core 1.10 | MIT | EquiformerV2 ODAC cross-check |

## Tools called by `./lab` (installed by the user with Homebrew, not redistributed)

| Tool | License | Use |
|---|---|---|
| llama.cpp (`llama-server`) | MIT | Runs Muse Glimmer 30B locally |
| uv | MIT or Apache-2.0 | Python environment |
| GitHub CLI (`gh`) | MIT | Login for `git push` |
| FFmpeg | LGPL-2.1-or-later (GPL parts in some builds) | Re-encoding video recordings; run as a separate program |
| Homebrew | BSD-2-Clause | Installs the tools above |
| git | GPL-2.0 | Version control |

macOS tools used by `./lab` (`caffeinate`, `sysctl`, `pmset`, `memory_pressure`, `shasum`) are part of the operating system.
