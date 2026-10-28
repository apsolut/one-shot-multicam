# AI-guided install: Multicam

> **For humans:** hand this file to your coding agent (Claude Code, Cursor, Codex, …) and say
> *"Set up Multicam for me following this file."* It contains everything the agent needs, in order,
> with checks after each step. You'll only be asked for your Comfy Cloud API key (and, for Fly, to log in).
>
> **For the agent:** follow the steps in order and run the **Check** after each one. Never print,
> echo, log or commit API keys, Fly tokens or the promo (judge) code. Pass them through environment variables
> or piped `fly secrets import`. Ask the user before anything that costs money (Comfy jobs, Fly machines)
> or is publicly visible (deploying, pushing).

Multicam turns one selfie video into a multi-camera edit. Generation runs on **Comfy Cloud**
(no local GPU needed). The local machine runs a small FastAPI server, whisper (CPU) and ffmpeg.
Modes: **Talk**, **Music**, **Scene** and **Dub**.

---

## 0. Prerequisites

| Need | Check | Install hint |
|---|---|---|
| Python ≥ 3.10 | `python3 --version` | python.org / `brew install python` / `apt install python3 python3-venv` |
| ffmpeg + ffprobe on PATH | `ffmpeg -version && ffprobe -version` | `brew install ffmpeg` / `apt install ffmpeg` / `winget install ffmpeg` |
| git | `git --version` | |
| ~3 GB disk | | whisper model + venv + renders |
| A Comfy Cloud API key | the user has it | <https://platform.comfy.org/profile/api-keys> (a Comfy Cloud plan with credits is required for generation) |

The free steps (transcribe, plan, render the cut, edit) work **without** a key. Only the AI re-shoots,
dubbing and Scene generations call Comfy Cloud.

## 1. Get the code

```bash
git clone https://github.com/apsolut/one-shot-multicam && cd one-shot-multicam
```

**Check:** `ls multicam/web/index.html workflows/` lists files.

## 2. Python environment

```bash
python3 -m venv .venv
. .venv/bin/activate                     # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[web,music]"            # web app + Music mode (librosa); or: pip install -r requirements.txt && pip install -e .
```

**Check:** `multicam --help` prints the commands (`serve`, `analyze`, `dub`, `assemble`, …), and
`python -m pytest -q tests` passes (no network, no key needed; run `pip install pytest` first if needed).

## 3. Run the web app locally

```bash
multicam serve                           # → http://127.0.0.1:8787  (first start downloads the whisper "small" model, ~500 MB)
```

**Check:** `curl -s http://127.0.0.1:8787/api/config` returns JSON. Open the URL in a browser
and you'll see the four mode tabs.

### The Comfy Cloud key (local)

Ask the user to paste it into the **Comfy Cloud key** field in the web UI. It stays in their browser's
localStorage and is sent per request to *their* local server only, which forwards it to `cloud.comfy.org`.
The agent never needs to see it.

For the CLI, let the user put it in the environment themselves (`export COMFY_API_KEY=...` in their
shell or a `.env` they keep private). Don't type it into a command line you print, and don't write it to a tracked file.

**Check (costs nothing):** in the UI, *Check key* shows the balance. Or run
`curl -s -X POST -H "X-Comfy-Key: $COMFY_API_KEY" http://127.0.0.1:8787/api/check-key` and look only at `ok`.

### First run (ask before spending)

