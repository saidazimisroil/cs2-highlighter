"""Records highlight segments as in-game footage (CS2 + HLAE).

Three modes (env CS2HL_RENDER_MODE):
  "hlae"     (default) drive HLAE directly with tick-scheduled commands (see hlae.py). Doesn't
             need CS Demo Manager's CS2 plugin, which game updates tend to break.
  "config"   one `csdm video --config-file` run with every segment as a sequence,
             so CS2 starts only once.
  "per-clip" one documented `csdm video <demo> <start> <end>` run per segment. Slower (CS2
             restarts each time) but every file lands in its own folder.
"""
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import find_csdm, find_ffmpeg
from .highlights import Highlight

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov"}
Log = Callable[[str], None]

# csdm can exit 0 even when CS2 crashed, so its output is checked for this line too.
GAME_ERROR = re.compile(r"^\s*game error\b", re.IGNORECASE)
GAME_ERROR_HINT = ("CS2 crashed or closed during recording (CS Demo Manager reported \"Game error\"). "
                   "This usually means a CS2 update broke HLAE or CS Demo Manager's plugin: update "
                   "both in CS Demo Manager's settings, record a short test video there, then try again.")


@dataclass
class RenderSettings:
    width: int = 1280
    height: int = 720
    framerate: int = 60
    crf: int = 18


@dataclass
class RenderedSegment:
    highlight_id: str
    index: int  # segment index within the highlight
    path: Path


class RenderError(RuntimeError):
    pass


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def _videos_in(folder: Path) -> list[Path]:
    return sorted((p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXTS), key=_natural_key)


def _ffmpeg_settings(settings: RenderSettings) -> dict:
    ffmpeg = find_ffmpeg()
    return {
        "audioBitrate": 256,
        "constantRateFactor": settings.crf,
        "customLocationEnabled": bool(ffmpeg),
        "customExecutableLocation": ffmpeg or "",
        "videoContainer": "mp4",
        "videoCodec": "libx264",
        "audioCodec": "aac",
        "inputParameters": "",
        "outputParameters": "",
    }


def build_config(demo_path: str, steamid: str, player_name: str, highlights: list[Highlight],
                 out_dir: Path, settings: RenderSettings) -> tuple[dict, list[tuple[str, int]]]:
    """CS Demo Manager video config + the (highlight id, segment index) of each sequence, in order."""
    sequences, order = [], []
    for h in highlights:
        for i, seg in enumerate(h.segments):
            sequences.append({
                "number": len(sequences) + 1,
                "startTick": seg.start_tick,
                "endTick": seg.end_tick,
                "showOnlyDeathNotices": True,
                "deathNoticesDuration": 5,
                "playersOptions": [{
                    "steamId": steamid,
                    "playerName": player_name,
                    "showKill": True,
                    "highlightKill": True,
                    "isVoiceEnabled": False,
                }],
                "playerCameras": [{"tick": seg.start_tick, "playerSteamId": steamid, "playerName": player_name}],
                "cameras": [],
                "showXRay": False,
                "showAssists": True,
                "playerVoicesEnabled": False,
                "recordAudio": True,
            })
            order.append((h.id, i))
    config = {
        "demoPath": str(demo_path),
        "outputFolderPath": str(out_dir),
        "recordingSystem": "HLAE",
        "recordingOutput": "video",
        "encoderSoftware": "FFmpeg",
        "width": settings.width,
        "height": settings.height,
        "framerate": settings.framerate,
        "closeGameAfterRecording": True,
        "concatenateSequences": False,
        "ffmpegSettings": _ffmpeg_settings(settings),
        "sequences": sequences,
    }
    return config, order


def _run(cmd: list[str], log: Log) -> None:
    log("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags)
    game_error = False
    for line in proc.stdout:
        if line.strip():
            log(line.rstrip())
            game_error = game_error or bool(GAME_ERROR.match(line))
    code = proc.wait()
    if game_error:
        raise RenderError(GAME_ERROR_HINT)
    if code != 0:
        raise RenderError(f"csdm exited with code {code} — see the log above.")


def _csdm() -> str:
    csdm = find_csdm()
    if not csdm:
        raise RenderError("csdm.exe not found. Install CS Demo Manager or set CSDM_PATH (see README).")
    return csdm


def render_with_config(demo_path, steamid, player_name, highlights, out_dir: Path,
                       settings: RenderSettings, log: Log) -> list[RenderedSegment]:
    out_dir.mkdir(parents=True, exist_ok=True)
    config, order = build_config(demo_path, steamid, player_name, highlights, out_dir, settings)
    cfg_path = out_dir.parent / "csdm-config.json"
    cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    log(f"Recording {len(order)} segment(s) in one CS2 session…")
    _run([_csdm(), "video", "--config-file", str(cfg_path)], log)

    files = _videos_in(out_dir)
    if len(files) != len(order):
        names = ", ".join(f.name for f in files) or "none"
        raise RenderError(
            f"Expected {len(order)} video files from CS Demo Manager but found {len(files)} ({names}). "
            "Try CS2HL_RENDER_MODE=per-clip.")
    return [RenderedSegment(hid, i, f) for (hid, i), f in zip(order, files)]


def render_per_clip(demo_path, steamid, player_name, highlights, out_dir: Path,
                    settings: RenderSettings, log: Log) -> list[RenderedSegment]:
    out: list[RenderedSegment] = []
    total = sum(len(h.segments) for h in highlights)
    n = 0
    ffmpeg = find_ffmpeg()
    for h in highlights:
        for i, seg in enumerate(h.segments):
            n += 1
            folder = out_dir / f"{h.id}-{i}"
            folder.mkdir(parents=True, exist_ok=True)
            log(f"Recording segment {n}/{total} ({h.label})…")
            cmd = [_csdm(), "video", str(demo_path), str(seg.start_tick), str(seg.end_tick),
                   "--output", str(folder), "--focus-player", steamid,
                   "--recording-system", "HLAE", "--recording-output", "video",
                   "--encoder-software", "FFmpeg",
                   "--width", str(settings.width), "--height", str(settings.height),
                   "--framerate", str(settings.framerate),
                   "--ffmpeg-crf", str(settings.crf), "--ffmpeg-video-codec", "libx264",
                   "--ffmpeg-audio-codec", "aac", "--ffmpeg-video-container", "mp4",
                   "--show-only-death-notices", "--no-show-x-ray", "--record-audio",
                   "--no-player-voices", "--close-game-after-recording"]
            if ffmpeg:
                cmd += ["--ffmpeg-executable-path", ffmpeg]
            _run(cmd, log)
            files = _videos_in(folder)
            if not files:
                raise RenderError(f"No video file was produced for {h.label}.")
            out.append(RenderedSegment(h.id, i, files[-1]))
    return out


def render(demo_path, steamid, player_name, highlights, out_dir: Path,
           settings: RenderSettings, log: Log) -> list[RenderedSegment]:
    mode = os.environ.get("CS2HL_RENDER_MODE", "hlae")
    if mode == "hlae":
        from .hlae import render_hlae  # hlae imports from this module
        fn = render_hlae
    else:
        fn = render_per_clip if mode == "per-clip" else render_with_config
    return fn(demo_path, steamid, player_name, highlights, out_dir, settings, log)
