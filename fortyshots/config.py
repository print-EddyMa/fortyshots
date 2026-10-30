"""Constants shared by the agent core."""
import os
from pathlib import Path

BUDGET = 40
ROUNDS = 4
BATCH = 10
HIT_THRESHOLD = -0.5
PENALTY_MARGIN = 0.1
MAX_DIRECTOR_CALLS = 3  # per round; each call costs ~1-2 min of generation on a base M4
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MAX_MINUTES = 120.0  # a campaign on a base M4 takes ~45-60 min (prefill ~34 tok/s, decode ~6.8 tok/s)
GOAL = "Find MOFs that bind CO2 strongly in humid air (CO2 must out-bind water)."
ARMS = ("A4", "A5", "A6", "L", "L2")
MODES = ("recorded", "live", "mock")
REPORTER_MODEL_LABEL = "Muse Glimmer 30B"


def data_dir(override: str | os.PathLike | None = None) -> Path:
    return Path(override or os.environ.get("FORTYSHOTS_DATA", "data"))


def dac_score(e_co2: float, e_h2o: float) -> float:
    return e_co2 + max(0.0, e_co2 - e_h2o + PENALTY_MARGIN)
