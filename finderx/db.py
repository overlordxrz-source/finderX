"""SQLite persistence: jobs, examined targets, candidates and their plots."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from typing import Any

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, engine TEXT, params TEXT, status TEXT,
  created REAL, finished REAL, examined INTEGER DEFAULT 0,
  candidates INTEGER DEFAULT 0, error TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS examined(
  engine TEXT, target TEXT, job TEXT, at REAL, outcome TEXT,
  PRIMARY KEY(engine, target)
);
CREATE TABLE IF NOT EXISTS candidates(
  id TEXT PRIMARY KEY, seq INTEGER, dedupe TEXT UNIQUE, job TEXT,
  engine TEXT, kind TEXT, target TEXT, ra REAL, dec REAL,
  title TEXT, subtitle TEXT, score REAL, status TEXT DEFAULT 'new',
  known TEXT, metrics TEXT, flags TEXT, payload TEXT,
  created REAL, updated REAL, note TEXT, analyst TEXT
);
CREATE INDEX IF NOT EXISTS cand_status ON candidates(status, score DESC);
CREATE TABLE IF NOT EXISTS payloads(id TEXT PRIMARY KEY, data BLOB);
CREATE TABLE IF NOT EXISTS results(job TEXT, idx INTEGER, data TEXT);
CREATE INDEX IF NOT EXISTS results_job ON results(job);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS events(sector INTEGER, t REAL, target TEXT);
CREATE INDEX IF NOT EXISTS events_st ON events(sector, t);
"""

PREFIX = {"transit": "T", "variable": "V", "stellar": "S", "galaxy": "G", "solar": "A"}
STATUSES = ("new", "confirmed", "rejected", "flagged")


