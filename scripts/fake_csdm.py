"""Stand-in for csdm.exe used to test the pipeline without CS2: writes one test-pattern clip per
sequence of a `video --config-file` run. Point CSDM_PATH at scripts/fake_csdm.bat to use it."""
import json
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
cfg = json.loads(Path(args[args.index("--config-file") + 1]).read_text(encoding="utf-8"))
out = Path(cfg["outputFolderPath"])
out.mkdir(parents=True, exist_ok=True)
w, h, fps = cfg["width"], cfg["height"], cfg["framerate"]
for seq in cfg["sequences"]:
    secs = (seq["endTick"] - seq["startTick"]) / 64
    print(f"fake csdm: sequence {seq['number']} ({secs:.1f}s)", flush=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={fps}:d={secs}",
                    "-f", "lavfi", "-i", f"sine=f={300 + 40 * seq['number']}:d={secs}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(out / f"sequence-{seq['number']}.mp4")], check=True)
