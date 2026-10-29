"""Run-header attestation: network state, machine, model build, prereg and git SHAs."""
import errno
import hashlib
import json
import os
import platform
import shlex
import socket
import subprocess
from pathlib import Path

NO_ROUTE = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EADDRNOTAVAIL}


def network_state(host: str = "1.1.1.1", timeout: float = 0.3) -> str:
    """'off' when there is no route to the internet, 'on' when 1.1.1.1 answers, else 'unknown'."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 53))  # routing-table lookup only, sends nothing
    except OSError as e:
        return "off" if e.errno in NO_ROUTE else "unknown"
    try:
        with socket.create_connection((host, 443), timeout=timeout):
            return "on"
    except OSError as e:
        return "off" if e.errno in NO_ROUTE else "unknown"


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def machine_info() -> dict:
    chip = None
    if platform.system() == "Darwin":
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        mem = _run(["sysctl", "-n", "hw.memsize"])
        ram = int(mem) / 2 ** 30 if mem else None
        os_name = f"macOS {platform.mac_ver()[0]}"
    else:
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    chip = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
        except (ValueError, OSError):
            ram = None
        os_name = platform.platform()
    return {"chip": chip or platform.machine(), "ram_gb": round(ram, 1) if ram else None, "os": os_name}


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def git_sha() -> str | None:
    return _run(["git", "rev-parse", "HEAD"])


REPO_ROOT = Path(__file__).resolve().parent.parent
PREREG_PATH = REPO_ROOT / "PREREG.md"


def prereg_sha(path: Path | None = None) -> str | None:
    """sha256 of PREREG.md at the repo root (the package's parent), whatever the working directory."""
    path = Path(path) if path else PREREG_PATH
    return sha256_file(path) if path.exists() else None


def model_info(client, mode: str, doctor_report: Path | None = None) -> dict:
    """What the server says about itself, plus the GGUF hash and flags recorded by doctor / ./lab model."""
    info = {"name": None, "gguf": None, "sha256": None, "quant": None, "server_build": None, "flags": []}
    try:
        props = client.get("/props").json()
        path = props.get("model_path") or ""
        info["gguf"] = Path(path).name or None
        info["server_build"] = props.get("build_info") or (props.get("default_generation_settings") or {}).get("build")
        info["name"] = props.get("model_alias") or None
    except Exception:  # noqa: BLE001 - attestation is best effort; the server may not expose /props
        props = {}
    try:
        models = client.get("/v1/models").json().get("data") or []
    except Exception:  # noqa: BLE001
        models = []
    info["name"] = info["name"] or (models[0].get("id") if models else None)
    gguf = info["gguf"] or ""
    for q in ("Q4_K_XL", "Q4_K_M", "Q3_K_XL", "Q4_K_S", "IQ4_XS", "Q5_K_M", "Q8_0", "BF16", "MOCK"):
        if q.lower() in gguf.lower():
            info["quant"] = q
            break
    if doctor_report and Path(doctor_report).exists():
        try:
            rep = json.loads(Path(doctor_report).read_text())
            m = rep.get("model") or {}
            if m.get("gguf") == info["gguf"]:
                info["sha256"] = m.get("sha256")
        except (OSError, json.JSONDecodeError):
            pass
    info["sha256"] = os.environ.get("FORTYSHOTS_GGUF_SHA256") or info["sha256"]
    flags = os.environ.get("FORTYSHOTS_SERVER_FLAGS")
    if flags:
        info["flags"] = shlex.split(flags)
    if mode == "mock" and not info["name"]:
        info["name"] = "mock-glimmer"
    return info
