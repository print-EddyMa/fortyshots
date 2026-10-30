# Demo video: storyboard, narration and recording checklist

Length: 3:00 or less. The judges score the video first, so it carries the project.

## Rules for every line

- Numbers come from `results/results.json` only. Fill each `{{...}}` before recording the narration.
- Say "picked {{H}} of the hidden hits", never "found new sorbents" or "discovered". The candidates are known MOFs and the labels already existed.
- Name both baselines whenever the agent's number is said: random and the random forest.
- Say "Wi-Fi off" only about a run whose header says `network: off` (visible in the console's Model tab). The benchmark nights may have had Wi-Fi on; their headers say so.
- Show what the selected run actually shows. The featured run is fixed by rule (PREREG §9: the A5 run on P1 with the median hits@40). If it did not change strategy after checking the simulator, use the second narration variant at 1:15.
- Any sped-up segment carries a visible speed label (for example "8x").
- No code editor on screen. No notifications, personal tabs or bookmarks.

## Storyboard and narration

| Time | Picture | Narration (your voice) |
|---|---|---|
| 0:00–0:08 | Black frame, two-tone mono text: "40 DFT lookups. {{H}} of the hidden hits." / "Random: {{RANDOM_HITS}}. Best baseline: {{A2_HITS}}." | "One open Meta model, running on my laptop with no API key, picked {{H}} of the hidden humid-air CO2 binders in forty lookups. The best simple baseline, greedy on the cheap simulator, picked {{A2_HITS}}. Random picks {{RANDOM_HITS}}." |
| 0:08–0:22 | Results page: CO2 vs water binding scatter of the pool, the 85 hits out of 3,026 highlighted | "Sorbents for direct air capture fail because water takes the binding site. Checking each candidate with DFT is expensive, so the real question is which forty to check." |
| 0:22–0:38 | Mac: Wi-Fi turned off in the menu bar; Terminal `./lab live`; console Model tab with the GGUF hash, quant, llama.cpp build, tok/s and `network: off` | "This is Muse Glimmer 30B, Meta's open weights, on a 24 gig M4. No API key, no cloud, and the Wi-Fi is off." (Say the quant the Model tab shows: `UD-Q3_K_XL` with Meta's DFlash drafter, the same build the benchmark used.) |
| 0:38–1:15 | Console at 1x, then labelled 8x: the director's parallel tool calls; the proposer's picks with P(hit); the critic opens a render and vetoes a pick; press `a`; sealed cells reveal; CO2 moves into the pore | "The same model plays four roles. It plans, commits to predictions before it sees any result, critiques its own picks by looking at the structures, and buys ten DFT results per round." |
| 1:15–1:40 | `calibrate_oracle`: the parity plot; the critic's `visual_rho`; the trace line that follows | Variant A (the run changed course): "We make it check its simulator against what it has paid for. What it does with the answer is its own call. Here it stopped trusting the cheap scores and moved to {{WHAT_IT_USED}}." Variant B (it did not): "We make it check its simulator against what it has paid for. What it does with the answer is its own call. Here it kept the cheap scores; across all runs, {{N_CHANGED}} of {{A5_N}} changed course after the check." |
| 1:40–2:15 | Results page: arms with intervals and both baselines, discovery curves, the A6 ablation, Brier score, leak probe, the `prereg-v1` hash | "This was pre-registered before any test run. Against random it {{VS_RANDOM}}. Against the strongest baseline, greedy on the simulator, it {{VS_A2: beats / ties / loses}}; against a random forest it {{VS_RF}}. Here are the intervals. The leak probe shows {{LEAK_SENTENCE}}." |
| 2:15–2:30 | Claim checker: "{{CLAIMS_N}} claims, {{CLAIMS_VERIFIED}} verified, {{CLAIMS_FLAGGED}} flagged" | "Every number it writes is checked against the computation it came from, and we publish the misses." |
| 2:30–2:47 | Approval queue: a mutant relaxing in 3D, labelled "unvalidated · ML only"; approve | "It also proposes variations on what it found. Those are unvalidated, so a person decides." |
| 2:47–3:00 | `git clone … && ./lab verify`; lower third "Built Oct 27 – Nov 4, 2026 · {{N_COMMITS}} commits since hackathon-start"; repo and site URLs | "Everything is open. Clone it, check every number without a model, or run it on your own Mac." |

About 260 words of narration (one variant at 1:15). At a calm 140 words per minute that is under 2 minutes of speech, which leaves room for pauses on the key screens.

## Recording checklist (Tue Nov 3, about 2 hours)

Setup:
- [ ] `./lab update`
- [ ] Mac on the charger. Low Power Mode off. Focus / Do Not Disturb on.
- [ ] Quit every app except Terminal, the browser and QuickTime. Browser zoom 100%, a clean window with only the console tab.
- [ ] `./lab doctor --rehearse` (it asks for the password if the Mac restarted). It ends with "Memory held up", or tells you to switch to `./lab live small`.

Live segment (one take is enough; you can retry):
- [ ] Start a screen recording: Cmd-Shift-5 > Record Entire Screen.
- [ ] On camera: turn Wi-Fi off from the menu bar.
- [ ] `./lab live`, then in a second Terminal tab `./lab live run`. This is a demo run on the development pool P0, not part of the benchmark; the video says so in a caption.
- [ ] Open the Model tab and hold on it for 3 seconds: hash, quant, build, tok/s, `network: off`.
- [ ] Approve each batch with `a` when asked.
- [ ] Stop the recording (Cmd-Ctrl-Esc). Save it as `live.mov` on the Desktop.
- [ ] `./lab stop`. Turn Wi-Fi back on.

Narration:
- [ ] QuickTime > File > New Audio Recording. Quiet room, phone away. Read the narration column above, two takes. Save the better one as `voice.m4a` on the Desktop.

Upload:
- [ ] `./lab push` (the demo run) and `./lab push-media ~/Desktop/voice.m4a ~/Desktop/live.mov` (re-encodes anything over 95 MB). Both end with a line starting `pushed`.

Claude then cuts the video from your two files plus browser B-roll (captions, speed labels, lower third) and sends it back for review. You upload the final file as unlisted to a video account you already have and paste the link into SUBMISSION.md.
