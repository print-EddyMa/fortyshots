# Launch: X thread, short clip, community posts

Post from accounts you already have. No new accounts, no paid promotion.

## Timing (ET)

| When | What |
|---|---|
| Wed Nov 4, 12:30 | X thread (after the submission is in) |
| Wed Nov 4, 12:30–13:00 | Reply to every comment |
| Wed Nov 4, 14:00 | r/LocalLLaMA post, only if you already have an account there |
| Thu Nov 5, 09:00 | Show HN, only if you already have an account there |
| Fri Nov 6 | One follow-up post: "where the physics failed" (the honesty panel) |

## X thread (fill every `{{...}}` from `results/results.json`; each post under 280 characters)

1. (attach the 40 s clip) fortyshots: Meta's open Muse Glimmer 30B on a 24 GB MacBook, no API key, gets 40 hidden DFT lookups to find MOFs that hold CO2 in humid air. It picked {{H}} of the hidden hits. Random: {{RANDOM_HITS}}. Random forest: {{A3_HITS}}. Pre-registered. (1/6)

2. One model, four roles: a director that explores 500 frameworks with tools, a proposer that commits to picks with a probability, a critic that reads its own plots through vision, and a reporter whose every number is checked against the run log. (2/6)

3. The cheap simulator ranks the whole pool no better than chance (hit AUC {{MACE_AUC}}), yet its 40 strongest binders hold twice the random rate of hits, which makes MACE-greedy the baseline to beat. We make the agent check it against the labels it buys; what it does with the answer is its own call. In the featured run it {{WHAT_IT_DID}}. (3/6)

4. Scored blind against Meta FAIR's ODAC23 DFT, pre-registered before any test run: agent {{A5_HITS}} [{{A5_LO}}–{{A5_HI}}] vs random forest {{A3_HITS}} [{{A3_LO}}–{{A3_HI}}]. {{WIN_TIE_LOSS_SENTENCE}} Every number links to its computation. (4/6)

5. Limits: the labels are existing DFT, not new experiments; the candidates are known MOFs, so these are picks, not discoveries; agent-proposed variants are ML-only and unvalidated. (5/6)

6. All open: replay every run in the browser, or clone it and run `./lab verify` to recompute every number with no model. Built in a week for the Meta hackathon. {{SITE_URL}} {{REPO_URL}} @AIatMeta @cerebral_valley {{EVENT_HASHTAG}} (6/6)

Then quote-post Meta's Muse Glimmer announcement with post 1, if you can find it.

## 40-second clip (1080×1920, captions burned in, no music needed)

| Time | Picture | Caption |
|---|---|---|
| 0–4 s | Hook orbit: the landing hero, a real DFT hit as a 2×2×2 supercell, CO2 settling into its pore | "One open Meta model on a laptop picks which 40 DFT experiments to run." |
| 4–10 s | The 85% scatter (Results, Why this is hard): 3,000 MOFs, water against CO2 | "In 85% of 3,000 MOFs, water takes the binding site first." |
| 10–22 s | Console timelapse labelled "8×", ending on a DFT reveal with CO2 settling and the binding-site distances | "No API key. No cloud. It plans, picks, critiques and buys DFT, ten at a time." Add "Wi-Fi off" only if the clip's run header says `network: off`. |
| 22–30 s | The atlas: the run's 40 purchases grow round by round on the map of 3,000 MOFs, hits flash | "Where it searched: 40 picks on the map of chemical space" |
| 30–40 s | Result: arms chart with both baselines and intervals; URL | "{{H}} of the hidden hits. Random: {{RANDOM_HITS}}. Pre-registered, every number reproducible." + site URL |

Claude cuts this from the same footage as the main video.

## Community Vote

- Votes are cast on the Cerebral Valley gallery ({{CV_GALLERY_URL}}). Share that link and the runs gallery once, in places you already take part in.
- Ask people to take a look, not to vote a particular way. Never ask for fake or coordinated engagement: no vote trading, no alternate accounts, no paid likes, no group-chat vote drives. That would break the event rules and Muse Glimmer's Usage Policy (§3.6, false online engagement).
- Answer questions honestly, including about the limits and any loss to the random forest.
