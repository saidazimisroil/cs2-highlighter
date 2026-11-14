import subprocess

import pytest

from app.compile import join
from app.config import find_ffmpeg, find_ffprobe

pytestmark = pytest.mark.skipif(not (find_ffmpeg() and find_ffprobe()), reason="needs FFmpeg")


def clip(path, seconds=1.5):
    subprocess.run([find_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)
    return path


def test_joined_video_plays_in_browsers(tmp_path):
    # xfade made libx264 write yuv444p (High 4:4:4), which Chrome shows as a blank 0:00 player.
    out = tmp_path / "out.mp4"
    join([clip(tmp_path / "a.mp4"), clip(tmp_path / "b.mp4")], out, 23, lambda _: None)
    pix_fmt = subprocess.run([find_ffprobe(), "-v", "error", "-select_streams", "v", "-show_entries",
                              "stream=pix_fmt", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert pix_fmt.strip() == "yuv420p"
