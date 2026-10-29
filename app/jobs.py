"""A single background worker that records and compiles one video at a time (CS2 runs once)."""
import queue
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .compile import ClipSpec, compile_video
from .config import OUTPUT_DIR, RENDERS_DIR
from .highlights import Highlight
from .parser import MatchData
from .render import RenderSettings, render

MAX_LOG = 3000


@dataclass
class Job:
    id: str
    demo_path: str
    match: MatchData
    steamid: str
    player_name: str
    highlights: list[Highlight]
    settings: RenderSettings
    status: str = "queued"  # queued | recording | compiling | done | error
    log: list[str] = field(default_factory=list)
    output: str | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None

    def add_log(self, line: str) -> None:
        self.log.append(line)
        if len(self.log) > MAX_LOG:
            del self.log[: len(self.log) - MAX_LOG]

    def to_dict(self, log_tail: int = 400) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "player": self.player_name,
            "map": self.match.map_name,
            "clips": [h.label for h in self.highlights],
            "segments": sum(len(h.segments) for h in self.highlights),
            "log": self.log[-log_tail:],
            "output": self.output,
            "error": self.error,
            "created": self.created,
            "finished": self.finished,
        }


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "player"


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._queue: queue.Queue[Job] = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, **kwargs) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], **kwargs)
        self.jobs[job.id] = job
        self._queue.put(job)
        return job

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._run(job)
                job.status = "done"
            except Exception as e:  # report every failure in the UI
                job.status = "error"
                job.error = str(e)
                job.add_log("ERROR: " + str(e))
                job.add_log(traceback.format_exc())
            finally:
                job.finished = time.time()

    def _run(self, job: Job) -> None:
        work = RENDERS_DIR / job.id
        s = job.settings
        job.status = "recording"
        job.add_log("Starting CS2. Don't use the game window while it records.")
        segments = render(job.demo_path, job.steamid, job.player_name, job.highlights,
                          work / "raw", s, job.add_log)

        job.status = "compiling"
        by_highlight: dict[str, list[Path]] = {}
        for seg in sorted(segments, key=lambda r: r.index):
            by_highlight.setdefault(seg.highlight_id, []).append(seg.path)
        clips = [ClipSpec(h.label, by_highlight[h.id]) for h in job.highlights if h.id in by_highlight]
        won, lost = job.match.score_for(job.steamid)
        out = OUTPUT_DIR / f"{job.match.map_name}_{_safe(job.player_name)}_{job.id}.mp4"
        compile_video(job.player_name, f"{job.match.map_name} · {won}–{lost}", clips, out,
                      work / "compile", s.width, s.height, s.framerate, s.crf, job.add_log)
        job.output = str(out)
