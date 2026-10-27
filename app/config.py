"""Paths and default settings. Override any path with an environment variable."""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CS2HL_DATA", ROOT / "data"))
DEMOS_DIR = DATA_DIR / "demos"
RENDERS_DIR = DATA_DIR / "renders"
OUTPUT_DIR = DATA_DIR / "output"

TICKRATE = 64  # CS2 demos: 64 ticks per second


def _first_existing(candidates):
    for c in candidates:
        if c and Path(c).is_file():
            return str(Path(c))
    return None


def find_csdm():
    local = os.environ.get("LOCALAPPDATA", "")
    return _first_existing([
        os.environ.get("CSDM_PATH"),
        shutil.which("csdm"),
        os.path.join(local, "Programs", "cs-demo-manager", "csdm.exe"),
        os.path.join(local, "Programs", "CS Demo Manager", "csdm.exe"),
        os.path.join(os.environ.get("APPDATA", ""), "Programs", "CS Demo Manager", "csdm.exe"),
        r"C:\Program Files\CS Demo Manager\csdm.exe",
    ])


def find_ffmpeg():
    return _first_existing([os.environ.get("FFMPEG_PATH"), shutil.which("ffmpeg")])


def find_ffprobe():
    ffmpeg = find_ffmpeg()
    sibling = str(Path(ffmpeg).with_name("ffprobe.exe")) if ffmpeg else None
    return _first_existing([os.environ.get("FFPROBE_PATH"), sibling, shutil.which("ffprobe")])


def find_font():
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    return _first_existing([
        os.environ.get("CS2HL_FONT"),
        fonts / "bahnschrift.ttf",
        fonts / "arialbd.ttf",
        fonts / "arial.ttf",
    ])


# Render defaults. 720p keeps recording time reasonable on integrated graphics.
DEFAULT_RENDER = {
    "width": 1280,
    "height": 720,
    "framerate": 60,
    "crf": 18,
}

for d in (DEMOS_DIR, RENDERS_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)
