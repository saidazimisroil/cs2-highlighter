"""Records highlight segments by driving HLAE directly, without CS Demo Manager's CS2 plugin.

HLAE starts CS2 with a generated cfg that schedules everything with mirv_cmd (HLAE's
tick-based command queue): jump to each segment with demo_gototick, lock the camera on the
player, record with mirv_streams (video piped to FFmpeg plus a WAV), then quit. Each segment
ends up as <out_dir>/<n>/video.mp4 + take*/audio.wav, muxed here into <out_dir>/<n>.mp4.
"""
import os
import subprocess
import time
from pathlib import Path

from .config import DATA_DIR, TICKRATE, find_cs2, find_ffmpeg, find_hlae
from .render import RenderedSegment, RenderError, RenderSettings, Log

MIN_TICK = 96            # commands right after the demo loads can be skipped by the game
SEEK_LEAD = 2 * TICKRATE  # land this far before a segment so the scene settles after a jump
PREP_LEAD = TICKRATE      # set camera / recording target this far before recording starts
CFG_DIR = "cs2hl"        # inside game/csgo/cfg, wiped before every run
START_TIMEOUT = 180       # seconds for CS2 to appear after HLAE starts
RUN_TIMEOUT = 3 * 3600


def _slash(p: Path) -> str:
    return str(p).replace("\\", "/")


