# Mac setup: the only page you need

This page lists everything you run on the MacBook (M4 Pro, 24 GB), day by day. Each step is one command. Every command prints what it did, and if something fails, the last lines tell you the exact command to try next.

There are no signups and no API keys. The only accounts are GitHub (yours) and, at the end, the Cerebral Valley form and X.

Replace `{{GITHUB_USER}}` with your GitHub user name everywhere below.

---

## Tonight, Thu Oct 29 (about 20 minutes of typing, then the download runs on its own)

**1. GitHub, in the browser (one time).**
- Create a public repository named `fortyshots` (empty, no README) if it does not exist yet.
- Settings > Pages > Build and deployment > Source: **Deploy from a branch**, Branch: **main**, Folder: **/docs**, Save. The site appears once the `docs/` folder exists; an error before then is expected.

**2. Terminal: install the tools.** If `brew` is not found, first install Homebrew from https://brew.sh (free, no account).
```bash
brew install llama.cpp uv gh ffmpeg
```

**3. Log in to GitHub from the Terminal** (choose GitHub.com, HTTPS, "Login with a web browser"):
```bash
gh auth login
```

**4. Get the code.**
```bash
gh repo clone {{GITHUB_USER}}/fortyshots ~/fortyshots
cd ~/fortyshots
```

**5. Download the model** (Muse Glimmer 30B from Hugging Face, no login). The command shows your free disk space first and stops if there is not enough.
```bash
./lab download default live
```
- `default` is about 18.2 GB (Meta's Q4_K_M model plus its vision file); `live` adds about 15.9 GB (a slightly smaller model used for the live demo). If the disk is tight, run `./lab download default` tonight and `./lab download live` later.
- If it stops (sleep, Wi-Fi), run the same command again. It continues where it stopped and checks every file against the sha256 published on Hugging Face.

**6. Smoke test.**
```bash
./lab smoke
```
It may ask for your Mac login password once, to run `sudo sysctl iogpu.wired_limit_mb=20480`. That lets the GPU use 20 GB of the 24 GB; without it the model partly runs on the CPU and is slow. The setting resets when the Mac restarts.

What you should see: `answer: 391 (correct: yes)`, a decode speed in tok/s, swap before and after, and the GPU memory numbers. The last line should start with `pushed`. If it says `NOT PUSHED`, run `./lab push`. Claude reads the numbers from the pushed file, so you do not need to copy anything.

> **If the repository is not on GitHub yet** (step 4 fails), you can still download and test with plain commands:
> ```bash
> mkdir -p ~/glimmer && cd ~/glimmer
> B=https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/main
> curl -L -C - -O $B/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf
> curl -L -C - -O $B/mmproj-Muse-Glimmer-30B-Q4_K_M.gguf
> ```
> Later, `./lab download default` finds these files, checks them and skips the download.

---

## Fri Oct 30, 20:00 (about 20 minutes, then it runs overnight)

Plug in the charger, quit other apps, leave the lid open.
```bash
cd ~/fortyshots
./lab update        # get Claude's latest code
./lab setup         # checks the Mac, installs anything missing (asks first), Python env, GitHub login, model files
./lab doctor        # about 15 min: tests tools, JSON output, vision, speed and memory; pushes a report
./lab pilot         # 3 practice campaigns on the development pool, about 1.5 h; pushes after each one
```
- `setup` ends with `Next: ./lab doctor`; `doctor` ends with `Next: ./lab pilot` when everything passed.
- If doctor sees the Mac swapping (more than 1 GB), it switches to a smaller model by itself and runs again. If it asks you to download that model, run the command it prints.
- You can go to bed once `./lab pilot` has started. It keeps the Mac awake on its own.

**Turn off automatic macOS updates until Nov 4**, so the Mac does not restart overnight: System Settings > General > Software Update > Automatic updates (i) > turn off "Install macOS updates". Turn off Low Power Mode: System Settings > Battery > Low Power Mode > Never.

**Saturday morning:** the last line in the Terminal should start with `pushed`. If it says `NOT PUSHED`, run `./lab push`.

---

## Recording nights: Sat Oct 31, Sun Nov 1, Mon Nov 2, at 21:00 (2 minutes each)

Plug in, quit other apps, lid open. Then one command per night:

| Night | Command |
|---|---|
| Sat Oct 31 | `./lab record main` |
| Sun Nov 1 | `./lab record second` |
| Mon Nov 2 | `./lab record resume` |

- Each command first fetches Claude's latest code, then runs every campaign in the plan and pushes after each finished campaign. If the Mac restarted since last time, it asks for your password once (the GPU memory setting).
- Finished campaigns are skipped and unfinished ones continue, so running a command twice is safe.
- Ctrl-C stops it cleanly. It tells you how to resume.
- **Morning check:** the last line should start with `pushed`. If not, run `./lab push`.
- Optional: if you turn Wi-Fi off after the first campaign has started, those runs are recorded with `network: off` and can honestly be shown as offline. Pushing then waits until the morning: turn Wi-Fi on and run `./lab push`.

---

## Tue Nov 3: video (about 2 hours)

Follow the recording checklist in [VIDEO.md](VIDEO.md). The Mac parts:
```bash
./lab update
./lab doctor --rehearse     # one short live round with the browser and screen recording open; checks memory
./lab live                  # loads the live model and opens the console in the browser
./lab live run              # starts the demo campaign (approve batches in the browser with the a key)
./lab stop                  # when you are done
```
Then upload your two recordings (narration and screen recording). Files over 95 MB are re-encoded automatically:
```bash
./lab push-media ~/Desktop/voice.m4a ~/Desktop/live.mov
```
Also on Cerebral Valley: switch the track to **Indie**.

## Wed Nov 4

- By 12:00 ET: submit on Cerebral Valley, copying each field from [SUBMISSION.md](SUBMISSION.md).
- 12:30 ET: post the X thread from [LAUNCH.md](LAUNCH.md).

---

## Every command

| Command | What it does |
|---|---|
| `./lab status` | What is running, the last campaign, the last push |
| `./lab model [profile]` | Start the model server in the background |
| `./lab stop` | Stop everything `./lab` started |
| `./lab push` | Upload results; the last line says `pushed` or `NOT PUSHED: run ./lab push` |
| `./lab update` | Get the latest code |
| `./lab verify` | Recompute every published number (no model needed) |
| `./lab help` | The full list |

Profiles (which model file and settings): `default` (Meta Q4_K_M, used for the benchmark), `live` (smaller, for the video), `small` (smallest fallback). `./lab help` describes each one.

## If something goes wrong

| You see | Do this |
|---|---|
| `NOT PUSHED` | Check Wi-Fi, then `./lab push` |
| `Only N of M layers are on the GPU` | `sudo sysctl iogpu.wired_limit_mb=20480`, then `./lab stop` and run your command again |
| `Something else is already answering on port 8080` | `./lab stop`; if that does not help, restart the Mac |
| `The model server exited while loading` | Run the printed command; if it fails again, `./lab model small` |
| Download stopped | Run the same `./lab download ...` again |
| Anything else | Run `./lab status` and `./lab push`, and tell Claude what the last 10 lines said |

Files `./lab` keeps outside the repository: model files and `server.log` in `~/glimmer`. Inside the repository it only writes `runs/<run id>/` and `doctor/`, and it pushes them to the branch `runs-mac`, never to `main`.
