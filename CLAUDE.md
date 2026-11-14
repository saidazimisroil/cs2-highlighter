# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-only local web app that turns a CS2 `.dem` into a highlight video of one player. It
finds and scores their best rounds, has CS2 itself record them from that player's view (HLAE),
then FFmpeg joins the clips with a title card, labels and crossfades. A demo contains no video,
so every render launches the real game. The user-facing setup and troubleshooting docs are in
`README.md`.

## Commands

```
run.bat                                                         # creates .venv on first run, serves http://127.0.0.1:8765
.venv\Scripts\python -m pytest tests                            # all tests
.venv\Scripts\python -m pytest tests/test_hlae.py::test_paused_demo_is_resumed_as_it_loads   # one test
.venv\Scripts\python -m app.highlights "C:\path\match.dem"                 # list players
.venv\Scripts\python -m app.highlights "C:\path\match.dem" --player NAME   # list highlights
.venv\Scripts\python scripts\probe_demo.py "C:\path\match.dem"             # dump raw demoparser2 output
```

There is no linter or build step. uvicorn runs without `--reload`, and parsed matches and jobs
live only in memory, so restart the server to pick up code changes. A restart drops the job list.

## Pipeline

`parser.py` → `highlights.py` → `render.py` / `hlae.py` → `compile.py`. `jobs.py` runs it, and
`main.py` exposes it over HTTP.

- **parser.py**: demoparser2 → `MatchData` (players, rounds with per-round alive rosters,
  deaths). Rounds count from the *last* match-start event, because FACEIT/ESEA demos have knife
  rounds and warmup first.
- **highlights.py**: pure functions, no I/O, which the tests exercise directly by building
  `Round`/`Death` objects by hand. The scoring constants at the top are the tuning knobs. Each
  selected round becomes one uncut `Segment` (pre-roll before the first kill → post-roll after
  the last kill, or on to death / round end + MVP music, depending on the per-round options).
  Ticks are 64/s (`config.TICKRATE`). `detect_highlights(mode=…)` picks the recording mode
  (`MODES`): `best` (scored rounds), `knife` (one clip per round with knife kills), `deaths` (each
  death as two segments: the player's view, then the killer's via `Segment.pov`) and `misses`
  (missed shots with the picked guns, from `MatchData.shots`, which holds every gun shot). A hit
  is any `player_hurt` by the shooter within `parser.HIT_WINDOW` ticks. Shots from one gun less
  than `BURST_GAP_S` apart form a burst, and a burst is one miss only if none of its shots hit, so
  a whiffed spray counts once. `miss_weapons` feeds the gun picker, and `title_for` names the title
  card ("AWP MISSES" when one gun is picked).
- **render.py**: picks the recording mode from `CS2HL_RENDER_MODE`. The default is `hlae`
  (in `hlae.py`). `config` and `per-clip` are older fallbacks that call CS Demo Manager's
  `csdm video`. Note that csdm exits 0 even when CS2 crashed, so its output is also checked for
  "Game error".
- **compile.py**: FFmpeg normalizes each clip, burns in labels, makes the title card, and
  crossfades everything into `data/output/<map>_<player>_<job>.mp4`.
- **jobs.py**: one worker thread with a queue, because only one CS2 instance can run at a time.
  Exceptions become `job.error`, and the traceback goes into the job log shown in the UI.
- **main.py**: FastAPI plus the static frontend. Demos registered by path or upload are stored in
  `data/demos/index.json` and keyed by a random id.
  - A background thread caches each demo's `parser.demo_summary` there (map, final score, source;
    about 1 s per demo, versus several seconds for `parse_demo`).
  - `DELETE /api/demos/{id}` deletes the file only for uploaded copies, which live in `DEMOS_DIR`.
  - `/api/videos` lists `data/output/*.mp4` from disk, so finished videos outlive the in-memory
    job list. Jobs write a `.json` sidecar with player/map/mode, and older files are described
    from their names.
  - Everything under `data/` is gitignored runtime state.
- **app/static**: plain JS, no build step. `app.js` has a hash router (`#/demos`,
  `#/demo/<id>/<steamid>/<mode>`, `#/videos`) and one render function per view, with events
  delegated on `#view`. Settings and recent demos are kept in `localStorage`.

## HLAE recording mode (`hlae.py`): the fragile part

`build_cfgs` writes cfgs to `<CS2>/game/csgo/cfg/cs2hl/`, and HLAE launches CS2 with
`+exec cs2hl/init`. `init.cfg` queues every action with `mirv_cmd addAtTick`, then calls
`playdemo`. Rules that were learned the hard way:

- **`demo_resume` at tick 0 (`RESUME_TICK`)**: FACEIT demos start paused. About 3 s in, CS2
  skips back to tick 0 and crashes in `engine2.dll`. Resuming as the demo loads prevents this.
  Every other command waits until `MIN_TICK` (96), because commands right after load can be
  dropped.
- **Forward-only jumps**: the demo only ever jumps forward with `demo_gototick`. A jump is
  skipped when the next segment is within about 1 s. Seeking backwards replays from the start
  and is very slow. Segments that overlap (a `deaths`-mode death replayed from the killer's view,
  `Segment.pov`) are split by `plan_passes` into separate CS2 runs, each forward-only.
- **Quoted arguments go in a cfg**: `mirv_cmd` can't hold nested quotes, so commands with quoted
  arguments (player name, output paths) go in `prepN.cfg`, which is exec'd at the right tick.
- **`demo_ui_mode 0` before `playdemo`**: otherwise the playback bar ends up in the recording.
  The same goes for `tv_listen_voice_indices -1` (player voices).
- **Separate CS2 profile**: `USRLOCALCSGO=data/cs2-profile` keeps the HUD tweaks out of the
  user's real CS2 settings.
- **Crash detection**: a missing `video.mp4` plus a new `*.mdmp` in `game/bin/win64/` means CS2
  crashed. Each segment's video and `take*/audio.wav` are muxed into `raw/<n>.mp4`.

Changes here can only be verified by a real render: it needs Steam running, CS2 not running,
and anti-cheat that blocks HLAE (e.g. the FACEIT driver) turned off. The unit tests only check
the generated cfg text. `CRASHING_DEMOS` in `render.py` maps an installed CS2 version to demo
versions that crash even without this app, and the UI warns about those pairs. The installed
version comes from `steam.inf`; a demo's version is
`DemoParser(path).parse_header()["patch_version"]`.

## Testing without CS2

`scripts\fake_csdm.bat` stands in for `csdm.exe` and writes test-pattern clips, so the
compile/UI path can run without the game. Set `CSDM_PATH=%CD%\scripts\fake_csdm.bat` and
`CS2HL_RENDER_MODE=config` (or `per-clip`); the default `hlae` mode never calls csdm.

## Environment overrides

Tool paths are resolved in `config.py`: `CSDM_PATH`, `HLAE_PATH` (default:
`~/.csdm/hlae/HLAE.exe`, CS Demo Manager's copy), `CS2_PATH` (otherwise found through the Steam
registry and library folders), `FFMPEG_PATH`, `FFPROBE_PATH`, `CS2HL_FONT`, and `CS2HL_DATA`
(the data directory).