1. Drop a vertical selfie clip (≤ 30 s is cheapest) and click **Analyze, free**.
2. Review the plan and the **estimate** (re-shoots cost about $0.05 each, Dub about $0.011 per second of speech). **Confirm with the user**, then click **Generate**.
3. When it finishes, download the MP4. **⬇ ComfyUI workflow(s)** gives you the exact API graphs that ran (also saved in the project's `workflows/` folder), so you can open them in ComfyUI.

CLI equivalent: see the README's *CLI usage* section (`multicam analyze …`, `multicam generate …`, `multicam dub …`).

### Docker (alternative)

```bash
docker build -t multicam .
docker run --rm -p 8787:8787 -v multicam-data:/data multicam
```

---

## 4. Deploy your own public instance on Fly.io (optional)

Only do this if the user asks for a public URL. It creates billable resources, so confirm first.

```bash
curl -L https://fly.io/install.sh | sh          # installs ~/.fly/bin/fly
export PATH="$HOME/.fly/bin:$PATH"
fly auth login                                   # the user logs in in their browser
```

1. **Pick a unique app name.** Edit `app = "…"` in `fly.toml` (the repo's value belongs to the demo) and check `primary_region`.
2. **Create the app and volume:**
   ```bash
   fly apps create <your-app>
   fly volumes create multicam_data --region fra --size 1 --app <your-app>
   ```
3. **Choose the access model** in `fly.toml` `[env]`:
   - **Bring-your-own-key only (safest, costs you nothing):** delete the `MULTICAM_HOSTED_KEY_ENV` line, set `MULTICAM_HOSTED_RUNS = "0"` and `MULTICAM_MAX_JOBS = "0"`, and skip the `COMFY_API_KEY` secret. Visitors paste their own Comfy key.
   - **Your key with hard limits:** keep `MULTICAM_HOSTED_KEY_ENV = "COMFY_API_KEY"` and set the limits:
     - `MULTICAM_HOSTED_RUNS = "6"`: a global pool of free runs, 1 per visitor (IP + cookie), clips ≤ 30 s.
     - `MULTICAM_TRIAL_JOBS_PER_RUN = "10"`: Comfy jobs per free run.
     - `MULTICAM_HOSTED_ACCESS = "judge"`, plus the secret `MULTICAM_JUDGE_CODE` and `MULTICAM_JUDGE_MAX_JOBS`: a private code for reviewers.
     - `MULTICAM_HOSTED_FLOOR_USD`: **a hard stop.** Set it to *current balance − what you're willing to spend*. The server refuses hosted jobs once the real Comfy balance would drop below it.
   - Keep `MULTICAM_PROXY_HOPS = "1"` on Fly so per-visitor limits see the real client IP.
4. **Set secrets without showing them.** Let the user run this in their own terminal, or pipe the values from the environment:
   ```bash
   printf 'COMFY_API_KEY=%s\n' "$COMFY_API_KEY" | fly secrets import --app <your-app> --stage
   # optional promo code (env MULTICAM_JUDGE_CODE) (generate one, store it in a 600-mode file, never commit it):
   printf 'MULTICAM_JUDGE_CODE=%s\n' "$(cat judge_code.txt)" | fly secrets import --app <your-app> --stage
   ```
5. **Deploy:**
   ```bash
   fly deploy --remote-only --depot=false --ha=false --app <your-app>
   ```
6. **Checks:**
   - `curl -s https://<your-app>.fly.dev/api/config` shows `"hosted"`, `"trial": {"runs_left": …}` and `byok_only` as configured.
   - Open the site on a phone. The four tabs show as a 2×2 grid, with no sideways scrolling.
   - Run one Analyze (free). Only run a paid Generate if the user agrees.
   - `fly logs --app <your-app>` shows no tracebacks. Keys never appear in the logs.

**Top up free runs later** (no code change): raise `MULTICAM_HOSTED_RUNS` (6 → 12). The used count in
`/data/hosted_jobs.json` → `"trial"` is kept. Lower `MULTICAM_HOSTED_FLOOR_USD` by the extra budget, then `fly deploy`.

---

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `ffmpeg: not found` | install ffmpeg and reopen the shell |
| Music tab errors with `No module named librosa` | `pip install -e ".[music]"` |
| `401` from Comfy | wrong or expired key; create a new one at platform.comfy.org |
| `402`/"below floor" on a hosted site | balance reached `MULTICAM_HOSTED_FLOOR_USD`; top up credits or lower the floor deliberately |
| `409` while clicking during analysis | wait for analysis to finish (the UI retries) |
| Fly build hangs on "depot" | add `--depot=false` |

## 6. What's where

- `multicam/`: the Python package (`server.py` web API, `scene_web.py`, `dub_web.py`, `comfy.py` Comfy Cloud client, `assemble.py` ffmpeg cut).
- `multicam/web/`: the static UI (no build step).
- `workflows/`: the ComfyUI API graphs Multicam fills in (LTX-2.3 CrossView re-shoot, LipDub, Gemma 3 translate, …).
- `<data>/<project id>/workflows/` (Talk/Music), `<data>/scenes/<id>/workflows/`, `<data>/dubs/<id>/workflows/`: the exact graphs each project submitted.
- `tests/`: offline tests (`python -m pytest -q tests`).
