# Demo video: storyboard, narration and recording checklist

Length: 3:00 or less (about 390 words of narration, roughly 2:45 spoken). The judges score the video first, so it carries the project. It tells one story: a real material, the problem, the open model on a laptop, what was built this week, the agent at work, the honest result, and where it goes next.

## Rules for every line

- Numbers come from `results/results.json` only. Fill each `{{...}}` before recording the narration (`python -m bench.fill_docs --write`).
- Say "picked {{H}} of the hidden hits", never "found new sorbents" or "discovered". The candidates are known MOFs and the labels already existed.
- Name both baselines whenever the agent's number is said: random and the strongest baseline (`{{WIN_TIE_LOSS_SENTENCE}}` also names the random forest).
- Say "Wi-Fi off" only about a run whose header says `network: off` (visible in the console's Model tab). The benchmark nights may have had Wi-Fi on; their headers say so.
- Show what the selected run actually shows. The featured run is fixed by rule (PREREG §9: the A5 run on P1 with the median hits@40). If it did not change strategy after checking the simulator, read variant B at 1:40.
- Any sped-up segment carries a visible speed label (for example "8× speed").
- The hackathon rules require that judges can tell what was built during the event: the 0:42 card and the closing lower third say it, with the commit count from git.
- No code editor on screen. No notifications, personal tabs or bookmarks.

## Storyboard and narration

| Time | Picture | Narration (your voice) |
|---|---|---|
| 0:00–0:10 | Hook. The landing hero: a real DFT hit from ODAC23 as a 2×2×2 supercell, slowly orbiting inside the mesh of its pores; CO2 settles into its DFT-relaxed pose; small mono caption with id, formula, E_CO2 and E_H2O | "This is a metal-organic framework — a crystal sponge with pores a few atoms wide. The right one could pull CO2 straight out of the air." |
| 0:10–0:24 | The problem. Results page, the Why this is hard scatter of 3,000 MOFs (water vs CO2, the 85% callout); cut to the atlas of all 3,000 MOFs on the landing page | "The catch is humidity. In 85% of these three thousand candidates, water grabs the binding site first. Checking just one with quantum chemistry takes hours of supercomputer time. So the real question is: which forty do you check?" |
| 0:24–0:42 | What it runs on. live.mov from the Mac: Wi-Fi turned off, `./lab live`, console Model tab with muse-glimmer-30b, UD-Q3_K_XL + DFlash, tok/s, `network: off`. B-roll fallback: the Model tab | "fortyshots is an AI scientist that answers that question on a laptop. Meta's open-weights Muse Glimmer 30B, running locally — no API key, no cloud, Wi-Fi off. Meta's DFlash drafter more than doubles its speed." |
| 0:42–0:52 | Built this week. Card styled like the site: left column Built Oct 27 – Nov 4 (the four-role agent and its 7 tools, the lab runtime for a laptop, the pre-registered benchmark and verifier, this site and console, the materials proposer); right column Used (Muse Glimmer 30B + DFlash drafter, Meta; ODAC23 DFT data + EquiformerV2, Meta FAIR; MACE-MP-0; llama.cpp); bottom: commits since hackathon-start, from git | "Everything here was built this week: the agent, the lab runtime, the benchmark and this site." |
| 0:52–1:40 | Watch it work, at 1× then labelled 8×: the Director's parallel tool calls; the Proposer's picks with P(hit); the Critic opens a render and its parity plot and vetoes a pick; a key press approves the batch; sealed cells reveal and the binding-site close-up shows CO2 settling with Å labels; cut to the atlas, where the run's path grows round by round and hits flash | "One model plays four roles. The Director explores the pool with tools. The Proposer commits to ten picks — each with a probability it will hit, scored later. The Critic looks at the structures and its own plots, through Glimmer's vision, and vetoes weak picks. I approve, and the hidden DFT results from Meta FAIR's ODAC23 are revealed — here, CO2 settling into its pore. On the map of chemical space, you can watch where it searches." |
| 1:40–1:58 | Self-check. `calibrate_oracle`: the parity plot and the trace line that follows | Variant A (the run changed course): "It also checks its own cheap simulator against what it has paid for. Here the simulator's ranking was weak, so it shifted to {{WHAT_IT_USED}}." Variant B (it did not): "It also checks its own cheap simulator against what it has paid for. Here it kept trusting the simulator's strongest picks, and the data backed it." |
| 1:58–2:28 | The honest result. Results page: the arms chart with intervals and both baselines labelled, the discovery curves, the reliability plot, the leak probe line, the `prereg-v1` hash | "Does it work? We pre-registered the test and scored it blind. With forty tests, the agent found 1.4 good materials. Random finds 1.1; the best simple method, 2.2. It doesn't beat that yet, but its confidence is well calibrated, and nine in ten of its claims check out." |
| 2:28–2:36 | Claim check. The claim-check panel: claims, verified, flagged | "Every number it writes is checked against the computation it came from." |
| 2:36–2:48 | The frontier. The approval queue with a proposed mutant relaxing in 3D, labelled unvalidated · ML only; approve. Without mutants in the run: the queue and the structure of the open batch | "Then it proposes new materials — modified frameworks, built and relaxed by simulation, ranked by what it learned. Unvalidated, so a person decides what gets tested next." |
| 2:48–3:00 | Close. Terminal-style card: `git clone github.com/print-EddyMa/fortyshots && ./lab verify` typed, then the repo and site URLs; lower third "Built Oct 27 – Nov 4, 2026 · {{N_COMMITS}} commits since hackathon-start" | "The same loop works for batteries and catalysts. The AI decides what to test; physics decides what's true. Check every number yourself." |

About 390 words of narration (one variant at 1:40). At a calm 140 words per minute that is about 2:45 of speech, so keep the pauses short; `captions.py` refuses a cut that runs past 3:00.

## Rubric coverage

| Rubric item | Where in the video |
|---|---|
| Impact (30%) | 0:00–0:24: a real sorbent, humidity as the reason direct air capture fails, 85% of 3,000 candidates lose to water, DFT is the expensive step. 2:36–3:00: proposing new frameworks, and the same loop for electrolytes and catalysts. |
| Use of Meta AI tooling (25%) | 0:24–0:42: Muse Glimmer 30B open weights on the laptop, Wi-Fi off, Meta's DFlash drafter. 0:52–1:40: one model in four roles, parallel tool calls, structured output (the Proposer's P(hit) schema), Glimmer's vision on renders and plots, Meta FAIR's ODAC23 DFT labels. 0:42 card: EquiformerV2 (Meta FAIR) in the physics checks. |
| Technical depth (20%) | 1:40–2:36: the simulator self-check, pre-registration, blind scoring on five pools, intervals and both baselines, Brier score, leak probe, claim checker. 0:52–1:40: binding-site distances from the DFT-relaxed geometry; the t-SNE atlas built from label-free descriptors. |
| Demo (25%) | 0:52–1:40: the console replaying a real ledger, a live approval, the DFT reveal; 0:24–0:42: the live run on the Mac. |
| Built during the hackathon | 0:42–0:52: the "Built this week" card with the commit count from git; 2:48–3:00: the lower third "Built Oct 27 – Nov 4, 2026 · N commits since hackathon-start". |
| Bonus: Best Agentic | 0:52–1:58: Director, Proposer, Critic and Reporter with tools, self-calibration and a human gate. |
| Bonus: Open-Weights, own hardware | 0:24–0:42: a 30B open-weights model on a 24 GB MacBook, no API key, Wi-Fi off. |
| Bonus: Most Viral | The 40-second vertical clip in LAUNCH.md: the hook orbit, the 85% scatter, the 8× loop with the CO2 reveal, the atlas path, the result. |

