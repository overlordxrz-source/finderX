"""Background jobs and the live event bus.

Engines run in worker threads and talk to the outside world only through a
``JobContext``: log lines, pipeline-stage changes, progress, candidates and
plain results. Every call is persisted (where it matters) and published on
the ``EventBus``, which fans events out to SSE subscribers and the CLI.
"""

from __future__ import annotations

import asyncio
import collections
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import db as dbmod


class EventBus:
    def __init__(self, history: int = 600):
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._sync_subs: list[Callable[[dict], None]] = []
        self._recent: collections.deque[dict] = collections.deque(maxlen=history)
        self._lock = threading.Lock()
        self._seq = 0

    def publish(self, event: dict) -> None:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "ts": time.time(), **event}
            if event.get("type") in ("log", "candidate", "job", "stage"):
                self._recent.append(event)
            subs = list(self._subs)
            sync = list(self._sync_subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, event)
            except RuntimeError:
                pass  # loop closed
        for fn in sync:
            try:
                fn(event)
            except Exception:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subs.append((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(lp, x) for lp, x in self._subs if x is not q]

    def on(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._sync_subs.append(fn)

    def recent(self) -> list[dict]:
        with self._lock:
            return list(self._recent)


def _offer(q: asyncio.Queue, ev: dict) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(ev)


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, manager: "JobManager", jid: str, engine: str, params: dict):
        self.m = manager
        self.id = jid
        self.engine = engine
        self.params = params
        self.db = manager.db
        self._cancel = threading.Event()
        self._result_idx = 0
        self.counters: dict[str, int] = collections.Counter()
        self.started = time.time()

    # signalling
    def emit(self, type_: str, **data: Any) -> None:
        self.m.bus.publish({"type": type_, "job": self.id, "engine": self.engine, **data})

    def log(self, msg: str, level: str = "info", src: str | None = None) -> None:
        self.emit("log", level=level, src=src or self.engine.upper(), msg=msg)

    def stage(self, name: str, state: str, target: str | None = None) -> None:
        self.emit("stage", stage=name, state=state, target=target)

    def progress(self, done: float, total: float | None = None, label: str | None = None) -> None:
        self.emit("progress", done=done, total=total, label=label)

    def count(self, key: str, inc: int = 1) -> None:
        self.counters[key] += inc
        self.emit("counter", key=key, value=self.counters[key])

    def candidate(self, cand: dict, payload: dict | None = None) -> str:
        cand = {**cand, "job": self.id}
        if payload is not None:
            pid = cand.get("payload") or f"{self.engine}:{cand['target']}"
            self.db.payload_put(pid, payload)
            cand["payload"] = pid
        cid, is_new = self.db.candidate_upsert(cand)
        if is_new:
            self.db.job_bump(self.id, candidates=1)
        self.count("candidates")
        self.emit(
            "candidate",
            id=cid,
            new=is_new,
            kind=cand["kind"],
            title=cand["title"],
            subtitle=cand.get("subtitle", ""),
            score=cand.get("score", 0),
            ra=cand.get("ra"),
            dec=cand.get("dec"),
            known=bool(cand.get("known")),
        )
        return cid

    def result(self, item: dict) -> None:
        self.db.result_add(self.id, self._result_idx, item)
        self._result_idx += 1
        self.emit("result", item=item)

    def examined(self, target: str) -> bool:
        return self.db.was_examined(self.engine, target)

    def mark(self, target: str, outcome: str) -> None:
        self.db.mark_examined(self.engine, target, self.id, outcome)
        self.db.job_bump(self.id, examined=1)
        self.count("examined")

    # cancellation
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def sleep(self, seconds: float) -> None:
        if self._cancel.wait(seconds):
            raise Cancelled()


class JobManager:
    def __init__(self, db: dbmod.DB | None = None, workers: int = 3):
        self.db = db or dbmod.get()
        self.bus = EventBus()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self.active: dict[str, JobContext] = {}
        self._lock = threading.Lock()
        self.db.reap_stale_jobs()

    def submit(self, engine: str, params: dict) -> str:
        from .engines import ENGINES

        if engine not in ENGINES:
            raise ValueError(f"unknown engine '{engine}' (have: {', '.join(ENGINES)})")
        jid = uuid.uuid4().hex[:8]
        ctx = JobContext(self, jid, engine, params)
        self.db.job_create(jid, engine, params)
        with self._lock:
            self.active[jid] = ctx
        ctx.emit("job", state="running", params=params)
        self.pool.submit(self._run, ctx, ENGINES[engine])
        return jid

    def run_sync(self, engine: str, params: dict) -> JobContext:
        """Run a job in the current thread (CLI)."""
        from .engines import ENGINES

        jid = uuid.uuid4().hex[:8]
        ctx = JobContext(self, jid, engine, params)
        self.db.job_create(jid, engine, params)
        with self._lock:
            self.active[jid] = ctx
        ctx.emit("job", state="running", params=params)
        self._run(ctx, ENGINES[engine])
        return ctx

    def _run(self, ctx: JobContext, fn) -> None:
        status, error, summary = "complete", None, {}
        try:
            summary = fn(ctx) or {}
        except Cancelled:
            status = "cancelled"
            ctx.log("job cancelled", level="warn", src="SYS")
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
            ctx.log(error, level="error", src="SYS")
            ctx.log(traceback.format_exc().splitlines()[-3].strip(), level="debug", src="SYS")
        summary = {**summary, **dict(ctx.counters), "elapsed_s": round(time.time() - ctx.started, 1)}
        self.db.job_finish(ctx.id, status, error, summary)
        ctx.emit("job", state=status, error=error, summary=summary)
        with self._lock:
            self.active.pop(ctx.id, None)

    def cancel(self, jid: str) -> bool:
        with self._lock:
            ctx = self.active.get(jid)
        if ctx:
            ctx._cancel.set()
            return True
        return False

    def running(self) -> list[dict]:
        with self._lock:
            return [
                {"id": c.id, "engine": c.engine, "params": c.params, "counters": dict(c.counters), "started": c.started}
                for c in self.active.values()
            ]
