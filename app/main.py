"""Local web app: python -m uvicorn app.main:app  (or run.bat)"""
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (DEMOS_DIR, OUTPUT_DIR, find_cs2, find_cs2_version, find_csdm, find_ffmpeg, find_ffprobe,
                     find_font, find_hlae)
from .highlights import MODES, Highlight, Options, detect_highlights, miss_weapons
from .jobs import JobManager
from .parser import MatchData, demo_summary, parse_demo
from .render import RenderSettings, demo_warning

STATIC = Path(__file__).parent / "static"
INDEX = DEMOS_DIR / "index.json"

app = FastAPI(title="CS2 Highlights")
jobs = JobManager()
_matches: dict[str, MatchData] = {}
_lock = threading.Lock()


# ---- demo registry -------------------------------------------------------------------------

def _load_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_index(idx: dict) -> None:
    INDEX.write_text(json.dumps(idx, indent=2), encoding="utf-8")


def _register(path: Path, name: str) -> str:
    with _lock:
        idx = _load_index()
        for demo_id, d in idx.items():
            if Path(d["path"]) == path:
                return demo_id
        demo_id = uuid.uuid4().hex[:10]
        idx[demo_id] = {"path": str(path), "name": name, "added": time.time()}
        _save_index(idx)
        return demo_id


def _is_upload(path: Path) -> bool:
    """Copies made by the upload button live in DEMOS_DIR; demos added by path are the user's own files."""
    return path.resolve().parent == DEMOS_DIR.resolve()


_summarizing = threading.Event()  # set while the background thread fills in demo summaries


def _fill_summaries() -> None:
    try:
        while True:
            todo = [(k, Path(v["path"])) for k, v in _load_index().items()
                    if "summary" not in v and Path(v["path"]).is_file()]
            if not todo:
                return
            demo_id, path = todo[0]
            try:
                summary = demo_summary(path)
            except Exception as e:  # unreadable demo: remember that instead of retrying forever
                summary = {"error": str(e)}
            with _lock:
                idx = _load_index()
                if demo_id in idx:
                    idx[demo_id]["summary"] = summary
                    _save_index(idx)
    finally:
        _summarizing.clear()


def _start_summaries() -> None:
    with _lock:
        if _summarizing.is_set():
            return
        _summarizing.set()
    threading.Thread(target=_fill_summaries, daemon=True).start()


def _demo_path(demo_id: str) -> Path:
    d = _load_index().get(demo_id)
    if not d:
        raise HTTPException(404, "Unknown demo")
    p = Path(d["path"])
    if not p.is_file():
        raise HTTPException(410, f"Demo file is gone: {p}")
    return p


async def _match(demo_id: str) -> MatchData:
    if demo_id not in _matches:
        path = _demo_path(demo_id)
        try:
            _matches[demo_id] = await run_in_threadpool(parse_demo, path)
        except Exception as e:  # demoparser2 raises plain Exceptions, e.g. "EntityNotFound"
            raise HTTPException(422, f"Couldn't read this demo ({e}). It may be from a newer CS2 update than "
                                     "the demo parser supports: restart run.bat to update it, then try again.")
    return _matches[demo_id]


def _highlight_dict(h: Highlight) -> dict:
    return {
        "id": h.id, "round": h.round, "score": h.score, "label": h.label, "tags": h.tags,
        "clutch": h.clutch, "kills": len(h.kills), "headshots": sum(k.headshot for k in h.kills),
        "duration_s": round(h.duration_s, 1), "selected": h.selected,
        "segments": [[s.start_tick, s.end_tick] for s in h.segments],
        "died": h.death_tick is not None, "until_death": h.until_death, "until_round_end": h.until_round_end,
        "misses": h.misses,
    }


def _mode(mode: str) -> str:
    if mode not in MODES:
        raise HTTPException(400, f"Unknown mode {mode!r}")
    return mode


def _ids(csv: str) -> set[str]:
    return {s for s in (p.strip() for p in csv.split(",")) if s}


# ---- API -----------------------------------------------------------------------------------