## Recording checklist (Tue Nov 3, about 1 hour)

1. [ ] `./lab update`. Mac on the charger, Low Power Mode off, Do Not Disturb on, only Terminal, the browser and QuickTime open.
2. [ ] `./lab doctor --rehearse`; it ends with "Memory held up" (or tells you to use `./lab live small`).
3. [ ] **live.mov, about 60 s, one take.** Cmd-Shift-5 > Record Entire Screen. Turn Wi-Fi off from the menu bar; `./lab live`, then `./lab live run` in a second tab (a demo run on P0, captioned as such); open the Model tab for 3 s; when asked, press `a` to approve the first batch and wait for the reveal. Stop (Cmd-Ctrl-Esc), save as `live.mov` on the Desktop, note the seconds of Wi-Fi off, the Model tab and your `a`. `./lab stop`; Wi-Fi back on.
4. [ ] **voice.m4a.** QuickTime > File > New Audio Recording, quiet room. Read the narration column straight through (one variant at 1:40), two takes; keep the better one as `voice.m4a` on the Desktop.
5. [ ] `./lab push`, then `./lab push-media ~/Desktop/voice.m4a ~/Desktop/live.mov` (both end with a line starting `pushed`).

Claude then cuts the video from your two files plus browser B-roll (captions, speed labels, lower third) and sends it back for review. You upload the final file as unlisted to a video account you already have and paste the link into SUBMISSION.md.