def build_cfgs(demo_path, steamid: str, player_name: str, segments: list[tuple[int, int]],
               out_dir: Path, settings: RenderSettings) -> dict[str, str]:
    """cfg file name -> contents. Segments are (start_tick, end_tick), sorted, non-overlapping."""
    name = player_name.replace('"', "")
    setup = [
        "sv_cheats 1",
        "volume 1",
        "demo_ui_mode 0",
        "cl_demo_predict 0",
        "cl_draw_only_deathnotices 1",
        "cl_hud_telemetry_frametime_show 0",
        "cl_hud_telemetry_net_misdelivery_show 0",
        "cl_hud_telemetry_ping_show 0",
        "cl_hud_telemetry_serverrecvmargin_graph_show 0",
        "cl_trueview_show_status 0",
        "r_show_build_info 0",
        "spec_show_xray 0",
        "mirv_deathmsg lifetime 5",
        "mirv_deathmsg filter clear",
        "mirv_deathmsg filter add block=1",
        f"mirv_deathmsg filter add attackerMatch=x{steamid} attackerIsLocal=1 block=0",
        "mirv_streams record screen enabled 1",
        "mirv_streams record startMovieWav 1",
        f"mirv_streams record fps {settings.framerate}",
    ]
    files = {"setup.cfg": setup}
    schedule = [(MIN_TICK, f"exec {CFG_DIR}/setup")]
    pos = MIN_TICK
    for n, (start, end) in enumerate(segments, 1):
        target = start - SEEK_LEAD
        if target > pos + TICKRATE:  # only jump forward, and only when it saves time
            schedule.append((pos + 1, f"demo_gototick {target}"))
            pos = target
        prep = max(start - PREP_LEAD, pos + 2)
        start = max(start, prep + 2)
        end = max(end, start + 1)
        seg_dir = _slash(out_dir / str(n))
        files[f"prep{n}.cfg"] = [
            "demo_ui_mode 0",  # keep the playback bar out of the recording
            "spec_mode 1",  # first person; must come before spec_player
            f'spec_player "{name}"',
            "mirv_deathmsg clear",
            f'mirv_streams record name "{seg_dir}"',
            f'mirv_streams settings add ffmpeg cs2hl{n} "-c:v libx264 -pix_fmt yuv420p -crf {settings.crf} '
            f'{{QUOTE}}{seg_dir}/video.mp4{{QUOTE}}"',
            f"mirv_streams record screen settings cs2hl{n}",
        ]
        schedule += [(prep, f"exec {CFG_DIR}/prep{n}"),
                     (start, "mirv_streams record start"),
                     (end, "mirv_streams record end")]
        pos = end
    schedule.append((pos + TICKRATE // 2, "quit"))

    files["init.cfg"] = ["mirv_cmd clear",
                         *(f'mirv_cmd addAtTick {t} "{c}"' for t, c in schedule),
                         "demo_ui_mode 0",  # before playdemo, or the playback bar stays up
                         f'playdemo "{_slash(Path(demo_path))}"']
    return {k: "\n".join(v) + "\n" for k, v in files.items()}


def _running(image: str) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH", "/FO", "CSV"],
                         capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return f'"{image.lower()}"' in out.lower()


def _kill(image: str) -> None:
    subprocess.run(["taskkill", "/IM", image, "/F"], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def _ensure_ffmpeg_ini(hlae: Path) -> None:
    """HLAE pipes frames to the FFmpeg named in <hlae>/ffmpeg/ffmpeg.ini."""
    ini = hlae.parent / "ffmpeg" / "ffmpeg.ini"
    if ini.is_file():
        for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("path=") and Path(line[5:].strip()).is_file():
                return
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RenderError("FFmpeg not found (HLAE needs it to encode video).")
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text(f"[Ffmpeg]\nPath={ffmpeg}\n", encoding="utf-8")


def _mux(video: Path, wav: Path | None, out: Path, log: Log) -> Path:
    if wav is None:
        return video
    cmd = [find_ffmpeg(), "-y", "-v", "error", "-i", str(video), "-i", str(wav),
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if proc.returncode != 0:
        log(f"Could not add audio to {video.parent.name} ({proc.stderr.strip()}); using video only.")
        return video
    return out


def render_hlae(demo_path, steamid, player_name, highlights, out_dir: Path,
                settings: RenderSettings, log: Log) -> list[RenderedSegment]:
    hlae, cs2 = find_hlae(), find_cs2()
    if not hlae:
        raise RenderError("HLAE.exe not found. Install HLAE (e.g. from CS Demo Manager's settings) or set HLAE_PATH.")
    if not cs2:
        raise RenderError("cs2.exe not found. Set CS2_PATH to ...\\game\\bin\\win64\\cs2.exe.")
    if _running("cs2.exe"):
        raise RenderError("CS2 is already running. Close it and try again.")
    if not _running("steam.exe"):
        raise RenderError("Steam isn't running. Start Steam (signed in), then try again.")
    hlae, cs2 = Path(hlae), Path(cs2)
    _ensure_ffmpeg_ini(hlae)

    segs = sorted(((s.start_tick, s.end_tick, h.id, i) for h in highlights for i, s in enumerate(h.segments)))
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = cs2.parents[2] / "csgo" / "cfg" / CFG_DIR
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for old in cfg_dir.glob("*.cfg"):
        old.unlink()
    for fname, text in build_cfgs(demo_path, steamid, player_name, [(s, e) for s, e, _, _ in segs],
                                  out_dir, settings).items():
        (cfg_dir / fname).write_text(text, encoding="utf-8")

    dumps_before = set(cs2.parent.glob("*.mdmp"))
    cmdline = f"-insecure -novid -width {settings.width} -height {settings.height} -sw +exec {CFG_DIR}/init"
    cmd = (f'"{hlae}" -noGui -autoStart -noConfig -afxDisableSteamStorage -customLoader '
           f'-hookDllPath "{hlae.parent / "x64" / "AfxHookSource2.dll"}" -programPath "{cs2}" -cmdLine "{cmdline}"')
    # A separate CS2 profile folder keeps the HUD tweaks out of the user's own settings.
    env = {**os.environ, "USRLOCALCSGO": str(DATA_DIR / "cs2-profile")}
    log(f"Recording {len(segs)} segment(s) with HLAE (without CS Demo Manager)…")
    log("$ " + cmd)
    subprocess.Popen(cmd, env=env)

    t0 = time.time()
    while not _running("cs2.exe"):
        if time.time() - t0 > START_TIMEOUT:
            _kill("HLAE.exe")
            raise RenderError("CS2 didn't start. If HLAE showed an error, close anti-cheat software "
                              "(e.g. disable the FACEIT driver and reboot) and try again.")
        time.sleep(2)
    log("CS2 started. It jumps to each segment and records it; don't use the game window.")

    seen = set()
    while _running("cs2.exe"):
        if time.time() - t0 > RUN_TIMEOUT:
            _kill("cs2.exe")
            raise RenderError("Recording took too long and was stopped.")
        for n in range(1, len(segs) + 1):
            if n not in seen and (out_dir / str(n) / "video.mp4").exists():
                seen.add(n)
                log(f"Recording segment {n}/{len(segs)}…")
        time.sleep(2)
    _kill("HLAE.exe")
    log("CS2 closed.")

    out = []
    for n, (_, _, hid, i) in enumerate(segs, 1):
        video = out_dir / str(n) / "video.mp4"
        if not video.is_file() or video.stat().st_size == 0:
            crashed = set(cs2.parent.glob("*.mdmp")) - dumps_before
            if crashed:
                raise RenderError(f"CS2 crashed during recording (crash dump: {next(iter(crashed)).name}). "
                                  "A CS2 update may have broken HLAE: update HLAE and try again.")
            raise RenderError(f"Segment {n} wasn't recorded ({video} is missing). CS2 may have closed early.")
        takes = sorted((out_dir / str(n)).glob("take*/audio.wav"))
        out.append(RenderedSegment(hid, i, _mux(video, takes[-1] if takes else None, out_dir / f"{n}.mp4", log)))
    for f in cfg_dir.glob("*.cfg"):
        f.unlink()
    return out
