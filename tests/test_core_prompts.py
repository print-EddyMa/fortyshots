"""Prompts must not tell the agent how good the cheap simulator is, and must never carry refcodes or unbought labels."""
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import FIXTURE_DATA, MockTransport, make_cfg, run

from fortyshots.mockllm import RESULT_BLOCK
from fortyshots.roles import prompts as P
from fortyshots.roles import schemas as S
from fortyshots.tools import TOOL_SPECS

BANNED = re.compile(r"\b(reliab\w*|unreliab\w*|auc|rho|spearman|trust\w*|distrust\w*|noisy|noise|MACE reliability)\b",
                    re.IGNORECASE)
ROLES_DIR = Path(P.__file__).parent


def _static_texts() -> dict[str, str]:
    texts = {f"template {k}": v for k, v in P.TEMPLATES.items()}
    texts["tool specs"] = json.dumps(TOOL_SPECS)
    ids, cids = ["m0000000", "m1111111"], ["c0001"]
    for name, js in [("batch", S.batch_schema(2, ids, cids)), ("review", S.review_schema(ids, cids)),
                     ("steps", S.steps_schema(list(TOOL_SPECS)[:6], 4)), ("scores", S.scores_schema(ids)),
                     ("mutations", S.mutations_schema({i: {"sites": 4, "swaps": {"Zn": ["Mg", "Co", "Ni"]}} for i in ids}))]:
        texts[f"schema {name}"] = json.dumps(js)
    for f in ROLES_DIR.glob("*.py"):
        texts[f"source {f.name}"] = f.read_text().split('"""', 2)[-1] if f.name == "prompts.py" else f.read_text()
    return texts


def test_templates_and_schemas_have_no_banned_words():
    hits = {k: BANNED.findall(v) for k, v in _static_texts().items()}
    assert {k: v for k, v in hits.items() if v} == {}


def test_banned_regex_catches_examples():
    for bad in ["MACE is unreliable", "do not trust the simulator", "AUC is 0.49", "noisy oracle", "rho ~ 0",
                "Spearman expectations", "its reliability is low"]:
        assert BANNED.search(bad), bad


def _prompt_texts(requests: list[dict]) -> list[str]:
    """Text the lab itself wrote: system and user messages, with tool results (model-visible data) removed."""
    out = []
    for req in requests:
        for m in req["messages"]:
            if m["role"] not in ("system", "user"):
                continue
            c = m["content"]
            text = "\n".join(p.get("text", "") for p in c if p.get("type") == "text") if isinstance(c, list) else c
            out.append(RESULT_BLOCK.sub("", text))
    return out


def test_live_prompts_of_a_campaign_are_clean(tmp_path):
    t = MockTransport()
    run(make_cfg(tmp_path, "A5"), transport=t)
    t2 = MockTransport()
    run(make_cfg(tmp_path, "A5", seed=1, action_mode="schema", vision=False), run_id="mock-schema", transport=t2)
    texts = _prompt_texts(t.requests + t2.requests)
    assert texts and all(not BANNED.search(x) for x in texts), [BANNED.findall(x) for x in texts if BANNED.search(x)]
    for req in t.requests + t2.requests:
        assert "temperature" in req and "chat_template_kwargs" in req


def test_no_refcode_or_unbought_label_reaches_the_model(tmp_path):
    t = MockTransport()
    summary, _ = run(make_cfg(tmp_path, "A5"), transport=t)
    blob = json.dumps(t.requests)
    refs = set(pd.read_parquet(FIXTURE_DATA / "id_map.parquet")["refcode"])
    assert not [r for r in refs if re.search(rf"\b{r}\b", blob)]
    labels = pd.read_parquet(FIXTURE_DATA / "labels.parquet").set_index("id")
    bought = set(summary["picks"])
    for col in ("E_CO2_init", "E_H2O_init"):
        assert col not in blob
    unbought = labels.drop(index=list(bought))
    for i, row in unbought.head(60).iterrows():
        for v in (row["E_CO2"], row["E_H2O"]):
            assert f"{v:.3f}" not in blob or _appears_elsewhere(f"{v:.3f}", blob, i)


def _appears_elsewhere(token: str, blob: str, mof_id: str) -> bool:
    """A 3-decimal value can coincide with some other number; it must never sit next to the unbought id."""
    return not re.search(rf"{mof_id}[^\]]{{0,80}}{re.escape(token)}", blob)


def test_leak_probe_logs_no_refcode(tmp_path):
    refs = pd.read_parquet(FIXTURE_DATA / "id_map.parquet")["refcode"].tolist()

    def hook(req, resp):  # the model echoes refcodes in its reasoning; the ledger must not
        resp["choices"][0]["message"]["reasoning_content"] += " I recall " + " and ".join(refs[:5]) + "."
        return resp
    t = MockTransport(hook=hook)
    summary, camp = run(make_cfg(tmp_path, "L"), run_id="mock-L", transport=t)
    assert any(r in json.dumps(t.requests) for r in refs)  # the probe does show refcodes to the model
    on_disk = "".join(p.read_text() for p in camp.run_dir.rglob("*") if p.is_file() and p.suffix != ".png")
    assert not [r for r in refs if re.search(rf"\b{r}\b", on_disk)]
    assert (camp.run_dir / "cassette.jsonl").exists()  # stored with hashed ids in place of refcodes
    assert summary["leak"]["n"] == 120 and summary["leak"]["auc"] is not None


def test_leak_probe_resumes_from_its_scrubbed_cassette(tmp_path):
    ref, _ = run(make_cfg(tmp_path / "a", "L"), run_id="mock-L-P0-s0-202610300009")
    count = {"n": 0}

    def hook(req, resp):
        count["n"] += 1
        if count["n"] == 2:
            raise RuntimeError("simulated kill")
        return resp
    cfg = make_cfg(tmp_path / "b", "L")
    with pytest.raises(RuntimeError):
        run(cfg, run_id="mock-L-P0-s0-202610300009", transport=MockTransport(hook=hook))
    t = MockTransport()
    again, camp = run(cfg, run_id="mock-L-P0-s0-202610300009", transport=t)
    assert camp.resumed and len(t.requests) == 2  # chunk 1 replayed from the cassette (refcodes restored)
    assert {k: v for k, v in again.items() if k != "duration_s"} == {k: v for k, v in ref.items()
                                                                      if k != "duration_s"}
