"""Live mode: the static site plus /api/runs, an SSE tail of events.jsonl, and the human approval gate."""
import asyncio
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import attest
from .ledger import run_info

RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
POLL_S = 0.5


class HumanDecision(BaseModel):
    action: str
    ids: list[str] = []
    note: str = ""
    round: int | None = None


def _read_from(path: Path, offset: int) -> tuple[str, int]:
    with open(path) as fh:
        fh.seek(offset)
        return fh.read(), fh.tell()


def create_app(runs_dir: Path, site_dir: Path | None = None) -> FastAPI:
    runs_dir = Path(runs_dir)
    app = FastAPI(title="fortyshots live")

    def run_dir(run_id: str) -> Path:
        d = runs_dir / run_id
        if not RUN_ID.match(run_id) or not (d / "events.jsonl").exists():
            raise HTTPException(404, f"no run {run_id}")
        return d

    @app.get("/api/runs")
    def runs():
        rows = [run_info(d) for d in sorted(runs_dir.iterdir()) if d.is_dir()] if runs_dir.exists() else []
        return [r for r in rows if r]

    @app.get("/api/network")
    def network():
        return {"network": attest.network_state()}

    @app.get("/api/runs/{run_id}/summary")
    def summary(run_id: str):
        p = run_dir(run_id) / "summary.json"
        if not p.exists():
            raise HTTPException(404, "run not finished")
        return JSONResponse(json.loads(p.read_text()))

    @app.get("/api/runs/{run_id}/pending")
    def pending(run_id: str):
        p = run_dir(run_id) / "pending.json"
        return JSONResponse(json.loads(p.read_text()) if p.exists() else {})

    @app.get("/api/runs/{run_id}/img/{name}")
    def image(run_id: str, name: str):
        p = run_dir(run_id) / "img" / name
        if not RUN_ID.match(name) or not p.exists():
            raise HTTPException(404, "no such image")
        return FileResponse(p, media_type="image/png")

    @app.post("/api/runs/{run_id}/human")
    def human(run_id: str, decision: HumanDecision):
        d = run_dir(run_id)
        if decision.action not in ("approve", "reject"):
            raise HTTPException(422, "action must be approve or reject")
        rnd = decision.round
        if rnd is None:
            p = d / "pending.json"
            if not p.exists():
                raise HTTPException(409, "no batch is waiting for approval")
            rnd = json.loads(p.read_text())["round"]
        entry = {"round": rnd, "action": decision.action, "ids": decision.ids, "note": decision.note[:500]}
        with open(d / "human.jsonl", "a") as fh:
            fh.write(json.dumps(entry) + "\n")
        return {"ok": True, **entry}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = -1):
        path = run_dir(run_id) / "events.jsonl"
        last = request.headers.get("last-event-id")
        if last and last.isdigit():
            after = max(after, int(last))

        async def tail():
            offset, buf = 0, ""
            while not await request.is_disconnected():
                chunk, offset = _read_from(path, offset)
                buf += chunk
                *lines, buf = buf.split("\n")
                done = False
                for line in lines:
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev["seq"] > after:
                        yield {"id": str(ev["seq"]), "event": "event", "data": line}
                    done = done or ev["kind"] == "summary"
                if done:
                    yield {"event": "end", "data": "{}"}
                    return
                await asyncio.sleep(POLL_S)

        return EventSourceResponse(tail(), ping=15)

    if runs_dir.exists():
        app.mount("/data/runs", StaticFiles(directory=runs_dir), name="runs")
    if site_dir and Path(site_dir).exists():
        app.mount("/", StaticFiles(directory=site_dir, html=True), name="site")
    return app


def serve(runs_dir: Path, site_dir: Path | None, port: int = 7777):
    import uvicorn

    if site_dir and not Path(site_dir).exists():
        print(f"note: site dir {site_dir} not found; serving the API only")
    uvicorn.run(create_app(runs_dir, site_dir), host="127.0.0.1", port=port, log_level="warning")