@app.get("/api/status")
def status():
    return {"mode": os.environ.get("CS2HL_RENDER_MODE", "hlae"), "csdm": find_csdm(), "hlae": find_hlae(),
            "cs2": find_cs2(), "ffmpeg": find_ffmpeg(), "ffprobe": find_ffprobe(), "font": find_font()}


@app.get("/api/demos")
def list_demos():
    """Every registered demo, newest first. Summaries (map, score, source) are filled in by a
    background thread; "pending" demos don't have one yet, so the page asks again shortly."""
    out = []
    for k, v in _load_index().items():
        p = Path(v["path"])
        st = p.stat() if p.is_file() else None
        out.append({"id": k, "name": v["name"], "path": v["path"], "exists": st is not None,
                    "size": st.st_size if st else None, "added": v.get("added") or (st.st_mtime if st else 0),
                    "summary": v.get("summary"), "pending": st is not None and "summary" not in v,
                    "upload": _is_upload(p)})
    if any(d["pending"] for d in out):
        _start_summaries()
    return sorted(out, key=lambda d: -d["added"])


@app.delete("/api/demos/{demo_id}")
def delete_demo(demo_id: str):
    """Removes the demo from the list. Deletes the file only if it's a copy made by the upload button."""
    with _lock:
        idx = _load_index()
        d = idx.pop(demo_id, None)
        if d is None:
            raise HTTPException(404, "Unknown demo")
        _save_index(idx)
    _matches.pop(demo_id, None)
    p = Path(d["path"])
    deleted = _is_upload(p) and p.is_file()
    if deleted:
        p.unlink()
    return {"deleted_file": deleted}


@app.post("/api/demos/upload")
async def upload_demo(file: UploadFile):
    if not file.filename.lower().endswith(".dem"):
        raise HTTPException(400, "Please choose a .dem file")
    dest = DEMOS_DIR / f"{uuid.uuid4().hex[:10]}_{Path(file.filename).name}"
    with dest.open("wb") as f:
        await run_in_threadpool(shutil.copyfileobj, file.file, f, 4 * 1024 * 1024)
    demo_id = _register(dest, file.filename)
    return await get_demo(demo_id)


class PathIn(BaseModel):
    path: str


@app.post("/api/demos/path")
async def add_demo_path(body: PathIn):
    p = Path(body.path.strip().strip('"'))
    if p.suffix.lower() != ".dem" or not p.is_file():
        raise HTTPException(400, "That isn't an existing .dem file")
    demo_id = _register(p.resolve(), p.name)
    return await get_demo(demo_id)


@app.get("/api/demos/{demo_id}")
async def get_demo(demo_id: str):
    m = await _match(demo_id)
    return {
        "id": demo_id,
        "name": _load_index()[demo_id]["name"],
        "map": m.map_name,
        "rounds": len(m.rounds),
        "warning": demo_warning(m.demo_version, find_cs2_version()),
        "players": [asdict(p) | {"score": m.score_for(p.steamid), "kd": m.kd_for(p.steamid)} for p in m.players],
    }


@app.get("/api/demos/{demo_id}/highlights")
async def get_highlights(demo_id: str, steamid: str, max_clips: int = 8, pre_s: float = 4, post_s: float = 2,
                         mvp_s: float = 10, until_death: str = "", until_round_end: str = "",
                         mode: str = "best", weapons: str = ""):
    m = await _match(demo_id)
    if not m.player(steamid):
        raise HTTPException(404, "Player not in this demo")
    hs = detect_highlights(m, steamid, Options(max_clips=max_clips, pre_s=pre_s, post_s=post_s, mvp_s=mvp_s),
                           until_death=_ids(until_death), until_round_end=_ids(until_round_end),
                           mode=_mode(mode), weapons=_ids(weapons))
    return [_highlight_dict(h) for h in hs]


@app.get("/api/demos/{demo_id}/weapons")
async def get_weapons(demo_id: str, steamid: str):
    """The player's guns with how many bursts they fired and missed, for the missed-shots picker."""
    m = await _match(demo_id)
    if not m.player(steamid):
        raise HTTPException(404, "Player not in this demo")
    return miss_weapons(m, steamid)


