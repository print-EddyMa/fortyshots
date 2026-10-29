from fortyshots.claimcheck import check, sources_from_events

SOURCES = {
    "c0014": {"summary": "bought 3: 2 hits; 2 hits total, 10/40 spent", "result": {
        "results": [{"id": "m1a2b3c4", "E_CO2": -1.203, "E_H2O": -0.412, "S": -1.203, "hit": True},
                    {"id": "m5d6e7f8", "E_CO2": -0.355, "E_H2O": -0.61, "S": 0.0, "hit": False},
                    {"id": "m9a8b7c6", "E_CO2": -0.734, "E_H2O": -0.533, "S": -0.734, "hit": True}],
        "hits_new": 2, "hits_total": 2, "spent": 10, "remaining": 30}},
    "c0029": {"summary": "bought 1: 0 hits; 2 hits total, 20/40 spent", "result": {
        "results": [{"id": "mabcdef1", "E_CO2": -0.912, "E_H2O": -1.1, "S": -0.624, "hit": False}],
        "hits_new": 0, "hits_total": 2, "spent": 20, "remaining": 20}},
    "c0049": {"summary": "n=30 spearman=0.242 auc=0.769", "result": {"n": 30, "n_hits": 4, "spearman": 0.242,
                                                                      "auc": 0.769}},
    "c0051": {"summary": "RF on 30 labels; best m0f0f0f0 pred_S -0.44", "result": {
        "n_train": 30, "kappa": 1.0, "columns": ["id", "formula", "pred_S", "std", "acq", "S_mace"],
        "rows": [["m0f0f0f0", "Zn4C12H8O13", -0.441, 0.213, -0.654, -0.31],
                 ["m1e1e1e1", "Cu2C8H4O8", -0.402, 0.188, -0.59, 0.12]]}},
}

CORRECT = [
    "The first order bought 3 MOFs and found 2 hits [c0014].",
    "The best binder was m1a2b3c4 with E_CO2 of -1.203 eV [c0014].",
    "Its water binding was -0.412 eV [c0014].",
    "m9a8b7c6 also qualified, binding CO2 at -0.73 eV [c0014].",
    "m5d6e7f8 had a DAC score of 0.0 eV [c0014].",
    "After the second order 20 lookups were spent and 20 remained [c0029].",
    "mabcdef1 bound water more strongly (-1.1 eV) than CO2 (-0.912 eV) [c0029].",
    "Its score was -0.624 eV [c0029].",
    "The cheap score had spearman 0.242 over 30 MOFs [c0049].",
    "The hit-ranking area was 0.77 [c0049].",
    "That is 76.9% [c0049].",
    "There were 4 hits among the 30 calibrated MOFs [c0049].",
    "The surrogate was trained on 30 labels with kappa 1.0 [c0051].",
    "It ranks m0f0f0f0 first with predicted S -0.441 eV [c0051].",
    "Its spread was 0.213 eV [c0051].",
    "m1e1e1e1 follows at -0.40 eV [c0051].",
    "Both orders together found 2 hits [c0014, c0029].",
    "The second order added 0 hits [c0029].",
    "Binding of 1.2 eV for m1a2b3c4 clears the -0.5 eV hit threshold [c0014].",
    "The acquisition value of m0f0f0f0 was -0.654 [c0051].",
]

WRONG = [
    "The first order bought 3 MOFs and found 5 hits [c0014].",
    "The best binder was m1a2b3c4 with E_CO2 of -1.62 eV [c0014].",
    "Its water binding was -0.215 eV [c0014].",
    "m9a8b7c6 also qualified, binding CO2 at -0.91 eV [c0014].",
    "m5d6e7f8 had a DAC score of 0.8 eV [c0014].",
    "After the second order 25 lookups were spent and 20 remained [c0029].",
    "mabcdef1 bound water more strongly (-1.4 eV) than CO2 (-0.912 eV) [c0029].",
    "Its score was -0.287 eV [c0029].",
    "The cheap score had spearman 0.524 over 30 MOFs [c0049].",
    "The hit-ranking area was 0.91 [c0049].",
    "That is 67.9% [c0049].",
    "There were 7 hits among the 30 calibrated MOFs [c0049].",
    "The surrogate was trained on 36 labels with kappa 1.0 [c0051].",
    "It ranks m0f0f0f0 first with predicted S -0.517 eV [c0051].",
    "Its spread was 0.0927 eV [c0051].",
    "m1e1e1e1 follows at -0.33 eV [c0051].",
    "Both orders together found 9 hits [c0014, c0029].",
    "The second order added 3 hits [c0029].",
    "The best binder was m7777777 [c0014].",
    "m0f0f0f0 had E_CO2 of -1.203 eV [c0014].",
]


def test_passes_all_correct_claims():
    for s in CORRECT:
        r = check(s, SOURCES)
        assert r["n_claims"] >= 1 and r["flagged"] == [], s


def test_flags_every_planted_wrong_claim():
    flagged = [s for s in WRONG if check(s, SOURCES)["flagged"]]
    assert len(flagged) == len(WRONG) == 20


def test_whole_report_counts_and_reasons():
    r = check(" ".join(CORRECT + WRONG[:3]), SOURCES)
    assert r["verified"] == r["n_claims"] - len(r["flagged"]) and len(r["flagged"]) == 3
    assert check("We found 2 hits.", SOURCES)["flagged"][0]["reason"] == "no citation"
    assert "unknown" in check("We found 2 hits [c9999].", SOURCES)["flagged"][0]["reason"]
    assert check("Round 3 used all 40 lookups [c0014].", SOURCES, (40,))["flagged"] == []


def test_sources_from_events_uses_only_successful_tool_results():
    evs = [{"kind": "tool_result", "call_id": "c0001", "payload": {"ok": True, "summary": "s", "result": {"x": 1}}},
           {"kind": "tool_result", "call_id": "c0002", "payload": {"ok": False, "summary": "e", "result": {}}},
           {"kind": "message", "call_id": "c0003", "payload": {"text": "t"}}]
    assert set(sources_from_events(evs)) == {"c0001"}
