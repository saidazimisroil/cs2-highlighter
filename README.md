# CS2 Highlights

Turns a CS2 `.dem` file into a 16:9 highlight video of one player: it finds their best rounds
(multi-kills, won clutches, wallbangs, noscopes…), has CS2 record them from that player's view,
and joins the clips with a title card, labels and crossfades.

A demo has no video in it, so the footage is recorded by CS2 itself: HLAE opens the game, the
demo jumps from one highlight to the next while HLAE records each one, then the game closes.
You don't touch the game, but the PC is busy while it records — start a render and leave it.

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
Rounds scoring 3+ or with a special kill qualify; the top 8 are pre-selected. Each round is one
uncut clip from before the first kill to after the last, so a slow ace or clutch keeps the
walking in between. Tune the numbers at the top of `app/highlights.py`.

Clips show the full HUD and include player voices (`tv_listen_voice_indices -1`). A demo only
has voices if the server recorded them, so some demos stay silent.

## Recording modes

Set `CS2HL_RENDER_MODE` before `run.bat` to pick how CS2 is driven:

- **`hlae`** (default): the app launches CS2 through HLAE itself, with a generated cfg
  (`game\csgo\cfg\cs2hl\`) that uses HLAE's `mirv_cmd` to jump to each segment and record it.
  The demo only ever jumps forward, so the long jump to the first highlight happens once. Needs
  HLAE (`HLAE_PATH`, or CS Demo Manager's copy in `%USERPROFILE%\.csdm\hlae`) and CS2
  (found through Steam, or `CS2_PATH`). CS Demo Manager itself isn't used.
- **`config`**: one `csdm video --config-file` run. CS Demo Manager reloads the demo from the
  start for every segment, so long demos take much longer.
- **`per-clip`**: one `csdm video` run per segment (slowest; CS2 restarts each time).

## If recording fails

- **"Loader could not create requested process" / Access denied**: anti-cheat is blocking HLAE.
  For FACEIT, choose *Disable driver* from its tray icon and reboot (turn it back on before
  playing FACEIT).
- **The app warns about a demo's version**: CS2 itself crashes loading that demo, even without
  this app. Keep the demo and try after the next CS2 update. Known pairs are in `CRASHING_DEMOS`
  in `app/render.py`; the warning stops by itself once CS2 updates.
- **CS2 crashes on one demo**: try another. If that one works, CS2 can't play the first demo.
- **CS2 crashes on every demo**: usually a CS2 update broke HLAE (or CS Demo Manager's plugin in
  the `config` / `per-clip` modes). Update HLAE and try again.
- **Wrong number of video files** (`config` mode): try `CS2HL_RENDER_MODE=per-clip`.
- The generated cfgs are in `game\csgo\cfg\cs2hl\` while recording (`hlae` mode); the config sent
  to CS Demo Manager is saved as `data\renders\<job>\csdm-config.json` (other modes).

## Testing without CS2

`scripts\fake_csdm.bat` stands in for `csdm.exe` and writes test-pattern clips, to check the
rest of the pipeline: `set CSDM_PATH=%CD%\scripts\fake_csdm.bat` then `run.bat`.

## Layout

```
app/parser.py      demo → players, rounds (with alive rosters), deaths   (demoparser2)
app/highlights.py  scoring, clutch detection, clip segments              (pure functions)
app/render.py      recording modes; CS Demo Manager config + `csdm video` runner
app/hlae.py        `hlae` mode: cfg generation + launching CS2 through HLAE
app/compile.py     FFmpeg title card, labels, crossfades
app/jobs.py        one background worker (CS2 can only run once)
app/main.py        FastAPI API + serves app/static
```
