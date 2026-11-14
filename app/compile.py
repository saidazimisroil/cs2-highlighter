"""Builds the final 16:9 compilation with FFmpeg: title card, per-clip labels, crossfades."""
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import find_ffmpeg, find_ffprobe, find_font

Log = Callable[[str], None]
ACCENT = "0xF5A524"
TITLE_S = 3.0
FADE_S = 0.5
LABEL_IN, LABEL_OUT = 0.3, 3.3


@dataclass
class ClipSpec:
    label: str
    segments: list[Path]
    captions: list[str] = field(default_factory=list)  # per segment, shown on top the whole time; "" = none


class CompileError(RuntimeError):
    pass


def _ff_path(p) -> str:
    """Path quoted for use inside an FFmpeg filter option (Windows drive colons escaped)."""
    return "'" + str(p).replace("\\", "/").replace(":", "\\:") + "'"


def _run(args: list[str], log: Log) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise CompileError("ffmpeg not found on PATH (or set FFMPEG_PATH).")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    if proc.returncode != 0:
        log(proc.stderr.strip()[-2000:])
        raise CompileError(f"ffmpeg failed (exit {proc.returncode}).")


def probe(path: Path) -> tuple[float, bool]:
    """(duration in seconds, has an audio stream)"""
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise CompileError("ffprobe not found next to ffmpeg (or set FFPROBE_PATH).")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                          "-of", "json", str(path)], capture_output=True, text=True, creationflags=flags)
    info = json.loads(out.stdout or "{}")
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return duration, has_audio


def _norm_v(w: int, h: int, fps: int) -> str:
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},format=yuv420p,settb=AVTB")


NORM_A = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asettb=AVTB"
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "192k"]


def _text(work: Path, name: str, text: str) -> str:
    f = work / f"{name}.txt"
    f.write_text(text, encoding="utf-8")
    return _ff_path(f)


def _fade_alpha(t_in: float, t_out: float, f: float = 0.3) -> str:
    return (f"'if(lt(t,{t_in}),0,if(lt(t,{t_in + f}),(t-{t_in})/{f},"
            f"if(lt(t,{t_out - f}),1,if(lt(t,{t_out}),({t_out}-t)/{f},0))))'")


def make_title(out: Path, work: Path, title: str, subtitle: str, w: int, h: int, fps: int, log: Log,
               kind: str = "HIGHLIGHTS") -> None:
    font = _ff_path(find_font())
    big, mid, small = h // 9, h // 22, h // 34
    vf = ",".join([
        f"drawtext=fontfile={font}:textfile={_text(work, 'title', title)}:expansion=none:fontcolor=white:"
        f"fontsize={big}:x=(w-tw)/2:y=(h-th)/2-{h // 14}",
        f"drawbox=x=(iw-{w // 8})/2:y=ih/2+{h // 40}:w={w // 8}:h={max(3, h // 180)}:color={ACCENT}:t=fill",
        f"drawtext=fontfile={font}:textfile={_text(work, 'subtitle', subtitle)}:expansion=none:"
        f"fontcolor={ACCENT}:fontsize={mid}:x=(w-tw)/2:y=h/2+{h // 16}",
        f"drawtext=fontfile={font}:textfile={_text(work, 'kind', kind)}:expansion=none:fontcolor=white@0.55:fontsize={small}:"
        f"x=(w-tw)/2:y=h/2+{h // 16 + mid + h // 40}",
        f"fade=t=in:d=0.5,fade=t=out:st={TITLE_S - 0.5}:d=0.5",
    ])
    _run(["-f", "lavfi", "-i", f"color=c=0x0D0F14:s={w}x{h}:r={fps}:d={TITLE_S}",
          "-f", "lavfi", "-t", str(TITLE_S), "-i", "anullsrc=r=48000:cl=stereo",
          "-vf", vf + ",format=yuv420p,settb=AVTB", "-af", NORM_A, "-shortest", *ENC, str(out)], log)