**Upload title:** `fortyshots — an AI scientist that shows its work` · **Description, first line:** `The AI decides what to test. Physics decides what's true. Meta's Muse Glimmer 30B, Meta Global AI Developer Hackathon 2026.` Then the site and repo links.

## How to assemble (in the container)

The tools are in `web/scripts/video/`; everything they write goes to `media/` (gitignored).

```bash
# 1. The recordings: copy media/voice.m4a and media/live.mov (or live.mp4) from origin/media
web/scripts/video/fetch-media.sh

# 2. The site with the real runs, then the browser B-roll of the showcase run
#    (runs/index.json `showcase`, else the first A5 run on P1): 18 shots, each 1920x1080 and 1080x1920
(cd web && npm run build)
node web/scripts/video/record-broll.mjs             # -> media/broll/*.webm + media/broll/shots.json
#    one shot again: node web/scripts/video/record-broll.mjs --only calibrate-oracle

# 3. Numbers. Placeholders fill from results/results.json and `git rev-list --count hackathon-start..HEAD`;
#    sentence placeholders go in media/values.json, e.g.
#    {"VS_RANDOM": "finds twice as many", "LEAK_SENTENCE": "no sign of recall", "WHAT_IT_USED": "the random forest"}
python3 web/scripts/video/captions.py --voice media/voice.m4a --variant A   # check: prints row timing, lists anything unfilled

# 4. Cut. live cuts are in-out seconds of live.mov (Wi-Fi off, ./lab live, the Model tab); if they add up to more
#    than the 16 s row they are sped up and labelled "N× speed".
web/scripts/video/assemble.sh --variant A --live-cuts "3-8,40-46,95-101"
#    -> media/fortyshots-demo.mp4 (1920x1080, H.264 + AAC, fails above 3:00) + media/fortyshots-demo.srt
#    -> media/fortyshots-x-clip.mp4 (1080x1920, 40 s, captions burned in)

# 5. Social card
(cd web && npm run og)                              # -> web/public/og.png and docs/og.png (1200x630)
```

- Draft without recordings (B-roll only, silent, estimated caption timing, numbers from the showcase run): `web/scripts/video/assemble.sh --draft` writes `media/draft-demo.mp4` and `media/draft-x-clip.mp4`.
- Timing: picture rows start at the storyboard times. `captions.py` finds the speech in `voice.m4a` with ffmpeg `silencedetect`, aligns the sentences to it, and places each row's narration 0.5 s into its row; a row whose narration runs long pushes the next row back rather than talking over it. Inspect or hand-edit `media/timeline.json`, then `assemble.sh --timeline media/timeline.json`.
- Footage per row is picked from the picture column; `media/cuts.json` overrides a row, e.g. `{"3": [{"live": true, "weight": 1}]}` or `{"x:2": [{"shot": "console-8x", "at": "play", "weight": 1}]}`.
- Other switches: `--no-captions` (main video without burned captions), `--no-x`, `--only-x`, `--preset veryfast`. On a Mac: `brew install ffmpeg-full` if `ffmpeg` lacks drawtext or libass.
