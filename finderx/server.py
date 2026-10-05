"""HTTP API + live event stream for the terminal UI."""

from __future__ import annotations

import asyncio
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, net, report
from .engines import STAGES
from .jobs import JobManager

_health_cache: dict = {"at": 0.0, "data": None}


def create_app(manager: JobManager | None = None) -> FastAPI:
    app = FastAPI(title="finderX", version=__version__, docs_url="/api/docs")
    m = manager or JobManager(reap=True)
    app.state.jobs = m
    started = time.time()

    app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        now = time.time()
        if _health_cache["data"] is None or now - _health_cache["at"] > 30:
            with ThreadPoolExecutor(len(config.LINKS)) as ex:
                res = dict(zip(config.LINKS, ex.map(net.ping, config.LINKS.values())))
            _health_cache["data"] = {k: {"ok": ok, "ms": round(ms)} for k, (ok, ms) in res.items()}
            _health_cache["at"] = now
        return {"version": __version__, "uptime_s": round(now - started), "links": _health_cache["data"], "stages": STAGES}

    @app.get("/api/stream")
    async def stream():
        q = m.bus.subscribe()

        async def gen():
            try:
                yield _sse({"type": "hello", "recent": m.bus.recent(), "running": m.running()})
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield _sse(ev)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                m.bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/scan")
    def scan(body: dict = Body(...)):
        engine = body.get("engine")
        params = body.get("params") or {}
        if engine == "patrol":
            engine, params = "transit", {**params, "patrol": True}
        if params.get("name"):
            r = net.resolve(params.pop("name"))
            if not r:
                raise HTTPException(404, "name not resolved")
            params.update(ra=r["ra"], dec=r["dec"])
        try:
            jid = m.submit(engine, params)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"job": jid, "engine": engine, "params": params}

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        return {"ok": m.cancel(jid)}

    @app.get("/api/jobs")
    def jobs():
        return {"running": m.running(), "recent": m.db.jobs(40)}

    @app.get("/api/jobs/{jid}/results")
    def job_results(jid: str):
        return {"job": jid, "results": m.db.results(jid)}

    @app.get("/api/candidates")
    def candidates(status: str | None = None, engine: str | None = None, kind: str | None = None, limit: int = 400, include_known: bool = True, order: str = "score"):
        rows = m.db.candidates(status=status, engine=engine, kind=kind, limit=limit, order=order)
        if not include_known:
            rows = [r for r in rows if not r["kind"].startswith("known_")]
        return {"candidates": rows}

    @app.get("/api/candidates/{cid}")
    def candidate(cid: str):
        c = m.db.candidate(cid)
        if not c:
            raise HTTPException(404, "no such candidate")
        c["route"] = report.route(c["kind"])
        return c

    @app.post("/api/candidates/{cid}/vote")
    def vote(cid: str, body: dict = Body(...)):
        try:
            ok = m.db.vote(cid, body.get("status", ""), body.get("note"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not ok:
            raise HTTPException(404, "no such candidate")
        m.bus.publish({"type": "vote", "id": cid, "status": body.get("status")})
        return {"ok": True}

    @app.get("/api/candidates/{cid}/report", response_class=PlainTextResponse)
    def candidate_report(cid: str):
        c = m.db.candidate(cid)
        if not c:
            raise HTTPException(404, "no such candidate")
        return report.text(c)

    @app.get("/api/export")
    def export(status: str = "confirmed", format: str = "csv"):
        rows = m.db.candidates(status=status, limit=100000)
        if format == "json":
            return Response(json.dumps(rows, default=str, indent=1), media_type="application/json",
                            headers={"Content-Disposition": "attachment; filename=finderx-discoveries.json"})
        return Response(report.to_csv(rows), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=finderx-discoveries.csv"})

    @app.get("/api/stats")
    def stats():
        return m.db.stats()

    @app.get("/api/resolve")
    def resolve(q: str):
        tic = re.fullmatch(r"\s*(?:TIC)?\s*(\d{4,12})\s*", q, re.I)
        if tic:
            return {"tic": int(tic.group(1))}
        coords = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)[\s,]+([-+]?\d+(?:\.\d+)?)\s*", q)
        if coords:
            return {"ra": float(coords.group(1)), "dec": float(coords.group(2)), "name": q.strip()}
        try:
            r = net.resolve(q)
        except net.ArchiveError as exc:
            raise HTTPException(502, str(exc))
        if not r:
            raise HTTPException(404, f"'{q}' not found")
        return r

    return app


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str, separators=(',', ':'))}\n\n"
