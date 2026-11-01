"""Paths and default settings. Override any path with an environment variable."""
import os
import re
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


def find_hlae():
    home = Path.home()
    return _first_existing([
        os.environ.get("HLAE_PATH"),
        home / ".csdm" / "hlae" / "HLAE.exe",  # the copy CS Demo Manager installs
        Path(os.environ.get("LOCALAPPDATA", "")) / "HLAE" / "HLAE.exe",
        r"C:\Program Files\HLAE\HLAE.exe",
    ])


def _steam_libraries():
    steam = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        pass
    steam = steam or Path(r"C:\Program Files (x86)\Steam")
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    return libs


def find_cs2():
    rel = Path("steamapps", "common", "Counter-Strike Global Offensive", "game", "bin", "win64", "cs2.exe")
    return _first_existing([os.environ.get("CS2_PATH"), *(lib / rel for lib in _steam_libraries())])


def find_cs2_version() -> int | None:
    """Installed CS2 version, numbered like demos: PatchVersion=1.41.9.4 -> 14194."""
    cs2 = find_cs2()
    if not cs2:
        return None
    try:
        text = (Path(cs2).parents[2] / "csgo" / "steam.inf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^PatchVersion=([\d.]+)", text, re.M)
    return int(m.group(1).replace(".", "")) if m else None


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
