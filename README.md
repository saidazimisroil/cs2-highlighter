# CS2 Highlights

Turns a CS2 `.dem` file into a 16:9 highlight video of one player: it finds their best rounds
(multi-kills, won clutches, wallbangs, noscopes…), has CS2 record them from that player's view,
and joins the clips with a title card, labels and crossfades.

A demo has no video in it, so the footage is recorded by CS2 itself: CS Demo Manager opens the
game, plays the demo, records with HLAE and closes it again. You don't touch the game, but the
PC is busy while it records — start a render and leave it.

## One-time setup

Needs Windows with CS2 installed (Steam), plus:

0. **Python 3.12+** (python.org, tick "Add to PATH") and **FFmpeg** on PATH — easiest:
   `winget install Gyan.FFmpeg`, then open a new terminal and check `ffmpeg -version`.
1. **PostgreSQL 17+** — CS Demo Manager needs it. Install from postgresql.org with
   *PostgreSQL Server* and *Command Line Tools*; keep port 5432 and remember the password.
2. **CS Demo Manager** (cs-demo-manager.com). On first launch, connect it to PostgreSQL.
3. In CS Demo Manager: **Settings → Video** → install **HLAE**. (This app passes the FFmpeg
   from your PATH to CS Demo Manager automatically.)
4. In CS Demo Manager, record one short test video from any demo by hand. This confirms CS2 +
   HLAE work with the current CS2 update before you queue a long render.
5. Make sure the `csdm` command is found: the app's header shows a green **CS Demo Manager** chip
   when it is. If it's red, add the CS Demo Manager install folder to `PATH`, or set
   `CSDM_PATH` to the full path of `csdm.exe`.

## Run

Double-click **`run.bat`**. The first run creates `.venv` and installs the Python packages, then
opens http://127.0.0.1:8765.

1. Paste the path of a `.dem` (or drop one in to upload a copy). CS2 match demos are usually in
   `...\Counter-Strike Global Offensive\game\csgo\`.
2. Pick the player.
3. Untick/tick highlights (best are pre-picked), choose quality, **Render video**.
4. Wait — the log shows progress. The finished MP4 plays in the page and is saved in
   `data\output\`.

Recording speed: HLAE records frame by frame, so the video is smooth even on integrated
graphics, it just takes longer than real time. 720p is the default for that reason.

## Command line

```
.venv\Scripts\python -m app.highlights "C:\path\match.dem"                    # list players
.venv\Scripts\python -m app.highlights "C:\path\match.dem" --player NAME      # list highlights
.venv\Scripts\python -m pytest tests                                          # unit tests
```

## How highlights are scored

Per round, for the chosen player: 1 point per kill; +0.3 headshot, +0.8 wallbang, +0.6 through
smoke, +0.8 while flashed, +0.8 airborne, +1.0 noscope, +1.5 knife/zeus; +2 / +4 / +7 for a
3K / 4K / ace; +2 / +4 / +6 / +9 for a won 1v2 / 1v3 / 1v4 / 1v5; +1 for two kills within 2 s.
Rounds scoring 3+ or with a special kill qualify; the top 8 are pre-selected. Kills more than
10 s apart become separate segments, so a slow ace or clutch skips the walking in between.
Tune the numbers at the top of `app/highlights.py`.

## If recording fails

- **Wrong number of video files**: switch to one recording per segment (slower, CS2 restarts
  each time): set `CS2HL_RENDER_MODE=per-clip` before `run.bat`.
- **CS2 crashes or the demo won't play**: usually a CS2 update broke HLAE / CS Demo Manager's
  plugin. Update both, and re-try the manual test video from setup step 4.
- The exact config sent to CS Demo Manager is saved as `data\renders\<job>\csdm-config.json`.

## Testing without CS2

`scripts\fake_csdm.bat` stands in for `csdm.exe` and writes test-pattern clips, to check the
rest of the pipeline: `set CSDM_PATH=%CD%\scripts\fake_csdm.bat` then `run.bat`.

## Layout

```
app/parser.py      demo → players, rounds (with alive rosters), deaths   (demoparser2)
app/highlights.py  scoring, clutch detection, clip segments              (pure functions)
app/render.py      CS Demo Manager config + `csdm video` runner
app/compile.py     FFmpeg title card, labels, crossfades
app/jobs.py        one background worker (CS2 can only run once)
app/main.py        FastAPI API + serves app/static
```