def make_clip(clip: ClipSpec, out: Path, work: Path, name: str, w: int, h: int, fps: int, log: Log) -> None:
    """Joins a highlight's segments (hard cuts) and adds its lower-third label."""
    args, chains, pairs = [], [], []
    idx = 0
    font = _ff_path(find_font())
    for k, seg in enumerate(clip.segments):
        duration, has_audio = probe(seg)
        args += ["-i", str(seg)]
        v_in, a_in = f"{idx}:v", f"{idx}:a"
        idx += 1
        if not has_audio:
            args += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            a_in = f"{idx}:a"
            idx += 1
        n = len(pairs)
        caption = clip.captions[k] if k < len(clip.captions) else ""
        draw = ""
        if caption:
            draw = (f",drawtext=fontfile={font}:textfile={_text(work, f'{name}-cap{k}', caption)}:expansion=none:"
                    f"fontcolor=white:fontsize={h // 30}:x=(w-tw)/2:y={h // 14}:box=1:boxcolor=black@0.55:"
                    f"boxborderw={h // 80}")
        chains.append(f"[{v_in}]{_norm_v(w, h, fps)}{draw}[v{n}]")
        chains.append(f"[{a_in}]{NORM_A}[a{n}]")
        pairs.append(f"[v{n}][a{n}]")

    size = h // 24
    pad = h // 50
    y = f"h-{h // 6}"
    alpha = _fade_alpha(LABEL_IN, LABEL_OUT)
    bar = max(4, h // 120)
    label = (
        f"drawtext=fontfile={font}:textfile={_text(work, name, clip.label)}:expansion=none:fontcolor=white:"
        f"fontsize={size}:x={w // 22}:y={y}:box=1:boxcolor=black@0.6:boxborderw={pad}:alpha={alpha},"
        f"drawbox=x={w // 22 - pad - bar}:y=ih-{h // 6 + pad}:w={bar}:h={size + 2 * pad}:"
        f"color={ACCENT}:t=fill:enable='between(t,{LABEL_IN + 0.15},{LABEL_OUT - 0.15})'"
    )
    chains.append(f"{''.join(pairs)}concat=n={len(pairs)}:v=1:a=1[cv][ca]")
    chains.append(f"[cv]{label}[vout]")
    _run([*args, "-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "[ca]", *ENC, str(out)], log)


def join(parts: list[Path], out: Path, crf: int, log: Log) -> None:
    """Crossfades all parts together and fades the end to black."""
    durations = [probe(p)[0] for p in parts]
    args = []
    for p in parts:
        args += ["-i", str(p)]
    chains = []
    v_prev, a_prev, offset = "0:v", "0:a", 0.0
    for i in range(1, len(parts)):
        offset += durations[i - 1] - FADE_S
        chains.append(f"[{v_prev}][{i}:v]xfade=transition=fade:duration={FADE_S}:offset={offset:.3f}[xv{i}]")
        chains.append(f"[{a_prev}][{i}:a]acrossfade=d={FADE_S}[xa{i}]")
        v_prev, a_prev = f"xv{i}", f"xa{i}"
    total = sum(durations) - FADE_S * (len(parts) - 1)
    # xfade/fade can hand libx264 yuv444p, which browsers (and most phones) can't play; force 4:2:0.
    chains.append(f"[{v_prev}]fade=t=out:st={max(0, total - 0.8):.3f}:d=0.8,format=yuv420p[vout]")
    chains.append(f"[{a_prev}]afade=t=out:st={max(0, total - 0.8):.3f}:d=0.8[aout]")
    _run([*args, "-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "[aout]",
          "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-c:a", "aac", "-b:a", "192k",
          "-movflags", "+faststart", str(out)], log)


def compile_video(title: str, subtitle: str, clips: list[ClipSpec], out: Path, work: Path,
                  w: int, h: int, fps: int, crf: int, log: Log, kind: str = "HIGHLIGHTS") -> Path:
    work.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    parts = [work / "00-title.mp4"]
    log("Making title card…")
    make_title(parts[0], work, title, subtitle, w, h, fps, log, kind)
    for i, clip in enumerate(clips, 1):
        log(f"Preparing clip {i}/{len(clips)}: {clip.label}")
        part = work / f"{i:02d}-clip.mp4"
        make_clip(clip, part, work, f"label{i:02d}", w, h, fps, log)
        parts.append(part)
    log("Joining everything with crossfades…")
    join(parts, out, crf, log)
    log(f"Done: {out}")
    return out