class JobIn(BaseModel):
    demo_id: str
    steamid: str
    highlight_ids: list[str]
    pre_s: float = 4
    post_s: float = 2
    mvp_s: float = 10
    until_death: list[str] = []  # highlight ids recorded until the player's death
    until_round_end: list[str] = []  # highlight ids recorded until the round ends
    mode: str = "best"  # see highlights.MODES
    weapons: list[str] = []  # misses mode: weapon_fire names of the guns to include (empty = all)
    killfeed_all: bool = True  # every kill in the kill feed, not only the player's
    width: int = 1280
    height: int = 720
    framerate: int = 60


@app.post("/api/jobs")
async def create_job(body: JobIn):
    m = await _match(body.demo_id)
    player = m.player(body.steamid)
    if not player:
        raise HTTPException(404, "Player not in this demo")
    wanted = set(body.highlight_ids)
    opts = Options(pre_s=body.pre_s, post_s=body.post_s, mvp_s=body.mvp_s)
    hs = [h for h in detect_highlights(m, body.steamid, opts, until_death=set(body.until_death),
                                       until_round_end=set(body.until_round_end), mode=_mode(body.mode),
                                       weapons=set(body.weapons))
          if h.id in wanted]
    if not hs:
        raise HTTPException(400, "Pick at least one highlight")
    job = jobs.submit(
        demo_path=str(_demo_path(body.demo_id)), match=m, steamid=player.steamid,
        player_name=player.name, highlights=hs, mode=body.mode, weapons=sorted(body.weapons),
        settings=RenderSettings(width=body.width, height=body.height, framerate=body.framerate,
                                killfeed_all=body.killfeed_all),
    )
    return job.to_dict()


@app.get("/api/jobs")
def list_jobs():
    return [j.to_dict(log_tail=0) for j in sorted(jobs.jobs.values(), key=lambda j: -j.created)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: str):
    job = jobs.jobs.get(job_id)
    if not job or not job.output or not Path(job.output).is_file():
        raise HTTPException(404, "No video yet")
    return FileResponse(job.output, media_type="video/mp4", filename=Path(job.output).name)


# ---- finished videos (data/output), listed from disk so they outlive a server restart ---------------

def _video_info_from_name(stem: str) -> dict:
    """Guess for videos made before the .json sidecar: <map>_<player>[_<mode>]_<job id>."""
    parts = stem.split("_")
    map_name, rest = ("_".join(parts[:2]), parts[2:-1]) if len(parts) >= 4 else (parts[0], parts[1:-1])
    player = "_".join(rest)
    mode = next((m for m in (*MODES, "awp_miss") if m != "best" and player.endswith("_" + m)), "best")
    return {"map": map_name, "player": player[: -len(mode) - 1] if mode != "best" else player, "mode": mode,
            "job": parts[-1]}


def _video_info(p: Path) -> dict:
    """Name, size, date, and who/what it shows: from the .json the job writes next to the video, or
    guessed from the file name for older videos."""
    st = p.stat()
    info = {"name": p.name, "size": st.st_size, "mtime": st.st_mtime} | _video_info_from_name(p.stem)
    try:
        info |= json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return info


@app.get("/api/videos")
def list_videos():
    return sorted((_video_info(p) for p in OUTPUT_DIR.glob("*.mp4")), key=lambda v: -v["mtime"])


@app.get("/api/videos/{name}")
def get_saved_video(name: str):
    p = OUTPUT_DIR / name
    if Path(name).name != name or p.suffix.lower() != ".mp4" or not p.is_file():
        raise HTTPException(404, "No such video")
    return FileResponse(p, media_type="video/mp4", filename=name)


# Without Cache-Control, browsers keep using an old app.js after an update and new UI doesn't show.
NO_CACHE = {"Cache-Control": "no-cache"}  # still served from cache, but revalidated (ETag) every time


class FreshStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(NO_CACHE)
        return response


@app.get("/")
def index():
    # Version the asset URLs too: a normal reload refetches the page but reuses cached scripts, so a
    # copy cached before NO_CACHE existed would otherwise stay until the browser expires it.
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={(STATIC / name).stat().st_mtime_ns}")
    return HTMLResponse(html, headers=NO_CACHE)


app.mount("/static", FreshStaticFiles(directory=STATIC), name="static")
