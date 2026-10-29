"""Prompt templates. Layout: one shared system prefix (role map, tools, pool summary, campaign state) for every
role, with the role instruction as the last user message so llama.cpp reuses the cached prefix across roles.

Never describe how good the cheap simulator is: the agent has to find out from the labels it buys.
"""
import pandas as pd

from ..tools import TOOL_SPECS

PREFIX = """You are one role in fortyshots, an autonomous materials-discovery lab. Every role (Director, Proposer, \
Critic, Reporter) is played by the same model; the lab's code runs the tools and enforces the budget.

GOAL: {goal}
A MOF is a hit when DFT gives E_CO2 < -0.5 eV and CO2 binds more strongly than water (E_CO2 < E_H2O).
DAC score S = E_CO2 + max(0, E_CO2 - E_H2O + 0.1) eV; lower S is better.
DFT lookups are the expensive experiment: {budget} in total, bought in {rounds} round(s) of {batch}. \
A cheap simulator value (E_CO2_mace, E_H2O_mace, S_mace, same units and sign convention) is free for every MOF.
MOF ids look like m3fa9c2e. Every tool result starts with its call id, like [c0012]; cite call ids as evidence.

TOOLS
{tools}

POOL
{pool}

CAMPAIGN STATE
{state}"""

DIRECTOR = """ROLE: Director, round {round} of {rounds}.
Explore the pool with tool calls so the Proposer can commit the next {batch} DFT lookups. You may call several tools \
at once, at most {max_calls} calls this round.{calibration}
When you have enough evidence, stop calling tools and write 2 to 4 sentences for the Proposer: what you learned and \
which candidates look best, citing call ids."""

DIRECTOR_CALIBRATION = """
From round 2 on you must call calibrate_oracle every round, compare the cheap simulator with the DFT labels bought so \
far, and decide how much weight each source of evidence deserves when ranking candidates."""

DIRECTOR_ZERO_SHOT = """ROLE: Director, single-shot campaign.
There is no feedback in this campaign: after your exploration the Proposer orders all {batch} DFT lookups at once. \
Explore the pool with tool calls, at most {max_calls} calls; you may call several tools at once.
When you have enough evidence, stop calling tools and write 2 to 4 sentences for the Proposer: what you learned and \
which candidates look best, citing call ids."""

DIRECTOR_SCHEMA = """
Reply with JSON only: "steps" lists the tool calls to run now (tool name plus args), "done" is true when you are \
finished exploring, and "note" holds your sentences for the Proposer once done."""

PROPOSER = """ROLE: Proposer, round {round} of {rounds}.
Commit exactly {n} MOFs to buy with DFT now. Choose unbought ids that appear in tool results. For each pick give \
p_hit, your probability from 0 to 1 that DFT will call it a hit; p_hit is scored against the outcome, so state what \
you actually believe. Add a one-sentence rationale and the call ids of the evidence. Reply with JSON only."""

PROPOSER_SHORT = """
Keep each rationale to 20 words or fewer."""

PROPOSER_RETRY = """That reply was not a valid batch: {error}
Reply again with JSON only: exactly {n} picks, unique unbought ids from the allowed list."""

REPLACE = """ROLE: Proposer, revision.
The Critic vetoed {k} pick(s):
{vetoes}
Replace exactly {k} pick(s) with other unbought ids not already in the batch, in the same format. Reply with JSON \
only."""

CRITIC = """ROLE: Critic, round {round} of {rounds}.
The Proposer wants to buy these {n} MOFs (id, p_hit, rationale, evidence):
{batch}
{evidence}
1. If a parity plot of the cheap score against the DFT score is provided, read it and estimate the rank correlation \
between the two axes, from -1 to 1. Use null when there is no plot.
2. Veto at most 3 picks that the evidence does not support, each with a reason and the call ids it rests on. Veto \
nothing if the batch is sound.
Reply with JSON only."""

CRITIC_IMAGES = """Image 1 is the parity plot for every MOF bought so far: cheap score S_mace on x, DFT score S on y, \
hits in orange.{render}"""
CRITIC_RENDER = """ Image {k} shows the top pick {id} with CO2 at its best cheap-simulator placement."""
CRITIC_TEXT = """No images are available, so the plot data follows as text.
Parity data (id, S_mace, DFT S, hit):
{table}{render}"""
CRITIC_NO_PARITY = """No DFT labels have been bought yet, so there is no parity plot.{render}"""

REPORTER = """ROLE: Reporter.
The campaign is over. Write the campaign report for a materials scientist: 150 to 300 words of plain prose, no \
headings or tables. Say what was bought, how many hits were found, what the DFT labels showed about the cheap \
simulator, and which MOFs to test next. Every sentence that contains a number or a MOF id must cite the call id of \
the tool result it comes from, like [c0012], and copy the number exactly as it appears there. Do not invent numbers.

EVIDENCE
{evidence}"""

