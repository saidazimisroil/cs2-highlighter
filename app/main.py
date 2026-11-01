"""Local web app: python -m uvicorn app.main:app  (or run.bat)"""
import json
import os
import shutil
import threading
import uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (DEMOS_DIR, find_cs2, find_cs2_version, find_csdm, find_ffmpeg, find_ffprobe, find_font,
                     find_hlae)
from .highlights import Highlight, Options, detect_highlights
from .jobs import JobManager
from .parser import MatchData, parse_demo
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
        idx[demo_id] = {"path": str(path), "name": name}
        _save_index(idx)
        return demo_id


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
        _matches[demo_id] = await run_in_threadpool(parse_demo, _demo_path(demo_id))
    return _matches[demo_id]


def _highlight_dict(h: Highlight) -> dict:
    return {
        "id": h.id, "round": h.round, "score": h.score, "label": h.label, "tags": h.tags,
        "clutch": h.clutch, "kills": len(h.kills), "headshots": sum(k.headshot for k in h.kills),
        "duration_s": round(h.duration_s, 1), "selected": h.selected,
        "segments": [[s.start_tick, s.end_tick] for s in h.segments],
    }


# ---- API -----------------------------------------------------------------------------------

@app.get("/api/status")
def status():
    return {"mode": os.environ.get("CS2HL_RENDER_MODE", "hlae"), "csdm": find_csdm(), "hlae": find_hlae(),
            "cs2": find_cs2(), "ffmpeg": find_ffmpeg(), "ffprobe": find_ffprobe(), "font": find_font()}


@app.get("/api/demos")
def list_demos():
    return [{"id": k, **v, "exists": Path(v["path"]).is_file()} for k, v in _load_index().items()]


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
        "players":[asdict(p) | {"score": m.score_for(p.steamid)} for p in m.players],
    }


@app.get("/api/demos/{demo_id}/highlights")
async def get_highlights(demo_id: str, steamid: str, max_clips: int = 8, pre_s: float = 4, post_s: float = 2):
    m = await _match(demo_id)
    if not m.player(steamid):
        raise HTTPException(404, "Player not in this demo")
    hs = detect_highlights(m, steamid, Options(max_clips=max_clips, pre_s=pre_s, post_s=post_s))
    return [_highlight_dict(h) for h in hs]


class JobIn(BaseModel):
    demo_id: str
    steamid: str
    highlight_ids: list[str]
    pre_s: float = 4
    post_s: float = 2
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
    hs = [h for h in detect_highlights(m, body.steamid, Options(pre_s=body.pre_s, post_s=body.post_s))
          if h.id in wanted]
    if not hs:
        raise HTTPException(400, "Pick at least one highlight")
    job = jobs.submit(
        demo_path=str(_demo_path(body.demo_id)), match=m, steamid=player.steamid,
        player_name=player.name, highlights=hs,
        settings=RenderSettings(width=body.width, height=body.height, framerate=body.framerate),
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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