class DB:
    def __init__(self, path=None):
        path = path or config.DB_PATH
        if str(path) != ":memory:":
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self.all(sql, args)
        return rows[0] if rows else None

    # ── jobs ───────────────────────────────────────────────────────────
    def job_create(self, jid: str, engine: str, params: dict) -> None:
        self.x(
            "INSERT INTO jobs(id, engine, params, status, created) VALUES(?,?,?,?,?)",
            (jid, engine, json.dumps(params), "running", time.time()),
        )

    def job_finish(self, jid: str, status: str, error: str | None = None, summary: dict | None = None) -> None:
        self.x(
            "UPDATE jobs SET status=?, finished=?, error=?, summary=? WHERE id=?",
            (status, time.time(), error, json.dumps(summary or {}), jid),
        )

    def job_bump(self, jid: str, examined: int = 0, candidates: int = 0) -> None:
        self.x(
            "UPDATE jobs SET examined=examined+?, candidates=candidates+? WHERE id=?",
            (examined, candidates, jid),
        )

    def jobs(self, limit: int = 30) -> list[dict]:
        rows = self.all("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,))
        for r in rows:
            r["params"] = json.loads(r["params"] or "{}")
            r["summary"] = json.loads(r["summary"] or "{}")
        return rows

    def reap_stale_jobs(self) -> None:
        self.x("UPDATE jobs SET status='interrupted', finished=? WHERE status='running'", (time.time(),))

    # ── examined targets ───────────────────────────────────────────────
    def was_examined(self, engine: str, target: str) -> bool:
        return self.one("SELECT 1 AS x FROM examined WHERE engine=? AND target=?", (engine, target)) is not None

    def examined_set(self, engine: str) -> set[str]:
        return {r["target"] for r in self.all("SELECT target FROM examined WHERE engine=?", (engine,))}

    def mark_examined(self, engine: str, target: str, job: str, outcome: str) -> None:
        self.x(
            "INSERT OR REPLACE INTO examined(engine, target, job, at, outcome) VALUES(?,?,?,?,?)",
            (engine, target, job, time.time(), outcome),
        )

    # ── candidates ─────────────────────────────────────────────────────
    def candidate_upsert(self, c: dict) -> tuple[str, bool]:
        """Insert or refresh a candidate. Returns (id, is_new)."""
        now = time.time()
        with self._lock:
            row = self.one("SELECT id FROM candidates WHERE dedupe=?", (c["dedupe"],))
            fields = (
                c.get("job"), c["engine"], c["kind"], c["target"], c.get("ra"), c.get("dec"),
                c["title"], c.get("subtitle", ""), float(c.get("score", 0)),
                json.dumps(c.get("known", [])), json.dumps(c.get("metrics", {})),
                json.dumps(c.get("flags", [])), c.get("payload"),
            )
            if row:
                self.x(
                    "UPDATE candidates SET job=?, engine=?, kind=?, target=?, ra=?, dec=?, title=?, subtitle=?,"
                    " score=?, known=?, metrics=?, flags=?, payload=?, updated=? WHERE id=?",
                    (*fields, now, row["id"]),
                )
                return row["id"], False
            seq = (self.one("SELECT MAX(seq) AS m FROM candidates") or {}).get("m") or 0
            seq += 1
            cid = f"FX{PREFIX.get(c['engine'], 'X')}-{seq:04d}"
            self.x(
                "INSERT INTO candidates(id, seq, dedupe, job, engine, kind, target, ra, dec, title, subtitle,"
                " score, known, metrics, flags, payload, created, updated, status)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, seq, c["dedupe"], *fields, now, now, c.get("status", "new")),
            )
            return cid, True

    def has_candidate(self, dedupe: str) -> bool:
        return self.one("SELECT 1 AS x FROM candidates WHERE dedupe=?", (dedupe,)) is not None

    def candidates(
        self, status: str | None = None, engine: str | None = None, kind: str | None = None,
        limit: int = 300, order: str = "score",
    ) -> list[dict]:
        where, args = [], []
        if status:
            where.append("status IN (%s)" % ",".join("?" * len(status.split(","))))
            args += status.split(",")
        if engine:
            where.append("engine=?")
            args.append(engine)
        if kind:
            where.append("kind=?")
            args.append(kind)
        sql = "SELECT id, seq, job, engine, kind, target, ra, dec, title, subtitle, score, status, known, metrics, flags, created, updated, note, analyst FROM candidates"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + ("score DESC, created DESC" if order == "score" else "created DESC")
        sql += " LIMIT ?"
        args.append(limit)
        return [_decode(r) for r in self.all(sql, tuple(args))]

    def candidate(self, cid: str) -> dict | None:
        r = self.one("SELECT * FROM candidates WHERE id=?", (cid,))
        if not r:
            return None
        r = _decode(r)
        if r.get("payload"):
            r["plots"] = self.payload_get(r["payload"])
        return r

    def vote(self, cid: str, status: str, note: str | None = None) -> bool:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        cur = self.x(
            "UPDATE candidates SET status=?, note=COALESCE(?, note), updated=? WHERE id=?",
            (status, note, time.time(), cid),
        )
        return cur.rowcount > 0

    def set_analyst_note(self, cid: str, text: str) -> bool:
        cur = self.x("UPDATE candidates SET analyst=?, updated=? WHERE id=?", (text, time.time(), cid))
        return cur.rowcount > 0

    # ── bundles: share a set of candidates (with plots and notes) ──────
    def export_bundle(self, exclude_kinds: tuple[str, ...] = ("systematic",)) -> dict:
        import base64

        rows = [r for r in self.all("SELECT * FROM candidates ORDER BY seq") if r["kind"] not in exclude_kinds]
        pids = {r["payload"] for r in rows if r.get("payload")}
        payloads = {}
        for pid in pids:
            blob = self.one("SELECT data FROM payloads WHERE id=?", (pid,))
            if blob:
                payloads[pid] = base64.b64encode(blob["data"]).decode()
        return {"format": "finderx-bundle/1", "candidates": rows, "payloads": payloads}

    def import_bundle(self, bundle: dict) -> tuple[int, int]:
        """Add a bundle's candidates; existing ones (same signal) are left alone."""
        import base64

        added = skipped = 0
        cols = [c[1] for c in self._conn.execute("PRAGMA table_info(candidates)").fetchall()]
        with self._lock:
            for pid, b64 in bundle.get("payloads", {}).items():
                self._conn.execute("INSERT OR IGNORE INTO payloads(id, data) VALUES(?,?)", (pid, base64.b64decode(b64)))
            for r in bundle.get("candidates", []):
                if self.has_candidate(r["dedupe"]):
                    skipped += 1
                    continue
                r = {k: v for k, v in r.items() if k in cols}
                if self.one("SELECT 1 AS x FROM candidates WHERE id=?", (r["id"],)):
                    seq = ((self.one("SELECT MAX(seq) AS m FROM candidates") or {}).get("m") or 0) + 1
                    r["seq"] = seq
                    r["id"] = r["id"].split("-")[0] + f"-{seq:04d}"
                keys = list(r)
                self._conn.execute(
                    f"INSERT INTO candidates({','.join(keys)}) VALUES({','.join('?' * len(keys))})", tuple(r[k] for k in keys)
                )
                added += 1
        return added, skipped

    # ── transit-event register (common-mode systematics) ──────────────
    def events_add(self, sector: int, target: str, times: list[float]) -> None:
        with self._lock:
            self._conn.executemany("INSERT INTO events(sector, t, target) VALUES(?,?,?)", [(sector, float(t), target) for t in times])

    def events_others(self, sector: int, target: str, t: float, tol: float) -> int:
        """Distinct *other* stars with a transit-like event within ±tol days."""
        r = self.one(
            "SELECT COUNT(DISTINCT target) AS n FROM events WHERE sector=? AND t BETWEEN ? AND ? AND target<>?",
            (sector, t - tol, t + tol, target),
        )
        return int((r or {}).get("n") or 0)

    def candidate_reclassify(self, cid: str, kind: str, flags: list[str], score: float) -> None:
        self.x("UPDATE candidates SET kind=?, flags=?, score=?, updated=? WHERE id=?", (kind, json.dumps(flags), score, time.time(), cid))

    # ── payloads / results / meta ──────────────────────────────────────
    def payload_put(self, pid: str, data: dict) -> str:
        blob = zlib.compress(json.dumps(data, separators=(",", ":")).encode(), 6)
        self.x("INSERT OR REPLACE INTO payloads(id, data) VALUES(?,?)", (pid, blob))
        return pid

    def payload_get(self, pid: str) -> dict | None:
        r = self.one("SELECT data FROM payloads WHERE id=?", (pid,))
        return json.loads(zlib.decompress(r["data"])) if r else None

    def result_add(self, job: str, idx: int, data: dict) -> None:
        self.x("INSERT INTO results(job, idx, data) VALUES(?,?,?)", (job, idx, json.dumps(data)))

    def results(self, job: str) -> list[dict]:
        return [json.loads(r["data"]) for r in self.all("SELECT data FROM results WHERE job=? ORDER BY idx", (job,))]

    def meta_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def meta_set(self, key: str, value: Any) -> None:
        self.x("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, json.dumps(value)))

    def stats(self) -> dict:
        c = {r["status"]: r["n"] for r in self.all("SELECT status, COUNT(*) AS n FROM candidates GROUP BY status")}
        k = {r["kind"]: r["n"] for r in self.all("SELECT kind, COUNT(*) AS n FROM candidates GROUP BY kind")}
        e = {r["engine"]: r["n"] for r in self.all("SELECT engine, COUNT(*) AS n FROM examined GROUP BY engine")}
        return {
            "candidates": c,
            "kinds": k,
            "examined": e,
            "examined_total": sum(e.values()),
            "jobs": (self.one("SELECT COUNT(*) AS n FROM jobs") or {}).get("n", 0),
        }


def _decode(r: dict) -> dict:
    for k in ("known", "metrics", "flags"):
        if k in r and isinstance(r[k], str):
            r[k] = json.loads(r[k] or "null") or ([] if k != "metrics" else {})
    return r


_db: DB | None = None
_db_lock = threading.Lock()


def get() -> DB:
    global _db
    with _db_lock:
        if _db is None:
            _db = DB()
        return _db