CHEMIST = """ROLE: Chemist.
Suggest at most 3 new candidate structures derived from the parents below, each either functionalize (add NH2, OH, \
F or CH3 at an aromatic C-H site) or metal_swap (charge-preserving: Mg, Zn, Co, Ni interchange; Al, Fe, Sc \
interchange). Give a one-sentence rationale each. These will be checked by simulation only. Reply with JSON only.
PARENTS (id, formula, metals, DFT S, hit)
{parents}"""

PROBE_SYSTEM = "You are a careful chemist. Answer from what you already know."

PROBE_REFCODES = """Below are {n} metal-organic frameworks named by their CSD refcodes. For each one give an integer \
from 0 to 10: how likely is it that DFT gives a CO2 adsorption energy below -0.5 eV with CO2 binding more strongly \
than water? Score every refcode. Reply with JSON only.
REFCODES
{items}"""

PROBE_DESCRIPTORS = """Below are {n} metal-organic frameworks given only by composition and pore descriptors (id, \
formula, metals, open metal sites, PLD A, LCD A, density g/cm3, void fraction, surface area m2/g, metal cost tier). \
For each one give an integer from 0 to 10: how likely is it that DFT gives a CO2 adsorption energy below -0.5 eV with \
CO2 binding more strongly than water? Score every id. Reply with JSON only.
MOFS
{items}"""

TEMPLATES = {k: v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, str)}


def tools_block(names: list[str]) -> str:
    lines = []
    for n in names:
        props = TOOL_SPECS[n]["parameters"].get("properties", {})
        lines.append(f"- {n}({', '.join(props)}): {TOOL_SPECS[n]['description']}")
    return "\n".join(lines)


def _q(s: pd.Series, nd: int = 2) -> str:
    q = s.quantile([0, 0.1, 0.5, 0.9, 1]).round(nd).tolist()
    return " / ".join(f"{v:g}" for v in q)


def pool_block(df: pd.DataFrame) -> str:
    metals = df["metals"].fillna("").str.split(",").explode().str.strip()
    counts = metals[metals != ""].value_counts()
    top = ", ".join(f"{m} {c}" for m, c in counts.head(14).items())
    tiers = df["metal_cost_tier"].value_counts().sort_index()
    return "\n".join([
        f"{len(df)} MOFs. Metals (MOFs containing): {top}{', ...' if len(counts) > 14 else ''}",
        f"open metal sites: {int(df['oms'].sum())} of {len(df)}; metal cost tier 1/2/3: "
        + "/".join(str(int(tiers.get(t, 0))) for t in (1, 2, 3)),
        "quantiles min / 10% / median / 90% / max:",
        f"  pld_A {_q(df['pld_A'])}; lcd_A {_q(df['lcd_A'])}; density_gcm3 {_q(df['density_gcm3'])}",
        f"  S_mace {_q(df['S_mace'])}; E_CO2_mace {_q(df['E_CO2_mace'])}",
    ])


def state_block(round_: int, rounds: int, budget: int, bought: list[dict], history: list[dict]) -> str:
    """Compact campaign state: everything bought so far plus one line per finished round."""
    spent = len(bought)
    hits = sum(b["hit"] for b in bought)
    lines = [f"round {round_} of {rounds}; DFT lookups spent {spent} of {budget}; hits found {hits}"]
    if bought:
        lines.append("DFT labels bought so far (id, S_mace, E_CO2, E_H2O, S, hit):")
        lines += [f"{b['id']} {b['S_mace']:.3f} {b['E_CO2']:.3f} {b['E_H2O']:.3f} {b['S']:.3f} "
                  f"{'HIT' if b['hit'] else '-'}" for b in bought]
    else:
        lines.append("No DFT labels bought yet.")
    for h in history:
        p = [x for x in h["p_hits"] if x is not None]
        mean_p = f"{sum(p) / len(p):.2f}" if p else "n/a"
        veto = f"; critic vetoed {', '.join(h['vetoed'])}" if h.get("vetoed") else ""
        lines.append(f"round {h['round']}: bought {len(h['ids'])}, mean p_hit {mean_p}, hits {h['hits']}{veto}")
    return "\n".join(lines)


def system_prompt(*, goal: str, budget: int, rounds: int, batch: int, tools: list[str], pool: str, state: str) -> str:
    return PREFIX.format(goal=goal, budget=budget, rounds=rounds, batch=batch, tools=tools_block(tools), pool=pool,
                         state=state)
